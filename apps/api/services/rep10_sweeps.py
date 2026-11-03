"""REP10 — the scheduler's step: the year-end charitable-gift reminder and the
completion staleness check, per org, NEVER a test org (organizations.is_test).
Nothing here runs a backtest — a backtest is only ever run by a person.

  gifts       a current-year gift approved or sent and not received by the
              firm's date (replication.gifts.year_end_receive_by, default
              12-15) → a to-do for the org's manage_tax holders.
  completion  an account with completion on whose counted held-away data is
              stale and unconfirmed (so completion is NOT being applied) → a
              to-do for the org's manage_replication holders to confirm it.
Both dedupe on an open to-do for the same subject.
"""

from __future__ import annotations

import logging
from datetime import date

from services import rep10_common as C

log = logging.getLogger(__name__)
TODO_COMPLETION = "completion_stale"


async def completion_sweep(pool, *, org_id, today: date) -> list[str]:
    from services import account_models as AM
    from services import completion as CO

    out = []
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT account_id FROM public.completion_settings WHERE org_id = $1::uuid AND "
                                "count_elsewhere AND valid_to IS NULL AND system_to IS NULL", str(org_id))
        recipients = await C.org_holders(conn, org_id, CO_MANAGE)
        for r in rows:
            account = await AM.load_account(conn, org_id, r["account_id"])
            s = await CO.settings_for(conn, org_id, account["id"])
            el = await CO.elsewhere(conn, org_id, account, s, today)
            blocking = [f for f in el.get("accounts", []) if f["blocking"]]
            if not blocking:
                continue
            got = await C.todo(conn, org_id, recipients, source=TODO_COMPLETION, related_type="account",
                               related_id=account["id"],
                               title=f"Completion paused: {account['account_number_masked']}",
                               detail="Held-away data counted by this account's completion is stale: "
                                      + ", ".join(f"{f['account']} as of {f['as_of'] or 'never'}" for f in blocking)
                                      + " — confirm it (or refresh the import) so completion applies again.",
                               action_key=f"/portfolio/replication/tilts?account={account['id']}&view=completion",
                               category="replication")
            if got:
                out.append(str(account["id"]))
    return out


CO_MANAGE = "manage_replication"


async def org_tick(pool, *, org_id, today: date | None = None) -> dict:
    from services import gifts as G

    today = today or date.today()
    res = {"gifts": [], "completion": [], "errors": []}
    try:
        res["gifts"] = await G.year_end_sweep(pool, org_id=org_id, today=today)
    except Exception as exc:  # noqa: BLE001 — a reminder; the tick goes on
        res["errors"].append(f"gifts: {type(exc).__name__}: {exc}")
    try:
        res["completion"] = await completion_sweep(pool, org_id=org_id, today=today)
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"completion: {type(exc).__name__}: {exc}")
    return res


async def tick(pool, *, today: date | None = None, include_test_orgs: bool = False, org_id=None) -> dict:
    """The cron entrypoint's step. Every non-test org (or one named org — a verify's own, explicitly)."""
    from services.database import reset_rls_context, set_rls_context

    today = today or date.today()
    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id FROM public.organizations WHERE ($1::uuid IS NULL OR id = $1::uuid) "
                                    "AND ($2::bool OR NOT COALESCE(is_test, false))",
                                    str(org_id) if org_id else None, include_test_orgs)
    finally:
        reset_rls_context(tokens)
    out = {}
    for r in rows:
        tokens = set_rls_context(str(r["id"]), False)
        try:
            out[str(r["id"])] = await org_tick(pool, org_id=r["id"], today=today)
        finally:
            reset_rls_context(tokens)
    return out
