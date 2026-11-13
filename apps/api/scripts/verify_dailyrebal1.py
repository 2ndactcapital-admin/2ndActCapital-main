"""verify_dailyrebal1 — the daily scheduled rebalancer: once per US business day per org, at the org's New York run
time, after the last close's prices are complete (or the fallback time — flagged partial) and the nightly refresh
ran, opted-in accounts (link rebalance_frequency due / drift past the threshold) get SYSTEM proposals (trigger
scheduled / drift) that wait for a PERSON; one summary to-do per approver per org per day; failures recorded, never
raised; the step is a declared draft-only unattended action that can never approve, execute or send.

    timeout 600 python3 apps/api/scripts/verify_dailyrebal1.py --self-checks-only
    timeout 600 python3 apps/api/scripts/verify_dailyrebal1.py --cleanup-only
    timeout 600 python3 apps/api/scripts/verify_dailyrebal1.py --only=c,r      # seed + chosen sections + teardown
    timeout 900 python3 apps/api/scripts/verify_dailyrebal1.py --only=x        # the simulated route (cpsim05 fixture)
    python3 apps/api/scripts/verify_dailyrebal1.py                            # everything + fast regressions

Sections (each SELF-CONTAINED: it seeds the links / rows it needs and sets its own clock):
  c  clock — a Saturday and a NYSE holiday never run (no row); before the run time nothing; the org's run-time setting
     moves it; one row per org per day (a second tick: 'already')
  r  readiness — waits for the nightly refresh, then for prices Complete; complete → runs (not partial); a later day
     with prices still running runs after the fallback time, flagged partial with the oldest valuation date
  s  selection + generation — exactly the opted-in links are checked; drift past the link threshold → 'drift';
     frequency due → 'scheduled'; skips with reasons (closed, restricted, advice-only, open proposal, not due); every
     proposal is maker_kind system, status awaiting approval on a review task — never approved, executed or sent; one
     summary to-do per approver; the next day: open proposals skipped, no new to-do
  h  a person approves a scheduled proposal through the existing route → approved by that person; its instructions
     wait for rep2's second-person approval; nothing sent
  f  failure — one account's failure is recorded and the others proceed; the step's own failure is recorded on the
     day row (status failed) and never raised; daily_tick survives an org raising; a live run never enumerates a
     test org and org_daily without include_test_orgs skips it with the reason
  u  the unattended scan — rebalance.scheduled_daily declared eligible + draft_only (sod 'propose'), not a registered
     action; no registered handler, no sweep, the tick and the new module reach an approve / execute / send entry
     point (positive control); cpsim04 / cpsim05 self-checks still pass
  p  the setup script — a dry run writes nothing (fingerprints), its checklist names every missing item and prints
     env NAMES only (planted values never appear); a real run proposes the settings through the maker-checker path
  x  the simulated route (cpsim05's fixture, its own run): a scheduled proposal on a simulated demo account, a person
     reviews it, a second person approves the instructions → sent, filled and reconciled automatically; P/L shown

THE CLOCK: every section passes ``now_utc`` (a New York wall time on an explicit day) to org_daily; D1 = the first US
business day after the database day, D2 = the next. Saturday / Thanksgiving are fixed calendar dates.

FIXTURES: verify_rep5's fixture orgs (99000000-…-0000000e5001 …, is_test) seeded by rep5's own seed (imported, not
copied); links are inserted directly as active fixture rows (the join flow is rep5's and is not under test); the
prices day rows use the fixture scope 'zzdr1verify' (passed as prices_scope), never 'platform'. TEARDOWN: rep5's
teardown (operator DSN, execution.teardown / models.teardown / replica.teardown GUCs, every row keyed by the fixture
orgs — day rows, links, proposals, to-dos, entity_holdings included) plus the fixture-scope price rows; zero fixture
rows remain; real rebalance_schedule_days / price_refresh_days / proposals unchanged (count + checksum).

NO NETWORK AND NO MODEL CALL (rep5's stubs refuse httpx). Never a real or demo row; never a real org setting.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import ast
import asyncio
import json
import pathlib
import re
import subprocess
import sys
import time
import traceback
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "dailyrebal1"
REGRESSIONS = ["verify_cpsim05.py", "verify_cpsim04.py", "verify_rep5.py", "verify_prices2.py", "verify_readerfix1.py"]
NY = ZoneInfo("America/New_York")
PRICES_SCOPE = "zzdr1verify"
NODE_TEST = "tests/dailyrebal1/dailyrebal1.test.mjs"


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    """Source without comments / docstrings (a scan must not match prose)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


#: the approve / execute / send entry points nothing unattended may reach (cpsim05's automatic sender, rep5's decide /
#: execute, rep2's approval / mark_sent, cpsim03's person Send / Import, the human-task completion)
FORBIDDEN_RX = re.compile(
    r"\b(?:after_proposal_routed|after_instruction_approved|collect_after_advance|run_person|after_block_approved|"
    r"run_one|after_batch)\(|fix_sim_auto|fix_sim_blocks|\b(?:RP|rebalance_proposals)\.(?:decide|execute|_apply_outcome|"
    r"sync_task_outcome|override_lot_method)\(|\bX\.|services\.execution|\bexecution\.(?:approve|mark_sent|enter_fill|"
    r"create_instructions)\(|\bmark_sent\(|\bcomplete_task\(|\bHT\.complete|custodian_execution\.(?:send|import)")


#: the tick legitimately imports rep2's execute-by expiry (services.execution.expire_tick — statuses only, never a send);
#: for the tick the scan is the automatic-send / approve / execute entry points themselves
TICK_RX = re.compile(
    r"\b(?:after_proposal_routed|after_instruction_approved|collect_after_advance|run_person|after_block_approved|"
    r"run_one|after_batch)\(|fix_sim_auto|fix_sim_blocks|\b(?:RP|rebalance_proposals)\.(?:decide|execute|"
    r"_apply_outcome)\(|\bmark_sent\(|\bcomplete_task\(|\.approve\(")


def ny(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=NY).astimezone(timezone.utc)


# ═══ self-checks (pure: no database, no network) ═══════════════════════════
def self_static(check, find):
    from services import rebalance_schedule as RS
    from services import us_business_days as BD

    src = _read("apps/api/services/rebalance_schedule.py")
    code = _code_only(src)
    hits = FORBIDDEN_RX.findall(code)
    check(bool(src) and not hits, "services/rebalance_schedule.py reaches no approve / execute / send / automatic-FIX "
          "entry point (code only, comments excluded)", f"{hits[:5]}")
    planted = code + "\nfrom services import fix_sim_auto\nfix_sim_auto.after_proposal_routed(None)\n"
    check(bool(FORBIDDEN_RX.findall(planted)), "positive control: the same scan catches a planted automatic-send call")
    gen = re.findall(r"RP\.generate\(([^)]*)\)", code)
    check(len(gen) == 1 and "maker_kind='system'" in gen[0] and "maker_id" not in gen[0] and "auto_submit" not in gen[0],
          "the step's ONE generate call is maker_kind='system' (no person, no auto_submit)", f"{gen}")
    tick = _code_only(_read("apps/api/workflow_scheduler_tick.py"))
    i_p, i_r = tick.find("proposals_tick(pool)"), tick.find("rebalance_daily_tick(pool)")
    check(0 <= i_p < i_r, "the cron tick runs the daily step after rep5's proposals step", f"{i_p} {i_r}")
    blk = tick[i_r - 400:i_r + 900] if i_r > 0 else ""
    check("try:" in tick[max(0, i_r - 300):i_r] and "except Exception" in blk,
          "… in its own try (a failure there never stops another step)")
    check(not TICK_RX.findall(tick) and TICK_RX.findall("await RP.decide(pool)"),
          "the cron tick itself names no approve / execute / automatic-send entry point (positive control: the "
          "pattern catches RP.decide)", f"{TICK_RX.findall(tick)}")
    from services import rebalance_proposals as RP
    from services import authority as AU

    mig = _read("docs/dailyrebal1_part1.sql")
    m = re.search(r"rebalance_proposals_trigger_chk CHECK \(trigger = ANY \(ARRAY\[(.*?)\]\)\)", mig, re.S)
    chk = set(re.findall(r"'([a-z_]+)'::text", m.group(1))) if m else set()
    check(chk == set(RP.TRIGGER_LABELS) and set(AU.TRIGGERS) == set(RP.TRIGGER_LABELS) and "scheduled" in chk,
          "the trigger vocabulary is ONE set: the migration's CHECK = TRIGGER_LABELS = authority.TRIGGERS, incl. "
          "'scheduled'", f"{sorted(chk)} {sorted(RP.TRIGGER_LABELS)} {sorted(AU.TRIGGERS)}")
    check("proposal:scheduled" in AU.MODEL_PATHS and "proposal:scheduled" not in AU.allowed_paths("advise", "outside")
          and "proposal:scheduled" not in AU.allowed_paths("read_only", "outside"),
          "'proposal:scheduled' is a MODEL path: managed only (advise / read-only refuse it)")
    from services import workflow_autonomy as WA
    from services import sod

    v = WA.VERB_AUTONOMY.get(RS.ACTION_KEY, {})
    check(v.get("class") == WA.ELIGIBLE and v.get("draft_only") is True and RS.ACTION_KEY in WA.DRAFT_ONLY_VERBS
          and not any(v.get(k) for k in WA.CLASS_REASONS) and sod.VERB_DUTIES.get(RS.ACTION_KEY) == "propose",
          "rebalance.scheduled_daily: declared eligible + draft_only, no money / custody / trades flag, sod duty "
          "'propose'", f"{v} {sod.VERB_DUTIES.get(RS.ACTION_KEY)}")
    check(WA.VERB_AUTONOMY.get("execution.fix_sim_auto", {}).get("class") == WA.NEVER
          and WA.VERB_AUTONOMY.get("execution.custodian_send", {}).get("class") == WA.NEVER,
          "cpsim05 / cpsim03's automatic send and person Send stay NEVER")
    rs = _read("scripts/regression_set.txt")
    check("verify_dailyrebal1.py" in rs.split(), "verify_dailyrebal1.py is in scripts/regression_set.txt")
    check((WEB_DIR / NODE_TEST).exists(), f"the panel's render test exists ({NODE_TEST})")
    setup = _read("apps/api/scripts/dailyrebal_setup.py")
    emits = re.findall(r"emit\((.*?)\)\n", setup, re.S)
    check(bool(setup) and not any("environ.get(name)" in e or "environ[" in e for e in emits)
          and "propose_change" in setup and "set_capability" not in setup
          and not re.search(r"\b(?:INSERT INTO|UPDATE [a-z_.]+ SET|DELETE FROM)\b", setup), "the setup script prints no environment VALUE, writes only through propose_change "
          "(never SQL, never a capability)")
    _ = BD


def self_rules(check, find):
    from services import rebalance_schedule as RS
    from services import us_business_days as BD

    sat, thanks = date(2026, 11, 14), date(2026, 11, 26)
    check(not BD.is_business_day(sat) and BD.holiday_name(thanks) and not BD.is_business_day(thanks)
          and BD.is_business_day(date(2026, 11, 27)), "calendar: Saturday 2026-11-14 and Thanksgiving 2026-11-26 are "
          "not business days; Friday 2026-11-27 is", f"{BD.holiday_name(thanks)}")
    check(RS.last_close_day(date(2026, 11, 30)) == date(2026, 11, 27) and RS.last_close_day(date(2026, 11, 27))
          == date(2026, 11, 25) and RS.last_close_day(date(2026, 11, 13)) == date(2026, 11, 12)
          and BD.prior_business_day(date(2026, 11, 13)) == date(2026, 11, 13),
          "the last close: Monday 11-30 → Friday 11-27; Friday 11-27 → Wednesday 11-25 (over Thanksgiving); never "
          "today (us_business_days.prior_business_day returns the day ITSELF on a business day — the trap)")
    fb = dtime(10, 0)
    cases = [("complete", True, 8, (True, False)), ("running", True, 8, (False, False)),
             ("running", True, 10, (True, True)), (None, True, 11, (True, True)), ("complete", False, 11, (False, False)),
             ("stopped_budget", True, 9, (False, False)), ("stopped_budget", True, 10, (True, True))]
    bad = []
    for st, nightly, hh, want in cases:
        r = RS.readiness(prices_status=st, nightly_ok=nightly, now_local=datetime(2026, 11, 13, hh, 0, tzinfo=NY),
                         fallback=fb)
        if (r["ready"], r["partial"]) != want:
            bad.append((st, nightly, hh, r))
    check(not bad, "readiness: complete → ready; not complete → waits until the fallback, then ready flagged PARTIAL; "
          "no nightly → waits whatever the prices", f"{bad}")
    d = date(2026, 11, 13)
    fq = [("daily", d - timedelta(days=1), True), ("daily", d, False), ("weekly", d - timedelta(days=6), False),
          ("weekly", d - timedelta(days=7), True), ("monthly", date(2026, 10, 14), False),
          ("monthly", date(2026, 10, 13), True), ("quarterly", date(2026, 8, 13), True),
          ("quarterly", date(2026, 8, 14), False), ("semiannually", date(2026, 5, 13), True),
          ("annually", date(2025, 11, 14), False), ("annually", date(2025, 11, 13), True),
          ("daily", None, True), (None, None, False), ("hourly", None, False)]
    badf = [(f, last, w) for f, last, w in fq if RS.frequency_due(f, last, d) != w]
    check(not badf, "frequency due: daily = an earlier day, weekly 7 days, monthly / quarterly / semiannually / "
          "annually by calendar months; never proposed → due; no / unknown frequency → never", f"{badf}")
    check(RS.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28) and RS.add_months(date(2026, 11, 30), 3)
          == date(2027, 2, 28), "month arithmetic clamps to the month's last day")
    D = Decimal
    check(RS.choose_trigger(drift=D("0.06"), threshold=D("0.05"), freq_due=False) == "drift"
          and RS.choose_trigger(drift=D("0.06"), threshold=D("0.05"), freq_due=True) == "drift"
          and RS.choose_trigger(drift=D("0.05"), threshold=D("0.05"), freq_due=True) == "scheduled"
          and RS.choose_trigger(drift=D("0.01"), threshold=D("0.05"), freq_due=False) is None,
          "trigger: drift strictly above the threshold → drift (even when the frequency is also due); else due → "
          "scheduled; else nothing")
    check(RS.summary_title([{"household": "Olivo Household"}] * 3) == "3 proposals waiting — Olivo Household"
          and RS.summary_title([{"household": "A"}]) == "1 proposal waiting — A"
          and RS.summary_title([{"household": h} for h in "ABCDE"]) == "5 proposals waiting — A, B, C and 2 more",
          "summary to-do title: '3 proposals waiting — Olivo Household'")
    check(RS.parse_hhmm("08:15", dtime(7, 30)) == dtime(8, 15) and RS.parse_hhmm('"06:05"', dtime(7, 30))
          == dtime(6, 5) and RS.parse_hhmm("noon", dtime(7, 30)) == dtime(7, 30),
          "run-time setting parse (HH:MM, JSON-quoted too); unreadable → the default")


def self_checks(check, find):
    self_static(check, find)
    self_rules(check, find)


if __name__ == "__main__" and VS.requested():
    for _s in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
        sys.path.insert(0, str(_s))
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

ONLY_X = __name__ == "__main__" and any(a.startswith("--only=") and "x" in a.split("=", 1)[1].split(",")
                                        for a in sys.argv[1:])

if not ONLY_X:
    import verify_rep5 as R5  # noqa: E402 — rep5's fixture (seed / stubs / teardown), imported, never copied

from _db_connect import admin_dsn, connect, hydrate  # noqa: E402

hydrate()

import asyncpg  # noqa: E402

from services import account_models as AM  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import rebalance_schedule as RS  # noqa: E402
from services import workflow_autonomy as WA  # noqa: E402
from services import us_business_days as BD  # noqa: E402

_n_pass = 0
_n_fail = 0
S: dict = {}


def check(passed: bool, label: str, detail: str = "") -> bool:
    global _n_pass, _n_fail
    ok = bool(passed)
    if ok:
        _n_pass += 1
    else:
        _n_fail += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok or not detail else f" — {detail}"), flush=True)
    return ok


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f" — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n{title}", flush=True)


def total_line() -> str:
    return f"TOTAL: {_n_pass} PASS, {_n_fail} FAIL"


# ═══ fixture helpers (rep5's fixture org A) ═════════════════════════════════
def D1() -> date:
    d = datetime.now(NY).date() + timedelta(days=1)
    while not BD.is_business_day(d):
        d += timedelta(days=1)
    return d


def D2() -> date:
    return BD.next_business_day(D1() + timedelta(days=1))   # next_business_day(d) is d itself on a business day


async def plat(conn, sql, *args, fetch="fetch"):
    return await R5.platform(conn, sql, *args, fetch=fetch)


async def insert_link(conn, account, model, *, freq=None, drift=None, advice_only=False) -> uuid.UUID:
    lid = uuid.uuid4()
    await plat(conn, f"""INSERT INTO {AM.T_LINK} (id, org_id, account_id, model_id, link_group_id, version_no,
            change_kind, status, advice_only, cash_floor, drift_threshold, rebalance_frequency, exclusions,
            exclusion_policy, avoid_short_term, short_term_wait_days, harvesting_engine, proposed_fee, maker_id,
            checker_id, decided_at, joined_at, harvest_enabled, notes)
        VALUES ($1, $2, $3, $4, $1, 1, 'join', 'active', $5, 0, $6, $7, '[]'::jsonb, 'pro_rata', true, 30,
                'hollisworks', '{{}}'::jsonb, $8, $9, now(), now() - interval '30 days', false,
                'dailyrebal1 verify fixture')""",
               lid, R5.ORG_A, account, model, advice_only, drift, freq, R5.U_ADMIN1, R5.U_ADMIN2, fetch="execute")
    return lid


async def fresh_org_state(op) -> None:
    """Remove every fixture link / proposal / instruction (operator DSN + execution.teardown — the guard triggers'
    only path) so each section starts from the seed alone."""
    async with op.transaction():
        await op.execute("SELECT set_config('execution.teardown', 'fixture', true)")
        orgs = R5.FIXTURE_ORGS
        # FK order: proposal trades point at instructions (instruction_id), instructions at proposals
        for t in ("public.execution_instruction_events", "public.execution_fills", "public.execution_instruction_lots",
                  "public.rebalance_proposal_trades", "public.account_transition_plans",
                  "public.execution_instructions", "public.rebalance_proposals", "public.account_model_links",
                  "public.rebalance_schedule_days"):
            await op.execute(f"DELETE FROM {t} WHERE org_id = ANY($1::uuid[])", orgs)
        await op.execute("DELETE FROM member_todos WHERE org_id = ANY($1::uuid[])", orgs)
        await op.execute("DELETE FROM portfolio.price_refresh_days WHERE scope = $1", PRICES_SCOPE)
        await op.execute("DELETE FROM entity_holdings WHERE org_id = ANY($1::uuid[]) AND source = 'portfolio'", orgs)
        await op.execute("DELETE FROM org_settings WHERE org_id = ANY($1::uuid[]) AND setting_key LIKE "
                         "'replication.schedule.%'", orgs)


async def reset(conn) -> None:
    op = await R5.operator_conn()
    try:
        await fresh_org_state(op)
    finally:
        await op.close()


async def prices_row(conn, price_day: date, status: str) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "INSERT INTO portfolio.price_refresh_days (scope, price_day, status, finished_at) VALUES ($1, $2, $3, "
            "CASE WHEN $3 = 'running' THEN NULL ELSE now() END) ON CONFLICT (scope, price_day) DO UPDATE SET "
            "status = EXCLUDED.status", PRICES_SCOPE, price_day, status)


async def nightly_row(conn, day: date) -> None:
    await R5.scoped(conn, R5.ORG_A, "INSERT INTO entity_holdings (org_id, entity_id, taxonomy_key, market_value, "
                    "as_of_date, source) VALUES ($1, $2, 'zz_dr1_verify', 1, $3, 'portfolio') ON CONFLICT DO NOTHING",
                    R5.ORG_A, R5.ENT_A, day, fetch="execute")


async def org_setting(conn, key: str, value: str) -> None:
    await R5.scoped(conn, R5.ORG_A, "INSERT INTO org_settings (org_id, setting_key, setting_value) VALUES ($1, $2, "
                    "$3::jsonb) ON CONFLICT (org_id, setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
                    R5.ORG_A, key, json.dumps(value), fetch="execute")


async def daily(now_utc, *, include_test_orgs=True, org=None, **kw) -> dict:
    pool = await R5.pool_()
    with R5._ctx(org or R5.ORG_A):
        return await RS.org_daily(pool, org_id=str(org or R5.ORG_A), now_utc=now_utc,
                                  include_test_orgs=include_test_orgs, prices_scope=PRICES_SCOPE, **kw)


async def day_rows(conn, org=None) -> list[dict]:
    return [dict(r) for r in await plat(conn, "SELECT * FROM public.rebalance_schedule_days WHERE org_id = $1 "
                                              "ORDER BY run_day", org or R5.ORG_A)]


async def props(conn) -> list[dict]:
    return [dict(r) for r in await plat(conn, "SELECT * FROM public.rebalance_proposals WHERE org_id = $1 AND "
                                              "system_to IS NULL ORDER BY created_at", R5.ORG_A)]


async def ready_day(conn, day: date, prices: str = "complete") -> None:
    await prices_row(conn, RS.last_close_day(day), prices)
    await nightly_row(conn, day)


# ═══ sections ══════════════════════════════════════════════════════════════
async def y_clock(conn):
    section("[C] Clock — business days only, the org's run time, one row per org per day")
    await reset(conn)
    await insert_link(conn, R5.ACCT_CALM, S["m2"], freq="daily")
    sat, thanks = date(2026, 11, 14), date(2026, 11, 26)
    r1 = await daily(ny(sat, 9))
    r2 = await daily(ny(thanks, 9))
    check(r1["action"] == "not_business_day" and r2["action"] == "not_business_day" and "thanksgiving" in str(r2).lower()
          and not await day_rows(conn), "a Saturday and Thanksgiving never run and write no row",
          f"{r1} {r2}")
    d1 = D1()
    r = await daily(ny(d1, 7, 29))
    check(r["action"] == "before_time" and r["run_time"] == "07:30" and not await day_rows(conn),
          "07:29 New York on a business day: before the default 07:30 run time, no row", f"{r}")
    await org_setting(conn, RS.RUN_TIME_SETTING, "08:15")
    r = await daily(ny(d1, 8, 0))
    check(r["action"] == "before_time" and r["run_time"] == "08:15" and not await day_rows(conn),
          "the org's run-time setting (08:15) moves it: 08:00 is still before time", f"{r}")
    await ready_day(conn, d1)
    r = await daily(ny(d1, 8, 15))
    rows = await day_rows(conn)
    check(r["action"] == "ran" and len(rows) == 1 and rows[0]["run_day"] == d1 and rows[0]["status"] == "completed"
          and rows[0]["run_time_local"] == "08:15" and rows[0]["is_fixture"] is True,
          "08:15: it runs — one completed row for the org's day, run time recorded, flagged fixture",
          f"{r.get('action')} {[(x['run_day'], x['status']) for x in rows]}")
    r = await daily(ny(d1, 9, 0))
    r_b = await daily(ny(d1, 16, 0))
    check(r["action"] == "already" and r_b["action"] == "already" and len(await day_rows(conn)) == 1,
          "later ticks the same day: 'already' — once per business day", f"{r} {r_b}")
    try:
        await plat(conn, "INSERT INTO public.rebalance_schedule_days (org_id, run_day, run_time_local) VALUES "
                         "($1, $2, '07:30')", R5.ORG_A, d1, fetch="execute")
        dup = None
    except asyncpg.UniqueViolationError as exc:
        dup = exc
    check(dup is not None and "rebalance_schedule_days_day_uq" in str(dup.constraint_name),
          "the database backstop: a second row for (org, day) is a unique violation", f"{dup}")


async def y_readiness(conn):
    section("[R] Readiness — the nightly refresh, then prices Complete (or the fallback → partial)")
    await reset(conn)
    await insert_link(conn, R5.ACCT_CASH, S["m2"], freq="daily", drift=Decimal("0.20"))
    d1, d2 = D1(), D2()
    r = await daily(ny(d1, 7, 30))
    rows = await day_rows(conn)
    check(r["action"] == "waiting" and "nightly" in (r.get("reason") or "") and len(rows) == 1
          and rows[0]["status"] == "waiting" and not rows[0]["nightly_ok"] and rows[0]["proposed"] == 0
          and not await props(conn), "07:30, no nightly refresh for today: the day row WAITS (the reason recorded), "
          "nothing proposed", f"{r}")
    await nightly_row(conn, d1)
    await prices_row(conn, RS.last_close_day(d1), "running")
    r = await daily(ny(d1, 8, 0))
    rows = await day_rows(conn)
    check(r["action"] == "waiting" and "running" in (r.get("reason") or "") and rows[0]["status"] == "waiting"
          and rows[0]["nightly_ok"] and rows[0]["prices_status"] == "running"
          and rows[0]["prices_day"] == RS.last_close_day(d1) and not await props(conn),
          "nightly ran, the last close's prices still running at 08:00: WAITS for prices (prices day = the prior "
          "business day), nothing proposed", f"{r}")
    await prices_row(conn, RS.last_close_day(d1), "complete")
    r = await daily(ny(d1, 8, 5))
    rows = await day_rows(conn)
    check(r["action"] == "ran" and rows[0]["status"] == "completed" and rows[0]["partial_prices"] is False
          and rows[0]["prices_status"] == "complete" and r["proposed"] == 1,
          "prices Complete at 08:05 → it runs, NOT partial, the proposal made", f"{r.get('action')} {r.get('proposed')}")
    # day 2: prices still running → waits until the fallback, then runs flagged partial with the oldest valuation date
    await nightly_row(conn, d2)
    await prices_row(conn, RS.last_close_day(d2), "running")
    r_w = await daily(ny(d2, 9, 59))
    r = await daily(ny(d2, 10, 0))
    rows = {x["run_day"]: x for x in await day_rows(conn)}
    r2 = rows.get(d2) or {}
    acct_val = await R5.scoped(conn, R5.ORG_A, "SELECT max(as_of_date) FROM portfolio.positions WHERE org_id = $1 AND "
                               "account_id = $2 AND valid_to IS NULL AND system_to IS NULL", R5.ORG_A, R5.ACCT_CASH,
                               fetch="fetchval")
    check(r_w["action"] == "waiting" and r["action"] == "ran" and r2.get("partial_prices") is True
          and r2.get("prices_status") == "running" and r2.get("oldest_price_date") == acct_val,
          "the next day, prices still running: 09:59 waits; 10:00 (the default fallback) runs flagged PARTIAL — the "
          "prices row's status and the oldest valuation date used are recorded",
          f"{r_w.get('action')} {r.get('action')} partial={r2.get('partial_prices')} "
          f"oldest={r2.get('oldest_price_date')} vs {acct_val}")
    res = r2.get("results") if isinstance(r2.get("results"), list) else json.loads(r2.get("results") or "[]")
    check(len(res) == 1 and res[0]["action"] == "skipped" and res[0]["reason"] == "open_proposal",
          "… and the account proposed yesterday (still awaiting approval) is skipped: open proposal", f"{res}")


async def y_selection(conn):
    section("[S] Selection + generation — exact opted-in set, drift vs scheduled, skips with reasons, system proposals "
            "that wait for a person, one summary to-do per approver")
    await reset(conn)
    d1, d2 = D1(), D2()
    links = {
        "drift": (R5.ACCT_DRIFT, await insert_link(conn, R5.ACCT_DRIFT, S["m2"], drift=Decimal("0.05"))),
        "cash": (R5.ACCT_CASH, await insert_link(conn, R5.ACCT_CASH, S["m2"], freq="daily", drift=Decimal("0.20"))),
        "calm": (R5.ACCT_CALM, await insert_link(conn, R5.ACCT_CALM, S["m2"], freq="monthly")),
        "sys": (R5.ACCT_SYS, await insert_link(conn, R5.ACCT_SYS, S["m3"], freq="weekly")),
        "adv": (R5.ACCT_ADV, await insert_link(conn, R5.ACCT_ADV, S["m2"], freq="daily", advice_only=True)),
        "held": (R5.ACCT_HELD, await insert_link(conn, R5.ACCT_HELD, S["m2"], freq="daily")),
        "closed": (R5.ACCT_CLOSED, await insert_link(conn, R5.ACCT_CLOSED, S["m2"], freq="daily")),
        "main": (R5.ACCT_MAIN, await insert_link(conn, R5.ACCT_MAIN, S["m2"])),      # NOT opted in
    }
    # CALM: a person's proposal 10 days ago (rejected) → monthly is not due; SYS: an open draft
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for key, status in (("calm", "rejected"), ("sys", "draft")):
            acct, lid = links[key]
            model, ver = (S["m2"], S["m2v1"]) if key != "sys" else (S["m3"], S["m3v1"])
            await conn.execute(
                "INSERT INTO rebalance_proposals (id, org_id, account_id, link_id, model_id, model_version_id, "
                "trigger, trigger_ref, status, expires_at, maker_kind, maker_id, proposal_kind, created_at, checker_id, "
                "decided_at, decision_note) VALUES ($1, $2, $3, $4, $5, $6, 'manual', '{}'::jsonb, $7, "
                "now() + interval '7 days', 'user', $8, 'model', $9, $10, $11, $12)",
                uuid.uuid4(), R5.ORG_A, acct, lid, model, ver, status, R5.U_ADMIN1,
                datetime.combine(d1 - timedelta(days=10), dtime(15, 0), tzinfo=timezone.utc),
                R5.U_ADMIN2 if status == "rejected" else None, datetime.now(timezone.utc) if status == "rejected"
                else None, "dailyrebal1 verify: rejected" if status == "rejected" else None)
    before_props = {str(p["id"]) for p in await props(conn)}
    await ready_day(conn, d1)
    r = await daily(ny(d1, 8, 0))
    res = {x["account_id"]: x for x in r.get("results", [])}
    opted_sql = {str(x["account_id"]) for x in await plat(
        conn, "SELECT account_id FROM public.account_model_links WHERE org_id = $1 AND status = 'active' AND valid_to "
              "IS NULL AND system_to IS NULL AND (rebalance_frequency IS NOT NULL OR drift_threshold IS NOT NULL)",
        R5.ORG_A)}
    want_ids = {str(links[k][0]) for k in ("drift", "cash", "calm", "sys", "adv", "held", "closed")}
    check(r["action"] == "ran" and set(res) == want_ids == opted_sql and str(R5.ACCT_MAIN) not in res,
          "EXACTLY the opted-in links were checked (= the predicate run directly in SQL); a link with neither "
          "frequency nor drift threshold (ACCT_MAIN) is not", f"{sorted(res)} vs {sorted(opted_sql)}")
    exp = {"drift": ("proposed", "drift"), "cash": ("proposed", "scheduled"), "calm": ("skipped", "not_due"),
           "sys": ("skipped", "open_proposal"), "adv": ("skipped", "advice_only"), "held": ("skipped", "restricted"),
           "closed": ("skipped", "closed")}
    got = {k: (res.get(str(links[k][0]), {}).get("action"), res.get(str(links[k][0]), {}).get("reason")) for k in exp}
    check(got == exp, "each account's outcome: drift past its 5% band → drift; daily frequency (drift within its 20% "
          "band) → scheduled; monthly proposed 10 days ago → not due; open draft → skipped; advice-only → skipped; "
          "held-away read-only → restricted; closed → closed", f"{got}")
    held = res.get(str(R5.ACCT_HELD), {})
    calm = res.get(str(R5.ACCT_CALM), {})
    check("read-only" in (held.get("detail") or "") and "next scheduled rebalance due" in (calm.get("detail") or "")
          and (calm.get("next_due") or "") == RS.add_months(d1 - timedelta(days=10), 1).isoformat(),
          "the reasons are specific: the authority's own refusal text; 'not due' names the next due date",
          f"{held.get('detail')!r} / {calm.get('detail')!r} {calm.get('next_due')}")
    drift_r = res.get(str(R5.ACCT_DRIFT), {})
    check(Decimal(drift_r.get("drift", "0")) > Decimal("0.05") and Decimal(drift_r.get("threshold", "0")) == Decimal("0.05")
          and Decimal(res.get(str(R5.ACCT_CASH), {}).get("drift", "1")) <= Decimal("0.20"),
          "the drift numbers recorded are compute()'s: DRIFT above its link threshold, CASH within its own",
          f"{drift_r.get('drift')} / {res.get(str(R5.ACCT_CASH), {}).get('drift')}")
    new = [p for p in await props(conn) if str(p["id"]) not in before_props]
    by_acct = {str(p["account_id"]): p for p in new}
    check(len(new) == 2 and set(by_acct) == {str(R5.ACCT_DRIFT), str(R5.ACCT_CASH)},
          "two proposals made — one each for DRIFT and CASH, none for any skipped account", f"{len(new)}")
    ok = all(p["maker_kind"] == "system" and p["maker_id"] is None and p["status"] == "pending_approval"
             and p["checker_id"] is None and p["decided_at"] is None and p["approval_task_id"] is not None
             for p in new)
    trig = {k: by_acct.get(str(links[k][0]), {}).get("trigger") for k in ("drift", "cash")}
    refs = [json.loads(p["trigger_ref"]) if isinstance(p["trigger_ref"], str) else p["trigger_ref"] for p in new]
    check(ok and trig == {"drift": "drift", "cash": "scheduled"} and all(x.get("schedule_day_id") == r["day_id"]
                                                                        for x in refs),
          "every proposal: maker_kind 'system' (no person), status awaiting approval on its task, no checker, never "
          "decided; trigger drift / scheduled; trigger_ref names the day row", f"{trig} {[p['status'] for p in new]}")
    tasks = [dict(t) for t in await plat(
        conn, "SELECT rs.id, rs.status, wr.context->'human_task'->>'kind' AS kind FROM workflow_run_steps rs JOIN "
              "workflow_runs wr ON wr.id = rs.workflow_run_id WHERE rs.id = ANY($1::uuid[])",
        [p["approval_task_id"] for p in new])]
    check(len(tasks) == 2 and all(t["kind"] == RP.TASK_REVIEW and t["status"] == "active" for t in tasks),
          "each waits on an OPEN rebalance_proposal_review task (a person decides)", f"{tasks}")
    instr = await plat(conn, "SELECT count(*) FROM execution_instructions WHERE org_id = $1", R5.ORG_A,
                       fetch="fetchval")
    approved_audit = await plat(conn, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND resource_id = "
                                      "ANY($2::uuid[]) AND action ~* '(approv|execut|route|sent|decid)'", R5.ORG_A,
                                [p["id"] for p in new], fetch="fetchval")
    check(int(instr) == 0 and int(approved_audit) == 0, "nothing approved, executed or sent by the step: zero "
          "execution instructions in the org, no approve / execute audit row", f"{instr} {approved_audit}")
    # the summary to-do
    appr = {str(x["id"]) for x in await plat(
        conn, "SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id JOIN roles r ON r.id = "
              "ur.role_id AND r.org_id = u.org_id JOIN role_permissions rp ON rp.role_id = ur.role_id JOIN permissions "
              "p ON p.id = rp.permission_id WHERE u.org_id = $1 AND p.name = 'approve_proposals' AND u.is_active",
        R5.ORG_A)}
    todos = [dict(t) for t in await plat(conn, "SELECT user_id, title, detail, status, related_id FROM member_todos "
                                                "WHERE org_id = $1 AND source = $2", R5.ORG_A, RS.TODO_SOURCE)]
    check({str(R5.U_ADMIN1), str(R5.U_ADMIN2), str(R5.U_ADMIN3)} <= appr and str(R5.U_VIEWER) not in appr
          and len(todos) == len(appr) and {str(t["user_id"]) for t in todos} == appr
          and all(str(t["related_id"]) == r["day_id"] and t["status"] == "open" for t in todos),
          "ONE summary to-do per approve_proposals holder (the three org admins; never the view-only user), "
          "related to the day row", f"{len(todos)} to-dos for {len(appr)} approvers")
    t0 = todos[0] if todos else {}
    check(t0.get("title") == "2 proposals waiting — No household" and "Drift over threshold" in (t0.get("detail") or "")
          and "Scheduled rebalance" in (t0.get("detail") or ""),
          "the title / detail list the day's proposals ('2 proposals waiting — …')", f"{t0.get('title')!r}")
    per_task = await plat(conn, "SELECT count(*) FROM member_todos WHERE org_id = $1 AND related_type = "
                                "'workflow_run_step' AND related_id = ANY($2::uuid[]) AND status = 'open'", R5.ORG_A,
                          [p["approval_task_id"] for p in new], fetch="fetchval")
    find("the engine's own per-TASK to-dos still open for each proposal (the approval controls) — the summary is "
         "added on top, not instead", f"{per_task} per-task to-do(s) for 2 proposals")
    r_again = await daily(ny(d1, 11, 0))
    n_after = await plat(conn, "SELECT count(*) FROM member_todos WHERE org_id = $1 AND source = $2", R5.ORG_A,
                         RS.TODO_SOURCE, fetch="fetchval")
    check(r_again["action"] == "already" and int(n_after) == len(todos), "a later tick the same day: 'already', no "
          "second to-do", f"{r_again} {n_after}")
    await ready_day(conn, d2)
    r2 = await daily(ny(d2, 8, 0))
    res2 = {x["account_id"]: x for x in r2.get("results", [])}
    n2 = await plat(conn, "SELECT count(*) FROM member_todos WHERE org_id = $1 AND source = $2", R5.ORG_A,
                    RS.TODO_SOURCE, fetch="fetchval")
    check(r2["action"] == "ran" and r2["proposed"] == 0 and res2.get(str(R5.ACCT_DRIFT), {}).get("reason")
          == "open_proposal" and res2.get(str(R5.ACCT_CASH), {}).get("reason") == "open_proposal"
          and int(n2) == len(todos) and len(await props(conn)) == len(before_props) + 2,
          "the next business day: yesterday's proposals still wait → skipped as open (never superseded), nothing "
          "new proposed, no new to-do", f"{r2.get('proposed')} {n2}")
    S["sel"] = {"cash_pid": str(by_acct.get(str(R5.ACCT_CASH), {}).get("id"))}


async def y_human(conn):
    section("[H] A person approves a scheduled proposal through the existing route; instructions wait for rep2's "
            "second person; nothing sent")
    await reset(conn)
    d1 = D1()
    await insert_link(conn, R5.ACCT_CASH, S["m2"], freq="daily", drift=Decimal("0.20"))
    await ready_day(conn, d1)
    r = await daily(ny(d1, 8, 0))
    pid = next((x.get("proposal_id") for x in r.get("results", []) if x.get("proposal_id")), None)
    if not pid:
        check(False, "the section's own scheduled proposal was made", f"{r}")
        return
    c, o = await R5.api(R5.U_ADMIN2, "POST", f"/api/v1/proposals/{pid}/approve", {"note": "dailyrebal1 verify"})
    p = await R5.prop_row(conn, R5.ORG_A, pid)
    ex = json.loads(p["execution"]) if isinstance(p["execution"], str) else (p["execution"] or {})
    ids = ex.get("instruction_ids") or []
    rows = [dict(x) for x in await plat(conn, "SELECT id, status, sent_at, maker_id, approved_by FROM "
                                              "execution_instructions WHERE id = ANY($1::uuid[]) AND system_to IS NULL",
                                        ids)] if ids else []
    check(c == 200 and p["status"] == "approved" and str(p["checker_id"]) == str(R5.U_ADMIN2) and p["trigger"] ==
          "scheduled", "a person (approve_proposals) approves it through POST /proposals/{id}/approve → approved, "
          "checker = that person", f"{c} {str(o)[:200]} {p['status']}")
    check(bool(rows) and all(x["status"] == "pending_approval" and x["sent_at"] is None and x["approved_by"] is None
                             for x in rows),
          "its instructions are drafted by the reviewer and wait for rep2's SECOND-person approval — nothing sent "
          "(the system-proposal path, unchanged)", f"{[(x['status'], x['sent_at']) for x in rows]}")


async def y_failure(conn):
    section("[F] Failures are recorded on the day row and never raised; one account / org never stops the others; "
            "test orgs never in a live run")
    await reset(conn)
    d1, d2 = D1(), D2()
    await insert_link(conn, R5.ACCT_DRIFT, S["m2"], drift=Decimal("0.05"))
    await insert_link(conn, R5.ACCT_CASH, S["m2"], freq="daily", drift=Decimal("0.20"))
    await ready_day(conn, d1)
    real_compute = RP.compute

    async def boom(c, org_id, account, *a, **kw):
        if str(account["id"]) == str(R5.ACCT_DRIFT):
            raise RuntimeError("dailyrebal1 verify: planted compute failure")
        return await real_compute(c, org_id, account, *a, **kw)

    RP.compute = boom
    try:
        r = await daily(ny(d1, 8, 0))
    except Exception as exc:  # noqa: BLE001
        r = {"raised": repr(exc)}
    finally:
        RP.compute = real_compute
    row = (await day_rows(conn) or [{}])[0]
    res = {x["account_id"]: x for x in (row.get("results") if isinstance(row.get("results"), list)
                                        else json.loads(row.get("results") or "[]"))}
    errs = row.get("errors") if isinstance(row.get("errors"), list) else json.loads(row.get("errors") or "[]")
    check("raised" not in r and row.get("status") == "completed" and row.get("failed") == 1
          and res.get(str(R5.ACCT_DRIFT), {}).get("action") == "failed"
          and "planted compute failure" in res.get(str(R5.ACCT_DRIFT), {}).get("detail", "")
          and res.get(str(R5.ACCT_CASH), {}).get("action") == "proposed" and any("planted" in e for e in errs),
          "one account's failure: recorded on the day row (failed 1, the error text), the other account still "
          "proposed, nothing raised", f"{r.get('action')} {row.get('status')} {row.get('failed')}")
    real_links = RS.opted_in_links

    async def boom_links(c, org_id):
        raise RuntimeError("dailyrebal1 verify: planted step failure")

    RS.opted_in_links = boom_links
    try:
        await ready_day(conn, d2)
        r2 = await daily(ny(d2, 8, 0))
    except Exception as exc:  # noqa: BLE001
        r2 = {"raised": repr(exc)}
    finally:
        RS.opted_in_links = real_links
    row2 = {x["run_day"]: x for x in await day_rows(conn)}.get(d2, {})
    e2 = row2.get("errors") if isinstance(row2.get("errors"), list) else json.loads(row2.get("errors") or "[]")
    check("raised" not in r2 and row2.get("status") == "failed" and row2.get("finished_at") is not None
          and any("planted step failure" in e for e in e2) and r2.get("status") == "failed",
          "the step's own failure: the day row ends 'failed' with the error, nothing raised", f"{r2} {e2}")
    r3 = await daily(ny(d2, 9, 0))
    check(r3["action"] == "already", "a failed day is finished: the next tick does not re-run it", f"{r3}")
    # daily_tick: a live run never enumerates a test org; an org that raises never stops another
    import services.orgs as ORGS

    real_sql, real_org_daily = ORGS.not_test_org_sql, RS.org_daily
    fixture = ", ".join(f"'{o}'::uuid" for o in R5.FIXTURE_ORGS)
    ORGS.not_test_org_sql = lambda col: f"({col} IN ({fixture}) AND {real_sql(col)})"
    pool = await R5.pool_()
    try:
        live = await RS.daily_tick(pool, now_utc=ny(d2, 12, 0))
    finally:
        ORGS.not_test_org_sql = real_sql
    check(live.get("action") == "ran" and live.get("orgs") == {},
          "a LIVE run (daily_tick) restricted to the fixture orgs enumerates none of them — every fixture org is "
          "is_test", f"{live}")
    r4 = await daily(ny(d2, 12, 0), include_test_orgs=False)
    check(r4["action"] == "skipped" and "test org" in r4["reason"], "org_daily without include_test_orgs on a test "
          "org: skipped with the reason", f"{r4}")
    calls = []

    async def fake_org_daily(pool_, *, org_id, now_utc=None, **kw):
        calls.append(str(org_id))
        if str(org_id) == str(R5.ORG_A):
            raise RuntimeError("dailyrebal1 verify: planted org failure")
        return {"action": "ran", "proposed": 0}

    ORGS.not_test_org_sql = lambda col: f"({col} IN ({fixture}))"
    RS.org_daily = fake_org_daily
    try:
        t = await RS.daily_tick(pool, now_utc=ny(d2, 12, 0))
    except Exception as exc:  # noqa: BLE001
        t = {"raised": repr(exc)}
    finally:
        ORGS.not_test_org_sql = real_sql
        RS.org_daily = real_org_daily
    check("raised" not in t and t.get("orgs", {}).get(str(R5.ORG_A), {}).get("action") == "failed"
          and "planted org failure" in str(t.get("orgs", {}).get(str(R5.ORG_A))),
          "daily_tick: an org whose step raises is recorded 'failed' and the tick returns normally", f"{t}")
    sat = await RS.daily_tick(pool, now_utc=ny(date(2026, 11, 14), 9))
    check(sat == {"action": "not_business_day"}, "daily_tick on a Saturday: nothing at all", f"{sat}")


async def y_unattended(conn):
    section("[U] The unattended scan — draft-only declaration; nothing reachable approves / executes / sends")
    from services.assistant_actions import register_all
    from services.action_registry import REGISTRY
    import importlib
    import inspect

    if not REGISTRY.all():
        register_all()
    acts = REGISTRY.all()
    keys = [a.key for a in acts]
    check(len(acts) >= 20 and RS.ACTION_KEY not in keys, f"rebalance.scheduled_daily is NOT a registered action "
          f"({len(acts)} enumerated): no workflow step, agent or trigger can name it", f"{len(acts)}")
    hits = []
    for a in acts:
        for h in (a.handler, getattr(a, "draft_handler", None)):
            if h is None:
                continue
            try:
                hs = _code_only(inspect.getsource(h))
            except (OSError, TypeError):
                hs = ""
            if "rebalance_schedule" in hs or "daily_tick" in hs and "rebalance" in hs:
                hits.append(a.key)
    check(not hits, "no registered handler calls the daily step", f"{hits}")
    v = WA.VERB_AUTONOMY.get(RS.ACTION_KEY, {})
    check(v.get("class") == WA.ELIGIBLE and RS.ACTION_KEY in WA.DRAFT_ONLY_VERBS,
          "declared an allowed unattended DRAFT action (eligible, draft_only)", f"{v}")
    sweeps, sweep_hits = [], []
    for sp in sorted((API_DIR / "services").glob("*.py")):
        if sp.stem in ("fix_sim_auto", "fix_sim_blocks"):
            continue
        txt = sp.read_text(encoding="utf-8")
        if not re.search(r"^(?:async )?def [A-Za-z_]*(?:sweep|tick)[A-Za-z_]*\(", txt, re.M):
            continue
        mod = importlib.import_module(f"services.{sp.stem}")
        for nm, f in inspect.getmembers(mod, inspect.isfunction):
            if ("sweep" in nm or nm.endswith("tick")) and f.__module__ == mod.__name__:
                sweeps.append(f"{sp.stem}.{nm}")
                if re.search(r"\b(?:after_proposal_routed|after_instruction_approved|collect_after_advance|run_person|"
                             r"after_block_approved|run_one|after_batch)\(|fix_sim_auto|fix_sim_blocks",
                             _code_only(inspect.getsource(f))):
                    sweep_hits.append(f"{sp.stem}.{nm}")
    check("rebalance_schedule.daily_tick" in sweeps and len(sweeps) >= 8 and not sweep_hits,
          f"every sweep / tick function ({len(sweeps)}, incl. rebalance_schedule.daily_tick) names no automatic "
          "entry point", f"{sweep_hits[:5]}")
    closure = []
    for nm, f in inspect.getmembers(RS, inspect.isfunction):
        if f.__module__ == RS.__name__:
            closure.append(_code_only(inspect.getsource(f)))
    hits2 = FORBIDDEN_RX.findall("\n".join(closure))
    check(not hits2 and len(closure) >= 10, f"every function of services.rebalance_schedule ({len(closure)}) reaches "
          "no approve / execute / send entry point; it calls only RP.generate (which SUBMITS a system proposal for "
          "a person's review)", f"{hits2[:5]}")


async def y_setup(conn):
    section("[P] The setup script — dry run writes nothing, names every missing item, never a secret; a real run "
            "proposes through maker-checker")
    import importlib

    await reset(conn)
    await insert_link(conn, R5.ACCT_MAIN, S["m2"])
    SU = importlib.import_module("dailyrebal_setup")
    planted = {"ALTRUIST_SIMULATOR_CLIENT_ID": "zzdr1-planted-id-7f3a", "ALTRUIST_SIMULATOR_CLIENT_SECRET":
               "zzdr1-planted-secret-91c2", "CPSIM_BASE_URL": "https://zzdr1-planted.invalid"}

    async def fp():
        out = {}
        for t in ("public.account_model_links", "public.rebalance_proposals", "public.audit_log", "public.org_settings",
                  "public.member_todos", "public.workflow_runs"):
            out[t] = await plat(conn, f"SELECT count(*)::text || ':' || coalesce(md5(string_agg(x::text, ',' ORDER BY "
                                      f"x::text)), '') FROM {t} x WHERE org_id = $1", R5.ORG_A, fetch="fetchval")
        return out

    before = await fp()
    lines: list[str] = []
    pool = await R5.pool_()
    rc = await SU.main(["--org", str(R5.ORG_A), "--household", "REP5 Verify Household", "--frequency", "daily",
                        "--drift", "0.02", "--dry-run"], pool=pool, environ=planted, emit=lines.append)
    out = "\n".join(lines)
    after = await fp()
    check(rc == 0 and before == after and "DRY RUN — nothing written (1 change(s) would be proposed)" in out,
          "dry run: exit 0, ONE change would be proposed (the household's linked account), and nothing written "
          "(links / proposals / audit / settings / to-dos / tasks fingerprints equal)", f"{rc} {out[-300:]}")
    check(all(v not in out for v in planted.values()) and "environment entry ALTRUIST_SIMULATOR_CLIENT_ID — present"
          in out and "[MISSING] environment entry ALTRUIST_SIMULATOR_WEBHOOK_SECRET" in out,
          "env: names only — planted VALUES never printed; a present name says present, an absent one MISSING")
    for item in ("org settings counterparty_sim.allowed", "an active connection to the simulated custodian",
                 "crosswalk ALTRUIST_SIMULATOR", "the simulated route decision"):
        check(f"[MISSING]" in out and any(item in ln and "[MISSING]" in ln for ln in lines),
              f"checklist names the missing item: {item}", out[-400:])
    check("skip    " in out and "does not follow a model" in out, "an account with no model is listed as skipped "
          "(the household's IRA)")
    rc2 = await SU.main(["--org", str(R5.ORG_A), "--household", "REP5 Verify Household", "--frequency", "daily",
                         "--actor-email", R5.email_of(R5.U_ADMIN1)], pool=pool, environ={}, emit=lines.append)
    pend = await plat(conn, "SELECT * FROM account_model_links WHERE org_id = $1 AND account_id = $2 AND status = "
                            "'pending' AND system_to IS NULL", R5.ORG_A, R5.ACCT_MAIN, fetch="fetchrow")
    live = await plat(conn, "SELECT rebalance_frequency FROM account_model_links WHERE org_id = $1 AND account_id = $2 "
                            "AND status = 'active' AND valid_to IS NULL AND system_to IS NULL", R5.ORG_A, R5.ACCT_MAIN,
                      fetch="fetchval")
    check(rc2 == 0 and pend is not None and pend["rebalance_frequency"] == "daily" and pend["change_kind"] == "settings"
          and str(pend["maker_id"]) == str(R5.U_ADMIN1) and pend["approval_task_id"] is not None and live is None,
          "real run: the settings change is PROPOSED (a pending version, maker = the operator, its approval task); the "
          "active link is unchanged until a different person approves", f"{rc2} {dict(pend) if pend else None}")
    rc3 = await SU.main(["--org", str(R5.ORG_A), "--household", "zz no such household", "--frequency", "daily",
                         "--dry-run"], pool=pool, environ={}, emit=lines.append)
    check(rc3 == 1, "an unknown household → exit 1 (no guess)")


async def y_routes(conn):
    section("[A] The panel's data rides on GET /proposals/accounts (same gate) — no new route")
    pool = await R5.pool_()
    await reset(conn)
    await insert_link(conn, R5.ACCT_CASH, S["m2"], freq="daily", drift=Decimal("0.20"))
    d1 = D1()
    await ready_day(conn, d1)
    await daily(ny(d1, 8, 0))
    c, o = await R5.api(R5.U_ADMIN1, "GET", "/api/v1/proposals/accounts")
    sch = o.get("schedule") or {}
    c2, o2 = await R5.api(R5.U_MEMBER, "GET", "/api/v1/proposals/accounts")
    check(c == 200 and sch.get("opted_in_accounts") == 1 and (sch.get("day") or {}).get("proposed") == 1
          and len(sch.get("proposals") or []) == 1 and sch["proposals"][0]["status"] == "pending_approval",
          "GET /proposals/accounts carries `schedule`: the latest run, its counts and the proposal waiting",
          f"{c} {json.dumps(sch)[:300]}")
    check(c2 == 403 and "schedule" not in o2, "a caller without view_proposals: 403, no schedule block", f"{c2}")
    _ = pool


async def y_front_end(conn):
    section("[W] The panel's render test (node)")
    p = subprocess.run(["node", "--test", NODE_TEST], cwd=str(WEB_DIR), capture_output=True, text=True, timeout=300)
    m = re.search(r"^# pass (\d+)", p.stdout, re.M)
    f = re.search(r"^# fail (\d+)", p.stdout, re.M)
    check(p.returncode == 0 and m and int(m.group(1)) >= 3 and f and int(f.group(1)) == 0,
          "render: today's run, inputs, counts, reasons, partial flag, the proposal waiting; read-only; no block → "
          "nothing", f"pass={m.group(1) if m else None} fail={f.group(1) if f else None} {p.stderr[-300:]}")


# ═══ the simulated route (cpsim05's fixture; its own run: --only=x) ═════════
async def y_simulated():
    import verify_cpsim05 as C5

    section("[X] The simulated route: a scheduled proposal on a simulated demo account → a person reviews → a second "
            "person approves the instructions → sent, filled, reconciled; P/L shown")
    dsn, how = await admin_dsn()
    os.environ["DATABASE_URL"] = dsn
    conn = await connect(dsn)
    C5.install_stubs()
    try:
        await C5.teardown_retrying()
        await C5.y_schema(conn)
        await C5.seed(conn)
        mirror = (C5._n_pass, C5._n_fail)
        check(C5.S.get("seeded") and C5._n_fail == 0, "cpsim05's fixture seeded (its own checks green)",
              f"{mirror}")
        if not C5.S.get("seeded"):
            return
        acct = C5.ACC_SIM          # cpsim05's model-linked simulated demo account (LINK_A)
        link = await C5.platform(conn, "SELECT * FROM account_model_links WHERE account_id = $1::uuid AND status = "
                                       "'active' AND valid_to IS NULL AND system_to IS NULL", acct, fetch="fetchrow")
        check(link is not None, "the simulated demo account follows a model (cpsim05's fixture link)")
        if link is None:
            return
        await C5.platform(conn, "DELETE FROM portfolio.price_refresh_days WHERE scope = $1", PRICES_SCOPE,
                          fetch="execute")
        today_ny = datetime.now(NY).date()
        run_day = BD.next_business_day(today_ny)
        await C5.platform(conn, "INSERT INTO portfolio.price_refresh_days (scope, price_day, status, finished_at) "
                                "VALUES ($1, $2, 'complete', now())", PRICES_SCOPE, RS.last_close_day(run_day),
                          fetch="execute")
        await C5.scoped(conn, C5.ORG_A, "INSERT INTO entity_holdings (org_id, entity_id, taxonomy_key, market_value, "
                        "as_of_date, source) SELECT $1, id, 'zz_dr1_verify', 1, $2, 'portfolio' FROM entities WHERE "
                        "org_id = $1 LIMIT 1 ON CONFLICT DO NOTHING", C5.ORG_A, ny(run_day, 8).date(), fetch="execute")
        pool = await RP_pool()
        from services.database import reset_rls_context, set_rls_context

        tok = set_rls_context(str(C5.ORG_A), False)
        try:
            ln = dict(link)
            ln["rebalance_frequency"] = "daily"
            res = await RS.evaluate_account(pool, org_id=str(C5.ORG_A), link=ln, today=run_day,
                                            day_id=uuid.uuid4())
        finally:
            reset_rls_context(tok)
        pid = res.get("proposal_id")
        check(res.get("action") == "proposed" and pid, "the daily step's evaluation makes a SYSTEM proposal on the "
              "simulated demo account", f"{res}")
        if not pid:
            return
        p = await C5.proposal_row(conn, pid)
        check(p.get("maker_kind") == "system" and p.get("status") == "pending_approval" and p.get("trigger") in
              ("scheduled", "drift"), "… maker_kind system, awaiting approval", f"{p.get('maker_kind')} {p.get('status')}")
        o0 = await C5.sim_count(conn, "orders")
        c, o = await C5.api(C5.U_CK, "POST", f"/api/v1/proposals/{pid}/approve", {"note": "dailyrebal1 verify"})
        await C5.FA.drain()
        ids = await C5.ids_of(conn, pid)
        rows = [await C5.instruction(conn, i) for i in ids]
        check(c == 200 and rows and all(r.get("status") == "pending_approval" for r in rows)
              and await C5.sim_count(conn, "orders") == o0,
              "a person reviews it (approve) → instructions wait for rep2's second-person approval; nothing sent yet",
              f"{c} {[r.get('status') for r in rows]}")
        for r in rows:
            c2, _o2 = await C5.api(C5.U_MA, "POST", f"/api/v1/execution/instructions/{r['id']}/approve", {})
            await C5.FA.drain()
            cur = await C5.instruction(conn, r["id"])
            fills = await C5.fills_of(conn, r["id"])
            check(c2 == 200 and cur.get("status") == "reconciled" and fills,
                  "a second person (approve_execution) approves → sent to the simulated counterparty, filled, "
                  "reconciled automatically (cpsim05's path, unchanged)", f"{c2} {cur.get('status')} {len(fills)}")
        c3, o3 = await C5.api(C5.U_CK, "GET", f"/api/v1/proposals/{pid}")
        fpl = (o3.get("proposal") or o3).get("fix_sim_pl") if isinstance(o3, dict) else None
        fpl = fpl if fpl is not None else o3.get("fix_sim_pl")
        tot = (fpl or {}).get("totals") or {}
        check(c3 == 200 and fpl is not None and tot.get("realized_lt") is not None and tot.get("realized_st") is not None
              and (fpl.get("unrealized") or {}).get("total") is not None,
              "the proposal's detail shows the SIMULATED P/L after the fills (cpsim05 D7: realized ST / LT, unrealized)",
              f"{c3} {json.dumps(fpl)[:300] if fpl else json.dumps(o3)[:200]}")
    finally:
        try:
            await C5.FA.drain()
            C5.restore_stubs()
            await C5.close_app_pools()
            await C5.platform(conn, "DELETE FROM portfolio.price_refresh_days WHERE scope = $1", PRICES_SCOPE,
                              fetch="execute")
            await C5.teardown_retrying()
            left = await C5.leftovers(conn)
            check(left == 0, "cpsim05's fixture torn down: zero fixture rows remain", f"{left}")
        except Exception as exc:  # noqa: BLE001
            check(False, "the simulated section's teardown raised", f"{type(exc).__name__}: {exc}")
        await conn.close()


async def RP_pool():
    from services.database import get_pool

    return await get_pool()


# ═══ driver ═════════════════════════════════════════════════════════════════
SECTIONS = {"c": y_clock, "r": y_readiness, "s": y_selection, "h": y_human, "f": y_failure, "u": y_unattended,
            "p": y_setup, "a": y_routes, "w": y_front_end}


def only_sections() -> list[str] | None:
    arg = next((a for a in sys.argv[1:] if a.startswith("--only=")), None)
    if arg is None:
        return None
    want = [x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()]
    bad = [x for x in want if x not in SECTIONS and x != "x"]
    if bad:
        raise SystemExit(f"--only: unknown section(s) {bad}; known: {', '.join(SECTIONS)}, x")
    return want


async def run(name, fn, *args):
    t0 = time.monotonic()
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"section '{name}' raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    print(f"[INFO] {name}: {time.monotonic() - t0:.1f}s", flush=True)


async def real_fp(conn) -> dict:
    out = {}
    for k, sql in (("rebalance_schedule_days", "SELECT count(*)::text || ':' || coalesce(md5(string_agg(id::text || "
                    "status, ',' ORDER BY id)), '') FROM public.rebalance_schedule_days WHERE NOT (org_id = "
                    "ANY($1::uuid[]))"),
                   ("price_refresh_days", "SELECT count(*)::text || ':' || coalesce(md5(string_agg(id::text || status, "
                    "',' ORDER BY id)), '') FROM portfolio.price_refresh_days WHERE scope <> $2::text AND $1::uuid[] IS NOT "
                    "NULL"),
                   ("rebalance_proposals", "SELECT count(*)::text || ':' || coalesce(md5(string_agg(id::text || status, "
                    "',' ORDER BY id)), '') FROM public.rebalance_proposals WHERE NOT (org_id = ANY($1::uuid[])) AND "
                    "$2::text IS NOT NULL"),
                   ("account_model_links", "SELECT count(*)::text || ':' || coalesce(md5(string_agg(id::text || status "
                    "|| coalesce(rebalance_frequency, ''), ',' ORDER BY id)), '') FROM public.account_model_links WHERE "
                    "NOT (org_id = ANY($1::uuid[])) AND $2::text IS NOT NULL")):
        args = (R5.FIXTURE_ORGS, PRICES_SCOPE) if "$2" in sql else (R5.FIXTURE_ORGS,)
        out[k] = await R5.platform(conn, sql, *args, fetch="fetchval")
    return out


async def teardown_all(conn) -> None:
    op = await R5.operator_conn()
    try:
        await fresh_org_state(op)
    finally:
        await op.close()
    await R5.teardown_retrying(conn)


async def leftovers(conn) -> int:
    n = await R5.leftovers(conn)
    n += int(await R5.platform(conn, "SELECT count(*) FROM public.rebalance_schedule_days WHERE org_id = ANY($1::uuid[])",
                               R5.FIXTURE_ORGS, fetch="fetchval"))
    n += int(await R5.platform(conn, "SELECT count(*) FROM portfolio.price_refresh_days WHERE scope = $1", PRICES_SCOPE,
                               fetch="fetchval"))
    n += int(await R5.platform(conn, "SELECT count(*) FROM entity_holdings WHERE org_id = ANY($1::uuid[])",
                               R5.FIXTURE_ORGS, fetch="fetchval"))
    return n


def regression():
    section("[J] Regressions — each ONCE, flat, after teardown, VERIFY_NESTED=1, its OWN final TOTAL line (fast)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


async def main_async() -> int:
    only = only_sections()
    if only == ["x"]:
        await run("simulated", y_simulated)
        print(total_line())
        return 0 if _n_fail == 0 else 1
    dsn, how = await admin_dsn()
    if not dsn:
        check(False, "a working DATABASE_URL", how)
        print(total_line())
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await connect(dsn)
    R5.install_stubs()
    if "--cleanup-only" in sys.argv:
        try:
            await teardown_all(conn)
        except Exception as exc:  # noqa: BLE001
            check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        left = await leftovers(conn)
        check(left == 0, "--cleanup-only: zero fixture rows remain (rep5's fixture + day rows + fixture price rows + "
              "fixture holdings)", f"{left}")
        await conn.close()
        print(total_line())
        return 0 if _n_fail == 0 else 1
    real = None
    try:
        await run("pre-teardown", teardown_all, conn)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        bypass = await R5.platform(conn, "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user",
                                   fetch="fetchval")
        check(bypass is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{bypass}")
        if not await R5.guard_prefix(conn):
            return 1
        real = await real_fp(conn)
        await run("seed literals", R5.y_seed_literals, conn)
        await run("seed", R5.seed, conn)
        S["m2"], S["m2v1"], S["m3"], S["m3v1"] = (R5.S.get("m2"), R5.S.get("m2v1"), R5.S.get("m3"), R5.S.get("m3v1"))
        check(bool(S["m2"] and S["m3"]) and R5._n_fail == 0, "rep5's fixture seeded through rep5's own seed (its checks "
              "green: users decided as meant, models approved)", f"rep5 seed fails={R5._n_fail}")
        chosen = [k for k in SECTIONS if only is None or k in only]
        if S["m2"]:
            for k in chosen:
                await run(k, SECTIONS[k], conn)
        else:
            for k in chosen:
                check(False, f"{k}: not run — the seed failed (never skipped silently)")
        check(R5.NETWORK_CALLS == [], "no network call during the run (rep5's stubs refuse httpx)",
              f"{R5.NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; real rows unchanged")
        try:
            await R5.close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            await teardown_all(conn)
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (rep5's fixture, day rows, fixture price rows, holdings)",
                  f"{left}")
            if real is not None:
                now = await real_fp(conn)
                for k in real:
                    check(now[k] == real[k], f"real {k} unchanged (count + checksum)", f"{real[k]} → {now[k]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await conn.close()
    if only is not None:
        print("[SKIP] regression — --only one-off run")
    elif os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        try:
            regression()
        except Exception as exc:  # noqa: BLE001
            check(False, "regression raised", f"{type(exc).__name__}: {exc}")
        await run("simulated (own fixture)", y_simulated)
    print(total_line())
    return 0 if _n_fail == 0 else 1


import os  # noqa: E402

if __name__ == "__main__":
    try:
        rc = asyncio.run(main_async())
    except BaseException as exc:  # noqa: BLE001 — TOTAL on every exit path
        if isinstance(exc, SystemExit) and exc.code in (0, None):
            raise
        traceback.print_exc()
        check(False, "the verify crashed", f"{type(exc).__name__}: {exc}")
        print(total_line())
        rc = 1
    sys.exit(rc)
