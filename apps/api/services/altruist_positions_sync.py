"""Altruist Sprint 3 — positions/transactions sync for RESOLVED accounts.

WHAT THIS IS, AND WHAT IT IS NOT
────────────────────────────────────────────────────────────────────────────
For every Altruist account that Sprint 2 (``services.altruist_identity``)
already matched to a real Hollisworks account — i.e. a row genuinely exists
in ``portfolio.external_references`` (``source_system='ALTRUIST'``,
``record_type='account'``) — this module pulls that account's current
positions and transaction history from Altruist and writes them into
``portfolio.positions`` / ``portfolio.transactions``, the SAME tables
``services.portfolio_import`` (Phase B's flat-file importer) already writes
to for every other custodian/reporting-tool source. There is no new table.

An Altruist account still sitting in ``public.account_import_exceptions``
(unresolved) is never touched here: this module never reads that table and
never receives an external_id it did not get from
``portfolio.external_references`` — the enumeration query at the bottom of
this file is the only source of which accounts are in scope, and an
unresolved account has no row there to enumerate. See
``verify_altruist_sprint3_positions_transactions_sync.py`` Task 4 for the
proof this actually excludes an unresolved account, not just by construction.

[FIND] NO CUSTODIAN/CUSTODIAN_SYSTEM COLUMN EXISTS ON EITHER TABLE
────────────────────────────────────────────────────────────────────────────
The sprint's standing instructions say to tag Altruist-sourced rows with the
seeded ``reference_data`` codes ``custodian='ALT'`` /
``custodian_system='ALT-DEF'``. Measured against the live schema (not
assumed): ``portfolio.positions`` and ``portfolio.transactions`` have NO
``custodian_code`` or ``custodian_system`` column to put those literal codes
in. The only column either table has for provenance is ``source_system`` —
and on ``portfolio.positions`` that column carries a REAL, deployed CHECK
constraint (``positions_source_chk``) whose vocabulary
(``services.portfolio_assets.SOURCE_SYSTEMS``) already contains ``'altruist'``
(lowercase), added ahead of this sprint for exactly this integration.
Writing the literal string ``'ALT'`` into ``source_system`` would be REFUSED
by the database outright (positions), and would silently diverge from the
established vocabulary token everywhere else in this codebase (transactions,
which has no DB-level CHECK but enforces the same list in Python).

Per the sprint's own instruction not to improvise ledger-adjacent DDL
unsupervised, this module does NOT add a column. It uses
``source_system='altruist'`` — the one already-live, DB-legal token — as the
actual provenance tag on both tables, and surfaces ``CUSTODIAN_CODE`` /
``CUSTODIAN_SYSTEM_CODE`` (the ``reference_data`` codes) as constants any
caller (a future API envelope) can attach for DISPLAY, exactly as CLAUDE.md's
Rule 1 intends config-table codes to be used — not by duplicating them onto
every ledger row. ``portfolio.external_references`` rows this module writes
DO carry the account-crosswalk's own source-system token
(``altruist_identity.SOURCE_SYSTEM_ALTRUIST`` = ``'ALTRUIST'``, reused
verbatim per Task 1b — that table has no CHECK constraint and is a different
column from the one described above).

IDEMPOTENCY — THE SAME PRE-INSERT-READ PATTERN AS PHASE B'S FILE IMPORTER
────────────────────────────────────────────────────────────────────────────
Exactly like ``portfolio_import.import_positions_file``: before writing a
position or a transaction, this module asks
``portfolio_assets.find_external_reference`` whether the row's external id
already maps to something. If it does, the row is skipped. The external id
for a position is Altruist's own position id (when the [FIND]-flagged
response shape actually includes one) or a content hash, in both cases
suffixed with the sync's ``as_of_date`` — a position is a same-day snapshot,
so a same-day re-sync of unchanged data is a no-op, while a DIFFERENT day's
sync is a legitimately new bitemporal fact (Rule 3), not a duplicate. A
transaction's external id has no date suffix — a trade only happens once and
Altruist's own transaction id (or its content hash) is stable forever.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from services.altruist_identity import RECORD_TYPE_ACCOUNT, SOURCE_SYSTEM_ALTRUIST
from services.altruist_oauth import call_positions, call_transactions
from services.altruist_oauth import require_active_connection
from services.portfolio_assets import (
    TABLE_EXT_REF,
    TABLE_POSITIONS,
    UNITS,
    VALUE,
    PortfolioError,
    _OrgWrite,
    _current,
    _require_org,
    add_identifier,
    create_asset,
    create_position,
    find_external_reference,
    upsert_external_reference,
)
from services.portfolio_assets import record_transaction as _record_transaction
from services.portfolio_import import _match_asset, _to_decimal

#: The DB-legal provenance tag — see the [FIND] in this module's docstring.
#: Matches ``positions_source_chk`` and the mirrored Python vocabulary in
#: ``services.portfolio_assets.SOURCE_SYSTEMS`` exactly.
SOURCE_SYSTEM = "altruist"

#: A live custodian API feed IS custodial data, unlike a reporting tool
#: (which merely aggregates it) — ``services.portfolio_import`` says as much
#: in its own module docstring when explaining why IT uses ``'aggregated'``.
AUTHORITY = "custodial"

#: The seeded ``reference_data`` codes this integration corresponds to.
#: Not a column value (see the [FIND] above) — exposed for a caller that
#: wants to display/label the source, per CLAUDE.md Rule 1.
CUSTODIAN_CODE = "ALT"
CUSTODIAN_SYSTEM_CODE = "ALT-DEF"

#: Altruist's own transaction ``type`` string → this codebase's
#: ``transaction_type_code``. [FIND] unconfirmed against a live spec, same as
#: the endpoint paths — a best-guess mapping over the public-market vocabulary
#: a retail/RIA custodian's activity feed would plausibly use. An unmapped
#: type is recorded as an error, never guessed into the closest-sounding code.
TRANSACTION_TYPE_MAP: dict[str, str] = {
    "buy": "buy",
    "sell": "sell",
    "dividend": "dividend",
    "interest": "interest",
    "fee": "fee_expense",
    "fee_expense": "fee_expense",
    "adjustment": "adjustment",
}


class AltruistSyncError(PortfolioError):
    """A sync-level problem distinct from a single row's error."""


@dataclass
class RowError:
    external_id: str | None
    reason: str


@dataclass
class AccountSyncResult:
    altruist_account_id: str
    hollisworks_account_id: str
    positions_created: int = 0
    positions_skipped_duplicate: int = 0
    positions_errors: list[RowError] = field(default_factory=list)
    transactions_created: int = 0
    transactions_skipped_duplicate: int = 0
    transactions_errors: list[RowError] = field(default_factory=list)


@dataclass
class SyncResult:
    accounts_synced: int = 0
    accounts: list[AccountSyncResult] = field(default_factory=list)


def _security_key(row: dict) -> str:
    """A within-run lookup key shared by positions and transactions rows, so
    a transaction can find the asset its matching position already resolved
    this run without a second identifier lookup. Identifier-based whenever
    any identifier is present (the common, reliable case); falls back to
    ``name`` only when a row carries none — two distinct nameless,
    identifier-less securities in the same run is a data-quality edge case
    this key does not attempt to disambiguate further.
    """
    ident = "|".join(str(row.get(f) or "") for f in ("cusip", "isin", "ticker"))
    if ident.strip("|"):
        return ident
    return f"name:{row.get('name') or ''}"


def _identifiers_from(row: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for id_type, field_name in (("cusip", "cusip"), ("isin", "isin"), ("ticker", "ticker")):
        value = row.get(field_name)
        if value:
            out.append((id_type, str(value).strip()))
    return out


def _position_external_id(altruist_account_id: str, row: dict, as_of: date) -> str:
    """See module docstring — a stable id if Altruist gives one, else a hash,
    always suffixed with the as-of date so a later day's snapshot is a new
    fact rather than a skipped duplicate."""
    explicit = row.get("id")
    if explicit:
        return f"pos:{explicit}:{as_of.isoformat()}"
    basis = "|".join([
        str(altruist_account_id),
        str(row.get("cusip") or row.get("isin") or row.get("ticker") or row.get("name") or ""),
        str(row.get("quantity")), str(row.get("market_value")),
        as_of.isoformat(),
    ])
    return f"pos:sha256:{hashlib.sha256(basis.encode('utf-8')).hexdigest()}"


def _transaction_external_id(altruist_account_id: str, row: dict) -> str:
    """No date suffix — unlike a position snapshot, a real-world trade is a
    one-time event and Altruist's own id (or a content hash of it) is a
    stable identity forever, not just for the day it was fetched."""
    explicit = row.get("id")
    if explicit:
        return f"txn:{explicit}"
    basis = "|".join([
        str(altruist_account_id),
        str(row.get("cusip") or row.get("isin") or row.get("ticker") or ""),
        str(row.get("type")), str(row.get("trade_date")),
        str(row.get("quantity")), str(row.get("gross_amount")),
    ])
    return f"txn:sha256:{hashlib.sha256(basis.encode('utf-8')).hexdigest()}"


async def _owner_entity_id_for_account(conn, org_id: str, hollisworks_account_id: str) -> str:
    async with _OrgWrite(conn, org_id) as c:
        owner = await c.fetchval(
            """
            SELECT primary_entity_id::text FROM public.accounts
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            hollisworks_account_id, org_id,
        )
    if not owner:
        raise AltruistSyncError(
            f"account {hollisworks_account_id} does not exist in this org, or "
            f"has no primary_entity_id — cannot own a synced position"
        )
    return owner


async def _find_current_position_id(
    conn, org_id: str, *, account_id: str, asset_id: str
) -> str | None:
    async with _OrgWrite(conn, org_id) as c:
        return await c.fetchval(
            f"""
            SELECT p.id::text FROM {TABLE_POSITIONS} p
            WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid
              AND p.asset_id = $3::uuid AND {_current('p')}
            """,
            org_id, account_id, asset_id,
        )


async def _resolve_or_create_asset(conn, org_id: str, row: dict) -> str:
    identifiers = _identifiers_from(row)
    name = row.get("name")
    asset_id = await _match_asset(conn, org_id, identifiers, name)
    if asset_id is not None:
        return asset_id

    if not name and not identifiers:
        raise AltruistSyncError(
            "position/transaction names no security — no name and no "
            "cusip/isin/ticker identifier"
        )
    asset_id = await create_asset(
        conn,
        org_id=org_id,
        name=name or identifiers[0][1],
        asset_type=row.get("asset_type") or "unclassified",
        currency_code=row.get("currency_code"),
    )
    for id_type, value in identifiers:
        try:
            await add_identifier(
                conn, org_id=org_id, asset_id=asset_id, id_type=id_type,
                id_value=value, is_primary=(id_type == identifiers[0][0]),
            )
        except PortfolioError:
            continue
    return asset_id


async def _sync_positions(
    conn, org_id: str, *, altruist_account_id: str, hollisworks_account_id: str,
    owner_entity_id: str, as_of: date, environment: str, transport: Any,
    result: AccountSyncResult,
) -> dict[str, str]:
    """Returns a map of a stable per-row content key -> asset_id, so
    ``_sync_transactions`` can hang a transaction off the asset its matching
    position just resolved, without a second identifier lookup."""
    asset_by_key: dict[str, str] = {}
    rows = await call_positions(
        conn, org_id=org_id, environment=environment,
        account_id=altruist_account_id, transport=transport,
    )
    for row in rows:
        ext_id = _position_external_id(altruist_account_id, row, as_of)
        key = _security_key(row)
        try:
            existing = await find_external_reference(
                conn, org_id=org_id, source_system=SOURCE_SYSTEM_ALTRUIST,
                external_id=ext_id, record_type="position",
            )
            if existing:
                result.positions_skipped_duplicate += 1
                # Still resolve the asset so a same-run transaction can find
                # it — a skipped position is not a skipped asset lookup.
                asset_by_key[key] = await _resolve_or_create_asset(conn, org_id, row)
                continue

            asset_id = await _resolve_or_create_asset(conn, org_id, row)
            quantity = _to_decimal(row.get("quantity"), "quantity")
            market_value = _to_decimal(row.get("market_value"), "market_value")
            cost_basis = _to_decimal(row.get("cost_basis"), "cost_basis")
            basis = UNITS if quantity is not None else VALUE

            position_id = await create_position(
                conn, org_id=org_id, owner_entity_id=owner_entity_id,
                asset_id=asset_id, as_of_date=as_of, authority=AUTHORITY,
                source_system=SOURCE_SYSTEM, ownership_basis=basis,
                quantity=quantity if basis == UNITS else None,
                market_value=market_value, cost_basis=cost_basis,
                account_id=hollisworks_account_id,
            )
            await upsert_external_reference(
                conn, org_id=org_id, source_system=SOURCE_SYSTEM_ALTRUIST,
                external_id=ext_id, record_type="position", record_id=position_id,
            )
            result.positions_created += 1
            asset_by_key[key] = asset_id
        except (PortfolioError, ValueError) as exc:
            result.positions_errors.append(RowError(external_id=ext_id, reason=str(exc)))
    return asset_by_key


async def _sync_transactions(
    conn, org_id: str, *, altruist_account_id: str, hollisworks_account_id: str,
    as_of: date, environment: str, transport: Any, asset_by_key: dict[str, str],
    result: AccountSyncResult,
) -> None:
    rows = await call_transactions(
        conn, org_id=org_id, environment=environment,
        account_id=altruist_account_id, transport=transport,
    )
    for row in rows:
        ext_id = _transaction_external_id(altruist_account_id, row)
        try:
            existing = await find_external_reference(
                conn, org_id=org_id, source_system=SOURCE_SYSTEM_ALTRUIST,
                external_id=ext_id, record_type="transaction",
            )
            if existing:
                result.transactions_skipped_duplicate += 1
                continue

            raw_type = (row.get("type") or "").strip().lower()
            txn_type_code = TRANSACTION_TYPE_MAP.get(raw_type)
            if txn_type_code is None:
                result.transactions_errors.append(RowError(
                    external_id=ext_id,
                    reason=f"unmapped Altruist transaction type {row.get('type')!r}",
                ))
                continue

            key = _security_key(row)
            asset_id = asset_by_key.get(key)
            if asset_id is None:
                # No matching position was synced this run for this security.
                # Never fabricated: an asset resolved by identifier only,
                # never a position invented to hang the transaction off.
                identifiers = _identifiers_from(row)
                if not identifiers:
                    result.transactions_errors.append(RowError(
                        external_id=ext_id,
                        reason="transaction names no security (no cusip/isin/ticker) "
                               "and matches no position synced this run",
                    ))
                    continue
                asset_id = await _match_asset(conn, org_id, identifiers, None)
                if asset_id is None:
                    result.transactions_errors.append(RowError(
                        external_id=ext_id,
                        reason="transaction's security matches no known asset and "
                               "no position for it was synced this run",
                    ))
                    continue

            position_id = await _find_current_position_id(
                conn, org_id, account_id=hollisworks_account_id, asset_id=asset_id,
            )
            if position_id is None:
                result.transactions_errors.append(RowError(
                    external_id=ext_id,
                    reason=f"no current position exists for asset {asset_id} under "
                           f"account {hollisworks_account_id} — a transaction cannot "
                           f"be recorded without a position to record it against",
                ))
                continue

            trade_date = row.get("trade_date")
            if isinstance(trade_date, str):
                trade_date = date.fromisoformat(trade_date)
            settle_date = row.get("settle_date")
            if isinstance(settle_date, str):
                settle_date = date.fromisoformat(settle_date)
            if not isinstance(trade_date, date):
                result.transactions_errors.append(RowError(
                    external_id=ext_id, reason=f"trade_date {row.get('trade_date')!r} "
                    f"is not a usable date",
                ))
                continue

            transaction_id = await _record_transaction(
                conn, org_id=org_id, position_id=position_id,
                transaction_type_code=txn_type_code, trade_date=trade_date,
                settle_date=settle_date, authority=AUTHORITY,
                source_system=SOURCE_SYSTEM,
                quantity=_to_decimal(row.get("quantity"), "quantity"),
                price=_to_decimal(row.get("price"), "price"),
                gross_amount=_to_decimal(row.get("gross_amount"), "gross_amount"),
                fees=_to_decimal(row.get("fees"), "fees"),
                taxes=_to_decimal(row.get("taxes"), "taxes"),
                net_amount=_to_decimal(row.get("net_amount"), "net_amount"),
                currency_code=row.get("currency_code"),
                external_ref=str(row.get("id")) if row.get("id") else None,
            )
            await upsert_external_reference(
                conn, org_id=org_id, source_system=SOURCE_SYSTEM_ALTRUIST,
                external_id=ext_id, record_type="transaction", record_id=transaction_id,
            )
            result.transactions_created += 1
        except (PortfolioError, ValueError) as exc:
            result.transactions_errors.append(RowError(external_id=ext_id, reason=str(exc)))


async def list_resolved_altruist_accounts(conn, *, org_id: str) -> list[dict]:
    """Every Altruist account already matched to a Hollisworks account.

    Queries ``portfolio.external_references`` directly — the exact crosswalk
    ``altruist_identity._lookup_account_reference`` reads (Task 1b), run in
    reverse: enumerate every matched row instead of probing one external id.
    An account still in ``account_import_exceptions`` has no row here and is
    therefore never returned — see the module docstring and Task 4's proof.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"""
            SELECT external_id, record_id::text AS hollisworks_account_id
            FROM {TABLE_EXT_REF}
            WHERE org_id = $1::uuid AND source_system = $2 AND record_type = $3
            """,
            org_id, SOURCE_SYSTEM_ALTRUIST, RECORD_TYPE_ACCOUNT,
        )
    return [dict(r) for r in rows]


async def sync_resolved_accounts(
    conn,
    *,
    org_id: str,
    environment: str,
    as_of_date: date | None = None,
    transport: Any = None,
) -> SyncResult:
    """The Sprint 3 entrypoint: sync positions + transactions for every
    RESOLVED Altruist account in this org.

    ``require_active_connection`` is called explicitly, up front, before
    enumerating accounts — unlike ``resolve_identity`` (Sprint 2), which gets
    the same guard for free because it unconditionally calls
    ``call_households`` at least once. This sync's per-account HTTP calls sit
    inside a loop over resolved accounts, which could be empty; without an
    explicit call here, an org with zero resolved accounts AND no connection
    would never reach a guard at all. See Task 4's proof.
    """
    org_id = _require_org(org_id)
    await require_active_connection(conn, org_id=org_id, environment=environment)

    as_of = as_of_date or date.today()
    accounts = await list_resolved_altruist_accounts(conn, org_id=org_id)

    result = SyncResult()
    for row in accounts:
        altruist_account_id = row["external_id"]
        hollisworks_account_id = row["hollisworks_account_id"]
        owner_entity_id = await _owner_entity_id_for_account(
            conn, org_id, hollisworks_account_id
        )
        account_result = AccountSyncResult(
            altruist_account_id=altruist_account_id,
            hollisworks_account_id=hollisworks_account_id,
        )
        asset_by_key = await _sync_positions(
            conn, org_id, altruist_account_id=altruist_account_id,
            hollisworks_account_id=hollisworks_account_id,
            owner_entity_id=owner_entity_id, as_of=as_of, environment=environment,
            transport=transport, result=account_result,
        )
        await _sync_transactions(
            conn, org_id, altruist_account_id=altruist_account_id,
            hollisworks_account_id=hollisworks_account_id, as_of=as_of,
            environment=environment, transport=transport,
            asset_by_key=asset_by_key, result=account_result,
        )
        result.accounts.append(account_result)
        result.accounts_synced += 1

    return result


__all__ = [
    "AUTHORITY",
    "CUSTODIAN_CODE",
    "CUSTODIAN_SYSTEM_CODE",
    "SOURCE_SYSTEM",
    "TRANSACTION_TYPE_MAP",
    "AccountSyncResult",
    "AltruistSyncError",
    "RowError",
    "SyncResult",
    "list_resolved_altruist_accounts",
    "sync_resolved_accounts",
]
