"""verify_cpsim06 — CPSIM06: ACCOUNT OPENING at the simulated custodian, in the SHAPE of Altruist's Realtime API, simplified
by Joe's rule ("all the data provided and in good order → the account gets opened, the response is generated and the open
account appears in the feed — no mock client approval"). Hollisworks' "Open account at custodian" (CRM household)
pre-fills from the CRM, Checks (validate, nothing opened) and Opens: the counterparty validates against Altruist's
published rules and opens at once, or refuses with EVERY problem; Hollisworks records the account through its EXISTING
household_admin.add_account + the crosswalk (one transaction) and syncs it. Simulated counterparty ONLY.

Proves (docs/cpsim/CPSIM06_NOTES.md D1–D6): discovery + notes; the schema (catalog: six cpsim realtime tables, cpsim_app
only, the tax-id last-four CHECK, the append-only validation log); the token rules (password grant, 5-minute access,
FIXED 10-hour refresh window, the original refresh token still valid, a wrong password refused); every supported type
opened in good order (ACTIVE at once, in the Open API feed with date_open, the Hollisworks account through add_account —
masked + keyed hash — crosswalked in the same transaction, synced, on the household); every refusal rule alone and
several together (every problem listed; nothing created in Hollisworks, no book row); Check never opens; Open is
idempotent; sensitive data (no full tax id or date of birth in any cpsim table, request_log, response, audit row, page or
this script's output); the guard (simulated only; the demo-household rule; absent for sandbox / production with the
reason; permissions both ways on the identical request; cross-org both directions); the 429 + Retry-After honoured; the
conformance realtime_open group; UI render tests; boundary; no network; real + demo rows unchanged; own routes;
regressions.

WRITTEN, NOT RUN by the sprint. The operator runs (one shell; nothing else heavy alongside):

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cpsim06.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on EVERY exit path (a crash included, with its traceback)
and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets hydrate from
Doppler over HTTPS first — the only network the script itself uses; after that every httpx network transport refuses
and RECORDS the URL. The simulator, the app API, the Realtime client and the post-open sync all run IN-PROCESS against
the real ``main.app`` (which mounts the simulator): ``altruist_oauth.set_simulator_transport`` — threaded on EVERY call.
No model call (the helpers refuse and record).

TIME IS INJECTED: the counterparty clock (cpsim.registry.set_today) is D0 = a US business day before today; dates of
birth and visa expiries are computed from D0; the Realtime token clock (cpsim.oauth.set_now + the client's
altruist_realtime_sim.set_now) is moved explicitly in the token section and reset after it.

FAKE TAX IDS ONLY: every tax id this script sends is generated in-process — valid ones in the reserved ITIN range
(9XX-7X-XXXX), EINs with an assigned prefix, refusal values in the documented never-issued SSN ranges (area 000 / 666 /
9XX-with-a-non-ITIN-group). None is ever printed: every value (and every date of birth string) is redacted from every
line this script prints, and the sensitive-data section scans this run's own output buffer for them.

FIXTURES (ids 99000000-0000-0000-0000-0000c966…; sign-in tag cpsim06verify; names 'CPSIM06 VERIFY …'): TEST orgs A and B
inside the reserved prefix (is_test asserted); a NON-test org OUTSIDE the prefix exists only inside a ROLLED-BACK
transaction (the demo-household rule). Users with real user_roles grants, each decided by the app (asserted): U_SA Super
Admin, U_OP opener (org_admin + manage_members — the permission add_account takes), U_RD reader (support_staff, internal,
WITHOUT manage_members), U_OPB org B's opener. Counterparties carry the reserved slug prefix ``cpsim6verify-``.

TEARDOWN (start and end): ``_verify_cleanup.cleanup`` on the operator DSN keyed by this run's fixture ids with every
teardown GUC a guard reads (``cpsim.allow_teardown`` — the append-only validation log / beneficiaries; ``sod.teardown``
always). Every touched table re-read for its exact pre-test count BY FIXTURE IDS ONLY (audit_log by fixture ids). Real
rows fingerprinted before and after.

--self-checks-only: git / route / static self-checks only. --cleanup-only: per-table fixture counts and real-row
fingerprints before / after the teardown. --only=<token|open_a|open_b|refusals|check_idem|sensitive|guard|ratelimit|
unchanged|conformance|safety>: the seed, the named sections, the full teardown. VERIFY_REGRESSIONS (fast by default):
nested regressions run --self-checks-only.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import inspect
import io
import json
import os
import pathlib
import random
import re
import secrets
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_self as VS  # noqa: E402

SPRINT_KEY = "cpsim06"
BEFORE_REF = "df11f40"   # cpsim06-work's base: 'merge cpsim05'
_P = "/api/v1/admin/counterparties"
_H = "/api/v1/admin/households"
ROUTES = {("GET", _H + "/{household_id}/custodian-account"): "manage_members",
          ("POST", _H + "/{household_id}/custodian-account/check"): "manage_members",
          ("POST", _H + "/{household_id}/custodian-account/open"): "manage_members",
          ("GET", _P + "/{counterparty_id}/realtime"): "super_admin",
          ("POST", _P + "/{counterparty_id}/realtime/users"): "super_admin",
          ("POST", _P + "/{counterparty_id}/realtime/users/{user_id}/revoke"): "super_admin"}
_RT = "/cpsim/c/{slug}/realtime"
SIM_ROUTES = {("POST", _RT + "/realms/altruist/protocol/openid-connect/token"), ("POST", _RT + "/api/households"),
              ("POST", _RT + "/api/contacts"), ("POST", _RT + "/api/account-drafts"),
              ("GET", _RT + "/api/account-drafts/{draft_id}"), ("POST", _RT + "/api/account-drafts/{draft_id}/validate"),
              ("POST", _RT + "/api/account-drafts/{draft_id}/submit")}
NODE_TEST = "tests/cpsim06/cpsim06.test.mjs"
NOTES = REPO / "docs" / "cpsim" / "CPSIM06_NOTES.md"
MIGRATION = REPO / "docs" / "cpsim06_part1.sql"
SNAPSHOT = REPO / "docs" / "schema_snapshot.sql"
BOUNDARY = HERE / "cpsim_boundary_check.py"
CONFORMANCE = HERE / "cpsim_altruist_conformance.py"
REGRESSION_SET = REPO / "scripts" / "regression_set.txt"
REGRESSIONS = ("verify_cpsim02.py", "verify_altread1.py", "verify_cpsim05.py", "verify_portentry2.py",
               "verify_crmdetail1.py", "verify_authz1.py")
HTTP_OK, HTTP_CREATED, HTTP_FORBIDDEN, HTTP_NOT_FOUND, HTTP_CONFLICT, HTTP_UNPROCESSABLE = 200, 201, 403, 404, 409, 422
SECTION_TIMEOUT = 600          # every section must finish in < 10 minutes
ACCESS_S, SESSION_S = 300, 36000   # published: access 5 min; refresh 10 h fixed
YES_TYPES = 14
NO_TYPES = 24
RETRY_AFTER_S = 1
CONFORMANCE_R_ROWS = 35


def _src(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _code_only(src: str) -> str:
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


def node_test_count() -> int:
    """Derived from the test file itself (never a title prefix)."""
    return len(re.findall(r"^test\(", _src(f"apps/web/{NODE_TEST}"), re.M))


def self_routes(check, find):
    """cpsim06's own routes: BEFORE_REF → its own end point (HEAD while unmerged); each exists today with that gate."""
    o = VS.own_routes(BEFORE_REF, SPRINT_KEY)

    def flat(keys):
        return {(m, p) for (ms, p) in keys for m in ms}

    changed = flat(o.changed)
    check(bool(len(o.before) > 0 and not changed and not o.removed),
          "cpsim06 changed no existing route's gate and removed none", f"{o.where()}; changed={sorted(changed)[:3]} "
          f"removed={o.removed[:3]}")
    mine = flat(o.new)
    want = set(ROUTES)
    check(mine == want, f"this sprint's own routes are exactly its {len(ROUTES)} routes (the household custodian-account "
          "context / check / open; the admin Realtime tab, user create, user revoke)",
          f"{o.where()}; extra={sorted(mine - want)[:3]} missing={sorted(want - mine)[:3]}")
    by = {(m, p): rec for (ms, p), rec in o.end.items() for m in ms}
    bad = [(k, (by.get(k) or {}).get("after")) for k, g in ROUTES.items()
           if k not in by or g not in str(by[k].get("after"))]
    check(not bad, "each own route names exactly its gate (manage_members — add_account's own / super_admin)",
          f"{bad[:4]}")
    keys = [k for k in o.new if any((m, k[1]) in ROUTES for m in k[0])]
    drift = VS.still_gated(o, keys)
    check(not drift, "cpsim06's routes all still exist today with the same gate", f"{drift[:4]}")
    data = json.loads(_src("docs/AUTHZ1_ROUTES.json") or "{}")
    sims = {(m, r.get("path")) for r in data.get("simulator_routes", []) for m in (r.get("methods") or [])}
    check(SIM_ROUTES <= sims, f"the {len(SIM_ROUTES)} simulator Realtime routes are listed in the simulator section of "
          "docs/AUTHZ1_ROUTES (own auth, not app routes)", f"missing={sorted(SIM_ROUTES - sims)}")


def self_static(check, find):
    n = _src("docs/cpsim/CPSIM06_NOTES.md")
    for d in ("D1", "D2", "D3", "D4", "D5", "D6"):
        check(f"| {d} |" in n, f"CPSIM06_NOTES records {d} as built")
    for w in ("ASSUMED", "Validation rules", "| V1 |", "| V16 |", "Reuse inventory", "realms/altruist/protocol",
              "account-drafts", "entity_tax_ids", "[FIND]"):
        check(w.lower() in n.lower(), f"CPSIM06_NOTES names {w!r}")
    rs = _src("scripts/regression_set.txt")
    check(bool(re.search(r"^verify_cpsim06\.py\s*$", rs, re.M)), "verify_cpsim06.py is in scripts/regression_set.txt")
    st = _src("docs/PROJECT_STATUS.md")
    at = st.find("## CPSIM06")
    check(at >= 0 and "STATUS: BUILT" in st[at:at + 800], "PROJECT_STATUS records CPSIM06 as BUILT")
    check("## CPSIM05" in st and "## ALTREAD1" in st, "PROJECT_STATUS keeps the CPSIM05 and ALTREAD1 headings intact")
    adm = _src("docs/cpsim/CPSIM_ADMIN.md")
    check(all(w in adm for w in ("Realtime tab", "simulator user", "validation rules")),
          "CPSIM_ADMIN.md: the Realtime tab, simulator users, the validation rules")
    cl = _code_only(_src("apps/api/services/altruist_realtime_sim.py"))
    check("altruist.com" not in cl and "altruistnet.tech" not in cl and "if environment != SIMULATOR" in cl,
          "no real Realtime client: the client names no real Altruist host and refuses any non-simulator environment")
    ha = _code_only(_src("apps/api/services/household_admin.py"))
    check("on_created=None" in ha and "await on_created(conn, row)" in ha,
          "add_account's only change is the optional in-transaction on_created hook (default None)")


def self_checks(check, find):
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))


from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _cpsim_fixtures as FX  # noqa: E402
import _verify_cleanup as VC  # noqa: E402
import _verify_roles as VR  # noqa: E402
import _verify_scope as VSC  # noqa: E402

import main  # noqa: E402
from cpsim import db as SDB  # noqa: E402
from cpsim import mirror as SM  # noqa: E402
from cpsim import oauth as SO  # noqa: E402
from cpsim import realtime as SRT  # noqa: E402
from cpsim import registry as R  # noqa: E402
from cpsim import webhooks as SW  # noqa: E402
from services import altruist_oauth as AO  # noqa: E402
from services import altruist_realtime_sim as RS  # noqa: E402
from services import custodian_account_opening as CAO  # noqa: E402
from services import extraction as EXT  # noqa: E402
from services import household_admin as HA  # noqa: E402
from services import rbac  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.crm_config import account_vocab  # noqa: E402
from services.us_business_days import prior_business_day  # noqa: E402
import services.database as DBM  # noqa: E402

TAG = "cpsim06verify"
NAME = "CPSIM06 VERIFY "
SLUG_PREFIX = "cpsim6verify-"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FIXTURE_ID_PREFIX = "99000000-0000-0000-0000-0000c966"
# the non-test org: ONLY inside a rolled-back transaction. NOT 99000000-…: rep6fix2's organizations_mark_test_fixture
# trigger forces is_test = true on every such id (cpsim03's ORG_R uses this family for the same reason)
OUTSIDE_ORG = UUID("c5130000-0000-4000-8000-0000c9660001")
D0 = prior_business_day(date.today() - timedelta(days=1))
SIM_BASE = "https://cpsim.verify6.invalid"
REDIRECT = "https://verify.cpsim06.invalid/altruist/callback"
SIM_XWALK = "ALTRUIST_SIMULATOR"
RNG = random.SystemRandom()


def _id(n: int) -> UUID:
    return UUID(f"{FIXTURE_ID_PREFIX}{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SA, U_OP, U_RD, U_OPB = _id(10), _id(11), _id(12), _id(13)
ALL_USERS = [U_SA, U_OP, U_RD, U_OPB]
H_A, H_B = _id(30), _id(31)
P_ADULT, P_JOINT, P_MINOR, P_TRUSTEE, T_TRUST, P_TRUSTED, E_CO, P_B = (_id(40), _id(41), _id(42), _id(43), _id(44),
                                                                      _id(45), _id(46), _id(47))
MEMBERS_A = [P_ADULT, P_JOINT, P_MINOR, P_TRUSTEE, T_TRUST, P_TRUSTED]
ENTITIES = MEMBERS_A + [E_CO, P_B]
HOUSEHOLDS = [H_A, H_B]
ROLE_PERMS = {"org_admin": ("manage_members",), "support_staff": ()}
USERS = {U_SA: (ORG_A, "super_admin", "sa", "super_admin"), U_OP: (ORG_A, "member", "op", "org_admin"),
         U_RD: (ORG_A, "member", "rd", "support_staff"), U_OPB: (ORG_B, "member", "opb", "org_admin")}
ROLE_EXPECT = {U_SA: {"org": ORG_A, "roles": ["super_admin"]},
               U_OP: {"org": ORG_A, "roles": ["org_admin"], "has": ["manage_members"]},
               U_RD: {"org": ORG_A, "roles": ["support_staff"], "lacks": ["manage_members"]},
               U_OPB: {"org": ORG_B, "roles": ["org_admin"], "has": ["manage_members"]}}
#: the shapes and which fixture members hold which role (the request owners)
OWNERS = {"single": [(P_ADULT, "owner")], "joint": [(P_ADULT, "owner"), (P_JOINT, "joint_owner")],
          "trust": [(T_TRUST, "trust"), (P_TRUSTEE, "trustee")], "custodial": [(P_MINOR, "minor"), (P_ADULT, "custodian")]}
PRIMARY = {"single": P_ADULT, "joint": P_ADULT, "trust": T_TRUST, "custodial": P_MINOR}
OPEN_A = ("individual", "jtwros", "trust", "ira", "roth_ira", "rollover_ira", "beneficiary_ira")
OPEN_B = ("simple_ira", "solo_401k", "utma", "ugma", "minor_roth_ira", "individual_cash", "trust_cash")
NO_SAMPLES = ("single_member_llc", "donor_advised_fund", "partnership", "sep_ira", "joint_tenants_in_common")
SEED_CHECK_LITERALS = (
    ("cpsim.realtime_tokens", "realtime_tokens_kind_chk", ("access", "refresh")),
    ("cpsim.realtime_contacts", "realtime_contacts_kind_chk", ("person", "entity")),
    ("cpsim.realtime_contacts", "realtime_contacts_tax_kind_chk", ("ssn", "itin", "ein")),
    ("cpsim.realtime_drafts", "realtime_drafts_status_chk", ("draft", "opened")),
    ("cpsim.realtime_validation_log", "realtime_validation_log_action_chk", ("validate", "submit")),
    ("cpsim.realtime_validation_log", "realtime_validation_log_outcome_chk", ("ok", "refused", "opened",
                                                                               "already_open")),
)
UNIQUES = ("realtime_users_live_uq", "realtime_tokens_hash_uq", "realtime_contacts_live_uq", "realtime_contacts_ref_uq",
           "realtime_drafts_live_uq", "realtime_drafts_ref_uq", "realtime_drafts_account_uq",
           "realtime_beneficiaries_row_uq", "book_accounts_live_uq", "book_households_live_uq",
           "external_references_org_source_ext_type_key", "accounts_active_identity_uq",
           "altruist_connections_active_unique", "org_settings_org_id_setting_key_key", "entity_rel_active_unique")
SEED_COLUMNS = {
    "organizations": ("id", "name", "slug", "is_test"),
    "users": ("id", "org_id", "email", "full_name", "auth0_sub", "role", "status", "is_active", "is_test"),
    "entities": ("id", "org_id", "display_name", "status", "entity_type", "first_name", "surname", "legal_name",
                 "tax_id", "date_of_birth", "primary_email", "primary_phone", "citizenship_status", "tags"),
    "entity_addresses": ("org_id", "entity_id", "address_type", "street1", "city", "state", "postal_code", "country",
                         "is_primary"),
    "entity_tax_ids": ("org_id", "entity_id", "tax_id_type", "tax_id_country", "tax_id_encrypted", "tax_id_last4",
                       "is_primary"),
    "entity_relationships": ("org_id", "from_entity_id", "to_entity_id", "relationship_type"),
    "entity_employment": ("org_id", "employee_id", "employer_id", "title", "is_current"),
    "households": ("id", "org_id", "name"),
    "household_memberships": ("household_id", "entity_id"),
    "org_settings": ("org_id", "setting_key", "setting_value"),
}
COUNTED_PUBLIC = ("public.organizations", "public.users", "public.user_roles", "public.entities",
                  "public.entity_addresses", "public.entity_tax_ids", "public.entity_relationships",
                  "public.entity_employment", "public.households", "public.household_memberships", "public.accounts",
                  "public.org_settings", "public.audit_log", "public.altruist_connections",
                  "public.altruist_oauth_states", "portfolio.external_references", "portfolio.positions",
                  "portfolio.transactions", "portfolio.custodian_tax_lots", "public.account_custodian_balances")
CPSIM_TABLES = ("cpsim.counterparties", "cpsim.counterparty_credentials", "cpsim.counterparty_behaviour",
                "cpsim.counterparty_clock", "cpsim.request_log", "cpsim.book_households", "cpsim.book_accounts",
                "cpsim.oauth_codes", "cpsim.oauth_tokens", "cpsim.realtime_users", "cpsim.realtime_tokens",
                "cpsim.realtime_contacts", "cpsim.realtime_drafts", "cpsim.realtime_beneficiaries",
                "cpsim.realtime_validation_log")
NEW_TABLES = ("realtime_users", "realtime_tokens", "realtime_contacts", "realtime_drafts", "realtime_beneficiaries",
              "realtime_validation_log")
NEW_CPSIM_PRIVS = {t: set(FX.cpsim_app_privileges(t)) for t in NEW_TABLES}
assert NEW_CPSIM_PRIVS["realtime_validation_log"] == {"SELECT", "INSERT"}
assert NEW_CPSIM_PRIVS["realtime_contacts"] == {"SELECT", "INSERT", "UPDATE"}
APPEND_ONLY = ("realtime_beneficiaries", "realtime_validation_log")

S: dict = {"cp": {}, "keys": {}, "client_ids": {}, "issued": [], "bodies": [], "tax": [], "dobs": [], "opened": {}}
NETWORK_CALLS: list[str] = []
MODEL_CALLS: list[str] = []
OUTPUT = io.StringIO()       # every line this script prints (the sensitive-data section scans it)
_n_pass = 0
_n_fail = 0


def _emit(line: str) -> None:
    line = _redact(line)
    OUTPUT.write(line + "\n")
    print(line, flush=True)


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    _emit(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    _emit(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def info(line: str) -> None:
    _emit(f"[INFO] {line}")


def section(title: str) -> None:
    _emit(f"\n── {title} ──")


def total_line() -> str:
    return f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL"


def _redact(text) -> str:
    """Never print a secret, a tax id or a date of birth: every issued value, every fake tax id (and its digits run),
    every date-of-birth string this run sent, every cpsim_ token; then any remaining 9-digit run."""
    t = str(text)
    for v in S["issued"]:
        if v:
            t = t.replace(v, "[secret]")
            body = v.split("_")[-1]
            if len(body) >= 16:
                t = t.replace(body, "[secret]")
    for v in S["tax"]:
        d = re.sub(r"\D", "", v)
        for form in {v, d, f"{d[:3]}-{d[3:5]}-{d[5:]}", f"{d[:2]}-{d[2:]}"}:
            t = t.replace(form, "[tax id]")
    for v in S["dobs"]:
        t = t.replace(v, "[dob]")
    t = re.sub(r"cpsim_(?:live|at|rt|code|whsec|rtat|rtrt|rtpw|fixpw)_[0-9A-Za-z_-]{8,}", "[secret]", t)
    return re.sub(r"(?<!\d)\d{9}(?!\d)", "[9 digits]", t)


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or "") if isinstance(exc.stdout, str) else "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return None, "", f"{exc}"


def sub_of(uid) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def dumps(o) -> str:
    try:
        return json.dumps(o, default=str)
    except Exception:  # noqa: BLE001
        return str(o)


def secret_leaks(text: str) -> list[str]:
    return [v[:10] + "…" for v in S["issued"] if v and (v in text or v.split("_")[-1] in text)]


RT_TOKEN_PATH = f"/cpsim/c/{SLUG_PREFIX}main/realtime/realms/altruist/protocol/openid-connect/token"
RT_GRANTS = ("password", "refresh_token")
_RT_SECRET = re.compile(r"cpsim_rt(?:at|rt|pw)_[0-9A-Za-z_\-]{16,}")


def rt_secret_shapes(text: str) -> list[str]:
    """Any Realtime token / password SHAPE (issued to this script or not) — 10-character prefixes only."""
    return [m.group(0)[:10] + "…" for m in _RT_SECRET.finditer(text)]


def sensitive_hits(text: str) -> list[str]:
    """Which of this run's fake tax ids (any form) or date-of-birth strings appear in ``text`` (labels only)."""
    out = []
    for i, v in enumerate(S["tax"]):
        d = re.sub(r"\D", "", v)
        if any(f in text for f in {v, d, f"{d[:3]}-{d[3:5]}-{d[5:]}", f"{d[:2]}-{d[2:]}"}):
            out.append(f"tax#{i}")
    out += [f"dob#{i}" for i, v in enumerate(S["dobs"]) if v in text]
    return out


# ── fake values (never a plausible real SSN) ────────────────────────────────────────────────────────────────────────
def fake_itin() -> str:
    v = f"9{RNG.randrange(10)}{RNG.randrange(10)}-{RNG.randrange(70, 89)}-{RNG.randrange(10000):04d}"
    S["tax"].append(v)
    return v


def fake_ein() -> str:
    v = f"81-{RNG.randrange(10 ** 7):07d}"
    S["tax"].append(v)
    return v


def bad_ssn(kind: str) -> str:
    """A value in a documented NEVER-issued range: area 000, area 666, or 9XX with a non-ITIN group (40)."""
    tail = f"{RNG.randrange(10, 99)}-{RNG.randrange(1000, 9999)}"
    v = {"000": f"000-{tail}", "666": f"666-{tail}", "9xx": f"9{RNG.randrange(10)}{RNG.randrange(10)}-40-"
                                                            f"{RNG.randrange(1000, 9999)}"}[kind]
    S["tax"].append(v)
    return v


def years_ago(n: int) -> date:
    d = D0.replace(year=D0.year - n) if not (D0.month == 2 and D0.day == 29) else date(D0.year - n, 2, 28)
    S["dobs"].append(d.isoformat())
    return d


# ═══ DB helpers (RLS context set ON the connection inside its transaction) ═════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', $1, true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def sim(conn, sql, *args, fetch="fetch"):
    """One transaction AS cpsim_app (SET LOCAL ROLE — the simulator's own path)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true), "
                           "set_config('app.current_org_id', '', true)")
        await conn.execute("SET LOCAL ROLE cpsim_app")
        return await getattr(conn, fetch)(sql, *args)


class Rollback(Exception):
    pass


async def rolled_back(conn, fn, *, super_admin: bool = True, org=None):
    """Run ``fn(conn)`` in a transaction that is ALWAYS rolled back; returns (result, error)."""
    out = {"res": None, "err": None}
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', $1, true), "
                               "set_config('app.current_org_id', $2, true)", "true" if super_admin else "false",
                               str(org) if org else "")
            try:
                async with conn.transaction():
                    out["res"] = await fn(conn)
            except Exception as exc:  # noqa: BLE001
                out["err"] = exc
            raise Rollback()
    except Rollback:
        pass
    return out["res"], out["err"]


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


async def close_app_pools() -> None:
    p, DBM._pool = DBM._pool, None
    await close_conn(p)
    try:
        await SDB.close_pool()
    except Exception:  # noqa: BLE001
        pass


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


REL = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 "
       "AND c.relname = $2")


def _arr(vals) -> str:
    return "ARRAY[" + ", ".join("'" + str(UUID(str(v))) + "'" for v in vals) + "]::uuid[]"


FIX_CP = f"(SELECT id FROM cpsim.counterparties WHERE slug LIKE '{SLUG_PREFIX}%')"


def fixture_scope(table: str) -> str:
    orgs, users = _arr(FIXTURE_ORGS), _arr(ALL_USERS)
    if table == "public.audit_log":
        # BY FIXTURE IDS ONLY (rule 6): a fixture org's row, or one written by / about a fixture user / household
        return (VSC.predicate("public.audit_log", FIXTURE_ORGS, ALL_USERS) + f" OR resource_id = ANY({_arr(HOUSEHOLDS)})"
                if VSC.is_live_written("public.audit_log")
                else f"org_id = ANY({orgs}) OR user_id = ANY({users}) OR resource_id = ANY({_arr(HOUSEHOLDS)})")
    return {
        "public.organizations": f"id = ANY({orgs})",
        "public.users": f"org_id = ANY({orgs}) OR id = ANY({users})",
        "public.user_roles": f"user_id = ANY({users})",
        "public.household_memberships": f"household_id = ANY({_arr(HOUSEHOLDS)})",
        "cpsim.counterparties": f"slug LIKE '{SLUG_PREFIX}%'",
        "cpsim.request_log": f"slug LIKE '{SLUG_PREFIX}%' OR counterparty_id IN {FIX_CP}",
    }.get(table, f"counterparty_id IN {FIX_CP}" if table.startswith("cpsim.") else f"org_id = ANY({orgs})")


async def counts(conn) -> dict:
    out = {}
    for t in COUNTED_PUBLIC:
        out[t] = int(await platform(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    for t in CPSIM_TABLES:
        out[t] = int(await sim(conn, f"SELECT count(*) FROM {t} WHERE {fixture_scope(t)}", fetch="fetchval"))
    return out


async def fingerprint(conn, sql, *args, as_sim=False) -> tuple[int, str]:
    q = f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) AS s FROM ({sql}) t"
    row = await (sim(conn, q, *args, fetch="fetchrow") if as_sim else platform(conn, q, *args, fetch="fetchrow"))
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    orgs = [str(o) for o in FIXTURE_ORGS]
    out = {
        "counterparty_sim.* settings of every real org": await fingerprint(
            conn, "SELECT org_id, setting_key, setting_value, updated_at FROM org_settings WHERE setting_key LIKE "
                  "'counterparty\\_sim.%' AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "demo-tagged entities": await fingerprint(
            conn, "SELECT id, display_name, tags, status, system_to FROM entities WHERE EXISTS (SELECT 1 FROM "
                  "unnest(tags) x WHERE x LIKE 'demo-%') AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "real accounts": await fingerprint(
            conn, "SELECT id, account_number_masked, custodian_code, household_id, system_to FROM accounts WHERE NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
        "real simulator / Altruist crosswalks": await fingerprint(
            conn, "SELECT id, org_id, source_system, external_id, record_id FROM portfolio.external_references WHERE "
                  "source_system LIKE 'ALTRUIST%' AND NOT (org_id = ANY($1::uuid[]))", orgs),
        "real entity tax ids": await fingerprint(
            conn, "SELECT id, entity_id, tax_id_last4, system_to FROM entity_tax_ids WHERE NOT (org_id = "
                  "ANY($1::uuid[]))", orgs),
        "real Altruist connections": await fingerprint(
            conn, "SELECT id, org_id, environment, status, system_to FROM altruist_connections WHERE NOT "
                  "(org_id = ANY($1::uuid[]))", orgs),
    }
    for label, sql in (("counterparties (non-fixture)", f"SELECT id, slug, status, system_to FROM cpsim.counterparties "
                                                        f"WHERE slug NOT LIKE '{SLUG_PREFIX}%'"),
                       ("book accounts (non-fixture)", "SELECT id, external_id, valid_to FROM cpsim.book_accounts "
                                                       f"WHERE counterparty_id NOT IN {FIX_CP}"),
                       ("realtime contacts (non-fixture)", "SELECT id, valid_to FROM cpsim.realtime_contacts WHERE "
                                                           f"counterparty_id NOT IN {FIX_CP}"),
                       ("realtime drafts (non-fixture)", "SELECT id, status, valid_to FROM cpsim.realtime_drafts WHERE "
                                                         f"counterparty_id NOT IN {FIX_CP}")):
        out[label] = await fingerprint(conn, sql, as_sim=True)
    return out


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        users, orgs = list(ALL_USERS), list(FIXTURE_ORGS)
        plan = {
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [list(HOUSEHOLDS)]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [orgs]),
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[]) OR resource_id = "
                                 "ANY($3::uuid[])", [orgs, users, list(HOUSEHOLDS)]),
            # the simulator has no org_id: by the reserved fixture slug prefix (every cpsim06 table reached by the FK
            # closure from cpsim.counterparties; the append-only guards honour cpsim.allow_teardown for the owner)
            "cpsim.counterparties": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
            "cpsim.request_log": ("slug LIKE $1::text", [SLUG_PREFIX + "%"]),
        }
        res = await VC.cleanup(op, orgs=orgs, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("cpsim.allow_teardown",), label="verify_cpsim06")
        return res["log"]
    finally:
        await close_conn(op)


async def teardown_retrying() -> list[str]:
    try:
        return await teardown()
    except (asyncpg.DeadlockDetectedError, asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError,
            ConnectionError) as exc:
        info(f"teardown hit {type(exc).__name__}; retrying once")
        return await teardown()


async def leftovers(conn) -> int:
    c = await counts(conn)
    n = sum(c.values())
    op = await operator_conn()
    try:
        n += await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=ALL_USERS, user_sub_like=f"%{TAG}%")
        n += int(await op.fetchval("SELECT count(*) FROM organizations WHERE id = $1", OUTSIDE_ORG))
    finally:
        await close_conn(op)
    return n


class org_ctx:
    """A direct service call in ONE org's RLS context — the same contextvars the API's middleware sets."""

    def __init__(self, org, super_admin: bool = False):
        self.org, self.su = str(org), super_admin

    async def __aenter__(self):
        self.tokens = DBM.set_rls_context(self.org, self.su)
        return await DBM.get_pool()

    async def __aexit__(self, *a):
        DBM.reset_rls_context(self.tokens)
        return False


# ═══ App access ═════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
APP_T = None
_ORIG_GRANT = None          # services.altruist_realtime_sim._grant, wrapped by install_stubs to record client grants


def token_for(uid) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def raw(method, path, *, headers=None, body=None, timeout=300.0, form=None):
    """(status, parsed body, headers) through the REAL app (which mounts the simulator)."""
    kw = {"headers": {"User-Agent": "verify_cpsim06", **(headers or {})}}
    if body is not None:
        kw["json"] = body
    if form is not None:
        kw["content"] = urlencode(form)
        kw["headers"]["Content-Type"] = "application/x-www-form-urlencoded"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=True),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {}, {}
        except Exception as exc:  # noqa: BLE001
            info(f"{method} {path} raised in the app (a 500): {type(exc).__name__}")
            traceback.print_exc(file=sys.stdout)
            return 500, {"raw": f"{type(exc).__name__}"}, {}
    try:
        data_out = res.json() if res.content else {}
    except ValueError:
        data_out = {"raw": res.text[:4000]}
    # the create calls show a new key / password ONCE, by design — every other body is scanned
    if not (method == "POST" and (path == _P or path.endswith("/realtime/users"))):
        # every body carries its source: a Hollisworks /api/v1 response or a simulator /cpsim answer (+ the grant)
        S["bodies"].append({"src": "api" if path.startswith("/api/v1") else "sim" if path.startswith("/cpsim/")
                            else "other", "method": method, "path": path.split("?")[0],
                            "grant": (form or {}).get("grant_type"), "status": res.status_code,
                            "text": res.text[:40000]})
    return res.status_code, data_out, dict(res.headers)


async def api(uid, method, path, body=None):
    code, out, _h = await raw(method, path, headers={"Authorization": f"Bearer {token_for(uid)}"}, body=body)
    return code, out


def cpu(key: str, tail: str = "") -> str:
    return f"{_P}/{S['cp'][key]}{tail}"


def install_stubs():
    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")

    def stub(tok):
        return dict(CLAIMS_BY_TOKEN[tok])

    main.verify_token = stub
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def refuse_network(self, request):
        NETWORK_CALLS.append(f"{request.method} {request.url}")
        raise httpx.ConnectError(f"verify_cpsim06: network disabled ({request.url.host})", request=request)

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network

    def refuse_model(name):
        async def _refuse(*a, **k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"verify_cpsim06: a model call was attempted ({name})")
        return _refuse

    for n in ("call_claude_text", "call_claude_json", "call_claude_with_tools"):
        setattr(EXT, n, refuse_model(n))
    for fn, nm in ((R.set_today, "registry.set_today"), (SM.set_now, "mirror.set_now"), (SO.set_now, "oauth.set_now"),
                   (RS.set_now, "altruist_realtime_sim.set_now"), (RS.set_sleep, "altruist_realtime_sim.set_sleep"),
                   (AO.set_simulator_transport, "altruist_oauth.set_simulator_transport"),
                   (SW.set_transport, "webhooks.set_transport")):
        if len(list(inspect.signature(fn).parameters)) != 1:
            raise RuntimeError(f"{nm} signature drifted")
    S["today"] = D0
    R.set_today(lambda: S["today"])
    S["sleeps"] = []
    RS.set_sleep(lambda sec: S["sleeps"].append(float(sec)))
    RS.clear_cache()
    global APP_T, _ORIG_GRANT
    if _ORIG_GRANT is None:
        _ORIG_GRANT = RS._grant
    S["client_grants"] = []

    async def _recorded_grant(sess, form, transport):
        S["client_grants"].append(form.get("grant_type"))
        d = await _ORIG_GRANT(sess, form, transport)
        S["issued"] += [v for v in (d.get("access"), d.get("refresh")) if v]     # scanned for everywhere, redacted
        return d
    RS._grant = _recorded_grant
    APP_T = FX.TimeoutASGITransport(app=main.app)
    AO.set_simulator_transport(APP_T)          # simulator environment ONLY (sandbox / production never read it)
    SW.set_transport(httpx.ASGITransport(app=main.app))
    SW.set_sleep(lambda s: asyncio.sleep(0))
    os.environ[AO.CPSIM_BASE_URL_ENV] = SIM_BASE
    os.environ["ALTRUIST_REDIRECT_URI"] = REDIRECT
    os.environ.setdefault("ALTRUIST_TOKEN_ENCRYPTION_KEY", "cpsim06verify-" + secrets.token_hex(16))


def set_clock(dt) -> None:
    """The Realtime token clock — the simulator's and the client's together (None = real time)."""
    SO.set_now((lambda: dt) if dt is not None else None)
    RS.set_now((lambda: dt) if dt is not None else None)


def restore_stubs():
    if _ORIG_GRANT is not None:
        RS._grant = _ORIG_GRANT
    R.set_today(None)
    SM.set_now(None)
    set_clock(None)
    RS.set_sleep(None)
    RS.clear_cache()
    SRT.force_429(0)
    AO.set_simulator_transport(None)
    SW.set_transport(None)
    SW.set_sleep(None)


def set_sim_creds(key="main") -> None:
    os.environ["ALTRUIST_SIMULATOR_CLIENT_ID"] = S["client_ids"][key]
    os.environ["ALTRUIST_SIMULATOR_CLIENT_SECRET"] = S["keys"][key]
    os.environ[RS.USERNAME_ENV] = S["rt_user"][key]["username"]
    os.environ[RS.PASSWORD_ENV] = S["rt_user"][key]["password"]


async def _constraints(conn, nsp: str, rel: str) -> dict:
    rows = await platform(conn, "SELECT k.conname, k.contype, pg_get_constraintdef(k.oid) AS d FROM pg_constraint k "
                                f"WHERE k.conrelid = ({REL})", nsp, rel)
    return {r["conname"]: r["d"] for r in rows}


def _snapshot_columns() -> dict[str, set]:
    out: dict[str, set] = {}
    cur = None
    for ln in _src("docs/schema_snapshot.sql").splitlines():
        m = re.match(r"^-- ===== ([\w.]+) =====", ln)
        if m:
            cur = m.group(1)
            out[cur] = set()
            continue
        m = re.match(r"^--   (\w+)\s+\S", ln)
        if cur and m and m.group(1) not in ("PRIMARY", "UNIQUE", "FOREIGN"):
            out[cur].add(m.group(1))
    return out


async def guard_prefix(conn) -> bool:
    o = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id::text LIKE $1 OR id = $2::uuid",
                           f"{FIXTURE_ID_PREFIX}%", OUTSIDE_ORG, fetch="fetchval"))
    u = int(await platform(conn, "SELECT count(*) FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", f"%{TAG}%", fetch="fetchval"))
    e = int(await platform(conn, "SELECT count(*) FROM entities WHERE id::text LIKE $1 OR display_name LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", NAME + "%", fetch="fetchval"))
    h = int(await platform(conn, "SELECT count(*) FROM households WHERE id::text LIKE $1 OR name LIKE $2",
                           f"{FIXTURE_ID_PREFIX}%", NAME + "%", fetch="fetchval"))
    c = int(await sim(conn, "SELECT count(*) FROM cpsim.counterparties WHERE slug LIKE $1", SLUG_PREFIX + "%",
                      fetch="fetchval"))
    outside = not str(OUTSIDE_ORG).startswith(FIXTURE_ID_PREFIX)
    return check(o == 0 and u == 0 and e == 0 and h == 0 and c == 0 and outside,
                 "the reserved prefixes (fixture ids, the out-of-prefix rolled-back org, the sign-in tag, 'CPSIM06 "
                 "VERIFY' people / households, the 'cpsim6verify-' slug) match no real row; the non-test org's id is "
                 "outside the reserved prefix", f"orgs={o} users={u} entities={e} households={h} counterparties={c} "
                                                f"outside={outside}")


async def point(org, key) -> bool:
    c1, _ = await api(U_SA, "PUT", f"{_P}/org-access/{org}", {"allowed": True})
    c2, _ = await api(U_SA, "PUT", cpu(key, f"/connections/{org}"), {"point": True})
    return c1 == HTTP_OK and c2 == HTTP_OK


def crm_hash(value: str) -> str:
    """The CRM's own tax-id store: sha256 of the value as typed (routers/entities.py add_tax_id)."""
    return hashlib.sha256(value.encode()).hexdigest()


async def seed(conn) -> None:
    S["itin_adult"], S["itin_joint"], S["itin_minor"], S["itin_trustee"], S["itin_trusted"] = (
        fake_itin(), fake_itin(), fake_itin(), fake_itin(), fake_itin())
    S["ein_trust"] = fake_ein()
    dob_adult, dob_joint, dob_minor, dob_trustee = years_ago(45), years_ago(43), years_ago(10), years_ago(60)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"CPSIM06 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        if "manage_members" not in perm_ids:
            raise RuntimeError("permission manage_members absent from the catalog")
        for org in (ORG_A, ORG_B):
            for rname, perms in ROLE_PERMS.items():
                rid = await rbac.ensure_role(conn, org, rname, f"CPSIM06 verify — {rname}")
                for pn in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[pn])
        for uid, (org, account_role, short, rname) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)", uid, org, f"{TAG}-{short}@test.local",
                f"CPSIM06 {short.upper()}", sub_of(uid), account_role)
            await rbac.grant_named_role(conn, uid, org, rname)
        people = ((P_ADULT, ORG_A, "Adult", "individual", S["itin_adult"], dob_adult, "adult"),
                  (P_JOINT, ORG_A, "Joint", "individual", None, dob_joint, "joint"),
                  (P_MINOR, ORG_A, "Minor", "individual", S["itin_minor"], dob_minor, None),
                  (P_TRUSTEE, ORG_A, "Trustee", "individual", S["itin_trustee"], dob_trustee, "trustee"),
                  (T_TRUST, ORG_A, "Family Trust", "trust", S["ein_trust"], None, None),
                  (P_TRUSTED, ORG_A, "Trusted", "individual", None, None, "trusted"),
                  (E_CO, ORG_A, "Employer Co", "llc", None, None, None),
                  (P_B, ORG_B, "B Person", "individual", None, years_ago(50), "bperson"))
        for eid, org, nm, typ, tax, dob, mail in people:
            await conn.execute(
                "INSERT INTO entities (id, org_id, display_name, status, entity_type, first_name, surname, legal_name, "
                "tax_id, date_of_birth, primary_email, primary_phone, citizenship_status) VALUES ($1, $2, $3, 'active', "
                "$4::entity_type, $5, $6, $7, $8, $9, $10, $11, $12)", eid, org, NAME + nm, typ,
                nm if typ == "individual" else None, "Verify" if typ == "individual" else None, NAME + nm, tax, dob,
                f"{TAG}-{mail}@example.test" if mail else None, "512-555-0100" if mail else None,
                "us_citizen" if typ == "individual" else None)
        # P_JOINT's tax id lives ONLY in the CRM's hashed store (the opener must type it); P_ADULT has both stores
        for eid, val in ((P_JOINT, S["itin_joint"]), (P_ADULT, S["itin_adult"])):
            await conn.execute("INSERT INTO entity_tax_ids (org_id, entity_id, tax_id_type, tax_id_country, "
                               "tax_id_encrypted, tax_id_last4, is_primary) VALUES ($1, $2, 'itin'::tax_id_type, 'US', $3, "
                               "$4, true)", ORG_A, eid, crm_hash(val), re.sub(r"\D", "", val)[-4:])
        for eid, kind in ((P_ADULT, "primary_residence"), (P_JOINT, "primary_residence"), (P_MINOR, "primary_residence"),
                          (P_TRUSTEE, "primary_residence"), (T_TRUST, "registered"), (P_B, "primary_residence")):
            await conn.execute("INSERT INTO entity_addresses (org_id, entity_id, address_type, street1, city, state, "
                               "postal_code, country, is_primary) VALUES ($1, $2, $3::address_type, '100 Congress Ave', "
                               "'Austin', 'TX', '78701', 'US', true)", ORG_B if eid == P_B else ORG_A, eid, kind)
        for f, t, k in ((P_TRUSTEE, T_TRUST, "trustee"), (P_ADULT, P_MINOR, "parent_of"),
                        (P_JOINT, T_TRUST, "beneficiary")):
            await conn.execute("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, "
                               "relationship_type) VALUES ($1, $2, $3, $4)", ORG_A, f, t, k)
        for eid in (P_ADULT, P_JOINT, P_TRUSTEE):
            await conn.execute("INSERT INTO entity_employment (org_id, employee_id, employer_id, title, is_current) "
                               "VALUES ($1, $2, $3, 'Partner', true)", ORG_A, eid, E_CO)
        for hh, org in ((H_A, ORG_A), (H_B, ORG_B)):
            await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, org,
                               NAME + "household")
        for eid in MEMBERS_A:
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", H_A, eid)
        await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", H_B, P_B)
    os_ = {r["id"] for r in await platform(conn, "SELECT id FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
                                           FIXTURE_ORGS)}
    us = {r["id"] for r in await platform(conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) AND is_test",
                                          ALL_USERS)}
    ok_test = check(os_ == set(FIXTURE_ORGS) and us == set(ALL_USERS), "fixture orgs A, B and their users are "
                    "is_test = true (privileged insert path; the reserved prefix)", f"{len(os_)} {len(us)}")
    ok_roles = await VR.check_roles_as_meant(conn, check, ROLE_EXPECT, "(Super Admin, opener with manage_members, "
                                                                       "reader without it, org B's opener)")
    ok_cp = True
    S["rt_user"] = {}
    for key in ("main",):
        code, b = await api(U_SA, "POST", _P, {"name": NAME + key, "institution_type": "altruist",
                                               "slug": SLUG_PREFIX + key})
        issued = (b or {}).get("issued") or {}
        if code != HTTP_OK or not issued.get("api_key", "").startswith("cpsim_live_"):
            ok_cp = False
            continue
        S["cp"][key], S["keys"][key] = b["id"], issued["api_key"]
        S["issued"] += [v for v in issued.values() if v]
        c2, _ = await api(U_SA, "POST", cpu(key, "/activate"))
        ok_cp &= c2 == HTTP_OK
        S["client_ids"][key] = await sim(conn, "SELECT id::text FROM cpsim.counterparty_credentials WHERE "
                                               "counterparty_id = $1::uuid AND kind = 'api_key' AND rotated_at IS NULL "
                                               "AND revoked_at IS NULL", b["id"], fetch="fetchval")
        c3, u3 = await api(U_SA, "POST", cpu(key, "/realtime/users"), {"username": f"{TAG}-{key}"})
        pw = ((u3 or {}).get("issued") or {}).get("realtime_password") or ""
        ok_cp &= c3 == HTTP_OK and pw.startswith("cpsim_rtpw_") and (u3 or {}).get("shown_once") is True
        S["issued"].append(pw)
        S["rt_user"][key] = {"username": f"{TAG}-{key}", "password": pw, "id": ((u3 or {}).get("user") or {}).get("id")}
    ok_pt = await point(ORG_A, "main")
    set_sim_creds("main")
    cc, oc = await api(U_OP, "POST", "/api/v1/altruist/connect", {"environment": "simulator"})
    ok_conn = cc == HTTP_OK and oc.get("connected") is True
    if not ok_conn:      # the connect route takes its own permission; the Super Admin is the fallback actor
        cc, oc = await api(U_SA, "POST", "/api/v1/altruist/connect", {"environment": "simulator"})
        ok_conn = cc == HTTP_OK and oc.get("connected") is True
    check(ok_cp and ok_pt and ok_conn, "one fixture counterparty created, activated, a simulator user created on its "
          "Realtime tab (password shown once), org A pointed at it and its simulator connected through the EXISTING "
          "/altruist/connect (org B is NOT allowed)", f"cp={ok_cp} point={ok_pt} conn={cc} {dumps(oc)[:200]}")
    S["seeded"] = bool(ok_test and ok_roles and ok_cp and ok_pt and ok_conn)


# ═══ cpsim06 helpers ═══════════════════════════════════════════════════════════════════════════════════════════
def owners_body(shape: str, overrides: dict | None = None) -> list[dict]:
    out = []
    for eid, role in OWNERS[shape]:
        o = {"entity_id": str(eid), "role": role}
        ov = dict((overrides or {}).get(eid) or {})
        if eid == P_JOINT and "tax_id" not in ov:      # the CRM keeps only its hash: the opener types it
            ov["tax_id"] = {"kind": "itin", "value": S["itin_joint"]}
        if ov:
            o["overrides"] = ov
        out.append(o)
    return out


def shape_of(account_type: str) -> str:
    return CAO.ACCOUNT_TYPES[account_type][1]


def body_for(account_type: str, **kw) -> dict:
    b = {"account_type": account_type, "owners": owners_body(shape_of(account_type), kw.pop("overrides", None))}
    b.update(kw)
    return b


async def check_req(uid, hh, body) -> tuple[int, dict]:
    return await api(uid, "POST", f"{_H}/{hh}/custodian-account/check", body)


async def open_req(uid, hh, body) -> tuple[int, dict]:
    return await api(uid, "POST", f"{_H}/{hh}/custodian-account/open", body)


async def hw_counts(conn) -> dict:
    """What a refusal must not create: Hollisworks accounts + crosswalks of org A; the counterparty's book accounts."""
    return {"accounts": int(await platform(conn, "SELECT count(*) FROM accounts WHERE org_id = $1", ORG_A,
                                           fetch="fetchval")),
            "xwalk": int(await platform(conn, "SELECT count(*) FROM portfolio.external_references WHERE org_id = $1 "
                                              "AND record_type = 'account'", ORG_A, fetch="fetchval")),
            "book": int(await sim(conn, "SELECT count(*) FROM cpsim.book_accounts WHERE counterparty_id = $1::uuid",
                                  S["cp"]["main"], fetch="fetchval")),
            "opened_drafts": int(await sim(conn, "SELECT count(*) FROM cpsim.realtime_drafts WHERE counterparty_id = "
                                                 "$1::uuid AND status = 'opened'", S["cp"]["main"], fetch="fetchval"))}


async def feed(conn, hh_ext: str) -> dict:
    """The Open API accounts feed (GET /v2/accounts?household_id=) through the app, with org A's connection token."""
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            row = await AO.require_active_connection(c, org_id=str(ORG_A), environment=AO.SIMULATOR)
            tok = await AO.get_decrypted_access_token(c, connection_id=row["id"])
    S["issued"].append(tok)
    code, out, _h = await raw("GET", f"/cpsim/c/{SLUG_PREFIX}main/v2/accounts?household_id={hh_ext}&page_size=1000",
                              headers={"Authorization": f"Bearer {tok}"})
    rows = ((out.get("data") or {}).get("accounts") or []) if code == HTTP_OK else []
    return {a["id"]: a for a in rows}


def problem_codes(out: dict) -> list[str]:
    return sorted(str(p.get("code")) for p in (out.get("problems") or []))


async def assert_open(conn, account_type: str) -> None:
    """One supported type opened in good order, end to end."""
    before = await hw_counts(conn)
    code, out = await open_req(U_OP, H_A, body_for(account_type))
    acct = out.get("account") or {}
    cpa = out.get("counterparty") or {}
    ext = cpa.get("account_id")
    ok = code == HTTP_OK and out.get("opened") is True and out.get("repeat") is False and bool(ext)
    check(ok, f"{account_type}: all data in good order → opened at once (one Open request)",
          f"{code} {problem_codes(out)} {dumps(out)[:300]}")
    if not ok:
        return
    S["opened"][account_type] = {"account_id": acct["id"], "ext": ext}
    b = await sim(conn, "SELECT household_external_id, account_number, date_open, account_type FROM cpsim.book_accounts "
                        "WHERE counterparty_id = $1::uuid AND external_id = $2 AND valid_to IS NULL", S["cp"]["main"],
                  ext, fetch="fetchrow")
    number = b["account_number"] if b else ""
    check(bool(b) and bool(re.fullmatch(r"CPSIM[0-9]{9}", number or "")) and b["date_open"] == D0
          and cpa.get("status") == "ACTIVE" and cpa.get("date_open") == D0.strftime("%Y/%m/%d")
          and bool(re.fullmatch(r"[A-Z0-9 ]{2,40}", str(cpa.get("account_type") or "")))
          and cpa.get("account_number_masked") == f"•••• {number[-4:]}" and b["account_type"] == cpa["account_type"],
          f"{account_type}: the response carries the account id and a fake CPSIM number (last four only shown), status "
          f"ACTIVE, date_open = the counterparty clock; the book holds it under its household",
          f"{dumps(cpa)} book={bool(b)}")
    listed = await feed(conn, b["household_external_id"]) if b else {}
    a = listed.get(ext) or {}
    check(a.get("status") == "ACTIVE" and a.get("date_open") == D0.strftime("%Y/%m/%d") and
          a.get("account_number") == number,
          f"{account_type}: the Open API accounts feed lists it at once — status ACTIVE, date_open, its number",
          f"{ {k: a.get(k) for k in ('status', 'date_open')} }")
    row = await platform(conn, "SELECT account_number_masked, account_number_hash, custodian_code, registration_type, "
                               "tax_status, primary_entity_id, household_id FROM accounts WHERE id = $1::uuid AND "
                               "system_to IS NULL", acct["id"], fetch="fetchrow")
    async with org_ctx(ORG_A) as pool:
        async with pool.acquire() as c:
            salt = await get_or_create_salt(c, str(ORG_A))
            vocab = await account_vocab(c, str(ORG_A))
    # the expected registration type / tax status: the custodian type mapped through the org's OWN vocabulary
    reg_opt = next(o for o in CAO.account_type_options(vocab)["supported"] if o["key"] == account_type)
    check(bool(row) and row["account_number_masked"] == AccountNumber(number).masked
          and row["account_number_hash"] == AccountNumber(number).hashed(salt) and row["custodian_code"] == "ALTRUIST"
          and row["household_id"] == H_A and row["primary_entity_id"] == PRIMARY[shape_of(account_type)]
          and row["registration_type"] == reg_opt["registration_type"] and row["tax_status"] == reg_opt["tax_status"]
          and number not in dumps(out),
          f"{account_type}: the Hollisworks account exists through add_account — the number stored MASKED + keyed hash "
          f"only (never returned), custodian ALTRUIST, the primary owner, registration type / tax status from the org "
          f"vocabulary", f"{dict(row) if row else None}")
    x = await platform(conn, "SELECT record_id FROM portfolio.external_references WHERE org_id = $1 AND source_system "
                             "= $2 AND external_id = $3 AND record_type = 'account'", ORG_A, SIM_XWALK, ext,
                       fetch="fetch")
    check(len(x) == 1 and x[0]["record_id"] == UUID(acct["id"]), f"{account_type}: crosswalked "
          "(external_references ALTRUIST_SIMULATOR / account / the counterparty's id)", f"{[dict(r) for r in x]}")
    check((out.get("sync") or {}).get("synced") is True, f"{account_type}: synced immediately (sync_resolved_accounts, "
          "that account only)", f"{out.get('sync')}")
    c2, hh = await api(U_OP, "GET", f"{_H}/{H_A}")
    check(c2 == HTTP_OK and any(a2.get("id") == acct["id"] for a2 in hh.get("accounts") or []),
          f"{account_type}: visible on the household", f"{c2}")
    after = await hw_counts(conn)
    check(after["accounts"] == before["accounts"] + 1 and after["xwalk"] == before["xwalk"] + 1 and
          after["book"] == before["book"] + 1, f"{account_type}: exactly one account, one crosswalk, one book row",
          f"{before} → {after}")


# ═══ Sections ══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Discovery + CPSIM06_NOTES (D1–D6 as built, ASSUMED Realtime contract, validation table with sources); "
            "cpsim05 merged")
    code, out, _e = run_cmd(["git", "log", "main", "--oneline"], REPO, timeout=120)
    check(code == 0 and bool(re.search(r"^\w+ merge cpsim05$", out, re.M)), "cpsim05 is merged to main")
    check(VT.regression_mode() == "fast" or os.environ.get("VERIFY_REGRESSIONS") == "full",
          "fast regression mode is present (VERIFY_REGRESSIONS, nested --self-checks-only by default)")
    self_static(check, find)
    find("CRM tax ids are stored as sha256 + last four (entity_tax_ids) — the full number is unrecoverable, so the "
         "opener types it once per request; entities.tax_id is a pre-existing plaintext column returned by every "
         "entity read (recorded in CPSIM06_NOTES §1d, not fixed here)")


async def y_schema(conn):
    section("[Y] Schema: cpsim realtime tables, cpsim_app only, tax-id last-four CHECK, validation log append-only "
            "(from the catalog)")
    app_role = await platform(conn, "SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'", fetch="fetchval")
    read = 0
    for t, want in NEW_CPSIM_PRIVS.items():
        oid = await platform(conn, REL, "cpsim", t, fetch="fetchval")
        if oid is None:
            check(False, f"cpsim.{t} exists (catalog, namespace + relname)")
            continue
        read += 1
        privs = {r["privilege_type"] for r in await platform(
            conn, "SELECT x.privilege_type, x.grantee FROM pg_class c, aclexplode(c.relacl) x WHERE c.oid = $1", oid)
            if r["grantee"] == app_role}
        others = await platform(conn, "SELECT r.rolname FROM pg_class c, aclexplode(c.relacl) x JOIN pg_roles r ON "
                                      "r.oid = x.grantee WHERE c.oid = $1 AND r.rolname NOT IN ('cpsim_app', "
                                      "'postgres')", oid)
        trg = int(await platform(conn, "SELECT count(*) FROM pg_trigger WHERE tgrelid = $1 AND NOT tgisinternal", oid,
                                 fetch="fetchval"))
        want_trg = 1 if t in APPEND_ONLY else 0
        check(privs == want and not others and trg == want_trg, f"cpsim.{t}: cpsim_app holds exactly {sorted(want)} "
              f"(the shared _cpsim_fixtures.CPSIM_APP_WITHHELD list); no other role; append-only guard trigger "
              f"{'present' if want_trg else 'n/a'}", f"{sorted(privs)} {[r['rolname'] for r in others]} trg={trg}")
    cons = {}
    for nsp, rel in [("cpsim", t) for t in NEW_TABLES] + [("cpsim", "book_accounts"), ("cpsim", "request_log"),
                                                           ("portfolio", "external_references"),
                                                           ("public", "accounts"), ("public", "entity_tax_ids")]:
        cons[f"{nsp}.{rel}"] = await _constraints(conn, nsp, rel)
    n_read = sum(len(v) for v in cons.values())
    check(n_read > 0 and all(cons[f"cpsim.{t}"] for t in NEW_TABLES),
          "UNIQUE / CHECK / FK constraints read from the catalog (cpsim objects by namespace + relname, never "
          "::regclass)", f"{n_read}")
    bad = []
    for table, con, lits in SEED_CHECK_LITERALS:
        d = cons.get(table, {}).get(con)
        if d is None:
            bad.append(f"{table}.{con} missing")
            continue
        bad += [f"{table}.{con} lacks {x!r}" for x in lits if f"'{x}'" not in d]
    check(not bad, "every literal the run writes into a CHECKed column matches its CHECK (read from the catalog)",
          f"{bad[:5]}")
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
    check(set(UNIQUES) <= set(idx) and all("UNIQUE" in d for d in idx.values()),
          "the unique indexes the seed and the run rely on exist and are UNIQUE (read before seeding)",
          f"missing {sorted(set(UNIQUES) - set(idx))}")
    c = cons["cpsim.realtime_contacts"]
    check("0,4" in (c.get("realtime_contacts_tax_last4_chk") or "") and "{9}" in (
        c.get("realtime_contacts_no_taxid_text_chk") or ""), "the tax-id CHECKs: last four only (≤ 4 digits) and no "
          "9-digit run in any free-text / jsonb column")
    cols = {r["attname"] for r in await platform(
        conn, "SELECT attname FROM pg_attribute WHERE attrelid = (" + REL + ") AND attnum > 0 AND NOT attisdropped",
        "cpsim", "realtime_contacts")}
    check(not ({"date_of_birth", "dob", "tax_id", "tax_id_value", "ssn"} & cols) and {"tax_id_last4", "tax_id_digest",
                                                                                      "age_years"} <= cols,
          "realtime_contacts has no date-of-birth or full tax-id column (last four + digest; age at the clock)",
          f"{sorted(cols)}")

    async def refuse_more_digits(c):
        await c.execute("SET LOCAL ROLE cpsim_app")
        # CHECK constraints are evaluated before the FK trigger, so a random counterparty id still reaches the CHECK
        await c.execute("INSERT INTO cpsim.realtime_contacts (counterparty_id, contact_id, kind, tax_id_last4) VALUES "
                        "(gen_random_uuid(), 'rtc-aaaaaaaaaaaaaaaa', 'person', '12345')")
    _r, err = await rolled_back(conn, refuse_more_digits)
    check(isinstance(err, asyncpg.CheckViolationError) and "tax_last4" in str(err),
          "the database refuses five tax-id digits in tax_id_last4 (rolled back)", f"{type(err).__name__}")

    async def refuse_update_log(c):
        await c.execute("SET LOCAL ROLE cpsim_app")
        await c.execute("UPDATE cpsim.realtime_validation_log SET outcome = 'ok'")
    _r, err = await rolled_back(conn, refuse_update_log)
    check(isinstance(err, asyncpg.InsufficientPrivilegeError), "cpsim_app cannot UPDATE the validation log "
          "(append-only — no UPDATE grant; the trigger refuses even the owner outside teardown)", f"{type(err).__name__}")
    b = cons["cpsim.book_accounts"]
    check("CPSIM" in (b.get("book_accounts_number_chk") or ""), "cpsim.book_accounts.account_number CHECK = CPSIM + 9 "
          "digits (an opened account's)")
    pub = int(await platform(conn, "SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = "
                                   "ANY($1::text[])", ["accounts", "entity_tax_ids", "audit_log", "entities"],
                             fetch="fetchval"))
    check(pub > 0, "no public DDL: the public tables the flow touches keep their RLS policies (none added or dropped "
          "by docs/cpsim06_part1.sql — the file has no public statement)", f"{pub}")
    mig = _code_only(_src("docs/cpsim06_part1.sql").replace("--", "#"))
    check(not re.search(r"\b(public\.\w+|ALTER TABLE (?!cpsim\.))", mig.replace("FROM PUBLIC", "")),
          "docs/cpsim06_part1.sql touches the cpsim schema only")
    snap = _snapshot_columns()
    missing = []
    for t, cols_ in SEED_COLUMNS.items():
        have = snap.get(t) or snap.get(t.split(".")[-1]) or set()
        missing += [f"{t}.{c_}" for c_ in cols_ if c_ not in have]
    check(not missing, "every INSERT's columns appear in docs/schema_snapshot.sql", f"{missing[:5]}")
    check(all(snap.get(f"cpsim.{t}") for t in NEW_TABLES) and "date_open" in (snap.get("cpsim.book_accounts") or set()),
          "the snapshot was refreshed (the six cpsim realtime tables and book_accounts.date_open are in it)")
    S["schema_ok"] = read == len(NEW_TABLES) and n_read > 0


async def y_token(conn):
    section("[Y] Token rules: password grant, 5-minute access, FIXED 10-hour refresh, wrong password refused")
    u = S["rt_user"]["main"]
    url = f"/cpsim/c/{SLUG_PREFIX}main/realtime/realms/altruist/protocol/openid-connect/token"
    base = {"client_id": S["client_ids"]["main"], "client_secret": S["keys"]["main"]}
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        set_clock(t0)
        c1, b1, _h = await raw("POST", url, form={**base, "grant_type": "password", "username": u["username"],
                                                  "password": u["password"]})
        for k in ("access_token", "refresh_token"):
            if b1.get(k):
                S["issued"].append(b1[k])
        check(c1 == HTTP_OK and b1.get("expires_in") == ACCESS_S and b1.get("refresh_expires_in") == SESSION_S
              and b1.get("token_type") == "Bearer", "password grant → access 300 s, refresh window 36000 s",
              f"{c1} {b1.get('expires_in')} {b1.get('refresh_expires_in')}")
        cw, bw, _h = await raw("POST", url, form={**base, "grant_type": "password", "username": u["username"],
                                                  "password": "cpsim_rtpw_" + secrets.token_hex(8)})
        check(cw == 401 and bw.get("error") == "invalid_grant", "a wrong password → refused (401 invalid_grant)",
              f"{cw} {bw.get('error')}")
        hdr = {"Authorization": f"Bearer {b1.get('access_token', '')}"}
        hp = f"/cpsim/c/{SLUG_PREFIX}main/realtime/api/households"
        ca, _o, _h = await raw("POST", hp, headers=hdr, body={"name": NAME + "token household"})
        set_clock(t0 + timedelta(seconds=ACCESS_S + 1))
        cb, _o, _h = await raw("POST", hp, headers=hdr, body={"name": NAME + "token household"})
        check(ca in (HTTP_OK, HTTP_CREATED) and cb == 401, "the access token works, then expires at 5 minutes "
              "(injected clock)", f"{ca} → {cb}")
        cr, br, _h = await raw("POST", url, form={**base, "grant_type": "refresh_token",
                                                  "refresh_token": b1.get("refresh_token", "")})
        for k in ("access_token", "refresh_token"):
            if br.get(k):
                S["issued"].append(br[k])
        cn, _o, _h = await raw("POST", hp, headers={"Authorization": f"Bearer {br.get('access_token', '')}"},
                               body={"name": NAME + "token household"})
        check(cr == HTTP_OK and br.get("refresh_expires_in") == SESSION_S - ACCESS_S - 1 and cn == HTTP_OK,
              "refresh → a new access token that works; the refresh window is NOT extended (fixed at 10 h)",
              f"{cr} {br.get('refresh_expires_in')} {cn}")
        co, _bo, _h = await raw("POST", url, form={**base, "grant_type": "refresh_token",
                                                   "refresh_token": b1.get("refresh_token", "")})
        check(co == HTTP_OK, "the ORIGINAL refresh token is still valid after a refresh", f"{co}")
        set_clock(t0 + timedelta(seconds=SESSION_S + 1))
        ce, _be, _h = await raw("POST", url, form={**base, "grant_type": "refresh_token",
                                                   "refresh_token": b1.get("refresh_token", "")})
        ce2, _b2, _h = await raw("POST", url, form={**base, "grant_type": "refresh_token",
                                                    "refresh_token": br.get("refresh_token", "")})
        check(ce == 400 and ce2 == 400, "after 10 hours both refresh tokens of the session are refused", f"{ce} {ce2}")
        # the CLIENT honours the same rules (cache → refresh inside the window → password grant after it)
        set_clock(t0)
        RS.clear_cache()
        async with org_ctx(ORG_A) as pool:
            async with pool.acquire() as c:
                sess = await RS.session(c, org_id=str(ORG_A))
        await RS.household(sess, NAME + "token household", transport=APP_T)
        await RS.household(sess, NAME + "token household", transport=APP_T)
        set_clock(t0 + timedelta(seconds=ACCESS_S + 60))
        await RS.household(sess, NAME + "token household", transport=APP_T)
        set_clock(t0 + timedelta(seconds=SESSION_S + 60))
        await RS.household(sess, NAME + "token household", transport=APP_T)
        steps = [e["step"] for e in RS.events]
        check(steps == ["password", "cached", "refresh", "password"], "the client: one password grant, reused inside 5 "
              "minutes, REFRESHED after them, a new password grant once the fixed 10-hour window ends", str(steps))
    finally:
        set_clock(None)
        RS.clear_cache()


async def _open_types(conn, types: tuple):
    for t in types:
        await assert_open(conn, t)


async def y_open_a(conn):
    section("[Y] Open every supported type in good order → ACTIVE at once, in the Open API feed, account in Hollisworks "
            "via add_account, crosswalked, synced (part 1)")
    await _open_types(conn, OPEN_A)

    # the crosswalk is written in add_account's OWN transaction: a failing hook leaves no account behind
    async def boom(c, row):
        raise RuntimeError("verify: the crosswalk write failed")
    before = await hw_counts(conn)
    try:
        async with org_ctx(ORG_A) as pool:
            await HA.add_account(pool, org_id=str(ORG_A), actor_id=str(U_OP), household_id=H_A,
                                 custodian_code="ALTRUIST", account_number="CPSIM" + "".join(
                                     RNG.choice("0123456789") for _ in range(9)), primary_entity_id=P_ADULT,
                                 authority="managed", custody="integrated", on_created=boom)
        raised = False
    except RuntimeError:
        raised = True
    after = await hw_counts(conn)
    check(raised and after["accounts"] == before["accounts"] and after["xwalk"] == before["xwalk"],
          "the account and its crosswalk commit together: a failing crosswalk write leaves NO account (one transaction)",
          f"{raised} {before} → {after}")


async def y_open_b(conn):
    section("[Y] Open every supported type in good order (part 2)")
    await _open_types(conn, OPEN_B)
    done = sorted(S["opened"])
    if ONLY is None or "open_a" in ONLY:
        check(len(CAO.ACCOUNT_TYPES) == YES_TYPES and set(done) == set(CAO.ACCOUNT_TYPES),
              f"every one of the {YES_TYPES} supported types opened (the published YES list)", f"{done}")
    else:
        check(set(OPEN_B) <= set(done), "every part-2 type opened", f"{done}")


async def _refuse(conn, label: str, body: dict, want: set, *, exact: bool = True, via: str = "open"):
    before = await hw_counts(conn)
    code, out = await (open_req if via == "open" else check_req)(U_OP, H_A, body)
    got = set(problem_codes(out))
    after = await hw_counts(conn)
    ok_codes = (got == want) if exact else want <= got
    check(code == HTTP_OK and out.get("ok") is False and out.get("opened") is not True and ok_codes and
          all(p.get("field") and p.get("message") for p in out.get("problems") or []),
          f"refused — {label}: {'exactly' if exact else 'at least'} {sorted(want)}, each problem named by field",
          f"{code} got={sorted(got)} {dumps(out)[:300]}")
    check(after == before, f"refused — {label}: nothing created in Hollisworks (accounts, crosswalks) and no book row",
          f"{before} → {after}")
    return out


async def y_refusals(conn):
    section("[Y] Every refusal rule, alone and together (all problems listed); nothing created on refusal")
    for t in NO_SAMPLES:
        n0 = int(await sim(conn, "SELECT count(*) FROM cpsim.realtime_validation_log WHERE counterparty_id = $1::uuid",
                           S["cp"]["main"], fetch="fetchval"))
        out = await _refuse(conn, f"NO-list type {t}", {"account_type": t, "owners": owners_body("single")},
                            {"unsupported_type"})
        n1 = int(await sim(conn, "SELECT count(*) FROM cpsim.realtime_validation_log WHERE counterparty_id = $1::uuid",
                           S["cp"]["main"], fetch="fetchval"))
        msg = " ".join(p.get("message", "") for p in out.get("problems") or [])
        check(CAO.UNSUPPORTED_REASON in msg and n1 == n0, f"{t}: 'not supported by the custodian's API for account "
              "opening — open it through the custodian's own process'; nothing even sent", f"{n0}→{n1}")
    pobox = {"street1": "P.O. Box 14", "city": "Austin", "state": "TX", "postal_code": "78701", "country": "US"}
    foreign = {"street1": "1 Rue de Rivoli", "city": "Paris", "state": "TX", "postal_code": "75001", "country": "FR"}
    cases = [
        ("a PO Box residential address", {"residential_address": pobox}, {"po_box"}),
        ("a foreign address", {"residential_address": foreign}, {"foreign_address"}),
        ("a visa outside the list", {"citizenship": "visa", "visa_type": "B2",
                                     "visa_expiration": (D0 + timedelta(days=400)).isoformat()}, {"visa_not_accepted"}),
        ("a listed visa expiring in < 90 days", {"citizenship": "visa", "visa_type": "H1B",
                                                 "visa_expiration": (D0 + timedelta(days=60)).isoformat()},
         {"visa_expiring"}),
        ("a duplicate email (another client's)", {"email": f"{TAG}-trustee@example.test"}, {"duplicate_email"}),
    ]
    # the trustee's contact exists at the custodian already (the trust accounts opened it); make sure in --only runs
    if "trust" not in S["opened"]:
        await check_req(U_OP, H_A, body_for("trust"))
    for label, ov, want in cases:
        await _refuse(conn, label, body_for("individual", overrides={P_ADULT: ov}), want)
    # the duplicate-email case restated P_ADULT's counterparty contact with the trustee's email (correct: the
    # custodian keeps what it was sent); restate it with its own CRM email before the next cases use the trustee
    cr, orr = await check_req(U_OP, H_A, body_for("individual"))
    check(cr == HTTP_OK and orr.get("ok") is True, "re-sent with its own CRM data, the same owner is in good order "
          "again (the earlier refusal left nothing behind in Hollisworks)", f"{cr} {problem_codes(orr)}")

    def trustee_owner(tax: dict) -> dict:
        # P_TRUSTEE has no hashed CRM tax id, so a typed number is SENT and the counterparty's own rule decides
        return {"account_type": "individual", "owners": [{"entity_id": str(P_TRUSTEE), "role": "owner",
                                                          "overrides": {"tax_id": tax}}]}
    for kind in ("000", "666", "9xx"):
        await _refuse(conn, f"an SSN with area {kind.upper()}", trustee_owner({"kind": "ssn", "value": bad_ssn(kind)}),
                      {"ssn_area"})
    ci, oi = await check_req(U_OP, H_A, trustee_owner({"kind": "itin", "value": fake_itin()}))
    check(ci == HTTP_OK and oi.get("ok") is True, "a valid ITIN is accepted (the counterparty's ITIN rule)",
          f"{ci} {problem_codes(oi)}")
    ca, oa = await check_req(U_OP, H_A, body_for("individual", overrides={P_ADULT: {"tax_id": {
        "kind": "itin", "value": S["itin_adult"]}}}))
    check(ca == HTTP_OK and oa.get("ok") is True, "a typed tax id matching the CRM's hashed record is accepted",
          f"{ca} {problem_codes(oa)}")
    n0 = int(await sim(conn, "SELECT count(*) FROM cpsim.realtime_validation_log WHERE counterparty_id = $1::uuid",
                       S["cp"]["main"], fetch="fetchval"))
    await _refuse(conn, "a typed tax id that does not match the CRM record",
                  body_for("individual", overrides={P_ADULT: {"tax_id": {"kind": "itin", "value": fake_itin()}}}),
                  {"crm_mismatch"})
    n1 = int(await sim(conn, "SELECT count(*) FROM cpsim.realtime_validation_log WHERE counterparty_id = $1::uuid",
                       S["cp"]["main"], fetch="fetchval"))
    check(n1 == n0, "a Hollisworks-side problem refuses Open before anything is sent to the counterparty", f"{n0}→{n1}")
    await _refuse(conn, "JTWROS with one owner", {"account_type": "jtwros", "owners": owners_body("single")},
                  {"role_count_joint_owner"})
    await _refuse(conn, "a trust without a trustee", {"account_type": "trust", "owners": owners_body("trust")[:1]},
                  {"role_count_trustee"})
    await _refuse(conn, "UTMA without a minor", {"account_type": "utma", "owners": owners_body("custodial")[1:]},
                  {"role_count_minor"})
    await _refuse(conn, "UTMA with an adult 'minor'", {"account_type": "utma", "owners": [
        {"entity_id": str(P_TRUSTEE), "role": "minor"}, {"entity_id": str(P_ADULT), "role": "custodian"}]},
        {"not_a_minor"})
    for total, pcts in (("99.99", ("50", "49.99")), ("100.01", ("50", "50.01"))):
        await _refuse(conn, f"beneficiaries summing to {total} (Decimal)", body_for("ira", beneficiaries=[
            {"tier": "primary", "name": NAME + "Ben A", "kind": "person", "percentage": pcts[0]},
            {"tier": "primary", "name": NAME + "Ben B", "kind": "person", "percentage": pcts[1]}]), {"percent_total"})
    await _refuse(conn, "a trusted contact who is an owner", body_for("individual", trusted_contact={
        "entity_id": str(P_ADULT)}), {"is_owner"})
    # several together → every problem listed
    together = body_for("ira", overrides={P_ADULT: {"residential_address": pobox, "citizenship": "visa",
                                                    "visa_type": "B2", "visa_expiration": (D0 + timedelta(
                                                        days=400)).isoformat()}},
                        beneficiaries=[{"tier": "primary", "name": NAME + "Ben", "kind": "person",
                                        "percentage": "99.99"}], trusted_contact={"entity_id": str(P_ADULT)})
    out = await _refuse(conn, "several faults together", together, {"po_box", "visa_not_accepted", "percent_total",
                                                                    "is_owner"}, exact=False)
    check(len(out.get("problems") or []) >= 4, "several faults → EVERY problem listed in one answer (never just the "
          "first)", f"{problem_codes(out)}")
    find("D2 'DOB in the past' and the tax-id format rules are proven by the conformance realtime_open group (R13–R22) "
         "at the counterparty; through Hollisworks a date of birth comes from the CRM or the opener's override")


async def y_check_idem(conn):
    section("[Y] Check never opens; Open idempotent (same draft → one account)")
    before = await hw_counts(conn)
    ref = "cp6v-" + secrets.token_hex(6)
    c1, o1 = await check_req(U_OP, H_A, body_for("roth_ira", draft_reference=ref))
    mid = await hw_counts(conn)
    log = await sim(conn, "SELECT action, outcome FROM cpsim.realtime_validation_log WHERE counterparty_id = $1::uuid "
                          "ORDER BY created_at DESC LIMIT 1", S["cp"]["main"], fetch="fetchrow")
    check(c1 == HTTP_OK and o1.get("ok") is True and mid == before and log and log["action"] == "validate",
          "Check: in good order, validated at the counterparty (validation log 'validate'), NOTHING opened (no book "
          "row, no Hollisworks account, no crosswalk)", f"{c1} {problem_codes(o1)} {before} → {mid}")
    c2, o2 = await open_req(U_OP, H_A, body_for("roth_ira", draft_reference=ref))
    c3, o3 = await open_req(U_OP, H_A, body_for("roth_ira", draft_reference=ref))
    after = await hw_counts(conn)
    a2, a3 = (o2.get("account") or {}).get("id"), (o3.get("account") or {}).get("id")
    check(c2 == HTTP_OK and c3 == HTTP_OK and o2.get("opened") is True and o2.get("repeat") is False and
          o3.get("repeat") is True and a2 == a3 and a2 is not None and after["accounts"] == before["accounts"] + 1 and
          after["xwalk"] == before["xwalk"] + 1 and after["book"] == before["book"] + 1,
          "Open twice on the same draft → ONE account (the second answer is the same account, repeat=true)",
          f"{c2}/{c3} {a2} {a3} {before} → {after}")


async def y_sensitive(conn):
    section("[Y] Sensitive data never stored or shown beyond last four (catalog + row scan, request_log, responses, "
            "audit, page, this output)")
    S["tax"] = sorted(set(S["tax"]))
    check(len(S["tax"]) >= 6 and len(S["dobs"]) >= 4, "the run sent fake tax ids and dates of birth to scan for",
          f"{len(S['tax'])} {len(S['dobs'])}")
    cols = await platform(conn, "SELECT c.relname, a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cpsim' AND a.attnum > 0 "
                                "AND NOT a.attisdropped AND (a.attname ~ '(dob|birth|ssn|itin|ein$)' OR (a.attname ~ "
                                "'tax_?id' AND a.attname NOT IN ('tax_id_kind', 'tax_id_last4', 'tax_id_digest', "
                                "'tax_id_salt')))")
    check(not cols, "catalog: no cpsim column can hold a date of birth or a full tax id", f"{[dict(r) for r in cols]}")
    hits = []
    for t in NEW_TABLES + ("book_accounts", "book_households", "request_log"):
        rows = await sim(conn, f"SELECT t::text AS x FROM cpsim.{t} t WHERE {fixture_scope('cpsim.' + t)}")
        for r in rows:
            h = sensitive_hits(r["x"])
            if h:
                hits.append(f"{t}:{h[:2]}")
    check(not hits, "row scan: no fake tax id (any form) and no date of birth in any fixture cpsim row (contacts, "
          "drafts, beneficiaries, validation log, book, request_log)", f"{hits[:5]}")
    l4 = await sim(conn, "SELECT tax_id_last4 FROM cpsim.realtime_contacts WHERE counterparty_id = $1::uuid AND "
                         "tax_id_last4 IS NOT NULL", S["cp"]["main"])
    check(bool(l4) and all(re.fullmatch(r"[0-9]{4}", r["tax_id_last4"]) for r in l4),
          "every stored tax id is its last four (+ a salted digest)", f"{len(l4)}")
    audit = await platform(conn, f"SELECT payload::text AS p FROM audit_log WHERE {fixture_scope('public.audit_log')}")
    ah = [h for r in audit for h in sensitive_hits(r["p"] or "")]
    check(bool(audit) and not ah, "no fake tax id or date of birth in any fixture audit row (opened / refused rows "
          "carry problem codes and last four only)", f"{len(audit)} rows; {ah[:3]}")
    bh = [h for b in S["bodies"] for h in sensitive_hits(b["text"])]
    check(bool(S["bodies"]) and not bh, f"no response body ({len(S['bodies'])} of them — the Check / Open results, the "
          "pre-fill envelope, the Realtime tab, every simulator answer) carried a tax id or a date of birth", f"{bh[:3]}")
    c, env = await api(U_OP, "GET", f"{_H}/{H_A}/custodian-account")
    m = {x["entity_id"]: x for x in env.get("members") or []}
    ok_l4 = (m.get(str(P_ADULT), {}).get("tax_id") or {}).get("last4") == re.sub(r"\D", "", S["itin_adult"])[-4:]
    check(c == HTTP_OK and ok_l4 and not sensitive_hits(dumps(env)) and all("date_of_birth" not in x for x in m.values())
          and (m.get(str(P_JOINT), {}).get("tax_id") or {}).get("on_record") == "hash_only",
          "the page's envelope: last four only, never the date of birth (date_of_birth_on_record + is_minor instead); a "
          "hash-only CRM tax id asks the opener to type it", f"{c}")
    c2, tab = await api(U_SA, "GET", cpu("main", "/realtime"))
    check(c2 == HTTP_OK and bool(tab.get("contacts")) and not sensitive_hits(dumps(tab)),
          "the Admin → Counterparties → Realtime tab shows contacts with last four only", f"{c2}")
    oh = sensitive_hits(OUTPUT.getvalue())
    check(not oh, "this script's own output never printed a tax id or a date of birth", f"{oh[:3]}")
    # The Realtime TOKEN endpoint answers with tokens BY PROTOCOL (server-to-server, as Altruist's real endpoint does);
    # it is exempt by EXACT path + grant, like the shown-once create answers. Every other body is scanned as before.
    def token_ep(b) -> bool:
        return (b["src"] == "sim" and b["method"] == "POST" and b["path"] == RT_TOKEN_PATH
                and b["grant"] in RT_GRANTS)

    for b in S["bodies"]:        # which body (source) holds which issued value — 10-character prefixes only
        for x in secret_leaks(b["text"]):
            info(f"body source: {b['src']} {b['method']} {b['path']} grant={b['grant']} {b['status']} → {x}"
                 f"{' (token endpoint, exempt)' if token_ep(b) else ''}")
    rest = [b for b in S["bodies"] if not token_ep(b)]
    leaks = [x for b in rest for x in secret_leaks(b["text"]) + rt_secret_shapes(b["text"])]
    check(bool(rest) and not leaks, f"no response body ({len(rest)} of them) carried a simulator key, password or "
          "token (the shown-once create answers and the Realtime token endpoint's own answers — exact path, password / "
          "refresh_token grant — are the only places one appears, by design)", f"{leaks[:3]}")
    te = [b for b in S["bodies"] if token_ep(b)]
    tok_bodies = [b for b in S["bodies"] if b["path"] == RT_TOKEN_PATH]
    n_log = await sim(conn, "SELECT count(*) FROM cpsim.request_log WHERE counterparty_id = $1::uuid AND path = $2",
                      S["cp"]["main"], RT_TOKEN_PATH, fetch="fetchval")
    check(all(b["src"] == "sim" for b in tok_bodies) and int(n_log) == len(tok_bodies) + len(S["client_grants"])
          and len(S["client_grants"]) > 0,
          "(a) every token-endpoint answer went to a Realtime CLIENT — this script's direct grant tests or the "
          "Realtime client (services.altruist_realtime_sim, which keeps them in its server-side cache): the logged "
          "token calls equal those two counts exactly, so no person-facing route fetched or relayed one",
          f"exempt {len(te)}; logged {n_log} = direct {len(tok_bodies)} + client {len(S['client_grants'])}?")
    api_b = [b for b in S["bodies"] if b["src"] == "api"]
    al = [f"{b['method']} {b['path']}: {x}" for b in api_b for x in secret_leaks(b["text"]) + rt_secret_shapes(b["text"])]
    check(bool(api_b) and not al, f"(b) no Hollisworks /api/v1 body ({len(api_b)} of them) carried a cpsim_rt* token "
          "(the script's OR the Realtime client's) or the simulator password", f"{al[:3]}")
    aud = [x for r in audit for x in secret_leaks(r["p"] or "") + rt_secret_shapes(r["p"] or "")]
    check(not aud, f"(b) no fixture audit row ({len(audit)}) carried a cpsim_rt* token or the simulator password",
          f"{aud[:3]}")
    logs = await sim(conn, f"SELECT t::text AS x FROM cpsim.request_log t WHERE {fixture_scope('cpsim.request_log')}")
    ll = [x for r in logs for x in secret_leaks(r["x"]) + rt_secret_shapes(r["x"])]
    check(bool(logs) and not ll, f"(b) no fixture cpsim.request_log row ({len(logs)}) stores a token or the simulator "
          "password", f"{ll[:3]}")
    out_now = OUTPUT.getvalue()
    ol = secret_leaks(out_now) + rt_secret_shapes(out_now)
    check(not ol, "(b) this script's own output never printed a cpsim_rt* token or the simulator password", f"{ol[:3]}")
    trows = await sim(conn, "SELECT method, path, message_type, auth_outcome, status FROM cpsim.request_log WHERE "
                            "counterparty_id = $1::uuid AND path = $2", S["cp"]["main"], RT_TOKEN_PATH)
    secrets_ = [S["rt_user"]["main"]["username"], S["rt_user"]["main"]["password"], S["client_ids"]["main"],
                S["keys"]["main"]]
    bad = [dict(r) for r in trows if r["message_type"] not in {f"realtime.token.{g}" for g in RT_GRANTS}
           or r["method"] != "POST" or any(v and v in json.dumps(dict(r), default=str) for v in secrets_)]
    check(bool(trows) and not bad, f"(c) each token call's request_log row ({len(trows)}) records the grant type only "
          "(message_type realtime.token.<grant>) — no token, password, user name, client id or client secret",
          f"{len(bad)} bad")


async def y_guard(conn):
    section("[Y] Guard: simulated only; demo-household rule; absent for sandbox / production with the reason; "
            "permissions both ways; cross-org both directions")
    ok_body = body_for("individual")
    c, d = await api(U_OP, "GET", f"{_H}/{H_A}")
    o = d.get("custodian_account_opening") or {}
    check(c == HTTP_OK and o.get("available") is True and o.get("can_open") is True, "org A (allowed + counterparty, a "
          "test org): the household envelope offers the action", f"{c} {o}")
    c, d = await api(U_OPB, "GET", f"{_H}/{H_B}")
    o = d.get("custodian_account_opening") or {}
    check(c == HTTP_OK and o.get("available") is False and o.get("can_open") is False and
          str(o.get("reason") or "").startswith(CAO.NO_REALTIME), "org B (no counterparty_sim flag): the action is "
          "absent with the reason", f"{c} {o}")
    c, out = await open_req(U_OPB, H_B, {"account_type": "individual", "owners": [{"entity_id": str(P_B),
                                                                                  "role": "owner"}]})
    check(c == HTTP_CONFLICT and CAO.NO_REALTIME in dumps(out), "org B: Open refused 409 with the reason, nothing sent",
          f"{c} {dumps(out)[:200]}")
    for env_ in ("sandbox", "production"):
        c, out = await open_req(U_OP, H_A, {**ok_body, "environment": env_})
        c2, ctx = await api(U_OP, "GET", f"{_H}/{H_A}/custodian-account?environment={env_}")
        check(c == HTTP_CONFLICT and CAO.NO_REALTIME in dumps(out) and c2 == HTTP_OK and
              (ctx.get("availability") or {}).get("reason") == CAO.NO_REALTIME and
              (ctx.get("permissions") or {}).get("can_open") is False and
              (ctx.get("vocabularies") or {}).get("editable") == [],
              f"{env_}: absent — 'account opening needs Altruist Realtime API access (not granted)'; Open refused",
              f"{c} {c2} {ctx.get('availability')}")
    # counterparty_sim.allowed switched off for org A → absent; on again → offered
    c1, _ = await api(U_SA, "PUT", f"{_P}/org-access/{ORG_A}", {"allowed": False})
    c, d = await api(U_OP, "GET", f"{_H}/{H_A}")
    off = (d.get("custodian_account_opening") or {})
    c2, out = await check_req(U_OP, H_A, ok_body)
    c3 = HTTP_OK if await point(ORG_A, "main") else 0
    c4, d4 = await api(U_OP, "GET", f"{_H}/{H_A}")
    check(c1 == HTTP_OK and off.get("available") is False and c2 == HTTP_CONFLICT and c3 == HTTP_OK and
          (d4.get("custodian_account_opening") or {}).get("can_open") is True,
          "counterparty_sim.allowed off → absent and refused; on again → back", f"{c1} {off} {c2} {c3}")

    # the demo-household rule: a NON-test org (outside the reserved prefix), inside a ROLLED-BACK transaction
    async def demo_rule(c):
        await c.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, false)", OUTSIDE_ORG,
                        "CPSIM06 VERIFY rolled-back real-shaped org", f"{TAG}-outside")
        if await c.fetchval("SELECT is_test FROM organizations WHERE id = $1", OUTSIDE_ORG) is not False:
            raise RuntimeError("the rolled-back org is not a NON-test org (is_test forced true)")
        for k, v in (("counterparty_sim.allowed", "true"), ("counterparty_sim.altruist_counterparty",
                                                            json.dumps(SLUG_PREFIX + "main"))):
            await c.execute("INSERT INTO org_settings (org_id, setting_key, setting_value) VALUES ($1, $2, $3::jsonb)",
                            OUTSIDE_ORG, k, v)
        hh_plain, hh_demo = _id(90), _id(91)
        e_plain, e_demo = _id(92), _id(93)
        for hh, eid, tags in ((hh_plain, e_plain, []), (hh_demo, e_demo, ["demo-cpsim06"])):
            await c.execute("INSERT INTO entities (id, org_id, display_name, status, entity_type, tags) VALUES ($1, $2, "
                            "$3, 'active', 'individual'::entity_type, $4::text[])", eid, OUTSIDE_ORG, NAME + "outside",
                            tags)
            await c.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", hh, OUTSIDE_ORG,
                            NAME + "outside")
            await c.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", hh, eid)
        return (await CAO.availability(c, OUTSIDE_ORG, hh_plain), await CAO.availability(c, OUTSIDE_ORG, hh_demo))
    res, err = await rolled_back(conn, demo_rule)
    plain, demo = res or ({}, {})
    check(err is None and plain.get("available") is False and plain.get("reason") == CAO.NOT_DEMO and
          demo.get("available") is True, "a NON-test org: a non-demo household → absent ('not a demo household'); an "
          "all-demo household → offered (cpsim.may_connect, rolled back)", f"{err!r} {plain} {demo}")
    n = int(await platform(conn, "SELECT count(*) FROM organizations WHERE id = $1", OUTSIDE_ORG, fetch="fetchval"))
    check(n == 0, "the non-test org existed only inside the rolled-back transaction", f"{n}")
    # permissions both ways on the IDENTICAL request
    rd = await check_req(U_RD, H_A, ok_body)
    op = await check_req(U_OP, H_A, ok_body)
    sa = await check_req(U_SA, H_A, ok_body)
    check(rd[0] == HTTP_FORBIDDEN and "manage_members" in dumps(rd[1]) and op[0] == HTTP_OK and sa[0] == HTTP_OK,
          "the identical Check: the reader (support_staff, no manage_members) 403 naming the permission; the opener "
          "200; the Super Admin 200 (Super Admin first)", f"{rd[0]} {op[0]} {sa[0]}")
    rc, _ = await api(U_RD, "GET", f"{_H}/{H_A}/custodian-account")
    ro, _ = await open_req(U_RD, H_A, ok_body)
    check(rc == HTTP_FORBIDDEN and ro == HTTP_FORBIDDEN, "the reader cannot read the form or Open", f"{rc} {ro}")
    # cross-org both directions
    x1, _ = await check_req(U_OPB, H_A, ok_body)
    x2, _ = await check_req(U_OP, H_B, {"account_type": "individual", "owners": [{"entity_id": str(P_B),
                                                                                  "role": "owner"}]})
    x3, _ = await api(U_OPB, "GET", f"{_H}/{H_A}/custodian-account")
    check(x1 == HTTP_NOT_FOUND and x2 == HTTP_NOT_FOUND and x3 == HTTP_NOT_FOUND,
          "cross-org both directions: org B's opener on org A's household 404; org A's opener on org B's 404",
          f"{x1} {x2} {x3}")
    c, out = await check_req(U_OP, H_A, {"account_type": "individual", "owners": [{"entity_id": str(P_B),
                                                                                  "role": "owner"}]})
    check(c == HTTP_OK and "not_a_member" in problem_codes(out), "an owner from another org is refused as not a member "
          "of this household", f"{c} {problem_codes(out)}")
    c, out = await api(U_OP, "POST", f"{_H}/{H_A}/custodian-account/check", {**ok_body, "org_id": str(ORG_B)})
    check(c == HTTP_UNPROCESSABLE, "a body naming org_id is refused (extra='forbid', Rule 6)", f"{c}")


async def y_ratelimit(conn):
    section("[Y] Rate-limit 429 + Retry-After honoured (the client waits and retries once)")
    S["sleeps"].clear()
    RS.clear_cache()
    SRT.force_429(1, RETRY_AFTER_S)
    c, out = await check_req(U_OP, H_A, body_for("individual"))
    steps = [e["step"] for e in RS.events]
    check(c == HTTP_OK and out.get("ok") is True and S["sleeps"] == [float(RETRY_AFTER_S)] and "429" in steps,
          f"a 429 + Retry-After {RETRY_AFTER_S} from the simulator → the client waited {RETRY_AFTER_S} s and retried once; "
          "the Check succeeded", f"{c} sleeps={S['sleeps']} steps={steps}")
    SRT.force_429(2, RETRY_AFTER_S)
    c, out = await check_req(U_OP, H_A, body_for("individual"))
    SRT.force_429(0)
    det = out.get("detail") if isinstance(out.get("detail"), dict) else {}
    check(c == 502 and det.get("status") == 429,
          "a second consecutive 429 is not retried again — the request fails with the counterparty's 429", f"{c}")


async def y_unchanged(conn):
    section("[Y] Unchanged: add_account for manual entry; no real Realtime client; no network")
    before = await hw_counts(conn)
    num = f"{RNG.randrange(10 ** 8, 10 ** 9)}"
    S["tax"].append(num)      # treated as sensitive for the output scan (an account number, never printed)
    c, out = await api(U_OP, "POST", f"{_H}/{H_A}/accounts", {
        "custodian_code": "ZZ_CPSIM6V_MANUAL", "account_number": num, "primary_entity_id": str(P_ADULT),
        "authority": "read_only", "custody": "outside", "registration_type": "individual", "tax_status": "taxable"})
    after = await hw_counts(conn)
    keys = {"id", "account_number_masked", "custodian_code", "household_id", "authority", "custody", "pending_change",
            "registration_type", "tax_status", "is_billable", "is_discretionary", "service_model", "primary_entity_id",
            "owner_linked_now"}
    check(c == HTTP_CREATED and set(out) == keys and num not in dumps(out) and after["accounts"] == before["accounts"] + 1
          and after["xwalk"] == before["xwalk"], "a manual add_account: 201, the same response keys, the number never "
          "returned, NO crosswalk written (on_created defaults to None)", f"{c} {sorted(set(out) ^ keys)}")
    for env_ in ("sandbox", "production"):
        try:
            async with org_ctx(ORG_A) as pool:
                async with pool.acquire() as cn:
                    await RS.session(cn, org_id=str(ORG_A), environment=env_)
            check(False, f"the Realtime client refuses {env_} before any request")
        except AO.AltruistOAuthError as exc:
            check(RS.NO_REALTIME in str(exc), f"the Realtime client refuses {env_} before any request (no real Realtime "
                  "client)", str(exc))
    check(NETWORK_CALLS == [], "no network call so far (every call in-process)", f"{NETWORK_CALLS[:3]}")


async def y_conformance(conn):
    section("[Y] Conformance: the realtime_open group passes, labelled 'ASSUMED — Realtime API paths not published'")
    jpath = pathlib.Path(f"/tmp/verify_cpsim06_conformance_{os.getpid()}.json")
    code, out, err = run_cmd([sys.executable, str(CONFORMANCE), "--target", "simulator", "--json-out", str(jpath)],
                             REPO, timeout=1500)
    try:
        results = json.loads(jpath.read_text(encoding="utf-8")).get("results") or []
    except (OSError, ValueError):
        results = []
    # the runner's table truncates the contract to 28 characters — the full label is read from its JSON output
    rows = [r for r in results if str(r.get("id", "")).startswith("R") and r.get("id") != "R00"]
    fails = [r["id"] for r in rows if r.get("passed") is not True]
    check(code == 0 and len(rows) >= CONFORMANCE_R_ROWS and not fails and
          all(str(r.get("contract", "")).startswith("ASSUMED — Realtime API paths not published") for r in rows) and
          re.search(r"CONFORMANCE .*: \d+ PASS, 0 FAIL", out) is not None,
          f"the in-process conformance run: {len(rows)} realtime_open rows, all PASS, all labelled ASSUMED",
          f"{code} {fails[:3]} {(out + err)[-300:]}")


async def y_safety(conn):
    section("[Y] Boundary check; app role cannot read cpsim.*; cpsim_app privileges as documented")
    code, out, err = run_cmd([sys.executable, str(BOUNDARY)], REPO, timeout=300)
    check(code == 0 and "0 violation" in out, "cpsim_boundary_check passes", f"{code} {(out + err)[-300:]}")
    for t in NEW_TABLES + ("book_accounts",):
        try:
            await platform(conn, f"SELECT count(*) FROM cpsim.{t}", fetch="fetchval")
            denied = False
        except asyncpg.InsufficientPrivilegeError:
            denied = True
        check(denied, f"the app role cannot read cpsim.{t} (permission denied)")
    usage = await platform(conn, "SELECT has_schema_privilege('app_service', 'cpsim', 'USAGE')", fetch="fetchval")
    check(usage is False, "app_service has no USAGE on schema cpsim")
    app_role = await platform(conn, "SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'", fetch="fetchval")
    for t, want in NEW_CPSIM_PRIVS.items():
        # from the catalog ACL (the app role holds no USAGE on cpsim, so has_table_privilege cannot be asked)
        got = {r["privilege_type"] for r in await platform(
            conn, "SELECT x.privilege_type, x.grantee FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, "
                  "aclexplode(c.relacl) x WHERE n.nspname = 'cpsim' AND c.relname = $1", t) if r["grantee"] == app_role}
        check(got == want, f"cpsim_app on cpsim.{t} = {sorted(want)} (as documented)", f"{sorted(got)}")


async def y_routes(conn):
    section("[Y] Own routes via _verify_self; authz1_routes.py and visibility1_routes.py --check CURRENT")
    self_routes(check, find)
    for script in ("authz1_routes.py", "visibility1_routes.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script), "--check"], REPO, timeout=900,
                                 env={**os.environ, "AUTHZ_TEST_MODE": "1"})
        check(code == 0 and "CURRENT" in out, f"{script} --check reports CURRENT", f"{code} {(out + err)[-300:]}")


async def y_front_end(conn):
    section("[Y] UI render tests (CRM action form, field-level problems, Realtime tab); node --test tests/; npm run "
            "build exits 0 (run by this script)")
    NODE_TEST_COUNT = node_test_count()
    c1, o1, e1 = run_cmd(["node", "--test", NODE_TEST], WEB_DIR, timeout=300)
    n = re.search(r"^# tests (\d+)$", o1, re.M)
    p = re.search(r"^# pass (\d+)$", o1, re.M)
    check(c1 == 0 and n is not None and p is not None and int(n.group(1)) == NODE_TEST_COUNT and
          n.group(1) == p.group(1) and re.search(r"^# fail 0$", o1, re.M) is not None,
          f"the cpsim06 render tests ({NODE_TEST}, counted by their file) all pass "
          f"({n.group(1) if n else '?'}/{NODE_TEST_COUNT})", f"{c1} {(o1 + e1)[-300:] if c1 else ''}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=1800)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-400:] if code else "")
    if os.environ.get("SKIP_BUILD") == "1":
        _emit("[SKIP] npm run build (SKIP_BUILD=1 — the outer run builds)")
        return
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regressions — each ONCE, flat, AFTER teardown, VERIFY_NESTED=1, its OWN final TOTAL line (fast by "
            "default)")
    for script in REGRESSIONS:
        t, out, err, code = VT.run_nested(HERE / script, cwd=REPO, timeout=2700, env_extra={"SKIP_BUILD": "1"})
        fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("[FAIL]")]
        check(bool(t.ok) and not fails, f"{script}: 0 FAIL (its own final TOTAL line and exit code)",
              f"{t.detail()}; {fails[:3]} {err[-200:] if not t.line else ''}")


async def run(name, fn, *args):
    t0 = time.monotonic()
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await asyncio.wait_for(res, SECTION_TIMEOUT)
    except asyncio.TimeoutError:
        check(False, f"{name} exceeded {SECTION_TIMEOUT} s")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stdout)
    finally:
        info(f"{name}: {time.monotonic() - t0:.0f} s")


async def cleanup_only(conn) -> int:
    before = await counts(conn)
    info(f"fixture rows before cleanup: {sum(before.values())} { {t: n for t, n in sorted(before.items()) if n} }")
    real = await real_fingerprints(conn)
    try:
        log = await teardown_retrying()
        info(f"teardown removed: {log}")
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stdout)
    after = await counts(conn)
    nonzero = {t: n for t, n in sorted(after.items()) if n}
    check(not nonzero, f"every counted table at zero fixture rows after cleanup ({len(after)} tables)", f"{nonzero}")
    left = await leftovers(conn)
    check(left == 0, "zero fixture rows remain (public + cpsim)", f"{left}")
    now = await real_fingerprints(conn)
    for k in real:
        check(now.get(k) == real[k], f"real {k} unchanged by cleanup (count + checksum)", f"{real[k]} → {now.get(k)}")
    await close_conn(conn)
    print(total_line())
    return 0 if _n_fail == 0 else 1


FIXTURE_SECTIONS = (("token", y_token), ("open_a", y_open_a), ("open_b", y_open_b), ("refusals", y_refusals),
                    ("check_idem", y_check_idem), ("guard", y_guard), ("ratelimit", y_ratelimit),
                    ("unchanged", y_unchanged), ("sensitive", y_sensitive), ("conformance", y_conformance),
                    ("safety", y_safety))
ONLY_NEEDS = {"token": (), "open_a": (), "open_b": (), "refusals": (), "check_idem": (), "guard": (),
              "ratelimit": (), "unchanged": (), "sensitive": ("token", "refusals"), "conformance": (), "safety": ()}
ONLY: list | None = None


def only_sections() -> list[str] | None:
    arg = next((a for a in sys.argv[1:] if a.startswith("--only=")), None)
    if arg is None:
        return None
    want = [x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()]
    bad = [x for x in want if x not in ONLY_NEEDS and x != "seed"]
    if bad:
        raise SystemExit(f"--only: unknown section(s) {sorted(bad)}; known: seed, {', '.join(ONLY_NEEDS)}")
    names = {x for x in want if x != "seed"}
    for x in list(names):
        names |= set(ONLY_NEEDS.get(x, ()))
    return [n for n, _f in FIXTURE_SECTIONS if n in names]


async def main_async() -> int:
    global ONLY
    dsn, how = await admin_dsn()
    if not dsn:
        check(False, "a working DATABASE_URL", how)
        print(total_line())
        return 1
    os.environ["DATABASE_URL"] = dsn
    info(f"database via {how}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        restore_stubs()
        return await cleanup_only(conn)
    if "--self-checks-only" in sys.argv:
        try:
            await run("task 1", y_task1, conn)
            await run("routes", y_routes, conn)
        finally:
            restore_stubs()
            await close_conn(conn)
        print(total_line())
        return 0 if _n_fail == 0 else 1
    pre = None
    ONLY = only_sections()
    if ONLY is not None:
        info(f"--only: seed + {ONLY or '(seed only)'}")
    try:
        await run("pre-teardown", teardown_retrying)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        bypass = await platform(conn, "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user",
                                fetch="fetchval")
        check(bypass is False, "the verify's own connection does not bypass RLS (rolbypassrls = false)", f"{bypass}")
        if not await guard_prefix(conn):
            S["no_teardown"] = True
            return 1
        pre = await counts(conn)
        S["real"] = await real_fingerprints(conn)
        if ONLY is None:
            await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        if S.get("schema_ok"):
            await run("seed", seed, conn)
        chosen = [(n, f) for n, f in FIXTURE_SECTIONS if ONLY is None or n in ONLY]
        if S.get("seeded"):
            for name, fn in chosen:
                await run(name, fn, conn)
        else:
            for name, _fn in chosen:
                check(False, f"{name}: not run — the schema read or the seed failed (never skipped silently)")
        if ONLY is None:
            await run("routes", y_routes, conn)
            await run("front end", y_front_end, conn)
        check(NETWORK_CALLS == [] and MODEL_CALLS == [], "no network call and no model call during the whole run "
              "(every simulator, Realtime, admin and sync call was in-process)",
              f"network: {NETWORK_CALLS[:3]} model: {MODEL_CALLS[:3]}")
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real + demo rows "
                "unchanged")
        await SW.drain()
        restore_stubs()
        await close_app_pools()
        try:
            if S.get("no_teardown"):
                raise RuntimeError("teardown skipped — a reserved fixture prefix matches a real row")
            t_td = time.monotonic()
            log = await teardown_retrying()
            info(f"teardown removed: {log} ({time.monotonic() - t_td:.0f} s)")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (orgs, users, grants, people, addresses, tax ids, relationships, "
                  "employment, households, accounts, crosswalks, positions, connections, settings, audit rows, every "
                  "cpsim table incl. the six realtime tables and the book)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in sorted(pre):
                    check(post[t] == pre[t], f"{t} at its pre-test count (fixture rows)", f"{pre[t]} → {post[t]}")
            if S.get("real") is not None:
                after = await real_fingerprints(conn)
                for k in S["real"]:
                    check(after.get(k) == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
                          f"{S['real'][k]} → {after.get(k)}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc(file=sys.stdout)
        await close_conn(conn)
        if S.get("no_teardown"):
            print(total_line())
    if ONLY is not None:
        _emit("[SKIP] regression — --only one-off run")
    elif os.environ.get("VERIFY_NESTED") == "1":
        _emit("[SKIP] nested run")
    else:
        try:
            regression()
        except Exception as exc:  # noqa: BLE001
            check(False, "regression raised", f"{type(exc).__name__}: {exc}")
    print(total_line())
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    try:
        rc = asyncio.run(main_async())
    except BaseException as exc:  # noqa: BLE001 — TOTAL on every exit path, the traceback printed
        if isinstance(exc, SystemExit) and exc.code in (0, None):
            raise
        traceback.print_exc(file=sys.stdout)
        check(False, "the verify crashed", f"{type(exc).__name__}: {_redact(exc)}")
        print(total_line())
        rc = 1
    sys.exit(rc)
