"""Altruist Sprint 2 — household/account identity resolution against
``portfolio.external_references``.

WHAT THIS IS, AND WHAT IT IS NOT
────────────────────────────────────────────────────────────────────────────
Reads FROM Altruist (``call_households`` / ``fetch_accounts_for_household``,
services/altruist_oauth.py) and resolves each Altruist account against the
existing generic crosswalk table, ``portfolio.external_references``. It does
NOT import Altruist's household/account structure wholesale, and it never
auto-creates a Hollisworks household/entity/account — per the Architecture
Decisions doc, Hollisworks' own households/entities remain the source of
truth. An Altruist account with no existing crosswalk row is routed to
``public.account_import_exceptions`` for manual review; nothing is guessed.

[FIND] HOUSEHOLDS ARE NOT A STORABLE CROSSWALK TARGET
────────────────────────────────────────────────────────────────────────────
Both ``portfolio.external_references.record_type`` and
``public.account_import_exceptions.record_kind`` carry deployed CHECK
constraints that admit ``'account'`` but NOT ``'household'``:

  ext_ref_record_type_chk:               record_type IN
      ('asset', 'position', 'transaction', 'account')
  account_import_exceptions_record_kind_check: record_kind IN
      ('account', 'balance', 'flow')

This exactly mirrors a precedent already in this codebase —
``services/portfolio_account_link.py`` (fee32) built an entirely SEPARATE
table (``position_account_exceptions``) rather than force-fit
``account_import_exceptions``, specifically because that table's NOT NULL
``batch_id`` (a real FK to ``account_import_batches``, itself shaped for a
custodian CSV upload — ``custodian_code``, ``source_filename``, ``row_count``)
does not fit every write path. Per this sprint's standing rule not to
improvise DDL, household identity is NOT persisted here.

This turns out not to be a real gap. ``GET /api/v2/households`` is used
purely as a traversal step to discover which household ids to pass to
``GET /v2/accounts?household_id=...`` — the actual identity-resolution unit
is the ACCOUNT. Once an Altruist account resolves to a Hollisworks
``public.accounts`` row via ``external_references`` (record_type='account'),
that row's own ``household_id`` column already carries the household
attachment (see ``public.accounts.household_id``, confirmed live in Task 1c)
— a second, separate household-level crosswalk row would be redundant even
if the CHECK constraint allowed one.

``account_import_batches`` IS required (the FK), but is NOT a new table: one
row is reused per org (``custodian_code='ALTRUIST'``), looked up before
being created, exactly like any other existing-table write.
"""

from __future__ import annotations

import json
from typing import Any

from services.altruist_oauth import call_households, fetch_accounts_for_household
from services.portfolio_assets import _OrgWrite, _require_org

TABLE_EXTERNAL_REFERENCES = "portfolio.external_references"
TABLE_ACCOUNT_IMPORT_BATCHES = "public.account_import_batches"
TABLE_ACCOUNT_IMPORT_EXCEPTIONS = "public.account_import_exceptions"

SOURCE_SYSTEM_ALTRUIST = "ALTRUIST"
RECORD_TYPE_ACCOUNT = "account"
RECORD_KIND_ACCOUNT = "account"
REASON_CODE_UNMATCHED = "altruist_account_unmatched"

# cpsim02 D5 — the crosswalk source is PER ENVIRONMENT: a simulator sync can never touch an account crosswalked to real
# Altruist, and a sandbox / production sync can never touch a simulator one. external_references has no source CHECK,
# so the token needs no DDL. Sandbox / production keep 'ALTRUIST' and every query below byte for byte.
SOURCE_SYSTEM_ALTRUIST_SIMULATOR = "ALTRUIST_SIMULATOR"
REASON_CODE_UNMATCHED_SIMULATOR = "altruist_simulator_account_unmatched"
REASON_CODE_NOT_DEMO = "not_a_demo_household"
SIMULATOR_ENVIRONMENT = "simulator"


def crosswalk_source(environment: str) -> str:
    """The external_references.source_system an environment's accounts are crosswalked under."""
    return SOURCE_SYSTEM_ALTRUIST_SIMULATOR if environment == SIMULATOR_ENVIRONMENT else SOURCE_SYSTEM_ALTRUIST


async def simulator_account_allowed(conn, org_id: str, hollisworks_account_id: str) -> bool:
    """cpsim02 — may this org feed this account from a SIMULATED custodian? The rule is the cpsim01 may-connect function,
    reached through its one public definer wrapper: a test org freely; a real org only for an all-demo household."""
    async with _OrgWrite(conn, org_id) as c:
        household_id = await c.fetchval(
            "SELECT household_id::text FROM public.accounts WHERE id = $1::uuid AND org_id = $2::uuid",
            hollisworks_account_id, org_id,
        )
        return bool(await c.fetchval(
            "SELECT public.counterparty_sim_may_connect($1::uuid, $2::uuid)", org_id, household_id,
        ))


async def simulator_fed(conn, org_id: str, hollisworks_account_id: str) -> bool:
    """cpsim02 — is this account crosswalked to a SIMULATED custodian (the "Simulated custodian" badge)?"""
    async with _OrgWrite(conn, org_id) as c:
        return bool(await c.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {TABLE_EXTERNAL_REFERENCES} WHERE org_id = $1::uuid AND source_system = $2 "
            f"AND record_type = $3 AND record_id = $4::uuid)",
            org_id, SOURCE_SYSTEM_ALTRUIST_SIMULATOR, RECORD_TYPE_ACCOUNT, hollisworks_account_id,
        ))


async def _lookup_account_reference(conn, org_id: str, external_id: str,
                                    source: str = SOURCE_SYSTEM_ALTRUIST) -> str | None:
    """The existing Hollisworks accounts.id for this Altruist account, if any."""
    async with _OrgWrite(conn, org_id) as c:
        return await c.fetchval(
            f"""
            SELECT record_id::text FROM {TABLE_EXTERNAL_REFERENCES}
            WHERE org_id = $1::uuid AND source_system = $2
              AND external_id = $3 AND record_type = $4
            """,
            org_id, source, external_id, RECORD_TYPE_ACCOUNT,
        )


async def _touch_last_seen(conn, org_id: str, external_id: str, source: str = SOURCE_SYSTEM_ALTRUIST) -> None:
    """A repeat match re-confirms the crosswalk row is still current.

    UPDATE only — never an INSERT here, so a re-run cannot create a duplicate
    ``external_references`` row (the UNIQUE constraint would refuse a second
    INSERT for the same key anyway; this makes the re-run a no-op write
    instead of a refused one).
    """
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"""
            UPDATE {TABLE_EXTERNAL_REFERENCES}
            SET last_seen = now()
            WHERE org_id = $1::uuid AND source_system = $2
              AND external_id = $3 AND record_type = $4
            """,
            org_id, source, external_id, RECORD_TYPE_ACCOUNT,
        )


async def _get_or_create_batch(conn, org_id: str, source: str = SOURCE_SYSTEM_ALTRUIST) -> str:
    """Reuse the org's single ALTRUIST import-batch row (satisfies the real
    FK on account_import_exceptions.batch_id) — never creates a second one.
    """
    async with _OrgWrite(conn, org_id) as c:
        existing = await c.fetchval(
            f"""
            SELECT id::text FROM {TABLE_ACCOUNT_IMPORT_BATCHES}
            WHERE org_id = $1::uuid AND custodian_code = $2
            ORDER BY created_at ASC LIMIT 1
            """,
            org_id, source,
        )
        if existing:
            return existing
        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_ACCOUNT_IMPORT_BATCHES}
                (org_id, custodian_code, source_filename, status)
            VALUES ($1::uuid, $2, 'altruist-identity-resolution', 'DRY_RUN')
            RETURNING id::text
            """,
            org_id, source,
        )


async def _record_unmatched_account(
    conn, org_id: str, *, batch_id: str, source_row: int,
    account: dict, household: dict | None,
    source: str = SOURCE_SYSTEM_ALTRUIST, reason_code: str = REASON_CODE_UNMATCHED,
) -> tuple[str, bool]:
    """Insert (or find) the exception row for one unmatched Altruist account.

    Returns ``(exception_id, created)`` — idempotent by CONTENT (the
    Altruist external_id inside ``raw_row``), not by ``batch_id``, since
    ``account_import_exceptions`` carries no unique constraint of its own
    (unlike ``position_account_exceptions``'s partial unique index) and a
    re-run reuses the same batch anyway.
    """
    raw_row = {
        "source_system": source,
        "external_id": account["id"],
        "name": account.get("name"),
        "household_external_id": (household or {}).get("id"),
        "household_name": (household or {}).get("name"),
        "org_id": org_id,
    }
    async with _OrgWrite(conn, org_id) as c:
        existing = await c.fetchval(
            f"""
            SELECT id::text FROM {TABLE_ACCOUNT_IMPORT_EXCEPTIONS}
            WHERE org_id = $1::uuid AND record_kind = $2 AND reason_code = $3
              AND raw_row ->> 'external_id' = $4
            """,
            org_id, RECORD_KIND_ACCOUNT, reason_code, account["id"],
        )
        if existing:
            return existing, False

        if reason_code == REASON_CODE_NOT_DEMO:
            reason = (
                f"SIMULATED custodian account {account.get('name') or '(unnamed)'} "
                f"(external_id={account['id']}) is crosswalked to a household that is not "
                f"tagged demo. In a real organization a simulated feed may only reach a demo "
                f"household; nothing was synced for it."
            )
        else:
            reason = (
                f"No portfolio.external_references row exists for Altruist "
                f"account {account.get('name') or '(unnamed)'} "
                f"(external_id={account['id']}). Needs manual review to link to "
                f"an existing Hollisworks account or confirm this is new."
            )
        new_id = await c.fetchval(
            f"""
            INSERT INTO {TABLE_ACCOUNT_IMPORT_EXCEPTIONS}
                (org_id, batch_id, source_row, record_kind, reason_code,
                 reason, raw_row)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb)
            RETURNING id::text
            """,
            org_id, batch_id, source_row, RECORD_KIND_ACCOUNT,
            reason_code, reason, json.dumps(raw_row),
        )
        return new_id, True


async def resolve_identity(
    conn,
    *,
    org_id: str,
    environment: str,
    transport: Any = None,
) -> dict:
    """The Sprint 2 entrypoint: fetch households → accounts → resolve each.

    Runs ``call_households``/``fetch_accounts_for_household`` first, so
    ``AltruistNotConnected`` (Sprint 1's guard) propagates unchanged for an
    org with no active connection — this function adds no guard of its own
    and does not catch that exception.

    ``transport`` is forwarded to both HTTP calls (see their docstrings in
    services/altruist_oauth.py) — production callers omit it.
    """
    org_id = _require_org(org_id)
    source = crosswalk_source(environment)
    simulated = environment == SIMULATOR_ENVIRONMENT
    unmatched_code = REASON_CODE_UNMATCHED_SIMULATOR if simulated else REASON_CODE_UNMATCHED

    households = await call_households(
        conn, org_id=org_id, environment=environment, transport=transport
    )

    resolved: list[dict] = []
    exceptions: list[dict] = []
    refused: list[dict] = []
    batch_id: str | None = None
    row_index = 0

    for household in households:
        accounts = await fetch_accounts_for_household(
            conn, org_id=org_id, environment=environment,
            household_id=household["id"], transport=transport,
        )
        for account in accounts:
            hollisworks_account_id = await _lookup_account_reference(
                conn, org_id, account["id"], source
            )
            if hollisworks_account_id and simulated and not await simulator_account_allowed(
                    conn, org_id, hollisworks_account_id):
                # cpsim02 D5 — a real org's non-demo household never takes a simulated feed: one exception, no write
                if batch_id is None:
                    batch_id = await _get_or_create_batch(conn, org_id, source)
                exception_id, _created = await _record_unmatched_account(
                    conn, org_id, batch_id=batch_id, source_row=row_index, account=account,
                    household=household, source=source, reason_code=REASON_CODE_NOT_DEMO,
                )
                refused.append({
                    "altruist_account_id": account["id"],
                    "altruist_household_id": household["id"],
                    "hollisworks_account_id": hollisworks_account_id,
                    "exception_id": exception_id,
                    "reason_code": REASON_CODE_NOT_DEMO,
                })
            elif hollisworks_account_id:
                await _touch_last_seen(conn, org_id, account["id"], source)
                resolved.append({
                    "altruist_account_id": account["id"],
                    "altruist_household_id": household["id"],
                    "hollisworks_account_id": hollisworks_account_id,
                })
            else:
                if batch_id is None:
                    batch_id = await _get_or_create_batch(conn, org_id, source)
                exception_id, _created = await _record_unmatched_account(
                    conn, org_id, batch_id=batch_id, source_row=row_index,
                    account=account, household=household, source=source, reason_code=unmatched_code,
                )
                exceptions.append({
                    "altruist_account_id": account["id"],
                    "altruist_household_id": household["id"],
                    "exception_id": exception_id,
                })
            row_index += 1

    out = {
        "households_seen": len(households),
        "resolved": resolved,
        "exceptions": exceptions,
    }
    if simulated:
        out["refused"] = refused
    return out


__all__ = [
    "RECORD_KIND_ACCOUNT",
    "RECORD_TYPE_ACCOUNT",
    "REASON_CODE_UNMATCHED",
    "SOURCE_SYSTEM_ALTRUIST",
    "SOURCE_SYSTEM_ALTRUIST_SIMULATOR",
    "REASON_CODE_NOT_DEMO",
    "crosswalk_source",
    "simulator_account_allowed",
    "TABLE_ACCOUNT_IMPORT_BATCHES",
    "TABLE_ACCOUNT_IMPORT_EXCEPTIONS",
    "TABLE_EXTERNAL_REFERENCES",
    "resolve_identity",
]
