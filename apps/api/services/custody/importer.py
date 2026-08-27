"""Custody import — parse, resolve, dry-run diff, commit. Sprint fee31.

THE FOUR STAGES, AND WHY DRY-RUN IS A SEPARATE ONE
──────────────────────────────────────────────────────────────────────────────
    parse    bytes → records + row errors            (the adapter)
    resolve  refs → real entity/household ids        (here, org-scoped)
    diff     records + resolutions → what WOULD change, writing nothing
    commit   the same plan, applied in ONE transaction

``dry_run`` and ``commit`` share :func:`build_plan`, so the diff an operator
approves is computed by the identical code path that then writes. A dry-run
that re-derived the change set independently would be a preview of a *different*
import, and the one case where that matters is exactly the case where it is
hardest to notice.

WHERE org_id COMES FROM
──────────────────────────────────────────────────────────────────────────────
Always an argument, always from the caller's session at the router. Never from
the uploaded file, never from a request body, never inherited from whatever the
connection's GUC happens to be set to. The uploaded file is attacker-influenced
by definition — it is a file — so a tenant id read out of it would be a
cross-tenant write reachable by anyone with access to the upload form.

IDEMPOTENCY, PER TABLE, FROM THE DEPLOYED SCHEMA
──────────────────────────────────────────────────────────────────────────────
* accounts               ON CONFLICT on the deployed partial unique index
                         (org_id, custodian_code, account_number_hash)
                         WHERE system_to IS NULL
* account_balances_daily ON CONFLICT on the PRIMARY KEY, which already IS the
                         natural key (org_id, account_id, as_of_date,
                         source_system)
* account_flows          had NO unique key at all. This sprint's addendum
                         migration adds source_row_hash + a partial unique
                         index; :func:`flow_row_hash` computes the fingerprint.

"Zero new rows on re-import" is therefore enforced by the database, not by a
Python ``if`` that a concurrent second upload could race past.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from services.custody.base import (
    AccountNumber,
    AccountRecord,
    BalanceRecord,
    CustodyError,
    FlowRecord,
    RowError,
)
from services.custody.registry import build_adapter, get_or_create_salt

logger = logging.getLogger(__name__)

READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_billing"

STATUS_DRY_RUN = "DRY_RUN"
STATUS_COMMITTED = "COMMITTED"

TABLE_ACCOUNTS = "public.accounts"
TABLE_BALANCES = "public.account_balances_daily"
TABLE_FLOWS = "public.account_flows"
TABLE_BATCHES = "public.account_import_batches"
TABLE_EXCEPTIONS = "public.account_import_exceptions"

#: accounts columns an incoming file may restate on an existing account. The
#: identity columns (org_id, custodian_code, account_number_hash/masked) are
#: deliberately absent: changing one of those does not correct an account, it
#: describes a DIFFERENT account, and letting a file rewrite them would silently
#: repoint an account's history at another client's account.
UPDATABLE_ACCOUNT_FIELDS = (
    "custodian_account_id",
    "registration_type",
    "tax_status",
    "primary_entity_id",
    "household_id",
    "service_model",
    "is_billable",
    "is_discretionary",
    "is_held_away",
    "opened_on",
    "closed_on",
    "base_currency",
)


class ImportError_(CustodyError):
    """The FILE or the request was unusable — distinct from a bad row."""


def flow_row_hash(
    account_number_hash: str, record: FlowRecord
) -> str:
    """Stable per-occurrence fingerprint for one flow.

    Folds in ``occurrence`` — the index of this flow within its own (account,
    date, amount, type) group in the source file — which is what separates
    "the same file again" from "a second, genuinely identical deposit". A
    fingerprint over just the four business values would silently discard the
    second of two real $500 deposits on the same day; one over the file's line
    number would stop deduplicating the moment a row was inserted above it.

    Uses the account's HASH, never its number: this value is stored in a
    database column, so it must not be derived from anything that could be
    brute-forced back to the account number.
    """
    payload = "|".join(
        (
            account_number_hash,
            record.flow_date.isoformat(),
            str(record.amount),
            record.flow_type,
            record.source_system,
            str(record.occurrence),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# The plan
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class AccountPlan:
    record: AccountRecord
    account_number_hash: str
    primary_entity_id: str | None
    household_id: str | None
    existing_id: str | None = None
    changes: dict[str, Any] = field(default_factory=dict)

    @property
    def action(self) -> str:
        if self.existing_id is None:
            return "create"
        return "update" if self.changes else "unchanged"


@dataclass
class BalancePlan:
    record: BalanceRecord
    account_number_hash: str
    action: str          # create | update | unchanged
    previous: dict[str, Any] | None = None


@dataclass
class FlowPlan:
    record: FlowRecord
    account_number_hash: str
    source_row_hash: str
    action: str          # create | duplicate


@dataclass
class ImportPlan:
    """Everything the commit will do, plus everything it will refuse to do."""

    custodian_code: str
    source_system: str
    filename: str | None
    file_row_count: int
    accounts: list[AccountPlan] = field(default_factory=list)
    balances: list[BalancePlan] = field(default_factory=list)
    flows: list[FlowPlan] = field(default_factory=list)
    exceptions: list[RowError] = field(default_factory=list)

    # ── Counts written onto the batch row ────────────────────────────────
    @property
    def matched_count(self) -> int:
        return len(self.accounts) + len(self.balances) + len(self.flows)

    @property
    def unmatched_count(self) -> int:
        return len(self.exceptions)

    @property
    def record_count(self) -> int:
        return self.matched_count + self.unmatched_count

    def to_json(self) -> dict[str, Any]:
        """The dry-run response. Every account number in here is masked —
        each record's own ``summary()`` is the only serialiser used."""
        return {
            "custodian_code": self.custodian_code,
            "source_system": self.source_system,
            "filename": self.filename,
            "file_row_count": self.file_row_count,
            "counts": {
                "accounts_new": sum(1 for a in self.accounts if a.action == "create"),
                "accounts_changed": sum(1 for a in self.accounts if a.action == "update"),
                "accounts_unchanged": sum(
                    1 for a in self.accounts if a.action == "unchanged"
                ),
                "balances_new": sum(1 for b in self.balances if b.action == "create"),
                "balances_changed": sum(1 for b in self.balances if b.action == "update"),
                "balances_unchanged": sum(
                    1 for b in self.balances if b.action == "unchanged"
                ),
                "flows_new": sum(1 for f in self.flows if f.action == "create"),
                "flows_duplicate": sum(1 for f in self.flows if f.action == "duplicate"),
                "unmatched": self.unmatched_count,
                "records_total": self.record_count,
            },
            "accounts": [
                {**a.record.summary(), "action": a.action,
                 "existing_account_id": a.existing_id,
                 "primary_entity_id": a.primary_entity_id,
                 "household_id": a.household_id,
                 "changes": {k: _jsonable(v) for k, v in a.changes.items()}}
                for a in self.accounts
            ],
            "balances": [
                {**b.record.summary(), "action": b.action, "previous": b.previous}
                for b in self.balances
            ],
            "flows": [
                {**f.record.summary(), "action": f.action} for f in self.flows
            ],
            "unmatched": [
                {
                    "source_row": e.source_row,
                    "record_kind": e.record_kind,
                    "reason_code": e.reason_code,
                    "reason": e.reason,
                    "raw": e.raw,
                }
                for e in self.exceptions
            ],
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


# ═══════════════════════════════════════════════════════════════════════════
# Resolution — the org-scoped half
# ═══════════════════════════════════════════════════════════════════════════


async def _resolve_entity(conn, org_id: str, ref: str | None) -> str | None:
    """A source's entity reference → an ``entities.id`` IN THIS ORG, or None.

    Every lookup carries ``org_id`` explicitly, including the uuid branch. A
    uuid that exists in another tenant must resolve to None here and become an
    exception — accepting it would let a file name a foreign entity and attach a
    billable account to it. RLS would also refuse, but as a 23503 foreign-key
    error that fails the whole batch instead of one row; being explicit turns
    it into the reviewable exception the sprint asks for.
    """
    if not ref:
        return None
    text = str(ref).strip()
    if not text:
        return None

    try:
        candidate = str(uuid.UUID(text))
    except (ValueError, AttributeError):
        candidate = None

    if candidate:
        return await conn.fetchval(
            "SELECT id::text FROM public.entities "
            "WHERE id = $1::uuid AND org_id = $2::uuid "
            "  AND valid_to IS NULL AND system_to IS NULL",
            candidate, org_id,
        )

    # Name lookup. An ambiguous name resolves to NOTHING rather than to the
    # first match: picking one of two clients called "Smith Family Trust" is a
    # billing error that nobody would catch, whereas an exception row is read.
    rows = await conn.fetch(
        "SELECT id::text FROM public.entities "
        "WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
        "  AND (lower(display_name) = lower($2) OR lower(legal_name) = lower($2)) "
        "LIMIT 2",
        org_id, text,
    )
    return rows[0]["id"] if len(rows) == 1 else None


async def _resolve_household(conn, org_id: str, ref: str | None) -> str | None:
    """Same contract as :func:`_resolve_entity`, for households.

    ``accounts.household_id`` is NULLABLE, so an unresolved household is NOT an
    exception — it is an account without a household, which is a real and common
    state. Introspection confirmed the households table is empty in every org
    today, so requiring one would fail every import ever run.
    """
    if not ref:
        return None
    text = str(ref).strip()
    if not text:
        return None
    try:
        candidate = str(uuid.UUID(text))
    except (ValueError, AttributeError):
        candidate = None

    if candidate:
        return await conn.fetchval(
            "SELECT id::text FROM public.households "
            "WHERE id = $1::uuid AND org_id = $2::uuid",
            candidate, org_id,
        )
    rows = await conn.fetch(
        "SELECT id::text FROM public.households "
        "WHERE org_id = $1::uuid AND lower(name) = lower($2) LIMIT 2",
        org_id, text,
    )
    return rows[0]["id"] if len(rows) == 1 else None


# ═══════════════════════════════════════════════════════════════════════════
# Plan construction
# ═══════════════════════════════════════════════════════════════════════════


async def build_plan(
    conn,
    *,
    org_id: str,
    custodian_code: str,
    file_bytes: bytes,
    filename: str | None = None,
    column_map_override: dict[str, dict[str, str]] | None = None,
) -> ImportPlan:
    """Parse + resolve + diff. Reads the database; writes nothing to it.

    One exception to "writes nothing": :func:`get_or_create_salt` mints the
    org's salt on first use. That has to happen before any hash can be computed,
    including the ones the dry-run displays, and minting it is idempotent —
    a dry-run that established the salt and was then abandoned leaves an org
    with a salt and no accounts, which is the harmless half of the trade.
    """
    if not org_id:
        raise ImportError_(
            "org_id is required and must come from the caller's session, never "
            "from the uploaded file or a request body"
        )
    org_id = str(org_id)

    adapter, profile = await build_adapter(
        conn, org_id, custodian_code,
        file_bytes=file_bytes, filename=filename,
        column_map_override=column_map_override,
    )
    salt = await get_or_create_salt(conn, org_id)

    plan = ImportPlan(
        custodian_code=custodian_code,
        source_system=profile.source_system,
        filename=filename,
        file_row_count=getattr(adapter, "row_count", 0),
    )

    # ── Accounts ─────────────────────────────────────────────────────────
    account_outcome = adapter.fetch_accounts()
    plan.exceptions.extend(account_outcome.errors)

    hashes_in_file: dict[str, AccountPlan] = {}
    for record in account_outcome.records:
        number_hash = record.account_number.hashed(salt)
        entity_id = await _resolve_entity(conn, org_id, record.primary_entity_ref)
        household_id = await _resolve_household(conn, org_id, record.household_ref)
        existing = await _load_existing_account(conn, org_id, custodian_code, number_hash)

        if entity_id is None and existing is None:
            # accounts.primary_entity_id is NOT NULL with an FK — a new account
            # with no resolvable owner CANNOT be inserted. Exception, not a
            # failed batch. An EXISTING account keeps the owner it already has.
            plan.exceptions.append(
                _entity_exception(record, "account")
            )
            continue

        account_plan = AccountPlan(
            record=record,
            account_number_hash=number_hash,
            primary_entity_id=entity_id,
            household_id=household_id,
            existing_id=existing["id"] if existing else None,
        )
        if existing is not None:
            account_plan.changes = _account_changes(existing, record, entity_id, household_id)
        plan.accounts.append(account_plan)
        hashes_in_file[number_hash] = account_plan

    # ── Balances ─────────────────────────────────────────────────────────
    # Ask the adapter which dates the file holds rather than assuming a range.
    # A file with no date column yields no dates and is stamped with today.
    balance_dates = adapter.balance_dates() or [date.today()]
    seen_balance_errors: set[tuple[int, str]] = set()
    for as_of in balance_dates:
        outcome = adapter.fetch_balances(as_of)
        for error in outcome.errors:
            # fetch_balances is called once per date and re-reads every row, so
            # a malformed row would be reported once per date in the file —
            # 30 identical exceptions for one bad line.
            key = (error.source_row, error.reason_code)
            if key not in seen_balance_errors:
                seen_balance_errors.add(key)
                plan.exceptions.append(error)

        for record in outcome.records:
            number_hash = record.account_number.hashed(salt)
            account_id = await _account_id_for(
                conn, org_id, custodian_code, number_hash, hashes_in_file
            )
            if account_id is _PENDING:
                plan.balances.append(
                    BalancePlan(record, number_hash, "create")
                )
                continue
            if account_id is None:
                plan.exceptions.append(_unmatched_account_exception(record, "balance"))
                continue
            previous = await conn.fetchrow(
                f"""
                SELECT total_market_value, cash_value, margin_balance, accrued_income
                FROM {TABLE_BALANCES}
                WHERE org_id = $1::uuid AND account_id = $2::uuid
                  AND as_of_date = $3 AND source_system = $4
                """,
                org_id, account_id, record.as_of_date, record.source_system,
            )
            if previous is None:
                plan.balances.append(BalancePlan(record, number_hash, "create"))
            elif _balance_differs(previous, record):
                plan.balances.append(
                    BalancePlan(
                        record, number_hash, "update",
                        previous={k: str(v) for k, v in dict(previous).items()},
                    )
                )
            else:
                plan.balances.append(BalancePlan(record, number_hash, "unchanged"))

    # ── Flows ────────────────────────────────────────────────────────────
    flow_range = adapter.flow_date_range()
    if flow_range is not None:
        outcome = adapter.fetch_flows(flow_range[0], flow_range[1])
        plan.exceptions.extend(outcome.errors)
        for record in outcome.records:
            number_hash = record.account_number.hashed(salt)
            row_hash = flow_row_hash(number_hash, record)
            account_id = await _account_id_for(
                conn, org_id, custodian_code, number_hash, hashes_in_file
            )
            if account_id is _PENDING:
                plan.flows.append(FlowPlan(record, number_hash, row_hash, "create"))
                continue
            if account_id is None:
                plan.exceptions.append(_unmatched_account_exception(record, "flow"))
                continue
            exists = await conn.fetchval(
                f"""
                SELECT 1 FROM {TABLE_FLOWS}
                WHERE org_id = $1::uuid AND account_id = $2::uuid
                  AND source_system = $3 AND source_row_hash = $4
                  AND system_to IS NULL
                """,
                org_id, account_id, record.source_system, row_hash,
            )
            plan.flows.append(
                FlowPlan(record, number_hash, row_hash,
                         "duplicate" if exists else "create")
            )

    plan.exceptions.sort(key=lambda e: (e.source_row, e.record_kind))
    return plan


#: Sentinel: the account is not in the database YET but IS being created by
#: this same file, so its balances and flows are matched, not unmatched.
#: Distinct from None (genuinely unresolvable) — collapsing the two would turn
#: every balance in a first-time import into an exception.
_PENDING = object()


async def _account_id_for(
    conn, org_id: str, custodian_code: str, number_hash: str,
    hashes_in_file: dict[str, AccountPlan],
):
    plan = hashes_in_file.get(number_hash)
    if plan is not None:
        return plan.existing_id if plan.existing_id else _PENDING
    return await conn.fetchval(
        f"""
        SELECT id::text FROM {TABLE_ACCOUNTS}
        WHERE org_id = $1::uuid AND custodian_code = $2
          AND account_number_hash = $3 AND system_to IS NULL
        """,
        org_id, custodian_code, number_hash,
    )


async def _load_existing_account(conn, org_id: str, custodian_code: str, number_hash: str):
    return await conn.fetchrow(
        f"""
        SELECT id::text AS id, custodian_account_id, registration_type, tax_status,
               primary_entity_id::text AS primary_entity_id,
               household_id::text AS household_id, service_model,
               is_billable, is_discretionary, is_held_away,
               opened_on, closed_on, base_currency
        FROM {TABLE_ACCOUNTS}
        WHERE org_id = $1::uuid AND custodian_code = $2
          AND account_number_hash = $3 AND system_to IS NULL
        """,
        org_id, custodian_code, number_hash,
    )


def _account_changes(existing, record: AccountRecord, entity_id, household_id) -> dict[str, Any]:
    """Which updatable fields the file actually restates differently.

    A field the file does not carry arrives as None/UNKNOWN from the adapter's
    defaults, and is NOT treated as a change: an export that omits
    ``service_model`` must not blank the value someone set by hand. Only a
    present, different value counts.
    """
    incoming = {
        "custodian_account_id": record.custodian_account_id,
        "registration_type": record.registration_type,
        "tax_status": record.tax_status,
        "primary_entity_id": entity_id,
        "household_id": household_id,
        "service_model": record.service_model,
        "is_billable": record.is_billable,
        "is_discretionary": record.is_discretionary,
        "is_held_away": record.is_held_away,
        "opened_on": record.opened_on,
        "closed_on": record.closed_on,
        "base_currency": record.base_currency,
    }
    changes: dict[str, Any] = {}
    for name in UPDATABLE_ACCOUNT_FIELDS:
        new = incoming.get(name)
        if new is None or new == "UNKNOWN":
            continue
        if existing[name] != new:
            changes[name] = new
    return changes


def _balance_differs(previous, record: BalanceRecord) -> bool:
    return (
        previous["total_market_value"] != record.total_market_value
        or previous["cash_value"] != record.cash_value
        or previous["margin_balance"] != record.margin_balance
        or previous["accrued_income"] != record.accrued_income
    )


def _entity_exception(record: AccountRecord, kind: str) -> RowError:
    return RowError(
        source_row=record.source_row,
        record_kind=kind,
        reason_code="unresolved_entity",
        reason=(
            f"no unique active entity in this org matches "
            f"{record.primary_entity_ref!r}. accounts.primary_entity_id is NOT "
            f"NULL, so this account cannot be created until the owner exists "
            f"or the reference is corrected. The rest of the file was imported."
        ),
        raw=record.summary(),
    )


def _unmatched_account_exception(record, kind: str) -> RowError:
    return RowError(
        source_row=record.source_row,
        record_kind=kind,
        reason_code="unmatched_account",
        reason=(
            f"no account {record.account_number.masked} exists for this "
            f"custodian, and the file does not create one. The row was kept "
            f"here rather than dropped so it can be re-driven once the account "
            f"is imported."
        ),
        raw=record.summary(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Commit
# ═══════════════════════════════════════════════════════════════════════════


async def commit_plan(
    conn, *, org_id: str, plan: ImportPlan, imported_by: str | None
) -> dict[str, Any]:
    """Write the batch and everything in the plan, in ONE transaction.

    ``SET LOCAL app.current_org_id`` is raised inside that transaction — the RLS
    policy's WITH CHECK is what actually stops a write landing in the wrong
    tenant, and it needs the GUC to compare against. Every statement ALSO names
    ``org_id`` explicitly, because the GUC is set FROM the argument and so RLS
    cannot catch a *wrong* org_id, only a missing one.

    The batch row, the accounts, the balances, the flows and the EXCEPTIONS all
    share the transaction. Committing the rows without their exception list
    would produce a batch that silently under-reports what it dropped, which is
    the precise failure the sprint forbids.
    """
    org_id = str(org_id)
    written = {
        "accounts_created": 0, "accounts_updated": 0,
        "balances_created": 0, "balances_updated": 0,
        "flows_created": 0, "flows_skipped_duplicate": 0,
        "exceptions": 0,
    }

    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true)", org_id
        )

        batch_id = await conn.fetchval(
            f"""
            INSERT INTO {TABLE_BATCHES}
                (org_id, custodian_code, source_filename, imported_by,
                 row_count, matched_count, unmatched_count, status)
            VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7, $8)
            RETURNING id::text
            """,
            org_id, plan.custodian_code, plan.filename,
            str(imported_by) if imported_by else None,
            plan.record_count, plan.matched_count, plan.unmatched_count,
            STATUS_COMMITTED,
        )

        # ── Accounts. Resolved ids are collected for the child rows. ─────
        account_ids: dict[str, str] = {}
        for account_plan in plan.accounts:
            account_id = await _write_account(conn, org_id, account_plan, imported_by)
            account_ids[account_plan.account_number_hash] = account_id
            if account_plan.action == "create":
                written["accounts_created"] += 1
            elif account_plan.action == "update":
                written["accounts_updated"] += 1

        async def account_id_for(number_hash: str) -> str | None:
            if number_hash in account_ids:
                return account_ids[number_hash]
            found = await conn.fetchval(
                f"SELECT id::text FROM {TABLE_ACCOUNTS} "
                f"WHERE org_id = $1::uuid AND custodian_code = $2 "
                f"  AND account_number_hash = $3 AND system_to IS NULL",
                org_id, plan.custodian_code, number_hash,
            )
            if found:
                account_ids[number_hash] = found
            return found

        # ── Balances ────────────────────────────────────────────────────
        for balance_plan in plan.balances:
            if balance_plan.action == "unchanged":
                continue
            account_id = await account_id_for(balance_plan.account_number_hash)
            if account_id is None:
                continue
            record = balance_plan.record
            result = await conn.fetchval(
                f"""
                INSERT INTO {TABLE_BALANCES}
                    (org_id, account_id, as_of_date, total_market_value,
                     cash_value, margin_balance, accrued_income, source_system,
                     source_confidence, is_billing_source, is_final)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (org_id, account_id, as_of_date, source_system)
                DO UPDATE SET
                    total_market_value = EXCLUDED.total_market_value,
                    cash_value         = EXCLUDED.cash_value,
                    margin_balance     = EXCLUDED.margin_balance,
                    accrued_income     = EXCLUDED.accrued_income,
                    source_confidence  = EXCLUDED.source_confidence,
                    is_billing_source  = EXCLUDED.is_billing_source,
                    is_final           = EXCLUDED.is_final
                RETURNING (xmax = 0) AS inserted
                """,
                org_id, account_id, record.as_of_date, record.total_market_value,
                record.cash_value, record.margin_balance, record.accrued_income,
                record.source_system, record.source_confidence,
                record.is_billing_source, record.is_final,
            )
            # xmax = 0 distinguishes a fresh INSERT from a DO UPDATE, so the
            # reported counts are what the database did rather than what the
            # plan predicted. A concurrent import between dry-run and commit
            # makes those two different, and the honest number is this one.
            if result:
                written["balances_created"] += 1
            else:
                written["balances_updated"] += 1

        # ── Flows ───────────────────────────────────────────────────────
        for flow_plan in plan.flows:
            if flow_plan.action == "duplicate":
                written["flows_skipped_duplicate"] += 1
                continue
            account_id = await account_id_for(flow_plan.account_number_hash)
            if account_id is None:
                continue
            record = flow_plan.record
            inserted = await conn.fetchval(
                f"""
                INSERT INTO {TABLE_FLOWS}
                    (org_id, account_id, flow_date, amount, flow_type,
                     is_billable_flow, source_system, import_batch_id,
                     source_row_hash)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::uuid, $9)
                ON CONFLICT (org_id, account_id, source_system, source_row_hash)
                    WHERE system_to IS NULL AND source_row_hash IS NOT NULL
                DO NOTHING
                RETURNING id::text
                """,
                org_id, account_id, record.flow_date, record.amount,
                record.flow_type, record.is_billable_flow, record.source_system,
                batch_id, flow_plan.source_row_hash,
            )
            if inserted:
                written["flows_created"] += 1
            else:
                written["flows_skipped_duplicate"] += 1

        # ── Exceptions ──────────────────────────────────────────────────
        for exception in plan.exceptions:
            await conn.execute(
                f"""
                INSERT INTO {TABLE_EXCEPTIONS}
                    (org_id, batch_id, source_row, record_kind, reason_code,
                     reason, raw_row)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb)
                """,
                org_id, batch_id, exception.source_row, exception.record_kind,
                exception.reason_code, exception.reason,
                json.dumps(exception.raw, default=_jsonable),
            )
            written["exceptions"] += 1
    except BaseException:
        await transaction.rollback()
        raise
    else:
        await transaction.commit()

    # Logged AFTER the commit and with counts only. No account number, masked or
    # otherwise, and no filename content beyond what the operator supplied —
    # check 6 of the verify script greps the log stream this produces.
    logger.info(
        "custody import committed: batch=%s org=%s custodian=%s %s",
        batch_id, org_id, plan.custodian_code, written,
    )
    return {"batch_id": batch_id, "status": STATUS_COMMITTED, **written}


async def _write_account(
    conn, org_id: str, account_plan: AccountPlan, imported_by: str | None
) -> str:
    """Insert or correct one account. Returns its id — which never changes.

    A correction archives on the SYSTEM axis: the outgoing version is copied to
    a NEW row stamped ``system_to = now()`` and the live row is updated in
    place, keeping the id. This is ``portfolio_securities.update_asset``'s
    pattern and it is chosen for the same reason — ``accounts.id`` is the target
    of three deployed foreign keys (account_owners, account_balances_daily,
    account_flows). A valid-axis restatement that minted a new id would leave
    every balance and every flow pointing at a row the ``system_to IS NULL``
    predicate no longer returns, i.e. an account whose entire history silently
    detaches the first time its registration type is corrected.
    """
    record = account_plan.record

    if account_plan.existing_id is None:
        return await conn.fetchval(
            f"""
            INSERT INTO {TABLE_ACCOUNTS}
                (org_id, account_number_masked, account_number_hash,
                 custodian_code, custodian_account_id, registration_type,
                 tax_status, primary_entity_id, household_id, service_model,
                 is_billable, is_discretionary, is_held_away, opened_on,
                 closed_on, base_currency, created_by)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::uuid, $9::uuid, $10,
                    $11, $12, $13, $14, $15, $16, $17::uuid)
            ON CONFLICT (org_id, custodian_code, account_number_hash)
                WHERE system_to IS NULL
            DO UPDATE SET updated_at = now()
            RETURNING id::text
            """,
            org_id, record.account_number.masked, account_plan.account_number_hash,
            record.custodian_code, record.custodian_account_id,
            record.registration_type, record.tax_status,
            account_plan.primary_entity_id, account_plan.household_id,
            record.service_model, record.is_billable, record.is_discretionary,
            record.is_held_away, record.opened_on, record.closed_on,
            record.base_currency, str(imported_by) if imported_by else None,
        )

    if not account_plan.changes:
        return account_plan.existing_id

    await conn.execute(
        f"""
        INSERT INTO {TABLE_ACCOUNTS}
            (org_id, account_number_masked, account_number_hash, custodian_code,
             custodian_account_id, registration_type, tax_status,
             primary_entity_id, household_id, advisor_of_record_id,
             service_model, is_billable, is_discretionary, is_held_away,
             opened_on, closed_on, base_currency, created_by, created_at,
             updated_at, valid_from, valid_to, system_from, system_to)
        SELECT a.org_id, a.account_number_masked, a.account_number_hash,
               a.custodian_code, a.custodian_account_id, a.registration_type,
               a.tax_status, a.primary_entity_id, a.household_id,
               a.advisor_of_record_id, a.service_model, a.is_billable,
               a.is_discretionary, a.is_held_away, a.opened_on, a.closed_on,
               a.base_currency, a.created_by, a.created_at, a.updated_at,
               a.valid_from, a.valid_to, a.system_from, now()
        FROM {TABLE_ACCOUNTS} a
        WHERE a.id = $1::uuid AND a.org_id = $2::uuid AND a.system_to IS NULL
        """,
        account_plan.existing_id, org_id,
    )

    ordered = [f for f in UPDATABLE_ACCOUNT_FIELDS if f in account_plan.changes]
    # Column names come from UPDATABLE_ACCOUNT_FIELDS, a tuple of literals in
    # this module. Nothing from the uploaded file is ever interpolated into SQL.
    assignments = ", ".join(f"{name} = ${i + 3}" for i, name in enumerate(ordered))
    await conn.execute(
        f"UPDATE {TABLE_ACCOUNTS} SET {assignments}, updated_at = now() "
        f"WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
        account_plan.existing_id, org_id,
        *[account_plan.changes[name] for name in ordered],
    )
    return account_plan.existing_id


# ═══════════════════════════════════════════════════════════════════════════
# Reads for the batch screen
# ═══════════════════════════════════════════════════════════════════════════


async def list_batches(conn, org_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        f"""
        SELECT b.id::text AS id, b.custodian_code, b.source_filename,
               b.row_count, b.matched_count, b.unmatched_count, b.status,
               b.created_at, b.imported_by::text AS imported_by
        FROM {TABLE_BATCHES} b
        WHERE b.org_id = $1::uuid
        ORDER BY b.created_at DESC
        LIMIT $2
        """,
        str(org_id), limit,
    )
    return [
        {**dict(r), "created_at": r["created_at"].isoformat()} for r in rows
    ]


async def get_batch(conn, org_id: str, batch_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"""
        SELECT b.id::text AS id, b.custodian_code, b.source_filename,
               b.row_count, b.matched_count, b.unmatched_count, b.status,
               b.created_at
        FROM {TABLE_BATCHES} b
        WHERE b.org_id = $1::uuid AND b.id = $2::uuid
        """,
        str(org_id), str(batch_id),
    )
    if row is None:
        return None
    exceptions = await conn.fetch(
        f"""
        SELECT source_row, record_kind, reason_code, reason, raw_row
        FROM {TABLE_EXCEPTIONS}
        WHERE org_id = $1::uuid AND batch_id = $2::uuid
        ORDER BY source_row, record_kind
        """,
        str(org_id), str(batch_id),
    )
    return {
        **dict(row),
        "created_at": row["created_at"].isoformat(),
        "exceptions": [
            {
                **dict(e),
                "raw_row": json.loads(e["raw_row"])
                if isinstance(e["raw_row"], str)
                else e["raw_row"],
            }
            for e in exceptions
        ],
    }
