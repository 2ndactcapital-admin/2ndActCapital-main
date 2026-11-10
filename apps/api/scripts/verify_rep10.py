"""verify_rep10 — Replication Studio, sprint 10 (REP10, closes phase 2): tilts,
completion portfolios and overlap consolidation, charitable lot gifting, and
the backtest under the firm's hypothetical-performance policy.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep10.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: the token boundary (``main.verify_token``) and the Auth0 profile call.
NO network: the httpx transport records and refuses — any attempt is a failed
check. REAL: the app through an ASGI transport (middleware, Principal, every
gate), the workflow engine (human tasks), rep5 / rep6 / rep7 / rep7b services,
the security-data layer, RLS and the live database. The script's own
connection is app_service (rolbypassrls = false, asserted).

FIXTURES: two orgs (99000000-0000-0000-0000-0000001a0001 / 0002, is_test =
true, asserted); users created explicitly with the auth0_sub their token
carries; securities with the reserved prefix ZZR10 and the name prefix
'REP10 VERIFY'; masked account numbers only. SYNTHETIC PRICE HISTORY lives in
the FIXTURE ORG'S scope only (portfolio.securities_global_prices org_id =
ORG_A, provider csv_upload) and goes with the org's teardown. All checked
absent from real rows first.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN with
execution.teardown = 'fixture' — keyed by this run's fixture ids; then every
counted table is re-read for its exact pre-test count, and real rows are
compared by count + checksum.

--self-checks-only: git / route self-checks only (no Doppler, database, npm).
--cleanup-only: the teardown and the leftover check only. With VERIFY_NESTED=1
the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "rep10"
BEFORE_REF = "bfdfe16"   # main @ 'merge vhk1' — the rep10-work base
_R = "/api/v1/replication"
_T = "/api/v1/tax/gifts"
ROUTES = {
    ("GET", f"{_R}/tilts/accounts/{{account_id}}"): "view_replication",
    ("POST", f"{_R}/tilts/accounts/{{account_id}}/preview"): "view_replication",
    ("POST", f"{_R}/tilts/accounts/{{account_id}}"): "manage_replication",
    ("POST", f"{_R}/tilts/sets/{{set_id}}/lines"): "manage_replication",
    ("POST", f"{_R}/tilts/sets/{{set_id}}/submit"): "manage_replication",
    ("POST", f"{_R}/tilts/sets/{{set_id}}/archive"): "manage_replication",
    ("POST", f"{_R}/tilts/sets/{{set_id}}/approve"): "approve_replication",
    ("POST", f"{_R}/tilts/sets/{{set_id}}/reject"): "approve_replication",
    ("GET", f"{_R}/completion/accounts/{{account_id}}"): "view_replication",
    ("POST", f"{_R}/completion/accounts/{{account_id}}"): "manage_replication",
    ("POST", f"{_R}/completion/accounts/{{account_id}}/confirm"): "manage_replication",
    ("GET", f"{_R}/consolidation/households/{{household_id}}"): "view_replication",
    ("POST", f"{_R}/consolidation/households/{{household_id}}"): "manage_replication",
    ("GET", f"{_R}/backtests"): "view_replication",
    ("POST", f"{_R}/backtests"): "manage_replication",
    ("GET", f"{_R}/backtests/{{backtest_id}}"): "view_replication",
    ("POST", f"{_R}/backtests/{{backtest_id}}/pdf"): "view_replication",
    ("GET", f"{_R}/member/backtests"): "none beyond auth",
    ("GET", f"{_R}/hypothetical-policy"): "view_replication",
    ("POST", f"{_R}/hypothetical-policy"): "manage_hypothetical_policy",
    ("POST", f"{_R}/hypothetical-policy/{{policy_id}}/approve"): "manage_hypothetical_policy",
    ("POST", f"{_R}/hypothetical-policy/{{policy_id}}/reject"): "manage_hypothetical_policy",
    ("GET", _T): "view_tax",
    ("POST", _T): "manage_tax",
    ("GET", f"{_T}/donees"): "view_tax",
    ("POST", f"{_T}/donees"): "manage_tax",
    ("POST", f"{_T}/donees/{{donee_id}}/retire"): "manage_tax",
    ("GET", f"{_T}/candidates"): "view_tax",
    ("GET", f"{_T}/{{gift_id}}"): "view_tax",
    ("POST", f"{_T}/{{gift_id}}/submit"): "manage_tax",
    ("POST", f"{_T}/{{gift_id}}/approve"): "approve_tax",
    ("POST", f"{_T}/{{gift_id}}/reject"): "approve_tax",
    ("POST", f"{_T}/{{gift_id}}/letter"): "manage_tax",
    ("POST", f"{_T}/{{gift_id}}/sent"): "manage_tax",
    ("POST", f"{_T}/{{gift_id}}/received"): "manage_tax",
    ("POST", f"{_T}/{{gift_id}}/acknowledged"): "manage_tax",
    ("POST", f"{_T}/{{gift_id}}/rebuy"): "manage_tax",
    ("POST", f"{_T}/{{gift_id}}/cancel"): "manage_tax",
}


def self_routes(check, find):
    """rep10's own routes: BEFORE_REF → its own merge (or HEAD while unmerged); each exists today with that gate.
    Never "every route added since a base commit"."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    check(len(o.before) > 0 and not o.changed and not o.removed, "rep10 changed or removed no existing route's gate",
          f"{o.where()}; {o.changed[:4]} {o.removed[:4]}")
    mine = {(m, p) for (ms, p) in o.new for m in ms if p.startswith((_R + "/", _T)) and not
            p.startswith((_R + "/options", _R + "/registry", _R + "/models", _R + "/lookup"))}
    check(mine == set(ROUTES), f"this sprint's own routes are exactly its {len(ROUTES)}",
          f"{o.where()}; extra={sorted(mine - set(ROUTES))[:3]} missing={sorted(set(ROUTES) - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items() if k not in by or g not in str(by[k].get("after"))]
    check(not bad, f"each of the {len(ROUTES)} names its gate at rep10's own end point", f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "rep10's routes all still exist today with the same gate", f"{drift[:4]}")


def self_checks(check, find):
    self_routes(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _authz1_common as AC  # noqa: E402
import _verify_cleanup as VC  # noqa: E402

sys.path.insert(0, str(API_DIR))
import main  # noqa: E402
from services import backtest as BT  # noqa: E402
from services import completion as CO  # noqa: E402
from services import gifts as G  # noqa: E402
from services import human_tasks as HT  # noqa: E402
from services import models as M  # noqa: E402
from services import rbac  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import rep10_common as RC  # noqa: E402
from services import security_data as SD  # noqa: E402
from services import rep10_sweeps as SW  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import tilts as TL  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.brief_blocks import register_brief_blocks  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.security_data import writes as SDW  # noqa: E402

TAG = "rep10verify"
PREFIX = "ZZR10"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
SEC_KEYS = ("K1", "K2", "K3", "FUND", "STK")
SEC_NAMES = {k: f"REP10 VERIFY {k}" for k in SEC_KEYS}
# rep10 fix 1: a security NO source holds a price for (created in y_no_data; torn down by name / PREFIX identifier)
SEC_NAMES["NP"] = "REP10 VERIFY NP"
SEC_TYPES = {"FUND": "fund"}
HH_NAME = "REP10 Verify Household"
DONEE_RAW = "ZZ10DONEE456789"          # the donee account's full number — must never reach the DB or a PDF
SEED_INSTRUMENT_CLASS = "index_etf"
HH_RATES = {"federal_ordinary_rate": "0.37", "federal_lt_rate": "0.2", "niit_applies": True, "state_code": "NY",
            "state_rate": "0.0685", "city": "New York", "city_rate": "0.03876", "city_lt_rate": "0.03"}
MODEL_W = {"K1": Decimal("0.40"), "K2": Decimal("0.30"), "K3": Decimal("0.25"), "cash": Decimal("0.05")}
FUND_W = {"K2": Decimal("0.6"), "K3": Decimal("0.4")}
HIST_DAYS = 70
Q = Decimal("0.0000000001")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000001a{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_ADMIN1, U_ADMIN2, U_VIEWER, U_MEMBER, U_ADMIN_B = (_id(110 + i) for i in range(5))
USERS = {
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP10 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP10 Admin Checker"),
    U_VIEWER: (ORG_A, "member", "viewer", "rep10_viewer", "REP10 Viewer"),
    U_MEMBER: (ORG_A, "member", "member", "rep10_member", "REP10 Member"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP10 Org B Admin"),
}
VIEWER_PERMS = ("view_replication", "view_tax")
ENT_A, ENT_B = _id(201), _id(202)
HH_A = _id(250)
A_M, A_HA, A_ADV, A_B = _id(301), _id(302), _id(303), _id(304)
ACCOUNTS = {   # id: (org, custodian, held_away, raw number, household)
    A_M: (ORG_A, "ALTRUIST", False, "ZZ10M0000001", HH_A),
    A_HA: (ORG_A, "FIDELITY", True, "ZZ10H0000002", HH_A),
    A_ADV: (ORG_A, "SCHWAB", True, "ZZ10A0000003", HH_A),
    A_B: (ORG_B, "ALTRUIST", False, "ZZ10B0000004", None),
}
CASH_A_M = Decimal("10000")
# positions: (account, security, quantity, market value, [(lot name, qty, unit cost, acquired)])
POSITIONS = (
    (A_M, "K1", "100", "4000", [("K1L", "100", "30", "2019-01-02")]),
    (A_M, "K2", "50", "3000", [("K2L", "50", "66", "2019-01-02")]),
    (A_M, "STK", "30", "3000", [("LTG1", "10", "20", "2019-01-02"), ("LTG2", "10", "50", "2020-01-02"),
                                ("STG", "5", "60", None), ("LOSS", "5", "150", "2019-01-02")]),
    (A_M, "FUND", "10", "1000", [("FM", "10", "90", "2019-01-02")]),
    (A_HA, "K1", "50", "2000", [("HK1", "50", "30", "2019-01-02")]),
    (A_HA, "FUND", "20", "2000", [("HF", "20", "80", "2019-01-02")]),
    (A_HA, "STK", "10", "1000", [("HS", "10", "20", "2019-01-02")]),
    (A_ADV, "FUND", "10", "1000", [("AF", "10", "70", "2019-01-02")]),
)

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.workflow_definitions",
    "public.workflow_runs", "public.workflow_run_steps", "public.entities", "public.households", "public.accounts",
    "public.account_balances_daily", "public.org_settings", "public.delegate_grants", "portfolio.assets",
    "portfolio.positions", "portfolio.tax_lots", "portfolio.securities_global", "portfolio.securities_global_identifiers",
    "portfolio.securities_global_prices", "portfolio.models", "portfolio.model_versions",
    "portfolio.model_version_positions", "portfolio.replica_securities", "portfolio.replica_holdings_snapshots",
    "portfolio.replica_holdings_rows", "public.account_model_links", "public.rebalance_proposals",
    "public.rebalance_proposal_trades", "public.account_authority_settings", "public.stale_confirmations",
    "public.tax_profiles", "public.tilt_sets", "public.tilt_lines", "public.completion_settings",
    "public.charitable_donees", "public.gift_proposals", "public.gift_lots", "public.hypothetical_performance_policies",
    "public.backtests", "portfolio.security_factor_scores",
)
NEW_TABLES = ("public.tilt_sets", "public.tilt_lines", "public.completion_settings", "public.charitable_donees",
              "public.gift_proposals", "public.gift_lots", "public.hypothetical_performance_policies", "public.backtests")
SEED_CHECK_LITERALS = (
    ("public.tilt_lines", "tilt_lines_kind_chk", ("security", "sector", "factor")),
    ("public.tilt_sets", "tilt_sets_status_chk", ("draft", "pending_approval", "approved", "archived")),
    ("public.charitable_donees", "charitable_donees_kind_chk", ("daf", "private_foundation")),
    ("public.gift_proposals", "gift_proposals_status_chk", ("draft", "pending_approval", "approved", "sent", "received",
                                                            "acknowledged", "cancelled")),
    ("public.gift_proposals", "gift_proposals_rebuy_chk", ("none", "same_security")),
    ("public.gift_proposals", "gift_proposals_itemizes_chk", ("yes", "no")),
    ("public.hypothetical_performance_policies", "hypothetical_policies_audience_chk", ("staff_and_members",)),
    ("public.backtests", "backtests_window_chk", ("2008", "custom")),
    ("public.backtests", "backtests_status_chk", ("computed", "not_estimated")),
    ("public.completion_settings", "completion_settings_authorities_chk", ("read_only", "advise")),
    ("portfolio.tax_lots", "tax_lots_status_check", ("open",)),
    ("portfolio.securities_global", "securities_global_type_chk", ("equity", "fund")),
    ("portfolio.securities_global_identifiers", "sec_global_ident_type_chk", ("cusip", "ticker")),
    ("portfolio.replica_holdings_snapshots", "replica_holdings_snapshots_status_chk", ("live",)),
    ("portfolio.data_source_rank", "data_source_rank_type_chk", ("factor_score",)),
    ("public.account_authority_settings", "account_authority_settings_authority_chk", ("advise",)),
)

S: dict = {}
NETWORK_CALLS: list[str] = []
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""), flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def today() -> date:
    """Always the date AT THE CALL — never fixed at script start."""
    return date.today()


def D(v) -> Decimal:
    return Decimal(str(v))


def tk(key):
    return f"{PREFIX}{key}"


def sub_of(uid):
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid):
    return f"{TAG}-{USERS[uid][2]}@test.local"


def sec(key) -> str:
    return str(S[f"sec_{key}"])


def pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


# ═══ The verify's OWN statement of the rules (independent of the services) ═══
def lr_allocate(weights: list[tuple[str, Decimal]], total: Decimal) -> dict:
    """Largest remainder at 1e-10, ties by sorted key."""
    with localcontext() as ctx:
        ctx.prec = 60
        units = total / Q
        s = sum((w for _, w in weights), Decimal(0))
        raw = {k: w * units / s for k, w in weights}
        fl = {k: v.to_integral_value(rounding=ROUND_FLOOR) for k, v in raw.items()}
        left = int(units - sum(fl.values(), Decimal(0)))
        order = sorted(weights, key=lambda kw: (-(raw[kw[0]] - fl[kw[0]]), kw[0]))
        for i in range(left):
            fl[order[i][0]] += 1
        return {k: (v * Q).quantize(Q) for k, v in fl.items()}


def exp_te(active: dict, series: dict) -> Decimal:
    """√(aᵀΣa × 252), Σ the sample covariance of daily simple returns on the common dates."""
    with localcontext() as ctx:
        ctx.prec = 50
        days = sorted(set.intersection(*[set(v) for v in series.values()]))
        rets = {k: [series[k][days[i]] / series[k][days[i - 1]] - 1 for i in range(1, len(days))] for k in series}
        n = len(days) - 1
        mean = {k: sum(v, Decimal(0)) / n for k, v in rets.items()}
        var = Decimal(0)
        for a in active:
            for b in active:
                cov = sum(((rets[a][t] - mean[a]) * (rets[b][t] - mean[b]) for t in range(n)), Decimal(0)) / (n - 1)
                var += active[a] * active[b] * cov
        return (var * 252).sqrt().quantize(Q, rounding=ROUND_HALF_EVEN)


def money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def exp_compare(fmv, basis, lt, ordinary, kind, itemizes, agi):
    lim = {"public_charity": (D("0.30"), D("0.60")), "daf": (D("0.30"), D("0.60")),
           "private_foundation": (D("0.20"), D("0.30"))}[kind]
    gain = fmv - basis
    cg = money(gain * lt)
    if itemizes != "yes":
        return {"cg": cg, "ded_ik": None, "ded_cash": None, "carry_ik": None, "saved": cg}
    ik = min(fmv, money(agi * lim[0]))
    ca = min(fmv, money(agi * lim[1]))
    return {"cg": cg, "ded_ik": money(ik * ordinary), "ded_cash": money(ca * ordinary), "carry_ik": fmv - ik,
            "saved": cg + money(ik * ordinary) - money(ca * ordinary)}


def exp_backtest(days, px, fund, weights, notional, loss_pct, loss_min):
    """cadence 'none': buy on day 0; each later day harvest a lot whose loss ≥ pct of cost and ≥ min, block 30 days,
    buy back the proceeds on the first day ≥ sale + 31 days. Returns (values, harvested losses)."""
    with localcontext() as ctx:
        ctx.prec = 50
        cash = notional
        lots = {}
        for k, w in weights.items():
            if k == "cash":
                continue
            amt = w * notional
            lots[k] = [[amt / px[k][days[0]], px[k][days[0]], days[0]]]
            cash -= amt
        pending, losses, vals = [], [], []
        for i, d in enumerate(days):
            if i > 0:
                for k in sorted(lots):
                    keep = []
                    for lot in lots[k]:
                        p = px[k][d]
                        loss = (lot[1] - p) * lot[0]
                        if loss > 0 and loss / (lot[0] * lot[1]) >= loss_pct and loss >= loss_min:
                            cash += lot[0] * p
                            losses.append(loss)
                            pending.append((k, lot[0] * p, d + timedelta(days=31)))
                        else:
                            keep.append(lot)
                    lots[k] = keep
                for pb in [x for x in pending if d >= x[2]]:
                    pending.remove(pb)
                    k, amt, _ = pb
                    spend = min(amt, cash)
                    lots[k].append([spend / px[k][d], px[k][d], d])
                    cash -= spend
            vals.append(cash + sum((sum((l[0] for l in ls), Decimal(0)) * px[k][d] for k, ls in lots.items()),
                                   Decimal(0)))
        rp = [vals[t] / vals[t - 1] - 1 for t in range(1, len(vals))]
        fv = [fund[d] for d in days]
        rf = [fv[t] / fv[t - 1] - 1 for t in range(1, len(fv))]
        diff = [a - b for a, b in zip(rp, rf)]
        m = sum(diff, Decimal(0)) / len(diff)
        te = (sum(((x - m) ** 2 for x in diff), Decimal(0)) / (len(diff) - 1) * 252).sqrt().quantize(Q)
        peak, worst = vals[0], Decimal(0)
        for v in vals:
            peak = max(peak, v)
            worst = min(worst, v / peak - 1)
        return {"end": money(vals[-1]), "ret": (vals[-1] / vals[0] - 1).quantize(Q), "te": te,
                "mdd": format(worst.quantize(Q), "f"), "losses": money(sum(losses, Decimal(0))), "count": len(losses)}


# ═══ DB helpers ═════════════════════════════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in COUNTED}


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """Rows this sprint must never touch: everything outside this run's own fixture orgs."""
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {}
    for name, table in (("accounts", "public.accounts"), ("positions", "portfolio.positions"),
                        ("tax_lots", "portfolio.tax_lots"), ("rebalance_proposals", "public.rebalance_proposals"),
                        ("execution_instructions", "public.execution_instructions"),
                        *[(t.split(".")[1], t) for t in NEW_TABLES]):
        out[name] = await fingerprint(conn, f"SELECT * FROM {table} WHERE NOT (org_id = ANY($1::uuid[]))", orgs)
    out["prices"] = await fingerprint(conn, "SELECT * FROM portfolio.securities_global_prices WHERE org_id IS NULL "
                                            "OR NOT (org_id = ANY($1::uuid[]))", orgs)
    out["users"] = await fingerprint(conn, "SELECT id, org_id, email, role, status FROM public.users WHERE NOT "
                                           "(org_id = ANY($1::uuid[])) AND auth0_sub NOT LIKE $2", orgs, f"%{TAG}%")
    return out


async def close_conn(c, timeout: float = 30) -> None:
    if c is None:
        return
    try:
        await asyncio.wait_for(c.close(), timeout)
    except Exception:  # noqa: BLE001
        try:
            c.terminate()
        except Exception:  # noqa: BLE001
            pass


async def close_app_pool() -> None:
    import services.database as DB
    p, DB._pool = DB._pool, None
    await close_conn(p)


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def fixture_security_ids(conn) -> list:
    rows = await conn.fetch("SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers "
                            "WHERE id_value LIKE $1", f"{PREFIX}%")
    ids = {r["global_security_id"] for r in rows}
    ids |= {r["id"] for r in await conn.fetch("SELECT id FROM portfolio.securities_global WHERE name = ANY($1::text[])",
                                              list(SEC_NAMES.values()))}
    return list(ids)


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        secs = await fixture_security_ids(op)
        orgs, users = FIXTURE_ORGS, list(USERS)
        by_org = "org_id = ANY($1::uuid[])"
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [orgs, users])
        snaps = "snapshot_id IN (SELECT id FROM portfolio.replica_holdings_snapshots WHERE " \
                "fund_global_security_id = ANY($1::uuid[]))"
        plan = {t: (by_org, [orgs]) for t in (
            "public.backtests", "public.hypothetical_performance_policies", "public.gift_lots", "public.gift_proposals",
            "public.charitable_donees", "public.completion_settings", "public.tilt_lines", "public.tilt_sets",
            "public.stale_confirmations", "public.rebalance_proposal_trades", "public.rebalance_proposals",
            "public.account_transition_plans", "public.account_model_links", "public.account_authority_settings",
            "public.tax_profiles", "public.domain_event_deliveries", "public.domain_events",
            "public.workflow_run_steps", "public.agent_proposals", "public.workflow_runs", "public.workflow_steps",
            "public.workflow_versions", "public.workflow_definitions", "portfolio.tax_lots", "portfolio.transactions",
            "portfolio.positions", "portfolio.assets", "public.account_balances_daily", "public.account_owners",
            "public.accounts", "public.delegate_grants", "public.household_memberships", "public.households",
            "public.entities", "public.org_settings", "portfolio.model_version_positions", "portfolio.model_versions",
            "portfolio.models", "portfolio.replica_securities", "portfolio.securities_global_prices",
            "portfolio.security_factor_scores")}
        plan["public.member_todos"] = org_or_user
        plan["public.audit_log"] = org_or_user
        plan["portfolio.replica_holdings_rows"] = (snaps, [secs])
        plan["portfolio.replica_holdings_snapshots"] = ("fund_global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global_identifiers"] = ("global_security_id = ANY($1::uuid[])", [secs])
        plan["portfolio.securities_global"] = ("id = ANY($1::uuid[])", [secs])
        exists = {r["t"] for r in await op.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r'")}
        cols = {(r["t"]) for r in await op.fetch(
            "SELECT table_schema || '.' || table_name AS t FROM information_schema.columns WHERE column_name = 'org_id'")}
        plan = {t: v for t, v in plan.items() if t in exists and (v[0] != by_org or t in cols)}
        if "public.household_memberships" in exists and "public.household_memberships" not in cols:
            plan["public.household_memberships"] = ("household_id = ANY($1::uuid[])", [[HH_A]])
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown", "models.teardown", "replica.teardown"),
                               cycle_order=("portfolio.model_library_entries",), label="verify_rep10")
        return res["log"]
    finally:
        await close_conn(op)


async def teardown_retrying() -> list[str]:
    try:
        return await teardown()
    except (asyncpg.DeadlockDetectedError, asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError,
            ConnectionError) as exc:
        print(f"[INFO] teardown hit {type(exc).__name__}; retrying once")
        return await teardown()


async def leftovers(conn) -> int:
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.securities_global WHERE name = ANY($1::text[])", [list(SEC_NAMES.values())]),
        ("SELECT count(*) FROM portfolio.securities_global_prices WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    op = await operator_conn()
    try:
        for r in await op.fetch(
                "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
                "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
                "'information_schema')"):
            n += int(await op.fetchval(f"SELECT count(*) FROM {r['t']} WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS))
    finally:
        await close_conn(op)
    return n


# ═══ App access ═════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api_raw(uid, method, path, body=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep10"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b"", {}
    return res.status_code, res.content, dict(res.headers)


async def api(uid, method, path, body=None):
    code, content, _ = await api_raw(uid, method, path, body)
    try:
        return code, json.loads(content.decode("utf-8") or "{}")
    except ValueError:
        return code, {"raw": content[:300].decode("utf-8", "replace")}


def install_stubs():
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_rep10: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


class _ctx:
    def __init__(self, org, super_admin=False):
        self.org, self.super_admin = org, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org) if self.org else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


async def in_org(fn, org=None):
    pool = await get_pool()
    with _ctx(org or ORG_A):
        async with pool.acquire() as c2:
            return await fn(c2)


def pos_row(row, key, w):
    sid = S.get(f"sec_{key}") if key != "cash" else None
    return {"row_num": row, "global_security_id": str(sid) if sid else None, "raw_ticker": tk(key) if sid else None,
            "raw_cusip": None, "raw_isin": None, "raw_sedol": None, "raw_name": SEC_NAMES.get(key, "Cash"),
            "target_weight": D(w), "asset_class": None, "sleeve_label": None, "drift_min": None, "drift_max": None,
            "alternates": [], "restrictions": None, "notes": None,
            "resolution_state": "resolved" if sid else "cash", "resolution_note": None}


async def make_model(name: str, positions: list[dict]) -> tuple[UUID, UUID]:
    pool = await get_pool()
    with _ctx(ORG_A):
        async with pool.acquire() as conn:
            model, ref = await M.create_model(conn, org_id=ORG_A, actor_id=U_ADMIN1, name=name, source="upload")
            v = await M._create_version(conn, org_id=ORG_A, model=model, origin="manual", maker_ids=[U_ADMIN1],
                                        created_by=U_ADMIN1, positions=positions)
        await M.publish(pool, [ref])
        await M.submit(pool, org_id=ORG_A, actor_id=U_ADMIN1, model_id=model["id"], version_id=v["id"])
        await M.decide(pool, org_id=ORG_A, actor_id=U_ADMIN2, model_id=model["id"], version_id=v["id"],
                       outcome="approve")
    return UUID(str(model["id"])), UUID(str(v["id"]))


def history_days() -> list[date]:
    """HIST_DAYS business days ending yesterday — computed at the call."""
    out, d = [], today() - timedelta(days=1)
    while len(out) < HIST_DAYS:
        if d.isoweekday() < 6:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def synthetic(days) -> dict:
    px = {"K1": {}, "K2": {}, "K3": {}, "FUND": {}}
    for i, d in enumerate(days):
        px["K1"][d] = D(100) if i < 5 else (D(90) if i < 40 else D(95))
        px["K2"][d] = D(50) + D(i) * D("0.1")
        px["K3"][d] = D(20) + D(i % 3) * D("0.2")
        px["FUND"][d] = D(30) + D(i) * D("0.05") + D(i % 2) * D("0.1")
    return px


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings 1a–1e")
    find("1a target path", "rebalance_proposals.target_for: model look-through → apply_exclusions → (rep10) the "
         "approved tilt set → completion → compute → rebalance_engine.build (cash floor, lot method, gain budget) → "
         "apply_wash_control. One hook (_rep10_adjust); with neither in effect the dict is exactly rep5's")
    find("1b X-ray", "xray.compute: constituents per account (look-through of registry funds with live files), "
         "accounts[] carry authority / custody; fund×fund and household×replica overlap by Σ min weights. Completion "
         "counts read_only + advise by default (managed opt-in) and trades only the managed account")
    find("1c gift path", "no non-sale removal path exists (no transfer_out / gift transaction type); the gift is a "
         "manual transfer letter + statuses, the lots leave the book when the custodian feed shows them gone")
    find("1d price history", "security_data.history takes the FIRST ranked source with ANY point — the custodian's "
         "one-day valuation of a held position shadows a full uploaded series. rep10 reads each source through the "
         "layer and uses the one with the most points (rep10_common.longest_history); never combined")
    find("1e compliance precedent", "nothing like a hypothetical-performance / Marketing Rule policy, disclosure "
         "library or member-visibility flag existed; modelled on rep9's allocation policy (authority_change_guard)")


def y_local_day():
    """rep10 fix 1 (FAIL B, a CODE defect): pure — reproduce, then the fix. The verify ran at ~21:55 ET on Nov 2:
    the letter's sent_at (timestamptz → asyncpg returns UTC) was 01:55 UTC Nov 3, so ``sent_at.date()`` said Nov 3 and
    a receipt dated today (``date.today()`` = Nov 2) was refused as "received before the letter was sent" (422)."""
    from datetime import datetime, timezone

    section("[Y] FAIL B — the received date vs the sent date, on one clock")
    et = timezone(timedelta(hours=-5))
    sent = datetime(2026, 11, 3, 1, 55, tzinfo=timezone.utc)          # 21:55 ET, Nov 2
    received = date(2026, 11, 2)
    check(received < sent.date(), "REPRODUCED: the old rule compared the local receipt day with the UTC day of "
          "sent_at — a same-evening receipt (Nov 2) looked earlier than the letter (Nov 3 UTC)", f"{sent.date()}")
    check(RC.local_day(sent, et) == received and not received < RC.local_day(sent, et)
          and RC.local_day(datetime(2026, 11, 2, 23, 0, tzinfo=et), et) == received
          and RC.local_day(None) is None,
          "fixed: rep10_common.local_day puts sent_at on date.today()'s clock — a same-day receipt is accepted, a "
          "genuinely earlier one is still refused", f"{RC.local_day(sent, et)}")
    check(date(2026, 11, 1) < RC.local_day(sent, et), "a receipt the day BEFORE the letter is still refused")


async def y_schema(conn):
    section("[Y] Schema landed, RLS for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep10.py"), "--verify"], REPO, timeout=600)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep10.py --verify: SCHEMA OK",
          f"{gaps[:3]} {err[-200:] if code else ''}")
    for t in NEW_TABLES + ("portfolio.security_factor_scores",):
        sch, name = t.split(".")
        cmds = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = $1 AND "
                                                       "tablename = $2", sch, name)}
        check(cmds == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(cmds)}")
    fks = await platform(conn, "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = ANY($1::regclass[])",
                         list(NEW_TABLES), fetch="fetchval")
    check(int(fks) >= 25, "FK constraints on the new tables read from the catalog", f"{fks}")


async def y_seed_literals(conn):
    section("[Y] Seed literals checked against the live catalog before seeding")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = await platform(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = $1::regclass "
                                 "AND conname = $2 AND contype = 'c'", table, con, fetch="fetchval")
        allowed = set(re.findall(r"'([^']*)'", d or ""))
        missing = [x for x in lits if x not in allowed]
        if d is None or missing:
            bad.append(f"{table}.{con}: {'absent' if d is None else missing}")
    ic = await platform(conn, "SELECT key FROM portfolio.replica_instrument_classes WHERE key = $1 AND enabled "
                              "AND valid_to IS NULL AND system_to IS NULL", SEED_INSTRUMENT_CLASS, fetch="fetchval")
    dg = [r["d"] for r in await platform(conn, "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE "
                                               "conrelid = 'public.delegate_grants'::regclass AND contype = 'c'")]
    scope_ok = all("view_only" in x for x in dg if "scope" in x)
    check(not bad and ic == SEED_INSTRUMENT_CLASS and scope_ok,
          f"every CHECK-constrained literal is admitted ({len(SEED_CHECK_LITERALS)} constraints, the instrument class, "
          "delegate scope 'view_only')", "; ".join(bad) + f" {ic} {dg}")
    S["literals_ok"] = not bad and ic == SEED_INSTRUMENT_CLASS and scope_ok


async def guard_prefix(conn) -> bool:
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
                       f"{PREFIX}%", fetch="fetchval")
    k = await platform(conn, "SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'REP10 VERIFY%'",
                       fetch="fetchval")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS,
                       fetch="fetchval")
    h = await platform(conn, "SELECT count(*) FROM households WHERE name = $1", HH_NAME, fetch="fetchval")
    return check(int(n) == 0 and int(k) == 0 and int(o) == 0 and int(h) == 0,
                 f"the reserved prefixes ({PREFIX}, 'REP10 VERIFY', the fixture orgs, the household) match no real row",
                 f"{n}/{k}/{o}/{h}")


async def seed(conn) -> None:
    S["seed_day"] = today()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"REP10 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        rid_view = await rbac.ensure_role(conn, ORG_A, "rep10_viewer", "REP10 verify — view only")
        for p in VIEWER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", rid_view,
                               perm_ids[p])
        rid_member = await rbac.ensure_role(conn, ORG_A, "rep10_member", "REP10 verify — a member, no staff grant")
        roles = {"rep10_viewer": rid_view, "rep10_member": rid_member}
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[rname])
        for ent, org, name in ((ENT_A, ORG_A, "REP10 Verify Member Alice"), (ENT_B, ORG_B, "REP10 Verify Org B")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', $3)", ent, org, name)
        await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)", HH_A,
                           ORG_A, HH_NAME, U_ADMIN1)
        await conn.execute("INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, $2, $3)",
                           HH_A, ENT_A, U_ADMIN1)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope, "
                           "effective_from, granted_by) VALUES ($1, $2, $3, 'view_only', now() - interval '1 day', $4)",
                           ORG_A, ENT_A, U_MEMBER, U_ADMIN1)
        for i, key in enumerate(SEC_KEYS):
            sid = await SG.create_security(conn, name=SEC_NAMES[key], security_type=SEC_TYPES.get(key, "equity"),
                                           is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}{i:04d}",
                                    is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=tk(key),
                                    is_super_admin=True)
            S[f"sec_{key}"] = UUID(str(sid))
        salts = {}
        for org in FIXTURE_ORGS:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for aid, (org, code, held, raw, hh) in ACCOUNTS.items():
            n = AccountNumber(raw)
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, $5, 'individual', 'taxable', $6, $7, true, $8)",
                aid, org, n.masked, n.hashed(salts[org]), code, ENT_A if org == ORG_A else ENT_B, hh, held)
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        assets = {}
        for key in SEC_KEYS:
            aid_ = uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) VALUES "
                               "($1, $2, $3, $4, $5)", aid_, ORG_A, S[f"sec_{key}"], SEC_NAMES[key],
                               SEC_TYPES.get(key, "equity"))
            assets[key] = aid_
        S["lots"] = {}
        for acct, key, qty, mv, lots in POSITIONS:
            pid = uuid.uuid4()
            as_of = S["seed_day"] - timedelta(days=5 if acct == A_ADV else 1)
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual', "
                "'manual', $8)", pid, ORG_A, ENT_A, assets[key], as_of, D(qty), D(mv), acct)
            for name, q, cost, acq in lots:
                lid = uuid.uuid4()
                acquired = date.fromisoformat(acq) if acq else S["seed_day"] - timedelta(days=100)
                await conn.execute(
                    "INSERT INTO portfolio.tax_lots (id, org_id, position_id, acquired_date, quantity, adjusted_cost, "
                    "original_cost, status) VALUES ($1, $2, $3, $4, $5, $6, $6, 'open')", lid, ORG_A, pid, acquired,
                    D(q), D(q) * D(cost))
                S["lots"][name] = str(lid)
        await conn.execute(
            "INSERT INTO public.account_balances_daily (org_id, account_id, as_of_date, total_market_value, cash_value, "
            "margin_balance, accrued_income, source_system, source_confidence, is_billing_source, is_final) VALUES "
            "($1, $2, $3, 0, $4, 0, 0, 'manual', 'CONFIRMED', true, true)", ORG_A, A_M,
            S["seed_day"] - timedelta(days=1), CASH_A_M)
        # FUND in the registry with a live holdings file (K2 60%, K3 40%) — the X-ray's look-through
        rs = uuid.uuid4()
        await conn.execute(
            "INSERT INTO portfolio.replica_securities (id, org_id, global_security_id, instrument_class, ticker, "
            "holdings_source_key, status, display_name, created_by) VALUES ($1, $2, $3, $4, $5, 'zz_rep10_test', "
            "'active', $6, $7)", rs, ORG_A, S["sec_FUND"], SEED_INSTRUMENT_CLASS, tk("FUND"), "Holdings of FUND",
            U_ADMIN1)
        sid = await conn.fetchval(
            """INSERT INTO portfolio.replica_holdings_snapshots (fund_global_security_id, as_of, source_key, file_sha256,
                   row_count, total_weight, total_weight_ex_cash, status, validation, diff, diff_exceeds_threshold)
               VALUES ($1::uuid, $2, 'zz_rep10_test', $3, 2, 1, 1, 'live', '{}'::jsonb,
                       '{"first_snapshot": true}'::jsonb, false) RETURNING id""",
            S["sec_FUND"], S["seed_day"] - timedelta(days=10), hashlib.sha256(TAG.encode()).hexdigest())
        for i, (key, w) in enumerate(FUND_W.items(), start=1):
            await conn.execute(
                """INSERT INTO portfolio.replica_holdings_rows (snapshot_id, row_num, raw_name, raw_ticker, raw_cusip, raw,
                       asset_type, constituent_global_security_id, resolution_state, resolved_via, weight, shares)
                   VALUES ($1, $2, $3, $4, NULL, '{}'::jsonb, 'equity', $5::uuid, 'resolved', 'cusip', $6, 100)""",
                sid, i, SEC_NAMES[key], tk(key), S[f"sec_{key}"], w)
    orgs = await platform(conn, "SELECT id, is_test FROM organizations WHERE id = ANY($1::uuid[])", FIXTURE_ORGS)
    check(len(orgs) == 2 and all(r["is_test"] is True for r in orgs), "every fixture org is is_test = true (the live "
          "scheduler skips them)", f"{[dict(r) for r in orgs]}")
    us = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                        fetch="fetchval")
    check(int(us) == len(USERS), "fixture users created explicitly, is_test = true", f"{us}")
    S["model"], S["model_v"] = await make_model("REP10 VERIFY Model", [pos_row(i + 1, k, w) for i, (k, w)
                                                                         in enumerate(MODEL_W.items())])
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{A_M}/model/join", {"model_id": str(S["model"]), "settings": {}})
    lid = (o.get("link") or {}).get("id")
    c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/accounts/{A_M}/model/changes/{lid}/approve", {})
    check(c == 201 and c2 == 200, "the managed account joins the model through rep5's real path", f"{c} {c2} {str(o)[:160]}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/authority/accounts/{A_ADV}/changes",
                     {"effective_from": today().isoformat(), "reason": "REP10 verify", "authority_override": "advise",
                      "custody": "outside"})
    cid = (o.get("change") or {}).get("id")
    ca, _ = await api(U_ADMIN2, "POST", f"/api/v1/authority/changes/{cid}/approve", {})
    check(c == 201 and ca == 200, "the second held-away account is set to advise through rep7b's approved change",
          f"{c} {ca}")
    body = {"subject_type": "household", "subject_id": str(HH_A), "tax_year": today().year,
            "filing_status": "married_filing_jointly", **HH_RATES, "as_of": (today() - timedelta(days=2)).isoformat(),
            "source_note": "REP10 verify — planning memo"}
    c, o = await api(U_ADMIN1, "POST", "/api/v1/tax/profiles", body)
    c2, _ = await api(U_ADMIN2, "POST", f"/api/v1/tax/profiles/{(o.get('profile') or {}).get('id')}/approve", {})
    check(c == 201 and c2 == 200, "the household's tax profile entered and approved (rep6)", f"{c} {c2}")
    S["seeded"] = True


def base_target() -> dict:
    return {sec(k) if k != "cash" else "cash": w for k, w in MODEL_W.items()}


async def tilt_count(conn) -> int:
    return int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.tilt_sets", fetch="fetchval"))


async def y_tilts(conn):
    section("[Y] Tilts: target exact, limits, factor refusal, active share, TE not-estimated vs computed, proposal "
            "lines")
    k1, k2, k3 = sec("K1"), sec("K2"), sec("K3")
    room = lr_allocate([(k2, MODEL_W["K2"]), (k3, MODEL_W["K3"])], MODEL_W["K2"] + MODEL_W["K3"] - D("0.02"))
    want = {k1: D("0.42"), k2: room[k2], k3: room[k3], "cash": MODEL_W["cash"]}
    line = [{"kind": "security", "subject": k1, "delta": "0.02", "reason": "conviction"}]
    c, p = await api(U_ADMIN1, "POST", f"{_R}/tilts/accounts/{A_M}/preview", {"lines": line})
    got = {w["key"]: D(w["final_weight"]) for w in p.get("weights") or []}
    check(c == 200 and p.get("ok") is True and got == want and sum(got.values()) == D(1),
          "preview: +2% on K1 → K1 0.42; the net taken pro rata (largest remainder) from the untilted K2 / K3; total "
          "exactly 1; cash unchanged", f"{c} {got} want {want}")
    check(D((p.get("metrics") or {}).get("active_share") or "-1") == D("0.0200000000"),
          "active share = ½ Σ |final − model| = 0.02 exactly", f"{(p.get('metrics') or {}).get('active_share')}")
    te = p.get("tracking_error") or {}
    check(te.get("estimated") is False and "not estimated — needs prices" in str(te.get("label"))
          and "0.00% of weight covered" in str(te.get("label")) and te.get("hypothetical") is True,
          "with zero price history: TE reads 'not estimated — needs prices (0.00% of weight covered)', hypothetical",
          f"{te}")
    before = await tilt_count(conn)
    c6, o6 = await api(U_ADMIN1, "POST", f"{_R}/tilts/accounts/{A_M}",
                       {"name": "Too much", "reason": "x", "lines": [{**line[0], "delta": "0.06"}]})
    cf, of = await api(U_ADMIN1, "POST", f"{_R}/tilts/accounts/{A_M}",
                       {"name": "Factor", "reason": "x", "lines": [{"kind": "factor", "subject": "value",
                                                                    "delta": "0.01", "reason": "views"}]})
    cs, os_ = await api(U_ADMIN1, "POST", f"{_R}/tilts/accounts/{A_M}/preview",
                        {"lines": [{"kind": "sector", "subject": "Energy", "delta": "0.01"}]})
    check(c6 == 422 and "per-security limit" in str(o6) and cf == 422 and "needs factor scores" in str(of)
          and await tilt_count(conn) == before,
          "refused with the plain reason and NOTHING written: a 6% tilt (limit 5%); a factor tilt with no factor scores",
          f"{c6} {str(o6)[:160]} {cf} {str(of)[:160]}")
    check(cs == 200 and os_.get("ok") is False and "needs sectors" in str(os_.get("refused")),
          "a sector tilt on a model with no source holdings file: 'needs sectors'", f"{os_.get('refused')}")
    cn, on = await api(U_ADMIN1, "POST", f"{_R}/tilts/accounts/{A_M}/preview",
                       {"lines": [{"kind": "security", "subject": sec("STK"), "delta": "0.01"}]})
    check(cn == 200 and any("not in the model — added" in n for n in on.get("notes") or []),
          "a tilt on a security not in the model adds it (stated)", f"{on.get('notes')}")
    # synthetic history — the FIXTURE org's scope only
    days = history_days()
    S["days"], S["px"] = days, synthetic(days)
    pool = await get_pool()
    with _ctx(ORG_A):
        async with pool.acquire() as c2:
            async with c2.transaction():
                for key in ("K1", "K2", "K3", "FUND"):
                    await SDW.upsert_prices(None, sec(key), sorted(S["px"][key].items()), provider_key="csv_upload",
                                            org_id=str(ORG_A), licence={"internal_use": True}, conn=c2)
    n_b = int(await scoped(conn, ORG_B, "SELECT count(*) FROM portfolio.securities_global_prices WHERE "
                                        "global_security_id = $1", S["sec_K1"], fetch="fetchval"))
    n_a = int(await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.securities_global_prices WHERE "
                                        "global_security_id = $1 AND org_id = $2", S["sec_K1"], ORG_A, fetch="fetchval"))
    check(n_a == HIST_DAYS and n_b == 0, "synthetic history lives in the fixture org's scope only (org B sees none)",
          f"{n_a} {n_b}")
    c, p = await api(U_ADMIN1, "POST", f"{_R}/tilts/accounts/{A_M}/preview", {"lines": line})
    te = p.get("tracking_error") or {}
    active = {k1: want[k1] - MODEL_W["K1"], k2: want[k2] - MODEL_W["K2"], k3: want[k3] - MODEL_W["K3"]}
    exp = exp_te(active, {sec(k): S["px"][k] for k in ("K1", "K2", "K3")})
    check(te.get("estimated") is True and D(te.get("tracking_error") or "-1") == exp and te.get("hypothetical") is True,
          "after history is loaded: TE computed EXACTLY against the hand-computed covariance (hypothetical)",
          f"{te.get('tracking_error')} want {exp}; {str(te)[:200]}")
    # the maker-checker path, the gate both ways on an identical request, then the proposal lines
    body = {"name": "REP10 views", "reason": "adviser conviction", "lines": line}
    cv, ov = await api(U_VIEWER, "POST", f"{_R}/tilts/accounts/{A_M}", body)
    c, o = await api(U_ADMIN1, "POST", f"{_R}/tilts/accounts/{A_M}", body)
    sid = (o.get("tilt_set") or {}).get("id")
    check(cv == 403 and "manage_replication" in str(ov) and c == 201,
          "identical create: the viewer refused naming manage_replication, the admin admitted", f"{cv} {c}")
    cs, _ = await api(U_ADMIN1, "POST", f"{_R}/tilts/sets/{sid}/submit")
    cm, om = await api(U_ADMIN1, "POST", f"{_R}/tilts/sets/{sid}/approve", {})
    ca, _ = await api(U_ADMIN2, "POST", f"{_R}/tilts/sets/{sid}/approve", {})
    row = await scoped(conn, ORG_A, "SELECT status, approved_by FROM public.tilt_sets WHERE id = $1 AND system_to IS NULL",
                       UUID(sid), fetch="fetchrow") if sid else None
    check(cs == 200 and cm == 403 and ca == 200 and row is not None and row["status"] == "approved"
          and row["approved_by"] == U_ADMIN2, "maker refused (403), a second person approves (re-read from the DB)",
          f"{cs} {cm} {str(om)[:100]} {ca} {dict(row) if row else None}")
    S["tilt_set"] = sid
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{A_M}/proposals", {"trigger": "manual"})
    pid = (o.get("proposal") or o).get("id")
    tw = await scoped(conn, ORG_A, "SELECT target_weights FROM public.rebalance_proposals WHERE id = $1",
                      UUID(pid) if pid else None, fetch="fetchval") if pid else None
    tw = json.loads(tw) if isinstance(tw, str) else (tw or [])
    k1row = next((x for x in tw if x.get("key") == k1), {})
    check(c == 201 and D(k1row.get("model_weight", "-1")) == D("0.4") and D(k1row.get("tilt", "-1")) == D("0.02")
          and "final_weight" in k1row, "the rep5 proposal shows model weight, tilt and final weight per line",
          f"{c} {k1row}")
    S["proposal_tilted"] = pid


async def y_completion(conn):
    section("[Y] Completion: household total, no negative target, read_only untouched, stale confirmation")
    c, o = await api(U_ADMIN1, "POST", f"{_R}/tilts/sets/{S['tilt_set']}/archive", {"reason": "REP10 verify: test "
                                                                                    "completion on the plain model"})
    check(c == 200, "the tilt set retired (the completion proof runs on the plain model target)", f"{c}")
    cr, orr = await api(U_ADMIN1, "POST", f"{_R}/completion/accounts/{A_HA}",
                        {"count_elsewhere": True, "count_authorities": ["read_only"], "reason": "x"})
    check(cr == 409 and "read-only" in str(orr).lower(), "completion settings on a read-only account are refused (409)",
          f"{cr} {str(orr)[:140]}")
    c, o = await api(U_ADMIN1, "POST", f"{_R}/completion/accounts/{A_M}",
                     {"count_elsewhere": True, "count_authorities": ["read_only", "advise"], "reason": "REP10 verify"})
    c2, v = await api(U_ADMIN1, "GET", f"{_R}/completion/accounts/{A_M}")
    # the verify's own expectation from the seed: A = valued holdings + cash; E from the look-through
    A = D(4000) + D(3000) + D(3000) + D(1000) + CASH_A_M
    E = {sec("K1"): D(2000), sec("K2"): D(2000) * FUND_W["K2"] + D(1000) * FUND_W["K2"],
         sec("K3"): D(2000) * FUND_W["K3"] + D(1000) * FUND_W["K3"], sec("STK"): D(1000)}
    H = A + D(2000) + D(2000) + D(1000) + D(1000)
    ok = c == 200 and c2 == 200 and v.get("applied") is True
    worst = Decimal(0)
    for k, t in MODEL_W.items():
        if k == "cash":
            continue
        row = next((x for x in v.get("household_check") or [] if x["key"] == sec(k)), None)
        if row is None:
            ok = False
            continue
        worst = max(worst, abs(D(row["household_dollars"]) - t * H))
    check(bool(ok) and D(v.get("household_value") or "0") == H and worst <= D("0.01"),
          "household total per security = model weight × household value (within the stated 1e-10 rounding, ≤ $0.01)",
          f"{c} {c2} H={v.get('household_value')} want {H}; worst {worst}; {str(v.get('reasons'))[:120]}")
    w = {x["key"]: D(x["weight"]) for x in v.get("weights") or []}
    exc = [e for e in v.get("excess") or [] if e["key"] == sec("STK")]
    check(bool(w) and all(x >= 0 for x in w.values()) and len(exc) == 1 and D(exc[0]["excess"]) == D("1000.00")
          and "over target elsewhere" in exc[0]["note"],
          "no negative target; the security held elsewhere above its target is reported 'over target elsewhere — not "
          "traded' with its excess", f"{w} {exc}")
    k1_w = w.get(sec("K1"))
    check(k1_w == ((MODEL_W["K1"] * H - E[sec("K1")]) / A).quantize(Q, rounding=ROUND_HALF_EVEN),
          "the account's own K1 weight = (T × H − E) ÷ A exactly", f"{k1_w}")
    c, o = await api(U_ADMIN1, "POST", f"/api/v1/accounts/{A_M}/proposals", {"trigger": "manual"})
    pid = (o.get("proposal") or o).get("id")
    warn = await scoped(conn, ORG_A, "SELECT warnings FROM public.rebalance_proposals WHERE id = $1",
                        UUID(pid) if pid else None, fetch="fetchval") if pid else "[]"
    check(c == 201 and "completion" in str(warn), "the rep5 proposal is built on the completed target (warning "
          "'completion' carried)", f"{c} {str(warn)[:160]}")
    ro = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = ANY($1::uuid[])",
                          [A_HA], fetch="fetchval"))
    ri = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.execution_instructions WHERE account_id = $1", A_HA,
                          fetch="fetchval"))
    check(ro == 0 and ri == 0, "the read-only account is never traded: no proposal, no instruction", f"{ro} {ri}")
    # stale held-away data: the advise account's data is 5 days old; a 3-day threshold → not applied until confirmed
    c, _ = await api(U_ADMIN1, "POST", f"{_R}/completion/accounts/{A_M}",
                     {"count_elsewhere": True, "count_authorities": ["read_only", "advise"], "stale_after_days": 3,
                      "reason": "REP10 verify — strict staleness"})
    c2, v = await api(U_ADMIN1, "GET", f"{_R}/completion/accounts/{A_M}")
    need = [f["account_id"] for f in v.get("needs_confirmation") or []]
    check(c == 200 and v.get("applied") is False and str(A_ADV) in need and "confirm" in (v.get("actions") or []),
          "data older than the threshold: completion NOT applied, the account named, 'confirm' offered", f"{v.get('applied')} {need}")
    c3, o3 = await api(U_ADMIN1, "POST", f"{_R}/completion/accounts/{A_M}/confirm",
                       {"held_away_account_id": str(A_ADV)})
    conf = o3.get("confirmation") or {}
    # rep10 fix 1 (FAIL A, a VERIFY defect): the task was completed by calling HT.complete_task on a pool connection
    # with NO org RLS context, so assert_human's users read matched zero rows → AgentActorRefused "the actor is not an
    # admitted user" and nothing was confirmed. An adviser confirms through rep7's own human route, as verify_rep7 does.
    c5, o5 = await api(U_ADMIN2, "POST", f"/api/v1/held-away/confirmations/{conf.get('id')}/confirm",
                       {"note": "REP10 verify — statement checked"})
    crow = await scoped(conn, ORG_A, "SELECT status, confirmed_by, as_of_confirmed FROM public.stale_confirmations "
                                     "WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL",
                        UUID(conf["id"]) if conf.get("id") else None, fetch="fetchrow") if conf.get("id") else None
    check(c3 == 200 and c5 == 200 and conf.get("purpose") == "general" and crow is not None
          and crow["status"] == "confirmed" and crow["confirmed_by"] == U_ADMIN2,
          "the adviser confirms the stale as-of through rep7's human route (task completed by an admitted user; "
          "re-read: status confirmed, by the checker)",
          f"{c3} {str(o3)[:200]} | {c5} {str(o5)[:200]} | {dict(crow) if crow else None}")
    c4, v4 = await api(U_ADMIN1, "GET", f"{_R}/completion/accounts/{A_M}")
    check(c3 == 200 and c5 == 200 and v4.get("applied") is True and not v4.get("needs_confirmation"),
          "after the adviser confirms that as-of (rep7's task), completion applies again",
          f"{c3} {c5} {v4.get('applied')} {v4.get('reasons')} {v4.get('needs_confirmation')}")


async def y_consolidation(conn):
    section("[Y] Consolidation: managed sells only, advise letter, read_only listed")
    c, v = await api(U_ADMIN1, "GET", f"{_R}/consolidation/households/{HH_A}")
    lines = {ln["account_id"]: ln for ln in v.get("lines") or [] if ln["fund_security_id"] == sec("FUND")}
    exp_ov = min(FUND_W["K2"], MODEL_W["K2"]) + min(FUND_W["K3"], MODEL_W["K3"])
    m, ro, adv = lines.get(str(A_M)), lines.get(str(A_HA)), lines.get(str(A_ADV))
    check(c == 200 and bool(m and ro and adv) and D(m["replica_overlap"]["overlap_weight"]) == exp_ov,
          f"the overlapping fund is listed in all three accounts; overlap with the replica = Σ min = {exp_ov}",
          f"{c} {list(lines)} {str(v)[:200]}")
    if not (m and ro and adv):
        return
    check(m["action"] == "rebalance" and adv["action"] == "one_off" and ro["action"] == "listed"
          and ro["actions"] == [] and "transition_tax" in m and "break_even" in m,
          "managed → rep5 proposal; advise → advice letter; read_only → listed, no action; transition tax and "
          "break-even shown", f"{m['action']} {adv['action']} {ro['action']} {ro['actions']}")
    before_ro = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1",
                                 A_HA, fetch="fetchval"))
    cr, orr = await api(U_ADMIN1, "POST", f"{_R}/consolidation/households/{HH_A}",
                        {"account_id": str(A_HA), "fund_security_id": sec("FUND")})
    cm, om = await api(U_ADMIN1, "POST", f"{_R}/consolidation/households/{HH_A}",
                       {"account_id": str(A_M), "fund_security_id": sec("FUND")})
    ca, oa = await api(U_ADMIN1, "POST", f"{_R}/consolidation/households/{HH_A}",
                       {"account_id": str(A_ADV), "fund_security_id": sec("FUND")})
    pid = om.get("proposal_id")
    sells = await scoped(conn, ORG_A, "SELECT side, global_security_id FROM public.rebalance_proposal_trades WHERE "
                                      "proposal_id = $1", UUID(pid) if pid else None) if pid else []
    after_ro = int(await scoped(conn, ORG_A, "SELECT count(*) FROM public.rebalance_proposals WHERE account_id = $1",
                                A_HA, fetch="fetchval"))
    kinds = await scoped(conn, ORG_A, "SELECT proposal_kind FROM public.rebalance_proposals WHERE account_id = $1 AND "
                                      "system_to IS NULL ORDER BY created_at DESC LIMIT 1", A_ADV, fetch="fetchval")
    check(cr == 409 and "read-only" in str(orr) and after_ro == before_ro,
          "read_only: refused (409), nothing written", f"{cr} {before_ro}->{after_ro}")
    check(cm == 200 and any(s["side"] == "sell" and str(s["global_security_id"]) == sec("FUND") for s in sells),
          "managed: a rep5 proposal that SELLS the fund (outside the replica target)", f"{cm} {str(om)[:160]} {sells}")
    check(ca == 200 and oa.get("action") == "one_off" and kinds == "one_off",
          "advise: a one-off advice proposal (its instructions print as the advice letter)", f"{ca} {kinds} {str(oa)[:160]}")


async def y_gifts(conn):
    section("[Y] Gifting: lot choice, exclusions, comparison math, transfer letter, received date, rebuy, maker-checker")
    c, v = await api(U_ADMIN1, "GET", f"{_T}/candidates?subject_type=household&subject_id={HH_A}")
    names = {lid: n for n, lid in S["lots"].items()}
    order = [names.get(x["tax_lot_id"]) for x in v.get("candidates") or []]
    exc = {names.get(x["tax_lot_id"]): x["reason_code"] for x in v.get("excluded") or []}
    check(c == 200 and order == ["LTG1", "LTG2", "AF", "K1L", "FM"],
          "only long-term gain lots, ordered by gain per dollar (0.8, 0.5, 0.3, 0.25, 0.1)", f"{c} {order}")
    check(exc.get("STG") == "short_term" and exc.get("LOSS") == "loss" and exc.get("K2L") == "loss"
          and exc.get("HK1") == "read_only" and any("sell it, take the loss" in x["reason"]
                                                    for x in v.get("excluded") or []),
          "short-term (deduction limited to basis), loss ('give cash instead') and read-only lots excluded with reasons",
          f"{exc}")
    cv, ov = await api(U_VIEWER, "POST", f"{_T}/donees", {"name": "ZZ DAF", "kind": "daf", "ein": "12-3456789",
                                                           "receiving_broker": "ZZ Broker", "dtc_participant": "0123",
                                                           "account_number": DONEE_RAW})
    c, o = await api(U_ADMIN1, "POST", f"{_T}/donees", {"name": "ZZ DAF", "kind": "daf", "ein": "12-3456789",
                                                         "receiving_broker": "ZZ Broker", "dtc_participant": "0123",
                                                         "account_number": DONEE_RAW})
    did = (o.get("donee") or {}).get("id")
    raw_hits = int(await platform(conn, "SELECT count(*) FROM public.charitable_donees WHERE to_jsonb(charitable_donees)"
                                        "::text LIKE $1", f"%{DONEE_RAW}%", fetch="fetchval"))
    check(cv == 403 and "manage_tax" in str(ov) and c == 201 and (o.get("donee") or {}).get("account_number_masked")
          == AccountNumber(DONEE_RAW).masked and raw_hits == 0,
          "identical donee request: viewer 403 (manage_tax), admin 201; the account stored masked, the full number "
          "nowhere", f"{cv} {c} {raw_hits}")
    rates = {"lt": D("0.2") + D("0.038") + D("0.0685") + D("0.03"), "ordinary": D("0.37")}
    fmv, basis = D(2000), D(10) * D(20) + D(10) * D(50)
    body = {"subject_type": "household", "subject_id": str(HH_A), "member_entity_id": str(ENT_A), "donee_id": did,
            "tax_year": today().year, "lots": [{"tax_lot_id": S["lots"]["LTG1"]}, {"tax_lot_id": S["lots"]["LTG2"]}],
            "agi_estimate": "5000", "agi_source": "CPA estimate", "agi_as_of": today().isoformat(),
            "itemizes": "yes", "itemizes_source": "prior-year return", "itemizes_as_of": today().isoformat(),
            "prior_carryforward": "0", "prior_carryforward_source": "none on file",
            "prior_carryforward_as_of": today().isoformat(), "rebuy": "same_security"}
    c, o = await api(U_ADMIN1, "POST", _T, body)
    g = o.get("gift") or {}
    cmp_ = g.get("comparison") or {}
    e = exp_compare(fmv, basis, rates["lt"], rates["ordinary"], "daf", "yes", D(5000))
    check(c == 201 and D(cmp_.get("fmv", "0")) == fmv and D(cmp_["in_kind"]["cg_tax_avoided"]) == e["cg"]
          and D(cmp_["in_kind"]["deduction"]["value"]) == e["ded_ik"]
          and D(cmp_["cash"]["deduction"]["value"]) == e["ded_cash"]
          and D(cmp_["in_kind"]["deduction"]["carryforward"]) == e["carry_ik"]
          and D(cmp_["tax_saved_in_kind"]) == e["saved"] and cmp_.get("label") == "estimate",
          "DAF, itemizes yes: CG avoided, deduction (30% of AGI, carryover), cash alternative (60%), tax saved — exact",
          f"{c} {str(cmp_)[:300]} want {e}")
    for kind in ("public_charity", "daf", "private_foundation"):
        for item in ("yes", "no"):
            r = G.compare([{"quantity": "10", "price": "100", "unit_cost": "20"},
                           {"quantity": "10", "price": "100", "unit_cost": "50"}],
                          rates={"lt": rates["lt"], "st": D("0.5"), "parts": {"federal_ordinary": "0.37"}},
                          donee_kind=kind, itemizes=item, agi=D(5000), prior_carryforward=D(0))
            ex = exp_compare(fmv, basis, rates["lt"], rates["ordinary"], kind, item, D(5000))
            ok = D(r["tax_saved_in_kind"]) == ex["saved"] and D(r["in_kind"]["cg_tax_avoided"]) == ex["cg"]
            if item == "yes":
                ok = ok and D(r["in_kind"]["deduction"]["value"]) == ex["ded_ik"] and \
                    D(r["in_kind"]["deduction"]["carryforward"]) == ex["carry_ik"]
            else:
                ok = ok and r["in_kind"]["deduction"]["value"] is None and "does not itemize" in r["in_kind"]["deduction"]["note"]
            check(bool(ok), f"comparison exact: {kind}, itemizes {item} (its AGI limit, carryover)", f"{r} want {ex}")
    gid = g.get("id")
    cs, _ = await api(U_ADMIN1, "POST", f"{_T}/{gid}/submit")
    cm, om = await api(U_ADMIN1, "POST", f"{_T}/{gid}/approve", {})
    cvw, ovw = await api(U_VIEWER, "POST", f"{_T}/{gid}/approve", {})
    ca, oa = await api(U_ADMIN2, "POST", f"{_T}/{gid}/approve", {})
    st = await scoped(conn, ORG_A, "SELECT status, approved_by FROM public.gift_proposals WHERE id = $1 AND system_to IS "
                                   "NULL", UUID(gid) if gid else None, fetch="fetchrow") if gid else None
    check(cs == 200 and cm == 403 and cvw == 403 and "approve_tax" in str(ovw) and ca == 200 and st is not None
          and st["status"] == "approved" and st["approved_by"] == U_ADMIN2,
          "maker refused, a viewer refused naming approve_tax, the checker approves (re-read)",
          f"{cs} {cm} {cvw} {ca} {dict(st) if st else None}")
    code, pdf, hdr = await api_raw(U_ADMIN1, "POST", f"{_T}/{gid}/letter")
    txt = pdf_text(pdf) if code == 200 else ""
    check(code == 200 and pdf[:4] == b"%PDF" and "ZZ DAF" in txt and "12-3456789" in txt and "0123" in txt
          and AccountNumber(DONEE_RAW).masked in txt and DONEE_RAW not in txt
          and AccountNumber(ACCOUNTS[A_M][3]).masked in txt and ACCOUNTS[A_M][3] not in txt,
          "transfer letter: %PDF; donee, EIN, DTC participant, masked donee and delivering accounts; no full number",
          f"{code} {txt[:200]}")
    c1, _ = await api(U_ADMIN1, "POST", f"{_T}/{gid}/sent", {"channel": "email to the custodian"})
    recv = today()
    c2, o2 = await api(U_ADMIN1, "POST", f"{_T}/{gid}/received", {"received_on": recv.isoformat()})
    gd = await scoped(conn, ORG_A, "SELECT received_on FROM public.gift_proposals WHERE id = $1 AND system_to IS NULL",
                      UUID(gid), fetch="fetchval")
    check(c1 == 200 and c2 == 200 and (o2.get("gift") or {}).get("gift_date") == recv.isoformat() and gd == recv,
          "gift date = the date the donee's account received the shares (entered; re-read)",
          f"{c1} {c2} {gd} body={str(o2)[:300]}")
    c3, o3 = await api(U_ADMIN1, "POST", f"{_T}/{gid}/acknowledged", {"acknowledged_on": recv.isoformat(),
                                                                      "reference": "DAF receipt 1"})
    c4, o4 = await api(U_ADMIN1, "POST", f"{_T}/{gid}/rebuy")
    rp = (o4.get("gift") or {}).get("rebuy_proposal_ids") or []
    prow = await scoped(conn, ORG_A, "SELECT account_id, trigger_ref FROM public.rebalance_proposals WHERE id = $1",
                        UUID(rp[0]) if rp else None, fetch="fetchrow") if rp else None
    check(c3 == 200 and c4 == 200 and len(rp) == 1 and prow is not None and prow["account_id"] == A_M
          and "gift_rebuy" in str(prow["trigger_ref"]) and "wash-sale" in str(o4.get("rebuy_note")),
          "rebuy: a rep5 proposal for the giving account (same security), the wash-sale note stated",
          f"{c3} {c4} {rp} ack={str(o3)[:200]} rebuy={str(o4)[:300]}")
    S["gift"] = gid


async def y_backtest(conn):
    section("[Y] Backtest: coverage refusal, exact small case, hypothetical label, policy gate for members both ways")
    c, o = await api(U_ADMIN1, "POST", f"{_R}/backtests", {"model_id": str(S["model"]), "fund_security_id": sec("FUND"),
                                                           "window": "2008", "household_id": str(HH_A)})
    b = o.get("backtest") or {}
    check(c == 201 and b.get("status") == "not_estimated" and b.get("result") is None
          and "needs prices" in str(b.get("not_estimated_reason")) and b.get("hypothetical") is True
          and set((b.get("coverage") or {}).get("missing") or []) >= {sec("K1"), sec("K2"), sec("K3")},
          "no history in 2008: stored not_estimated with the coverage and the missing securities — no partial figures",
          f"{c} {str(b)[:300]}")
    days = S["days"]
    body = {"model_id": str(S["model"]), "fund_security_id": sec("FUND"), "window": "custom",
            "start": days[0].isoformat(), "end": days[-1].isoformat(), "cadence": "none", "harvest": True,
            "loss_pct": "0.05", "loss_min": "500", "notional": "1000000", "household_id": str(HH_A)}
    c, o = await api(U_ADMIN1, "POST", f"{_R}/backtests", body)
    b = o.get("backtest") or {}
    st = (b.get("result") or {}).get("statistics") or {}
    hv = (b.get("result") or {}).get("harvest") or {}
    e = exp_backtest(days, {sec(k): S["px"][k] for k in ("K1", "K2", "K3")}, S["px"]["FUND"],
                     {sec(k) if k != "cash" else "cash": w for k, w in MODEL_W.items()}, D(1000000), D("0.05"), D(500))
    check(c == 201 and b.get("status") == "computed" and D(st.get("end_value", "0")) == e["end"]
          and D(st.get("replica_return", "9")) == e["ret"] and D(st.get("tracking_error") or "9") == e["te"]
          and st.get("max_drawdown_replica") == e["mdd"],
          "small case exact against the verify's own replay: end value, return, realised TE, max drawdown",
          f"{c} {st} want {e}")
    check(hv.get("count") == e["count"] == 1 and D(hv.get("losses_total", "0")) == e["losses"] == D("40000.00"),
          "harvested losses exact: K1 −10% → one lot sold, $40,000.00 (4,000 × $10)", f"{hv} want {e['losses']}")
    check(b.get("hypothetical") is True and b.get("label") == "hypothetical"
          and "staff use only" in str(b.get("disclosure")) and o.get("label") == "hypothetical",
          "every envelope says hypothetical; with no policy the disclosure is 'staff use only'", f"{b.get('disclosure')}")
    code, pdf, _ = await api_raw(U_ADMIN1, "POST", f"{_R}/backtests/{b.get('id')}/pdf")
    txt = pdf_text(pdf) if code == 200 else ""
    check(code == 200 and pdf[:4] == b"%PDF" and "HYPOTHETICAL" in txt and "staff use only" in txt,
          "the backtest PDF is labelled HYPOTHETICAL and carries the disclosure", f"{code} {txt[:160]}")
    S["backtest"] = b.get("id")
    cm, om = await api(U_MEMBER, "GET", f"{_R}/member/backtests")
    cms, _ = await api(U_MEMBER, "GET", f"{_R}/backtests/{S['backtest']}")
    check(cm == 200 and om.get("rows") == [] and cms == 403,
          "no approved policy: the member gets NO backtest (empty member envelope; the staff route 403)",
          f"{cm} {str(om)[:160]} {cms}")
    pol = {"policy_text": "REP10 verify — the firm's hypothetical-performance policy.", "audience": "staff_and_members",
           "disclosure_text": "REP10 DISCLOSURE: hypothetical results have inherent limitations.", "reason": "verify"}
    cv, ov = await api(U_VIEWER, "POST", f"{_R}/hypothetical-policy", pol)
    c, o = await api(U_ADMIN1, "POST", f"{_R}/hypothetical-policy", pol)
    pid = (o.get("policy") or {}).get("id")
    cmk, _ = await api(U_ADMIN1, "POST", f"{_R}/hypothetical-policy/{pid}/approve", {})
    ca, _ = await api(U_ADMIN2, "POST", f"{_R}/hypothetical-policy/{pid}/approve", {})
    check(cv == 403 and "manage_hypothetical_policy" in str(ov) and c == 201 and cmk == 403 and ca == 200,
          "policy: viewer 403 (manage_hypothetical_policy); maker refused; a second person approves", f"{cv} {c} {cmk} {ca}")
    cm, om = await api(U_MEMBER, "GET", f"{_R}/member/backtests")
    rows = om.get("rows") or []
    check(cm == 200 and any(r["id"] == S["backtest"] for r in rows)
          and all(r["hypothetical"] is True and "REP10 DISCLOSURE" in r["disclosure"] for r in rows),
          "with a policy allowing members: the member sees the household's backtest, with the disclosure", f"{cm} {len(rows)}")
    c2, o2 = await api(U_ADMIN1, "GET", f"{_R}/backtests/{S['backtest']}")
    check(c2 == 200 and "REP10 DISCLOSURE" in str(o2.get("disclosure")),
          "the staff screen carries the approved disclosure", f"{c2}")


async def y_sweep(conn):
    section("[Y] Schedules: the rep10 sweep skips is_test orgs; run explicitly for the fixture org it works")
    pool = await get_pool()
    r = await SW.tick(pool, org_id=ORG_A)      # the live tick's own filter, scoped to this run's org — no real org
    check(r == {}, "the live tick skips an is_test org (named, but not included)", f"{list(r)[:3]}")
    r2 = await SW.tick(pool, org_id=ORG_A, include_test_orgs=True)
    check(str(ORG_A) in r2 and not r2[str(ORG_A)]["errors"], "named explicitly, the fixture org's sweep runs clean",
          f"{r2.get(str(ORG_A))}")


async def y_no_data(conn):
    """rep10 fix 1 (FAIL D): a price exists only where a source holds one for THAT org (or the platform). Proven
    through every reader rep5 / rep10 use: layer.get, get_bulk, history, rep10_common.longest_history and
    rebalance_proposals' unheld-price lookup — for (a) a security no source holds anywhere and (b) org B asking for
    K1, which org A holds (custodian valuation) AND has 70 days of synthetic csv_upload history for. Org A's own K1
    read is the positive control (the same calls DO find it), so the negatives are not vacuous."""
    section("[Y] No data → not found: get / get_bulk / history / longest_history / the proposal price lookup")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        np_ = await SG.create_security(conn, name=SEC_NAMES["NP"], security_type="equity", is_super_admin=True)
        await SG.add_identifier(conn, global_security_id=np_, id_type="cusip", id_value=f"{PREFIX}NP01",
                                is_super_admin=True)
    S["sec_NP"] = UUID(str(np_))
    t = today()
    start = t - timedelta(days=400)
    k1, np_s = sec("K1"), str(S["sec_NP"])

    async def reads(org, key):
        async def fn(c):
            g = await SD.get(c, org, "eod_price", key, as_of=t, purpose="internal")
            b = await SD.get_bulk(c, org, "eod_price", [key], as_of=t, purpose="internal")
            h = await SD.history(c, org, "eod_price", key, start=start, end=t, purpose="internal")
            lh = await RC.longest_history(c, org, key, start, t)
            px = await RP._unheld_prices(c, [key], t - timedelta(days=30), org_id=org, today=t)
            return {"get": bool(g.found), "get_bulk": bool(b.get(key) is not None and b[key].found),
                    "history": bool(h.found and h.value), "longest_history": lh is not None, "unheld_price": key in px,
                    "detail": {"get": (g.provider_key, str(g.value)), "price": str(px.get(key))}}
        return await in_org(fn, org)

    na = await reads(ORG_A, np_s)
    nb = await reads(ORG_B, k1)
    pa = await reads(ORG_A, k1)
    flags = ("get", "get_bulk", "history", "longest_history", "unheld_price")
    check(not any(na[f] for f in flags), "a security no source holds (org A): found = false through get, get_bulk, "
          "history and longest_history; the proposal lookup has no price (bought by notional)", f"{na}")
    check(not any(nb[f] for f in flags), "org B, which holds no data for K1, finds nothing — org A's custodian "
          "valuation and its synthetic history are invisible to it", f"{nb}")
    check(all(pa[f] for f in flags), "control: org A's own K1 IS found through the same five calls (the negatives "
          "above are not vacuous)", f"{pa}")
    find("FAIL D source", "verify_rep5's sec_B was priced by marketdata1's custodian provider from rep5's OWN fixture: "
         "ACCT_IRA holds B (10 shares, market value 1000, as of seed_val) → 1000 ÷ 10 = 100.00 as of yesterday. A "
         "real valuation in the same org — correct layer behaviour since 6423d80, not a rep10 leak; verify_rep5's "
         "fixture now values that IRA position as unknown so sec_B is genuinely unpriced")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false); gates both ways; envelopes")
    who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                              "current_user) AS b")
    check(who["u"] == "app_service" and who["b"] is False, "the verify connection is app_service with rolbypassrls = "
          "false", f"{who['u']} {who['b']}")
    cb, _ = await api(U_ADMIN_B, "POST", f"{_T}/donees", {"name": "ZZ B Charity", "kind": "public_charity",
                                                           "ein": "98-7654321", "receiving_broker": "ZZ B",
                                                           "dtc_participant": "0999", "account_number": "ZZ10B9999"})
    cp, _ = await api(U_ADMIN_B, "POST", f"{_R}/hypothetical-policy", {"policy_text": "B", "audience": "staff_only",
                                                                       "disclosure_text": "B", "reason": "B"})
    res = {}
    for t in ("public.charitable_donees", "public.hypothetical_performance_policies"):
        res[t] = [int(await scoped(conn, a, f"SELECT count(*) FROM {t} WHERE org_id = $1", b, fetch="fetchval"))
                  for a, b in ((ORG_A, ORG_A), (ORG_B, ORG_A), (ORG_B, ORG_B), (ORG_A, ORG_B))]
    one = {t: [int(await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")),
               int(await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval"))]
           for t in ("public.tilt_sets", "public.tilt_lines", "public.completion_settings", "public.gift_proposals",
                     "public.gift_lots", "public.backtests")}
    check(cb == 201 and cp == 201 and all(a > 0 and b == 0 and c > 0 and d == 0 for a, b, c, d in res.values())
          and all(a > 0 and b == 0 for a, b in one.values()),
          "RLS both directions: each org sees its own donees / policies (non-empty) and none of the other's; org B sees "
          "none of org A's tilts, completion, gifts, gift lots or backtests", f"{res} {one}")
    codes = [(await api(U_ADMIN_B, "GET", f"{_T}/{S['gift']}"))[0],
             (await api(U_ADMIN_B, "GET", f"{_R}/backtests/{S['backtest']}"))[0],
             (await api(U_ADMIN_B, "GET", f"{_R}/tilts/accounts/{A_M}"))[0]]
    check(codes == [404, 404, 404], "another org's gift, backtest and account tilts are a 404", f"{codes}")
    _, ev = await api(U_VIEWER, "GET", f"{_R}/tilts/accounts/{A_M}")
    _, ea = await api(U_ADMIN1, "GET", f"{_R}/tilts/accounts/{A_M}")
    pv = ev.get("permissions") or {}
    check(pv.get("can_write") is False and pv.get("can_approve") is False and ev.get("actions") == []
          and (ev.get("vocabularies") or {}).get("editable") == [] and ea.get("actions") == ["create"]
          and bool((ev.get("vocabularies") or {}).get("tilt_kinds")),
          "envelopes: view-only → no actions, empty editable; the admin may create; labels from the vocabularies",
          f"{ev.get('actions')} {ea.get('actions')}")
    _, gv = await api(U_VIEWER, "GET", _T)
    check(gv.get("actions") == [] and (gv.get("vocabularies") or {}).get("editable") == []
          and bool((gv.get("vocabularies") or {}).get("open_items")),
          "gifts envelope for a viewer: no actions, empty editable; the CPA / counsel open items carried", f"{gv.get('actions')}")
    missing = await platform(conn, """SELECT count(*) FROM roles r CROSS JOIN permissions p WHERE r.name = 'org_admin'
                                      AND p.name = 'manage_hypothetical_policy' AND NOT (r.org_id = ANY($1::uuid[]))
                                      AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id
                                                      AND rp.permission_id = p.id)""",
                             [str(o) for o in FIXTURE_ORGS], fetch="fetchval")
    check(int(missing) == 0, "every real org_admin role holds manage_hypothetical_policy", f"{missing}")
    perms = {r["name"] for r in await platform(conn, "SELECT p.name FROM user_roles ur JOIN role_permissions rp ON "
                                                     "rp.role_id = ur.role_id JOIN permissions p ON p.id = "
                                                     "rp.permission_id WHERE ur.user_id = $1", U_ADMIN_B)}
    check("manage_hypothetical_policy" in perms, "a new org's first org_admin holds it (rbac.ensure_org_admin_role)")


async def y_real_rows(conn):
    section("[Y] Real-data rules")
    rows = await platform(conn, "SELECT id, hypothetical FROM public.backtests WHERE NOT (org_id = ANY($1::uuid[]))",
                          [str(o) for o in FIXTURE_ORGS])
    VS.when_empty(rows, "every real backtest is hypothetical = true", lambda r: r["hypothetical"] is True,
                  check=check, find=find)


async def y_routes(conn):
    section("[Y] Own routes gated and in AUTHZ1_ROUTES.json; --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] node --test tests/ passes; npm run build exits 0 (run by this script, not during the sprint)")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    mine = re.findall(r"^\s*ok \d+ - (?:tilts|completion|consolidation|backtest|gifts|hypothetical policy|lib: "
                      r"deltaFromPoints)", out, re.M)
    check(len(mine) >= 9 and "not ok" not in out, "the rep10 render tests ran and passed (no approve / edit control "
          "without the flags)", f"{len(mine)}")
    if os.environ.get("SKIP_BUILD") == "1":
        print("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep5.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1", "SKIP_BUILD": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep5.py")], REPO, timeout=2700, env=env)
    total = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
    check(bool(total) and total[-1][1] == "0" and not fails and code == 0, "verify_rep5.py: 0 FAIL",
          f"{total[-1] if total else 'no summary'}; {fails[:3]} {err[-200:] if not total else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def cleanup_only(conn) -> int:
    import time as _time

    t0 = _time.monotonic()
    log: list[str] = []
    try:
        log = await teardown_retrying()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    seconds = _time.monotonic() - t0
    left = await leftovers(conn)
    print(f"CLEANUP_SECONDS: {seconds:.1f}")
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await close_conn(conn)
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = real = None
    try:
        await run("pre-teardown", teardown_retrying)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        y_local_day()
        await run("schema", y_schema, conn)
        await run("seed literals", y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", seed, conn)
        if S.get("seeded"):
            await run("tilts", y_tilts, conn)
            if S.get("tilt_set"):
                await run("completion", y_completion, conn)
            await run("consolidation", y_consolidation, conn)
            await run("gifts", y_gifts, conn)
            if S.get("days"):
                await run("backtest", y_backtest, conn)
            await run("sweep", y_sweep, conn)
            if S.get("days"):
                await run("no data → not found", y_no_data, conn)
            if S.get("gift") and S.get("backtest"):
                await run("isolation + gates", y_isolation, conn)
            else:
                check(False, "isolation + gates needs the gift and the backtest", "skipped — an earlier section failed")
        else:
            check(False, "seed completed", "every fixture-scoped section skipped")
        await run("real rows", y_real_rows, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [], "no network call was attempted during the whole run", f"{NETWORK_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        await close_app_pool()
        try:
            log = await teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (synthetic price history included)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if real is not None:
                after = await real_fingerprints(conn)
                for k in real:
                    check(after[k] == real[k], f"real {k} unchanged (count + checksum)", f"{real[k][0]} → {after[k][0]}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        await close_conn(conn)
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        try:
            regression()
        except Exception as exc:  # noqa: BLE001
            check(False, "regression raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
