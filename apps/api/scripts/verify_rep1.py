"""verify_rep1 — Replication Studio, sprint 1 (REP1).

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep1.py 2>&1 | grep -E "^\\[FAIL\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first.

MOCKED: every HTTP request (an httpx.MockTransport serves the SEC ticker file,
the series atom feeds, the filing index, the 497K, the N-PORT XML and the
issuer xlsx; any other URL is a 404 AND a failed check), every model call
(``note_extraction.proxy.chat`` + ``deployment_catalog``), and R2
(``storage.upload_bytes`` into memory; ``get_s3_client`` fails the run).

REAL: the app (middleware, Principal, every gate, every handler) through an
ASGI transport, RLS, and the live database. The script's own connection is
DATABASE_URL = app_service (rolbypassrls = false, asserted).

FIXTURES: two orgs (99000000-…-0000000e1001 / e1002); users created
explicitly with the auth0_sub their token carries and an @test.local email
(is_test by trigger). Every fixture fund / constituent carries the reserved
test prefix ZZRP (ticker or CUSIP), checked absent from the real security
master before anything is seeded. The daily-refresh proof runs on fixture
dates in 2099 so it can never take a real day's claim.

TEARDOWN (start and end): the operator DSN (role postgres) with
replica.teardown = 'fixture' in its transaction — the only path past the
immutability triggers — FK child-before-parent, then every counted table is
re-read for its exact pre-test count and the real security-master rows are
compared by count + checksum.

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)
os.environ.setdefault("EDGAR_USER_AGENT", "Hollisworks verify_rep1 (ops@hollisworks.com)")
os.environ.pop("REPLICA_HOLDINGS_AUTO_REFRESH", None)

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

BEFORE_REF = "fb4205d"   # rep1-work base — main after the bulkfetchui3 merge
TAG = "rep1verify"
PREFIX = "ZZRP"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
FUND_NAMES = ("REP1 VERIFY FUND", "REP1 VERIFY NPORT FUND", "REP1 VERIFY ALPHA", "REP1 VERIFY BETA",
              "REP1 VERIFY DECOY")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000e1{n:03d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SUPER, U_ADMIN1, U_ADMIN2, U_STAFF, U_MEMBER, U_ADMIN_B = (_id(110 + i) for i in range(6))
USERS = {   # org, users.role, short, rbac role ('org_admin' = grant_org_admin + replication default)
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "REP1 Super Admin"),
    U_ADMIN1: (ORG_A, "org_admin", "admin1", "org_admin", "REP1 Admin Maker"),
    U_ADMIN2: (ORG_A, "org_admin", "admin2", "org_admin", "REP1 Admin Checker"),
    U_STAFF: (ORG_A, "member", "staff", "investment_staff", "REP1 Staff"),
    U_MEMBER: (ORG_A, "member", "member", "member", "REP1 Member"),
    U_ADMIN_B: (ORG_B, "org_admin", "adminb", "org_admin", "REP1 Org B Admin"),
}
REPLICATION_PERMS = ("view_replication", "manage_replication", "approve_replication")

TICKER = "ZZRPQ"          # the issuer-file fund (SPY-shaped xlsx — the live-confirmed format)
TICKER_N = "ZZRPN"        # the N-PORT fallback fund
TICKER_X = "ZZRPX"        # never in the master: dry run only
CUSIP_A, CUSIP_B, CUSIP_C, CUSIP_D = "ZZRP00011", "ZZRP00012", "ZZRP00013", "ZZRP00014"
DECOY_TICKER = "ZZRPC"    # a master security carrying C's TICKER — C must NOT resolve to it
SERIES_Q, SERIES_N = "S000ZZRP01", "S000ZZRP02"
CIK_Q, CIK_N = "999999901", "999999902"
ACC_Q, ACC_N = "9999999999-26-000001", "9999999999-26-000002"
DAY1 = datetime(2099, 1, 5, 11, 5, tzinfo=timezone.utc)      # a Monday

COUNTED = (
    "public.users", "public.organizations", "public.roles", "public.user_roles", "public.role_permissions",
    "public.audit_log", "public.member_todos", "public.domain_events", "public.document_field_corrections",
    "public.ai_ensemble_configs", "portfolio.securities_global", "portfolio.securities_global_identifiers",
    "portfolio.securities_global_relationships", "portfolio.replica_securities", "portfolio.replica_terms_versions",
    "portfolio.replica_terms_actions", "portfolio.replica_holdings_snapshots", "portfolio.replica_holdings_rows",
    "portfolio.replica_source_documents", "portfolio.replica_term_readings", "portfolio.replica_refresh_events",
)

S: dict = {}
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def git_show(ref: str, path: str) -> str:
    code, out, _ = run_cmd(["git", "show", f"{ref}:{path}"], REPO, timeout=60)
    return out if code == 0 else ""


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


# ═══ DB helpers (verify's own app_service connection; RLS context explicit) ═══
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
    """The real security master — never touched (count + checksum)."""
    return {
        "securities_global": await fingerprint(conn, "SELECT * FROM portfolio.securities_global"),
        "securities_global_identifiers": await fingerprint(conn, "SELECT * FROM portfolio.securities_global_identifiers"),
        "securities_global_relationships": await fingerprint(
            conn, "SELECT * FROM portfolio.securities_global_relationships"),
    }


_FK_CACHE: dict = {}


async def fk_children(conn, table: str):
    if table not in _FK_CACHE:
        _FK_CACHE[table] = await conn.fetch("""
            SELECT cn.nspname || '.' || cc.relname AS child,
                   array_agg(ca.attname ORDER BY k.ord) AS child_cols,
                   array_agg(pa.attname ORDER BY k.ord) AS parent_cols
            FROM pg_constraint c
            JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, pk, ord) ON true
            JOIN pg_class cc ON cc.oid = c.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
            JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = k.ck
            JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.pk
            WHERE c.contype::text = 'f' AND c.confrelid = $1::regclass
            GROUP BY c.oid, cn.nspname, cc.relname""", table)
    return _FK_CACHE[table]


async def delete_tree(conn, table: str, where: str, params: list, depth=0, log=None, path=()):
    """Child-before-parent delete, FKs read from pg_constraint (recursively)."""
    if depth > 14:
        raise RuntimeError(f"FK recursion too deep at {table} via {' > '.join(path)}")
    path = path + (table,)
    for fk in await fk_children(conn, table):
        if fk["child"] in path:
            continue
        cc = ", ".join(f'"{c}"' for c in fk["child_cols"])
        pc = ", ".join(f'"{c}"' for c in fk["parent_cols"])
        await delete_tree(conn, fk["child"], f"({cc}) IN (SELECT {pc} FROM {table} WHERE {where})",
                          params, depth + 1, log, path)
    status = await conn.execute(f"DELETE FROM {table} WHERE {where}", *params)
    n = int(status.rsplit(" ", 1)[-1])
    if n and log is not None:
        log.append(f"{table}: {n}")


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def fixture_security_ids(conn) -> list:
    rows = await conn.fetch(
        "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1",
        f"{PREFIX}%")
    return [r["global_security_id"] for r in rows]


async def teardown(_conn=None) -> list[str]:
    """Operator connection, replica.teardown = 'fixture', FK child-before-parent."""
    log: list[str] = []
    op = await operator_conn()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('replica.teardown', 'fixture', true)")
            secs = await fixture_security_ids(op)
            users = list(USERS)
            steps = (
                ("member_todos", "DELETE FROM member_todos WHERE org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
                 [FIXTURE_ORGS, users]),
                ("domain_event_deliveries", "DELETE FROM domain_event_deliveries WHERE org_id = ANY($1::uuid[])",
                 [FIXTURE_ORGS]),
                ("domain_events", "DELETE FROM domain_events WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
                ("audit_log", "DELETE FROM audit_log WHERE org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
                 [FIXTURE_ORGS, users]),
                # rep3: terms approvals are human tasks — the engine's rows in the fixture orgs
                ("workflow_run_steps", "DELETE FROM workflow_run_steps WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
                ("workflow_runs", "DELETE FROM workflow_runs WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
                ("workflow_steps", "DELETE FROM workflow_steps WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
                ("workflow_versions", "DELETE FROM workflow_versions WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
                ("workflow_definitions", "DELETE FROM workflow_definitions WHERE org_id = ANY($1::uuid[])",
                 [FIXTURE_ORGS]),
                ("replica_terms_actions", "DELETE FROM portfolio.replica_terms_actions WHERE org_id = ANY($1::uuid[])",
                 [FIXTURE_ORGS]),
                ("document_field_corrections", "DELETE FROM document_field_corrections WHERE org_id = ANY($1::uuid[])",
                 [FIXTURE_ORGS]),
                ("replica_terms_versions", "DELETE FROM portfolio.replica_terms_versions WHERE org_id = ANY($1::uuid[])",
                 [FIXTURE_ORGS]),
                ("replica_securities", "DELETE FROM portfolio.replica_securities WHERE org_id = ANY($1::uuid[])",
                 [FIXTURE_ORGS]),
                ("replica_term_readings", "DELETE FROM portfolio.replica_term_readings "
                                          "WHERE fund_global_security_id = ANY($1::uuid[])", [secs]),
                ("ai_ensemble_configs", "DELETE FROM ai_ensemble_configs WHERE notes = $1", [TAG]),
                ("replica_source_documents", "DELETE FROM portfolio.replica_source_documents "
                                             "WHERE fund_global_security_id = ANY($1::uuid[])", [secs]),
                ("replica_holdings_rows", "DELETE FROM portfolio.replica_holdings_rows WHERE snapshot_id IN (SELECT id "
                                          "FROM portfolio.replica_holdings_snapshots WHERE fund_global_security_id = "
                                          "ANY($1::uuid[]))", [secs]),
                ("replica_holdings_snapshots", "DELETE FROM portfolio.replica_holdings_snapshots "
                                               "WHERE fund_global_security_id = ANY($1::uuid[])", [secs]),
                ("replica_refresh_events (finish)", "DELETE FROM portfolio.replica_refresh_events WHERE run_date >= "
                                                    "'2099-01-01' AND phase = 'finish'", []),
                ("replica_refresh_events (claim)", "DELETE FROM portfolio.replica_refresh_events WHERE run_date >= "
                                                   "'2099-01-01'", []),
                ("securities_global_relationships", "DELETE FROM portfolio.securities_global_relationships WHERE "
                 "from_global_security_id = ANY($1::uuid[]) OR to_global_security_id = ANY($1::uuid[]) "
                 "OR proposed_global_security_id = ANY($1::uuid[])", [secs]),
                ("securities_global_identifiers", "DELETE FROM portfolio.securities_global_identifiers "
                                                  "WHERE global_security_id = ANY($1::uuid[])", [secs]),
                ("securities_global", "DELETE FROM portfolio.securities_global WHERE id = ANY($1::uuid[])", [secs]),
            )
            for label, sql, args in steps:
                status = await op.execute(sql, *args)
                n = int(status.rsplit(" ", 1)[-1])
                if n:
                    log.append(f"{label}: {n}")
            await delete_tree(op, "public.users", "id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                              [users, f"%{TAG}%"], log=log)
            await delete_tree(op, "public.roles", "org_id = ANY($1::uuid[])", [FIXTURE_ORGS], log=log)
            await delete_tree(op, "public.organizations", "id = ANY($1::uuid[])", [FIXTURE_ORGS], log=log)
    finally:
        await op.close()
    return log


async def leftovers(conn) -> int:
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_value LIKE $1", [f"{PREFIX}%"]),
        ("SELECT count(*) FROM portfolio.replica_securities WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.replica_terms_versions WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.replica_terms_actions WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.replica_refresh_events WHERE run_date >= '2099-01-01'", []),
        ("SELECT count(*) FROM ai_ensemble_configs WHERE notes = $1", [TAG]),
        ("SELECT count(*) FROM member_todos WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM workflow_runs WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM workflow_definitions WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM domain_events WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM audit_log WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM document_field_corrections WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ Fixture files (shaped exactly like the Task 1b samples) ════════════════
def ssga_xlsx(as_of: str, rows: list[tuple]) -> bytes:
    """Same layout as SPY's live daily file (Task 1b): preamble, header row
    'Name, Ticker, Identifier, SEDOL, Weight, Sector, Shares Held, Local
    Currency', weights in PERCENT, a blank row, then the disclaimer."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "holdings"
    ws.append(["Fund Name:", "REP1 Verify Index Fund"])
    ws.append(["Ticker Symbol:", TICKER])
    ws.append(["Holdings:", f"As of {as_of}"])
    ws.append([])
    ws.append(["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency"])
    for name, ticker, ident, weight, shares in rows:
        ws.append([name, ticker, ident, "-", weight, "-", shares, "USD"])
    ws.append([])
    ws.append(["Before investing in a fund, consider its investment objectives, risks, charges, and expenses."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


CASH = ("US DOLLAR", "-", "999USDZ92", 0.05, 1000.0)
FILE1 = ssga_xlsx("20-Oct-2026", [("REP1 VERIFY ALPHA", "ZZRPA", CUSIP_A, 50.0, 1000.0),
                                  ("REP1 VERIFY BETA", "ZZRPB", CUSIP_B, 30.0, 500.0),
                                  ("REP1 VERIFY GAMMA", DECOY_TICKER, CUSIP_C, 19.95, 250.0), CASH])
FILE2 = ssga_xlsx("21-Oct-2026", [("REP1 VERIFY ALPHA", "ZZRPA", CUSIP_A, 55.0, 1100.0),
                                  ("REP1 VERIFY BETA", "ZZRPB", CUSIP_B, 24.95, 400.0),
                                  ("REP1 VERIFY DELTA", "ZZRPD", CUSIP_D, 20.0, 300.0), CASH])
FILE_BAD = ssga_xlsx("22-Oct-2026", [("REP1 VERIFY ALPHA", "ZZRPA", CUSIP_A, 50.0, 1000.0),
                                     ("REP1 VERIFY BETA", "ZZRPB", CUSIP_B, 20.0, 400.0),
                                     ("REP1 VERIFY DELTA", "ZZRPD", CUSIP_D, 20.0, 300.0), CASH])
FILE4 = ssga_xlsx("23-Oct-2026", [("REP1 VERIFY ALPHA", "ZZRPA", CUSIP_A, 40.0, 900.0),
                                  ("REP1 VERIFY BETA", "ZZRPB", CUSIP_B, 39.95, 700.0),
                                  ("REP1 VERIFY DELTA", "ZZRPD", CUSIP_D, 20.0, 300.0), CASH])
FILE_NEW_X = ssga_xlsx("21-Oct-2026", [("REP1 VERIFY ALPHA", "ZZRPA", CUSIP_A, 99.95, 10.0), CASH])

NPORT = f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><headerData><submissionType>NPORT-P</submissionType></headerData>
<formData><genInfo><regName>REP1 VERIFY TRUST</regName><seriesId>{SERIES_N}</seriesId><repPdEnd>2026-12-31</repPdEnd>
<repPdDate>2026-06-30</repPdDate></genInfo><invstOrSecs>
<invstOrSec><name>REP1 VERIFY ALPHA</name><lei>N/A</lei><title>REP1 VERIFY ALPHA</title><cusip>{CUSIP_A}</cusip>
<identifiers><isin value="US{CUSIP_A}0"/></identifiers><balance>1000</balance><units>NS</units><curCd>USD</curCd>
<valUSD>60000</valUSD><pctVal>60.0</pctVal><payoffProfile>Long</payoffProfile><assetCat>EC</assetCat></invstOrSec>
<invstOrSec><name>REP1 VERIFY BETA</name><title>REP1 VERIFY BETA</title><cusip>{CUSIP_B}</cusip>
<identifiers><ticker value="ZZRPB"/></identifiers><balance>500</balance><units>NS</units><curCd>USD</curCd>
<valUSD>39900</valUSD><pctVal>39.9</pctVal><assetCat>EC</assetCat></invstOrSec>
<invstOrSec><name>REP1 VERIFY MMF</name><title>MONEY MARKET</title><cusip>{PREFIX}00099</cusip><balance>100</balance>
<units>NS</units><valUSD>100</valUSD><pctVal>0.1</pctVal><assetCat>STIV</assetCat></invstOrSec>
</invstOrSecs></formData></edgarSubmission>""".encode()

PROSPECTUS = b"""<html><body>
<p>REP1 Verify Index Fund (ZZRPQ)</p>
<p>Summary Prospectus dated 2026-03-01</p>
<p>Investment Objective: The Fund seeks to track the investment results of the ZZRP Verify 100 Index.</p>
<p>The Index is a modified market capitalization weighted index composed of 100 securities.</p>
<p>The Index is rebalanced quarterly and reconstituted annually in December.</p>
<p>Total annual fund operating expenses 0.20%</p>
<p>Net expenses after fee waiver 0.18%</p>
<p>Dividends are paid quarterly.</p>
<p>The Fund may lend its portfolio securities.</p>
<p>The Fund commenced operations on 2010-05-15.</p>
</body></html>"""

QUOTES = {
    "fund_name": ("REP1 Verify Index Fund", "REP1 Verify Index Fund (ZZRPQ)"),
    "index_name": ("ZZRP Verify 100 Index", "the investment results of the ZZRP Verify 100 Index"),
    "investment_objective": ("The Fund seeks to track the investment results of the ZZRP Verify 100 Index.",
                             "The Fund seeks to track the investment results of the ZZRP Verify 100 Index."),
    "weighting_method": ("modified_market_cap", "a modified market capitalization weighted index"),
    "rebalance_frequency": ("quarterly", "The Index is rebalanced quarterly"),
    "reconstitution_schedule": ("annually in December", "reconstituted annually in December"),
    "holdings_count_target": (100, "composed of 100 securities"),
    "expense_ratio_gross_pct": (0.20, "Total annual fund operating expenses 0.20%"),
    # the quote below is NOT in the document → unverified → needs review
    "expense_ratio_net_pct": (0.18, "Net expenses after waiver were 0.18% for the year"),
    "distribution_frequency": ("quarterly", "Dividends are paid quarterly."),
    "securities_lending": (True, "The Fund may lend its portfolio securities."),
    "inception_date": ("2010-05-15", "The Fund commenced operations on 2010-05-15."),
    "prospectus_date": ("2026-03-01", "Summary Prospectus dated 2026-03-01"),
}


def atom(cik: str, entries: list[tuple[str, str, str]]) -> bytes:
    body = "".join(
        f"<entry><content type=\"text/xml\"><accession-number>{acc}</accession-number><filing-date>{d}"
        f"</filing-date><filing-href>https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
        f"{acc}-index.htm</filing-href><filing-type>{form}</filing-type></content></entry>"
        for form, acc, d in entries)
    return (f'<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"<company-info><cik>{int(cik):010d}</cik><conformed-name>REP1 VERIFY TRUST</conformed-name>"
            f"</company-info>{body}</feed>").encode()


# ═══ HTTP mock ═══════════════════════════════════════════════════════════════
HTTP_STATE = {"issuer_file": FILE1, "issuer_status": 200, "requests": [], "unmocked": []}
SSGA_URL = (f"https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/"
            f"holdings-daily-us-en-{TICKER.lower()}.xlsx")
FOLDER_Q = f"https://www.sec.gov/Archives/edgar/data/{int(CIK_Q)}/{ACC_Q.replace('-', '')}"
FOLDER_N = f"https://www.sec.gov/Archives/edgar/data/{int(CIK_N)}/{ACC_N.replace('-', '')}"


def _routes() -> dict:
    mf = {"fields": ["cik", "seriesId", "classId", "symbol"],
          "data": [[int(CIK_Q), SERIES_Q, "C000ZZRP01", TICKER], [int(CIK_N), SERIES_N, "C000ZZRP02", TICKER_N]]}
    feed = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={s}&type={f}&dateb=&owner=include&count=10&output=atom"
    return {
        "https://www.sec.gov/files/company_tickers_mf.json": (200, json.dumps(mf).encode()),
        feed.format(s=SERIES_Q, f="497K"): (200, atom(CIK_Q, [("497K", ACC_Q, "2026-03-01")])),
        feed.format(s=SERIES_Q, f="485BPOS"): (200, atom(CIK_Q, [])),
        feed.format(s=SERIES_N, f="NPORT-P"): (200, atom(CIK_N, [("NPORT-P", ACC_N, "2026-08-25")])),
        feed.format(s=SERIES_N, f="497K"): (200, atom(CIK_N, [])),
        feed.format(s=SERIES_N, f="485BPOS"): (200, atom(CIK_N, [])),
        f"{FOLDER_Q}/index.json": (200, json.dumps({"directory": {"item": [
            {"name": "zzrp497k.htm", "size": "5000"}, {"name": f"{ACC_Q}-index.htm", "size": "100"}]}}).encode()),
        f"{FOLDER_Q}/zzrp497k.htm": (200, PROSPECTUS),
        f"{FOLDER_N}/primary_doc.xml": (200, NPORT),
    }


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    HTTP_STATE["requests"].append(url)
    if url == SSGA_URL:
        return httpx.Response(HTTP_STATE["issuer_status"], content=HTTP_STATE["issuer_file"])
    hit = _routes().get(url)
    if hit is None:
        HTTP_STATE["unmocked"].append(url)
        return httpx.Response(404, content=b"not mocked")
    return httpx.Response(hit[0], content=hit[1])


def mock_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(_handler), follow_redirects=True)


# ═══ Model mock (LiteLLM proxy) ═════════════════════════════════════════════
MODEL_CALLS: list[dict] = []
DEP_A, DEP_B = "zzrp-reader-a", "zzrp-reader-b"


def fake_catalog():
    from services.note_extraction.proxy import Deployment

    return {
        DEP_A: Deployment(DEP_A, "dep-a", "deepinfra/openai/gpt-oss-120b", "gpt-oss-120b", 1e-7, 4e-7, None, True,
                          128000),
        DEP_B: Deployment(DEP_B, "dep-b", "deepinfra/deepseek-v3.2", "deepseek-v3.2", 2e-7, 4e-7, None, True, 128000),
        "zzrp-claude": Deployment("zzrp-claude", "dep-c", "anthropic/claude-haiku-4-5", "claude-haiku-4-5", 1e-6,
                                  5e-6, None, True, 200000),
    }


async def fake_chat(body: dict, *, timeout: float = 180.0):
    from services.note_extraction.proxy import ProxyResponse

    MODEL_CALLS.append(body)
    model = body["model"]
    answer = {k: {"value": v, "quote": q} for k, (v, q) in QUOTES.items()}
    reported = "openai/gpt-oss-120b"
    dep_id = "dep-a"
    if model == DEP_B:
        answer["weighting_method"] = {"value": "market_cap", "quote": "a modified market capitalization weighted index"}
        reported, dep_id = "deepseek-v3.2", "dep-b"
    resp_body = {"model": reported, "choices": [{"message": {"content": json.dumps(answer)}}],
                 "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
    return ProxyResponse(200, resp_body, json.dumps(resp_body)[:2000],
                         {"x-litellm-model-id": dep_id, "x-litellm-attempted-fallbacks": "0",
                          "x-litellm-response-cost": "0.0001"}, 5)


# ═══ The real app, token stubbed by bearer string ══════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
R2_STORE: dict = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, timeout=240.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_rep1"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"timeout": True}
    try:
        return res.status_code, json.loads(res.text)
    except ValueError:
        return res.status_code, {"raw": res.text}


ENSEMBLE: dict = {}


def install_stubs():
    import main
    from services import replica_holdings as H
    from services import replica_terms as T
    from services import storage
    from services import users as users_mod
    from services.note_extraction import proxy

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    def no_r2(*a, **k):
        raise AssertionError("verify: real R2 must never be called")

    def fake_upload(key, data, content_type=None, bucket=None):
        R2_STORE[(bucket, key)] = bytes(data)
        return key

    storage.get_s3_client = no_r2
    storage.delete_object = no_r2
    storage.upload_bytes = fake_upload
    H.make_http = mock_http
    H.clear_sec_cache()
    proxy.chat = fake_chat
    proxy.deployment_catalog = fake_catalog

    async def fixture_ensemble(conn):
        return dict(ENSEMBLE) if ENSEMBLE else None

    S["real_active_ensemble"] = T.active_ensemble
    T.active_ensemble = fixture_ensemble


# ═══ Sections ═══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 findings (1a–1f) — files, URLs, formats, terms-of-use notes, the workflow mechanism")
    from services import edgar_fetch, securities_global as SG
    from services.domain_events import publish_event  # noqa: F401
    from services.note_extraction import proxy, quote_match, schema  # noqa: F401

    for fn in ("get_by_identifier", "create_security", "add_identifier", "add_relationship",
               "get_by_identifiers_bulk", "add_relationships_bulk"):
        check(callable(getattr(SG, fn, None)), f"1a services/securities_global.{fn} exists (the ONE resolver/creator)")
    find("1a resolution", "ticker/CUSIP/ISIN → get_by_identifier (merge-following join; cusip/isin/figi/lei unique, "
         "ticker/sedol not); creation create_security + add_identifier (Super Admin, platform job); unresolved → "
         "add_relationship link_state 'unresolved' (constituent_of) — the underlying_resolution queue. Bulk variants "
         "added IN that module (one statement each), never a second resolver")
    find("1b SSGA (SPY)", "200 xlsx, sheet 'holdings', 'Holdings: As of 21-Oct-2026', header Name/Ticker/Identifier"
         "(CUSIP)/SEDOL/Weight(%)/Sector/Shares Held/Local Currency, 505 rows incl. US DOLLAR cash; site terms NOT "
         "reviewed — counsel must confirm automated retrieval")
    find("1b Invesco (QQQ)", "HTTP 406 (empty) to a non-browser client, twice — source seeded INACTIVE; QQQ has SEC "
         "series S000101292 so it falls back to N-PORT")
    find("1b iShares (IVV)", "the .ajax CSV URL answered 200 text/csv with a 2.3 MB HTML product page (bot/JS gate) — "
         "INACTIVE; IVV → N-PORT (S000004310)")
    find("1b SEC", "company_tickers_mf.json fields [cik, seriesId, classId, symbol], 28,592 rows; SPY ABSENT (a UIT, no "
         "series → issuer file only). Series atom feed (browse-edgar CIK=<series>&type=NPORT-P|497K|485BPOS&output="
         "atom) confirmed for IVV; latest IVV NPORT-P filed 2026-08-25. N-PORT is public quarterly, up to 60 days "
         "after period end: 2-5 months stale — a fallback, never daily")
    check(callable(edgar_fetch._get) and callable(edgar_fetch._pick_primary_document),
          "1b the existing EDGAR client (_get: User-Agent + rate limit) is what fund documents use")
    real = await S["real_active_ensemble"](conn)
    find("1c ensemble", "readers = services.note_extraction (proxy.chat one attempt/no fallback, schema.FieldSpec/"
         "json_schema/parse_reader_output, quote_match.TextIndex, schema.value_in_quote) reused with the replica "
         f"registry; ai_ensemble_configs active row for replica/note extraction: {'NONE' if real is None else real['id']}"
         " — without one, extraction is recorded 'blocked' and every model field needs review")
    find("1d corrections", "document_field_corrections target_type CHECK widened with 'replica_terms' (org_id NOT "
         "NULL, document_id NULL): its readers filter by field_name 'doc_category' / template joins / note_terms / "
         "FEE_SCHEDULE_SPEC, so none sees the new rows; global-read policy lists note_terms/template_proposal only")
    find("1e workflow mechanism", "the engine opens User Tasks only from a published BPMN version — no ad-hoc task "
         "service. CHOSEN: replica_terms_actions row + audit_log (same tx) + domain_events (publish_event, "
         "'replication.<action>'); 'approval needed' = member_todos for approve_replication holders, closed on decide "
         "(rep3: moved onto services.human_tasks — one task per draft, kind replica_terms_approval)")
    find("1f scheduler", "bulkfetchui3 runs from workflow_scheduler_tick.py's entrypoint after run_scheduler_tick "
         "(edgar_index_schedule.maybe_refresh_index, once per weekday by a partial unique index) — "
         "replica_schedule.maybe_refresh_holdings runs right after it")
    doc = (REPO / "docs" / "REPLICATION_STUDIO.md").read_text(encoding="utf-8")
    check(all(f"1{c}" in doc for c in "abcdef") and "406" in doc and "company_tickers_mf" in doc,
          "docs/REPLICATION_STUDIO.md records findings 1a–1f (URLs, formats, terms-of-use, mechanism)")


async def y_schema(conn):
    section("[Y] Schema landed — tables, constraints, partial uniques, RLS on, a policy for every operation")
    code, out, err = run_cmd([sys.executable, str(HERE / "_apply_rep1.py"), "--verify"], REPO, timeout=300)
    gaps = [ln for ln in out.splitlines() if ln.startswith("[GAP]")]
    check(code == 0 and "SCHEMA OK" in out and not gaps, "_apply_rep1.py --verify: SCHEMA OK", f"{gaps[:5]} {err[-200:]}")
    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(role["u"] == "app_service" and role["rolbypassrls"] is False,
          "the verify connection is app_service with rolbypassrls = false", f"{dict(role)}")
    # Every table seed() writes directly (rbac.ensure_role → roles; securities_global → its two tables), plus the
    # replica_* tables the services under test write. Names are bare (relname), never regclass::text — `portfolio`
    # is not on app_service's search_path, so regclass renders those schema-qualified.
    seeded = ["public.organizations", "public.roles", "public.role_permissions", "public.users",
              "public.user_roles", "public.ai_ensemble_configs", "portfolio.securities_global",
              "portfolio.securities_global_identifiers"]
    rows = await conn.fetch(
        """WITH t AS (SELECT c.oid, n.nspname || '.' || c.relname AS t FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                      WHERE c.relkind IN ('r', 'p') AND (n.nspname || '.' || c.relname = ANY($1::text[])
                            OR (n.nspname = 'portfolio' AND c.relname LIKE 'replica\\_%')))
           SELECT t.t, NULL::text AS conname, NULL::text AS k, NULL::text AS d FROM t
           UNION ALL
           SELECT t.t, x.conname::text, x.contype::text, pg_get_constraintdef(x.oid) FROM t
           JOIN pg_constraint x ON x.conrelid = t.oid AND x.contype IN ('u', 'c', 'f')
           UNION ALL
           SELECT t.t, ic.relname::text, 'i', pg_get_indexdef(i.indexrelid) FROM t
           JOIN pg_index i ON i.indrelid = t.oid AND i.indisunique AND NOT i.indisprimary
           JOIN pg_class ic ON ic.oid = i.indexrelid""", seeded)
    read_tables = {r["t"] for r in rows}
    cons = [r for r in rows if r["conname"]]
    for r in cons:
        print(f"[INFO] {r['t']}: {r['conname']} ({r['k']}) — {r['d'][:140]}")
    have = {r["conname"]: r["d"] for r in cons}
    unread = [t for t in seeded if t not in read_tables]
    check(not unread and any(t.startswith("portfolio.replica_") for t in read_tables)
          and "replica_holdings_snapshots_file_uq" in have and "WHERE" in have.get("replica_securities_live_uq", ""),
          "seed constraints read from the catalog first — every seeded table resolved, the partial unique has its "
          "WHERE", f"{len(cons)} read over {len(read_tables)} tables; not read: {unread or 'none'}")
    try:
        await conn.execute("UPDATE portfolio.replica_holdings_sources SET active = active WHERE false")
        check(True, "app_service can address platform tables (no-op update, RLS)")
    except Exception as exc:  # noqa: BLE001
        check(False, "app_service can address platform tables", str(exc))


async def seed(conn) -> None:
    from services import rbac
    from services import securities_global as SG

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"REP1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        for org, rname in ((ORG_A, "member"), (ORG_A, "investment_staff"), (ORG_A, "super_admin")):
            desc, perms = C.BASELINE_ROLES[rname]
            rid = await rbac.ensure_role(conn, org, rname, desc)
            for p in perms:
                if p in perm_ids:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            roles[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            elif rname:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid,
                                   roles[(org, rname)])
        # The migration's org_admin default, applied to the fixture orgs' org_admin roles (created on demand
        # by rbac.ensure_org_admin_role, which grants manage_org_settings only — see the [FIND] in y_gates).
        await conn.execute(
            "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r JOIN permissions p "
            "ON p.name = ANY($2::text[]) WHERE r.name = 'org_admin' AND r.org_id = ANY($1::uuid[]) "
            "ON CONFLICT DO NOTHING", FIXTURE_ORGS, list(REPLICATION_PERMS))
        # constituents A and B are in the master (by CUSIP); C's TICKER is on a decoy — C must stay unresolved
        for name, cusip, ticker in (("REP1 VERIFY ALPHA", CUSIP_A, "ZZRPA"), ("REP1 VERIFY BETA", CUSIP_B, "ZZRPB")):
            sid = await SG.create_security(conn, name=name, security_type="equity", is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=cusip, is_super_admin=True)
            await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=ticker,
                                    is_super_admin=True)
            S[f"sec_{ticker}"] = sid
        decoy = await SG.create_security(conn, name="REP1 VERIFY DECOY", security_type="equity", is_super_admin=True)
        await SG.add_identifier(conn, global_security_id=decoy, id_type="ticker", id_value=DECOY_TICKER,
                                is_super_admin=True)
        S["decoy"] = decoy
        ens = await conn.fetchrow(
            "INSERT INTO ai_ensemble_configs (task_key, model_1, model_1_version, model_2, model_2_version, "
            "system_one_model, system_one_model_version, is_active, notes) VALUES ('replica_terms_extraction', $1, "
            "'v', $2, 'v', (SELECT key FROM ai_system_one_models LIMIT 1), 'v', false, $3) RETURNING *",
            DEP_A, DEP_B, TAG)
        ENSEMBLE.update(dict(ens))


async def y_ingest(conn):
    section("[Y] Ingestion — snapshot + rows; same bytes = nothing; diff; bad weights → needs_review; unresolved "
            "queued, never guessed; N-PORT fallback")
    from services import replica_holdings as H
    from services.database import get_pool

    pool = await get_pool()
    http = mock_http()
    fund = await H.resolve_fund(pool, ticker=TICKER, cusip=None, http=http, fund_name="REP1 VERIFY FUND")
    S["fund"] = fund.global_security_id
    check(fund.created is True and fund.sec == {"cik": CIK_Q, "series_id": SERIES_Q, "class_id": "C000ZZRP01"},
          "a missing fund is created through securities_global (type 'fund') and its SEC ids come from the ticker file",
          f"{fund}")
    async with pool.acquire() as c:
        sources = {s["key"]: s for s in await H.list_sources(c)}
        chosen = await H.choose_source(c, ticker=TICKER, series_id=SERIES_Q)
    check(chosen["key"] == "sec_nport", "with no issuer listing the ticker, the source falls back to N-PORT",
          chosen["key"])
    src = sources["ssga_spdr"]
    snap_n = lambda: platform(conn, "SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE "  # noqa: E731
                                    "fund_global_security_id = $1", UUID(S["fund"]), fetch="fetchval")

    HTTP_STATE["issuer_file"] = FILE1
    r1 = await H.ingest(pool, fund_global_security_id=S["fund"], ticker=TICKER, source=src, series_id=SERIES_Q,
                        http=http)
    S["snap1"] = r1.snapshot_id
    rows = await platform(conn, "SELECT * FROM portfolio.replica_holdings_rows WHERE snapshot_id = $1 ORDER BY row_num",
                          UUID(r1.snapshot_id))
    check(r1.created is True and r1.status == "live" and len(rows) == 4 and r1.row_count == 4,
          "a SPY-shaped file creates ONE live snapshot with 4 rows (re-read)", f"{r1.status} {len(rows)}")
    w = {r["raw_cusip"] or r["raw_name"]: r["weight"] for r in rows}
    check(w.get(CUSIP_A) == Decimal("0.5") and w.get(CUSIP_B) == Decimal("0.3") and w.get(CUSIP_C) == Decimal("0.1995"),
          "weights stored as decimal fractions (percent / 100)", f"{w}")
    snap = await platform(conn, "SELECT * FROM portfolio.replica_holdings_snapshots WHERE id = $1",
                          UUID(r1.snapshot_id), fetch="fetchrow")
    check(snap["as_of"].isoformat() == "2026-10-20" and snap["total_weight_ex_cash"] == Decimal("0.9995")
          and snap["r2_key"] in {k for (_, k) in R2_STORE},
          "as_of from the FILE; ex-cash weight 0.9995; raw bytes gzipped to R2 (mocked)", f"{snap['r2_key']}")
    by = {r["raw_cusip"]: r for r in rows if r["raw_cusip"]}
    check(by[CUSIP_A]["resolution_state"] == "resolved" and str(by[CUSIP_A]["constituent_global_security_id"])
          == S["sec_ZZRPA"] and by[CUSIP_A]["resolved_via"] == "cusip",
          "a constituent with a known CUSIP resolves through the security master")
    c_row = by[CUSIP_C]
    check(c_row["resolution_state"] == "unresolved" and c_row["constituent_global_security_id"] is None,
          "NEVER GUESSED: C published a CUSIP the master lacks — not matched via its ticker, though a decoy carries it",
          f"decoy {S['decoy']}")
    rel = await platform(conn, "SELECT * FROM portfolio.securities_global_relationships WHERE id = $1",
                         c_row["relationship_id"], fetch="fetchrow")
    check(rel is not None and rel["link_state"] == "unresolved" and rel["relationship_type"] == "constituent_of"
          and rel["to_global_security_id"] is None and str(rel["from_global_security_id"]) == S["fund"]
          and CUSIP_C in rel["raw_underlying_text"],
          "the unresolved constituent is QUEUED on the shared relationships table (proposal pattern)",
          f"{rel and rel['raw_underlying_text']}")
    cash = [r for r in rows if r["asset_type"] == "cash"]
    check(len(cash) == 1 and cash[0]["resolution_state"] == "not_applicable", "cash is not resolved or queued")

    n_before, rel_before, r2_before = await snap_n(), await platform(
        conn, "SELECT count(*) FROM portfolio.securities_global_relationships WHERE from_global_security_id = $1",
        UUID(S["fund"]), fetch="fetchval"), len(R2_STORE)
    again = await H.ingest(pool, fund_global_security_id=S["fund"], ticker=TICKER, source=src, series_id=SERIES_Q,
                           http=http)
    rel_after = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_relationships "
                                     "WHERE from_global_security_id = $1", UUID(S["fund"]), fetch="fetchval")
    check(again.created is False and again.snapshot_id == r1.snapshot_id and await snap_n() == n_before
          and rel_after == rel_before and len(R2_STORE) == r2_before,
          "the same bytes again create nothing (no snapshot, no edge, no upload)", f"{n_before}")

    HTTP_STATE["issuer_file"] = FILE2
    r2 = await H.ingest(pool, fund_global_security_id=S["fund"], ticker=TICKER, source=src, series_id=SERIES_Q,
                        http=http)
    S["snap2"] = r2.snapshot_id
    d = (await platform(conn, "SELECT diff, previous_snapshot_id, diff_exceeds_threshold FROM "
                              "portfolio.replica_holdings_snapshots WHERE id = $1", UUID(r2.snapshot_id),
                        fetch="fetchrow"))
    diff = json.loads(d["diff"])
    moves = {m["key"]: Decimal(m["delta"]) for m in diff["moves"]}
    check(r2.created and str(d["previous_snapshot_id"]) == r1.snapshot_id and await snap_n() == n_before + 1,
          "a changed file creates a SECOND snapshot linked to the previous one")
    check([a["key"] for a in diff["added"]] == [CUSIP_D] and [x["key"] for x in diff["removed"]] == [CUSIP_C]
          and moves == {CUSIP_A: Decimal("0.05"), CUSIP_B: Decimal("-0.0505")}
          and Decimal(diff["turnover"]) == Decimal("0.25") and d["diff_exceeds_threshold"] is True,
          "the diff is exactly: D added, C removed, A +5.00pp, B -5.05pp, turnover 25% → exceeds threshold",
          f"{diff['added']} {diff['removed']} {moves} {diff['turnover']}")

    HTTP_STATE["issuer_file"] = FILE_BAD
    rb = await H.ingest(pool, fund_global_security_id=S["fund"], ticker=TICKER, source=src, series_id=SERIES_Q,
                        http=http)
    S["snap_bad"] = rb.snapshot_id
    async with pool.acquire() as c:
        live = await H.latest_snapshot(c, S["fund"], "ssga_spdr")
    vb = rb.validation["checks"]
    check(rb.created and rb.status == "needs_review" and vb["weights_sum"]["ok"] is False
          and str(live["id"]) == r2.snapshot_id,
          "weights summing to 0.9 → 'needs_review', and the live snapshot is still the previous one",
          f"{vb['weights_sum']['detail']}")

    fund_n = await H.resolve_fund(pool, ticker=TICKER_N, cusip=None, http=http, fund_name="REP1 VERIFY NPORT FUND")
    S["fund_n"] = fund_n.global_security_id
    async with pool.acquire() as c:
        src_n = await H.choose_source(c, ticker=TICKER_N, series_id=SERIES_N)
    rn = await H.ingest(pool, fund_global_security_id=S["fund_n"], ticker=TICKER_N, source=src_n, series_id=SERIES_N,
                        http=http)
    nrows = await platform(conn, "SELECT raw_cusip, weight, asset_type, resolution_state FROM "
                                 "portfolio.replica_holdings_rows WHERE snapshot_id = $1 ORDER BY row_num",
                           UUID(rn.snapshot_id))
    check(src_n["key"] == "sec_nport" and rn.created and rn.as_of.isoformat() == "2026-06-30" and len(nrows) == 3
          and nrows[0]["weight"] == Decimal("0.6") and nrows[2]["asset_type"] == "cash"
          and nrows[0]["resolution_state"] == "resolved",
          "N-PORT fallback: series feed → primary_doc.xml parsed (as_of repPdDate, pctVal/100, STIV = cash)",
          f"{[dict(r) for r in nrows]}")
    from services import replica_parsers as P

    inv = P.parse("invesco_csv", (
        b"Fund Ticker,Security Identifier,Holding Ticker,Shares/Par Value,MarketValue,Weight,Name,Class of Shares,"
        b"Sector,Date\nZZRPQ,ZZRP00011,ZZRPA,\"1,000\",100000,60.00,REP1 VERIFY ALPHA,Common Stock,IT,10/21/2026\n"
        b"ZZRPQ,ZZRP00012,ZZRPB,500,66000,40.00,REP1 VERIFY BETA,Common Stock,IT,10/21/2026\n"))
    check(inv.as_of.isoformat() == "2026-10-21" and [r.weight for r in inv.rows] == [Decimal("0.6"), Decimal("0.4")],
          "the Invesco parser reads its documented layout (live format NOT confirmed — 406)")
    try:
        P.parse("ishares_csv", b"<!DOCTYPE html><html><body>iShares</body></html>")
        check(False, "an HTML page served as CSV is refused, not parsed into zero rows")
    except P.HoldingsParseError as exc:
        check("HTML" in str(exc), "an HTML page served as CSV (what iShares returned) is refused", str(exc))
    await http.aclose()


async def y_dry_run(conn):
    section("[Y] Dry run writes nothing (before/after counts equal)")
    from services import replica_holdings as H
    from services.database import get_pool

    pool = await get_pool()
    http = mock_http()
    before, r2 = await counts(conn), len(R2_STORE)
    fund = await H.resolve_fund(pool, ticker=TICKER_X, cusip=None, http=http, create=False)
    async with pool.acquire() as c:
        src = {s["key"]: s for s in await H.list_sources(c)}["ssga_spdr"]
    HTTP_STATE["issuer_file"] = FILE4
    res = await H.ingest(pool, fund_global_security_id=S["fund"], ticker=TICKER, source=src, series_id=SERIES_Q,
                         http=http, dry_run=True)
    after = await counts(conn)
    check(fund.global_security_id is None and res.dry_run and res.created is False and res.row_count == 4
          and res.unresolved >= 1 and res.diff.get("added") is not None,
          "the dry run fetched, parsed, resolved and diffed (it reports what it would write)",
          f"rows {res.row_count} unresolved {res.unresolved}")
    changed = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
    check(not changed and len(R2_STORE) == r2, "...and wrote NOTHING: every counted table and R2 unchanged",
          f"{changed}")
    HTTP_STATE["issuer_file"] = FILE_BAD
    await http.aclose()


async def y_extraction(conn):
    section("[Y] Registry add (API) + extraction — readings with verified quotes; unverified → needs review; no Claude")
    calls0 = len(MODEL_CALLS)
    code, out = await api(U_ADMIN1, "POST", "/api/v1/replication/registry",
                          {"ticker": TICKER, "instrument_class": "index_etf", "holdings_source_key": "ssga_spdr"})
    check(code == 201 and out["entry"]["display_name"] == f"Holdings of {TICKER}", "org admin adds the fund (201)",
          f"{code} {str(out)[:300]}")
    if code != 201:
        return
    S["entry"] = out["entry"]["id"]
    check(out["entry"]["global_security_id"] == S["fund"], "the existing fund is reused (no second security)")
    check(out["steps"]["terms"]["ok"] is True and out["steps"]["terms"]["result"]["extraction_status"] == "extracted",
          "the terms step ran the ensemble", f"{out['steps']['terms']}")
    S["v1"] = out["steps"]["terms"]["result"]["version_id"]
    bodies = MODEL_CALLS[calls0:]
    from services import replica_terms as T

    models = sorted(b["model"] for b in bodies)
    check(models == sorted([DEP_A, DEP_B]) and not any(T.is_claude(b["model"]) for b in bodies)
          and not any(T.is_claude(fake_catalog()[m].upstream) for m in models),
          "exactly the two configured readers were called — no Claude model id in any call", f"{models}")
    check(len(bodies) == 2 and all("usage:replica_terms_extraction" in b["metadata"]["tags"] for b in bodies)
          and all(b["response_format"]["type"] == "json_schema" for b in bodies),
          "each reader call is tagged replica_terms_extraction and asks for the registry's JSON schema")
    readings = await platform(conn, "SELECT * FROM portfolio.replica_term_readings WHERE fund_global_security_id = $1 "
                                    "AND source <> 'data'", UUID(S["fund"]))
    by = {(r["source"], r["field_key"]): r for r in readings}
    idx = by.get(("model_1", "index_name"))
    check(idx is not None and idx["quote_verified"] is True and idx["raw_char_start"] is not None
          and idx["provider_model"] == "openai/gpt-oss-120b" and str(idx["ensemble_config_id"]) == str(ENSEMBLE["id"]),
          "readings are written with their quote, a verified span, provider model and ensemble id (re-read)")
    net = by.get(("model_1", "expense_ratio_net_pct"))
    check(net is not None and net["quote_verified"] is False, "a quote that is not in the document is UNVERIFIED")
    v = await platform(conn, "SELECT * FROM portfolio.replica_terms_versions WHERE id = $1", UUID(S["v1"]),
                       fetch="fetchrow")
    vals = json.loads(v["term_values"])
    check(v["status"] == "draft" and vals["expense_ratio_net_pct"]["needs_review"] is True
          and "expense_ratio_net_pct" in v["needs_review_fields"]
          and "quote was not found" in " ".join(vals["expense_ratio_net_pct"]["review_reasons"]),
          "...so its field is marked needs review on the DRAFT", f"{v['needs_review_fields']}")
    check(vals["weighting_method"]["confidence"] == "disagreed" and vals["weighting_method"]["needs_review"] is True,
          "reader disagreement → needs review")
    check(vals["index_name"]["needs_review"] is False and vals["index_name"]["origin"] == "extracted"
          and vals["ticker"]["source"] == "data" and vals["ticker"]["value"] == TICKER
          and vals["holdings_count_latest"]["value"] == 3,
          "agreed+verified fields are clean; ticker / holdings count come from data (origin 'extracted')",
          f"{vals['holdings_count_latest']}")
    # a Claude slot is refused BEFORE any call
    from services.database import get_pool

    saved = dict(ENSEMBLE)
    ENSEMBLE.update(model_1="zzrp-claude")
    n0 = len(MODEL_CALLS)
    run = await T.run_extraction(await get_pool(), fund_id=S["fund"], ticker=TICKER, series_id=SERIES_Q, http=mock_http())
    ENSEMBLE.clear()
    ENSEMBLE.update(saved)
    check(run.status == "blocked" and "Claude" in (run.reason or "") and len(MODEL_CALLS) == n0,
          "an ensemble slot that is a Claude model is refused before any call", run.reason or "")
    check(not HTTP_STATE["unmocked"], "no request left the mocks", f"{HTTP_STATE['unmocked'][:3]}")


async def _action_logged(conn, org, action_id, action) -> bool:
    a = await scoped(conn, org, "SELECT action FROM portfolio.replica_terms_actions WHERE id = $1", UUID(action_id),
                     fetch="fetchval")
    ev = await scoped(conn, org, "SELECT event_type FROM domain_events WHERE source_type = 'replica_terms_action' "
                                 "AND source_id = $1", UUID(action_id), fetch="fetchval")
    au = await scoped(conn, org, "SELECT action FROM audit_log WHERE resource_id = $1", UUID(action_id),
                      fetch="fetchval")
    return a == action and ev == f"replication.{action}" and au == f"replication.{action}"


async def y_edits(conn):
    section("[Y] Correction vs deliberate change — corrections table vs departures; approved immutable (409)")
    if not S.get("entry"):
        check(False, "registry entry exists for the edit section")
        return
    e = S["entry"]
    corr0 = await scoped(conn, ORG_A, "SELECT count(*) FROM document_field_corrections WHERE target_type = "
                                      "'replica_terms'", fetch="fetchval")
    code, out = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/corrections",
                          {"version_id": S["v1"], "field_key": "expense_ratio_net_pct", "value": "0.18",
                           "notes": "checked against the fee table"})
    check(code == 200, "a correction to the draft is accepted", f"{code} {str(out)[:200]}")
    if code != 200:
        return
    S["act_correct"] = out["action_id"]
    corr = await scoped(conn, ORG_A, "SELECT * FROM document_field_corrections WHERE target_type = 'replica_terms' "
                                     "AND target_id = $1", UUID(S["v1"]))
    tv = out["version"]["term_values"]["expense_ratio_net_pct"]
    check(len(corr) == 1 and corr[0]["field_name"] == "expense_ratio_net_pct" and corr[0]["document_id"] is None
          and corr[0]["org_id"] == ORG_A and str(corr[0]["corrected_by"]) == str(U_ADMIN1)
          and await scoped(conn, ORG_A, "SELECT count(*) FROM document_field_corrections WHERE target_type = "
                                        "'replica_terms'", fetch="fetchval") == corr0 + 1,
          "the correction writes document_field_corrections (target_type 'replica_terms', target = the draft)")
    check(tv["origin"] == "corrected" and tv["value"] == 0.18 and tv["needs_review"] is False
          and not any(d["field_key"] == "expense_ratio_net_pct" for d in out["version"]["departures"]),
          "...origin 'corrected' and NO departure", f"{out['version']['departures']}")
    acts0 = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.replica_terms_actions", fetch="fetchval")
    code, _ = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/changes",
                        {"field_key": "rebalance_frequency", "value": "monthly", "reason": "   "})
    acts1 = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.replica_terms_actions", fetch="fetchval")
    check(code == 422 and acts1 == acts0, "a change with a blank reason is refused (422) and writes nothing")
    code, out = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/changes",
                          {"field_key": "rebalance_frequency", "value": "monthly",
                           "reason": "Monthly rebalancing keeps the member's account closer to the index"})
    check(code == 200, "a deliberate change with a reason is accepted", f"{code} {str(out)[:200]}")
    if code == 200:
        S["act_change"] = out["action_id"]
        tv = out["version"]["term_values"]["rebalance_frequency"]
        dep = [d for d in out["version"]["departures"] if d["field_key"] == "rebalance_frequency"]
        check(tv["origin"] == "deliberate" and tv["value"] == "monthly" and len(dep) == 1
              and dep[0]["fund_value"] == "quarterly" and dep[0]["our_value"] == "monthly"
              and out["version"]["id"] == S["v1"],
              "...origin 'deliberate' + a departure {fund quarterly → ours monthly}, on the SAME draft",
              f"{dep}")
        n_corr = await scoped(conn, ORG_A, "SELECT count(*) FROM document_field_corrections WHERE field_name = "
                                           "'rebalance_frequency' AND target_type = 'replica_terms'", fetch="fetchval")
        check(n_corr == 0, "a deliberate change writes NO correction row")
    code, out = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/changes",
                          {"field_key": "index_name", "value": "Something Else", "reason": "x"})
    check(code == 422, "the index name (disclosure only) can be corrected but never changed", f"{code}")
    for aid, act in ((S.get("act_correct"), "correct"), (S.get("act_change"), "change")):
        check(bool(aid) and await _action_logged(conn, ORG_A, aid, act),
              f"'{act}' is logged as an action row + domain_events workflow event + audit_log (re-read)")
    # rep3: "approval needed" is ONE governed human task (services.human_tasks), whose engine todos reach
    # every approve_replication holder — no longer replica_terms' own member_todos.
    tasks = await scoped(conn, ORG_A, "SELECT rs.id, rs.status, ws.assigned_permission, d.name FROM workflow_run_steps rs "
                                      "JOIN workflow_runs r ON r.id = rs.workflow_run_id JOIN workflow_steps ws ON "
                                      "ws.id = rs.workflow_step_id JOIN workflow_versions v ON v.id = r.workflow_version_id "
                                      "JOIN workflow_definitions d ON d.id = v.workflow_definition_id WHERE "
                                      "r.context->'human_task'->>'kind' = 'replica_terms_approval' AND "
                                      "r.context->'human_task'->>'subject_id' = $1", S["v1"])
    S["v1_task"] = str(tasks[0]["id"]) if len(tasks) == 1 else None
    todos = await scoped(conn, ORG_A, "SELECT user_id, status FROM member_todos WHERE source = 'workflow_user_task' "
                                      "AND related_type = 'workflow_run_step' AND related_id = $1",
                         tasks[0]["id"]) if len(tasks) == 1 else []
    old_path = await scoped(conn, ORG_A, "SELECT count(*) FROM member_todos WHERE source = 'replica_terms_approval'",
                            fetch="fetchval")
    check(len(tasks) == 1 and tasks[0]["status"] == "active" and tasks[0]["assigned_permission"] == "approve_replication"
          and tasks[0]["name"] == "system:human_task:approve_replication"
          and {str(t["user_id"]) for t in todos} == {str(U_ADMIN1), str(U_ADMIN2)}
          and all(t["status"] == "open" for t in todos) and old_path == 0,
          "an 'approval needed' HUMAN TASK is open (approve_replication), its todos reach every holder; no old-path todos",
          f"{[dict(t) for t in tasks]} {[dict(t) for t in todos]} old={old_path}")


async def y_maker_checker(conn):
    section("[Y] Maker-checker on the identical request; approval supersedes; one approved version live")
    e = S.get("entry")
    if not e:
        check(False, "registry entry exists for maker-checker")
        return
    path = f"/api/v1/replication/registry/{e}/terms/{S['v1']}/approve"
    before = await scoped(conn, ORG_A, "SELECT status FROM portfolio.replica_terms_versions WHERE id = $1",
                          UUID(S["v1"]), fetch="fetchval")
    code, out = await api(U_ADMIN1, "POST", path, {"note": "ok"})
    after = await scoped(conn, ORG_A, "SELECT status FROM portfolio.replica_terms_versions WHERE id = $1",
                         UUID(S["v1"]), fetch="fetchval")
    check(code == 403 and "Maker-checker" in str(out.get("detail")) and before == after == "draft",
          "the MAKER's approve is refused (403 naming the rule) and the draft is unchanged", f"{code} {out}")
    try:
        await platform(conn, "UPDATE portfolio.replica_terms_versions SET status = 'approved', checker_id = $2, "
                             "approved_at = now() WHERE id = $1", UUID(S["v1"]), U_ADMIN1, fetch="execute")
        check(False, "the database refuses a maker as checker")
    except Exception as exc:  # noqa: BLE001
        check("maker_checker" in str(exc), "the database CHECK refuses a maker as checker too", str(exc)[:120])
    code, out = await api(U_ADMIN2, "POST", path, {"note": "ok"})
    check(code == 200 and out["version"]["status"] == "approved" and out["version"]["checker_id"] == str(U_ADMIN2),
          "the identical request by a different approver succeeds", f"{code}")
    if code != 200:
        return
    S["act_approve1"] = out["action_id"]
    check(await _action_logged(conn, ORG_A, out["action_id"], "approve"), "'approve' logged (action + event + audit)")
    # rep3: the decision completed the version's human task through the engine (approved_by = the checker),
    # and the engine closed its todos.
    task = await scoped(conn, ORG_A, "SELECT rs.status, rs.approved_by, rs.result->>'outcome' AS outcome FROM "
                                     "workflow_run_steps rs WHERE rs.id = $1", UUID(S["v1_task"]), fetch="fetchrow") \
        if S.get("v1_task") else None
    todos = await scoped(conn, ORG_A, "SELECT status FROM member_todos WHERE source = 'workflow_user_task' AND "
                                      "related_id = $1", UUID(S["v1_task"])) if S.get("v1_task") else []
    check(task is not None and task["status"] == "completed" and task["approved_by"] == U_ADMIN2
          and task["outcome"] == "approve" and bool(todos) and all(t["status"] != "open" for t in todos),
          "the approval human task is completed by the checker (outcome approve) and its todos are closed",
          f"{dict(task) if task else None} {[dict(t) for t in todos]}")
    ent = await scoped(conn, ORG_A, "SELECT status FROM portfolio.replica_securities WHERE id = $1 AND system_to IS NULL",
                       UUID(e), fetch="fetchval")
    check(ent == "active", "first approval makes the registry entry active")
    snap = await scoped(conn, ORG_A, "SELECT term_values::text FROM portfolio.replica_terms_versions WHERE id = $1",
                        UUID(S["v1"]), fetch="fetchval")
    code, _ = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/corrections",
                        {"version_id": S["v1"], "field_key": "fund_name", "value": "Changed"})
    check(code == 409, "a correction aimed at the APPROVED version is 409", f"{code}")
    code, _ = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/changes",
                        {"version_id": S["v1"], "field_key": "distribution_frequency", "value": "monthly",
                         "reason": "r"})
    check(code == 409, "a change aimed at the APPROVED version is 409", f"{code}")
    try:
        await scoped(conn, ORG_A, "UPDATE portfolio.replica_terms_versions SET term_values = '{}'::jsonb WHERE id = $1",
                     UUID(S["v1"]), fetch="execute")
        check(False, "a direct UPDATE of an approved version is refused")
    except Exception as exc:  # noqa: BLE001
        check("never edited" in str(exc), "a direct UPDATE of an approved version is refused (trigger)", str(exc)[:100])
    snap2 = await scoped(conn, ORG_A, "SELECT term_values::text FROM portfolio.replica_terms_versions WHERE id = $1",
                         UUID(S["v1"]), fetch="fetchval")
    check(snap == snap2, "the approved version is byte-for-byte unchanged (re-read)")
    # a change with no draft open starts v2 from the approved version
    code, out = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/changes",
                          {"field_key": "distribution_frequency", "value": "monthly", "reason": "Monthly income"})
    check(code == 200 and out["version"]["version_no"] == 2 and out["version"]["status"] == "draft",
          "a change with no draft open creates draft v2 from the approved version", f"{code}")
    if code != 200:
        return
    S["v2"] = out["version"]["id"]
    code, out = await api(U_ADMIN2, "POST", f"/api/v1/replication/registry/{e}/terms/{S['v2']}/approve", {})
    check(code == 200, "v2 approved by the checker", f"{code}")
    vers = await scoped(conn, ORG_A, "SELECT id, status FROM portfolio.replica_terms_versions WHERE "
                                     "replica_security_id = $1 AND system_to IS NULL", UUID(e))
    st = {str(v["id"]): v["status"] for v in vers}
    check(st.get(S["v1"]) == "superseded" and st.get(S["v2"]) == "approved"
          and list(st.values()).count("approved") == 1,
          "approval supersedes the prior version; exactly ONE approved version live (re-read)", f"{st}")
    archived = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.replica_terms_versions WHERE "
                                         "replica_security_id = $1 AND system_to IS NOT NULL", UUID(e), fetch="fetchval")
    check(archived >= 3, "every edit archived the outgoing state on the system axis (bi-temporal)", f"{archived}")
    # reject: extract → new draft → maker rejects (refused) → checker rejects
    code, out = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/extract")
    if code == 200:
        v3 = out["version_id"]
        await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/corrections",
                  {"version_id": v3, "field_key": "fund_name", "value": "REP1 Verify Index Fund"})
        c1, _ = await api(U_ADMIN1, "POST", f"/api/v1/replication/registry/{e}/terms/{v3}/reject", {"note": "no"})
        c2, o2 = await api(U_ADMIN2, "POST", f"/api/v1/replication/registry/{e}/terms/{v3}/reject", {"note": "no"})
        check(c1 == 403 and c2 == 200 and o2["version"]["status"] == "rejected"
              and await _action_logged(conn, ORG_A, o2["action_id"], "reject"),
              "reject: refused for the maker, done by the checker, logged", f"{c1} {c2}")
    else:
        check(False, "re-run extraction for the reject proof", f"{code} {out}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions under app_service (rolbypassrls = false); platform snapshots shared")
    e = S.get("entry")
    if not e:
        check(False, "registry entry exists for isolation")
        return
    bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bypass is False, "isolation is tested on a role WITHOUT rolbypassrls")
    snaps0 = await platform(conn, "SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE "
                                  "fund_global_security_id = $1", UUID(S["fund"]), fetch="fetchval")
    calls0 = len(MODEL_CALLS)
    code, out = await api(U_ADMIN_B, "POST", "/api/v1/replication/registry",
                          {"ticker": TICKER, "instrument_class": "index_etf", "holdings_source_key": "ssga_spdr"})
    check(code == 201, "org B adds the SAME fund to its own registry", f"{code} {str(out)[:200]}")
    if code != 201:
        return
    S["entry_b"] = out["entry"]["id"]
    snaps1 = await platform(conn, "SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE "
                                  "fund_global_security_id = $1", UUID(S["fund"]), fetch="fetchval")
    check(snaps1 == snaps0 and out["steps"]["terms"]["result"]["extraction_status"] == "reused"
          and len(MODEL_CALLS) == calls0,
          "platform data is shared: no second snapshot, the extraction is REUSED (no model call)",
          f"{snaps0}->{snaps1} {out['steps']['terms']}")
    for viewer, own, other, label in ((U_ADMIN_B, S["entry_b"], e, "B → A"), (U_ADMIN1, e, S["entry_b"], "A → B")):
        c_own, _ = await api(viewer, "GET", f"/api/v1/replication/registry/{own}")
        c_other, _ = await api(viewer, "GET", f"/api/v1/replication/registry/{other}")
        c_edit, _ = await api(viewer, "POST", f"/api/v1/replication/registry/{other}/terms/changes",
                              {"field_key": "fund_name", "value": "x", "reason": "x"})
        _, lst = await api(viewer, "GET", "/api/v1/replication/registry")
        ids = {r["id"] for r in lst.get("rows", [])}
        check(c_own == 200 and c_other == 404 and c_edit == 404 and own in ids and other not in ids,
              f"{label}: own entry 200, the other org's entry 404 (read AND edit), absent from the list",
              f"{c_own} {c_other} {c_edit}")
    for org, other_org in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
        n_ent = await scoped(conn, org, "SELECT count(*) FROM portfolio.replica_securities WHERE org_id = $1",
                             other_org, fetch="fetchval")
        n_ver = await scoped(conn, org, "SELECT count(*) FROM portfolio.replica_terms_versions WHERE org_id = $1",
                             other_org, fetch="fetchval")
        n_own = await scoped(conn, org, "SELECT count(*) FROM portfolio.replica_securities WHERE org_id = $1",
                             org, fetch="fetchval")
        n_snap = await scoped(conn, org, "SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE "
                                         "fund_global_security_id = $1", UUID(S["fund"]), fetch="fetchval")
        check(n_own > 0 and n_ent == 0 and n_ver == 0 and n_snap == snaps1,
              f"SQL under app_service as {org}: own entries visible, the other org's entries/versions 0, "
              "platform snapshots all readable", f"own {n_own} other {n_ent}/{n_ver} snaps {n_snap}")
    try:
        await scoped(conn, ORG_B, "INSERT INTO portfolio.replica_terms_actions (org_id, replica_security_id, action) "
                                  "VALUES ($1, $2, 'extract')", ORG_A, UUID(e), fetch="execute")
        check(False, "org B cannot write an org A row")
    except Exception as exc:  # noqa: BLE001
        check("row-level security" in str(exc), "org B cannot write an org A row (RLS WITH CHECK)", str(exc)[:100])


async def y_gates(conn):
    section("[Y] Permission gates both ways on identical requests")
    e = S.get("entry_b") and S.get("entry")
    for path, perm in (("/api/v1/replication/registry", "view_replication"),
                       (f"/api/v1/replication/registry/{S.get('entry')}", "view_replication")):
        for uid, who in ((U_MEMBER, "member"), (U_STAFF, "staff")):
            code, out = await api(uid, "GET", path)
            check(code == 403 and perm in str(out.get("detail")), f"GET {path.split('/')[-1]}: {who} → 403 naming {perm}",
                  f"{code}")
        code, _ = await api(U_ADMIN2, "GET", path)
        check(code == 200, f"GET {path.split('/')[-1]}: org admin → 200 on the identical request")
    if not e:
        check(False, "entries exist for the write gates")
        return
    body = {"field_key": "securities_lending", "value": False, "reason": "No lending in the member's account"}
    path = f"/api/v1/replication/registry/{S['entry']}/terms/changes"
    for uid, who in ((U_MEMBER, "member"), (U_STAFF, "staff")):
        code, out = await api(uid, "POST", path, body)
        check(code == 403 and "manage_replication" in str(out.get("detail")), f"POST changes: {who} → 403 naming "
              "manage_replication", f"{code}")
    code, out = await api(U_ADMIN1, "POST", path, body)
    check(code == 200, "POST changes: org admin → 200 on the identical request", f"{code}")
    if code == 200:
        vid = out["version"]["id"]
        apath = f"/api/v1/replication/registry/{S['entry']}/terms/{vid}/approve"
        code, out = await api(U_STAFF, "POST", apath, {})
        check(code == 403 and "approve_replication" in str(out.get("detail")),
              "POST approve: staff → 403 naming approve_replication", f"{code}")
        code, _ = await api(U_ADMIN2, "POST", apath, {})
        check(code == 200, "POST approve: org admin (not the maker) → 200 on the identical request", f"{code}")
    code, out = await api(U_SUPER, "GET", "/api/v1/replication/registry")
    check(code == 200 and out["permissions"]["can_write"] is True and out["permissions"]["is_super_admin"] is True,
          "Super Admin first: no replication grant, full envelope", f"{code}")
    code, out = await api(U_ADMIN1, "GET", f"/api/v1/replication/registry/{S['entry']}")
    if code == 200:
        check(out["permissions"]["can_write"] is True and isinstance(out["vocabularies"]["changeable"], list),
              "org admin's envelope: can_write true, field vocabularies from the server")
    code, out = await api(U_MEMBER, "GET", f"/api/v1/replication/registry/{S['entry']}")
    check(code == 403 and "rows" not in out, "a view-less caller gets no envelope at all (fails closed)")
    have = await platform(conn, "SELECT p.name FROM role_permissions rp JOIN roles r ON r.id = rp.role_id JOIN "
                                "permissions p ON p.id = rp.permission_id WHERE p.name = ANY($1::text[]) AND "
                                "r.name <> 'org_admin'", list(REPLICATION_PERMS))
    check(not have, "no role other than org_admin holds a replication permission (live grants)", f"{have[:3]}")
    find("org_admin default for NEW orgs", "the migration granted the three permissions to every EXISTING org_admin "
         "role; rbac.ensure_org_admin_role (an org's FIRST org_admin) still grants manage_org_settings only — re-run "
         "the docs/rep1_part1.sql §14 grant (idempotent) after creating a new org, or extend that helper in a "
         "sprint that owns it")


async def y_refresh(conn):
    section("[Y] Daily refresh — once per weekday after 11:00 UTC, idempotent, off switch, failure recorded, review task")
    from services import replica_schedule as RS
    from services.database import get_pool

    pool = await get_pool()
    real_active = RS.active_funds

    async def fixture_funds(p):
        return [f for f in await real_active(p) if f["fund_id"] == S.get("fund")]

    RS.active_funds = fixture_funds
    try:
        ev = lambda: platform(conn, "SELECT count(*) FROM portfolio.replica_refresh_events WHERE run_date >= "  # noqa
                                    "'2099-01-01'", fetch="fetchval")
        os.environ["REPLICA_HOLDINGS_AUTO_REFRESH"] = "0"
        r = await RS.maybe_refresh_holdings(pool, now_utc=DAY1, http_factory=mock_http, log=lambda m: None)
        check(r["action"] == "off" and await ev() == 0, "REPLICA_HOLDINGS_AUTO_REFRESH=0 → off, nothing claimed")
        os.environ.pop("REPLICA_HOLDINGS_AUTO_REFRESH", None)
        sat = datetime(2099, 1, 3, 12, 0, tzinfo=timezone.utc)
        early = datetime(2099, 1, 5, 10, 59, tzinfo=timezone.utc)
        r_sat = await RS.maybe_refresh_holdings(pool, now_utc=sat, http_factory=mock_http, log=lambda m: None)
        r_early = await RS.maybe_refresh_holdings(pool, now_utc=early, http_factory=mock_http, log=lambda m: None)
        check(r_sat["action"] == "not_due" and r_early["action"] == "not_due" and await ev() == 0,
              "Saturday and 10:59 UTC Monday → not due, nothing claimed")
        HTTP_STATE["issuer_file"] = FILE4
        snaps0 = await platform(conn, "SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE "
                                      "fund_global_security_id = $1", UUID(S["fund"]), fetch="fetchval")
        r1 = await RS.maybe_refresh_holdings(pool, now_utc=DAY1, http_factory=mock_http, log=lambda m: None)
        mine = [x for x in r1.get("results", []) if x["fund_id"] == S["fund"]]
        check(r1["action"] == "succeeded" and len(mine) == 1 and mine[0]["action"] == "created",
              "Monday 11:05 UTC: claims the day and ingests the active fund", f"{r1.get('action')} {mine}")
        r2 = await RS.maybe_refresh_holdings(pool, now_utc=DAY1.replace(hour=15), http_factory=mock_http,
                                             log=lambda m: None)
        snaps1 = await platform(conn, "SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE "
                                      "fund_global_security_id = $1", UUID(S["fund"]), fetch="fetchval")
        rows = await platform(conn, "SELECT phase, status FROM portfolio.replica_refresh_events WHERE run_date = $1",
                              DAY1.date())
        check(r2["action"] == "already" and snaps1 == snaps0 + 1 and sorted(r["phase"] for r in rows)
              == ["claim", "finish"], "a second tick the same day → 'already'; ONE snapshot; claim + finish rows",
              f"{snaps0}->{snaps1} {[dict(r) for r in rows]}")
        todos = await scoped(conn, ORG_A, "SELECT user_id FROM member_todos WHERE source = 'replica_holdings_review'")
        acts = await scoped(conn, ORG_A, "SELECT id FROM portfolio.replica_terms_actions WHERE action = "
                                         "'holdings_review'")
        check(len(todos) >= 1 and len(acts) >= 1 and await _action_logged(conn, ORG_A, str(acts[0]["id"]),
                                                                          "holdings_review"),
              "the change exceeded the threshold → a review task + action/event/audit in the holding org",
              f"{len(todos)} todos")
        HTTP_STATE["issuer_status"] = 500
        day2 = DAY1.replace(day=6)
        r3 = await RS.maybe_refresh_holdings(pool, now_utc=day2, http_factory=mock_http, log=lambda m: None)
        HTTP_STATE["issuer_status"] = 200
        fin = await platform(conn, "SELECT status, results FROM portfolio.replica_refresh_events WHERE run_date = $1 "
                                   "AND phase = 'finish'", day2.date(), fetch="fetchrow")
        res = json.loads(fin["results"]) if fin else []
        check(r3["action"] in ("failed", "partial") and fin is not None and fin["status"] == r3["action"]
              and any(x.get("action") == "failed" and "500" in x.get("error", "") for x in res),
              "an issuer failure is recorded per fund on the finish row; the tick does not raise",
              f"{r3['action']} {res[:1]}")
    finally:
        RS.active_funds = real_active
        os.environ.pop("REPLICA_HOLDINGS_AUTO_REFRESH", None)
    src = (API_DIR / "workflow_scheduler_tick.py").read_text(encoding="utf-8")
    check("maybe_refresh_holdings(pool)" in src and src.index("maybe_refresh_index(conn)")
          < src.index("maybe_refresh_holdings(pool)"),
          "wired into the cron entrypoint right after the weekday index refresh (bulkfetchui3's path)")


async def y_routes(conn):
    section("[Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and gb[k].get("after") != gn[k].get("after")]
    removed = [k for k in gb if k not in gn]
    check(len(gb) > 0 and not changed and not removed, "no existing route's gate changed or vanished",
          f"{changed[:5]} {removed[:5]}")
    # rep1's OWN routes by path — later sprints (rep2, rep3 …) add routes past BEFORE_REF, so a count of
    # everything new since the baseline stops describing rep1 the moment they merge
    reg = "/api/v1/replication/registry"
    rep1 = {(("GET",), reg): "view_replication", (("GET",), "/api/v1/replication/lookup"): "manage_replication",
            (("POST",), reg): "manage_replication", (("GET",), f"{reg}/{{entry_id}}"): "view_replication",
            (("POST",), f"{reg}/{{entry_id}}/holdings/refresh"): "manage_replication",
            (("POST",), f"{reg}/{{entry_id}}/terms/extract"): "manage_replication",
            (("POST",), f"{reg}/{{entry_id}}/terms/corrections"): "manage_replication",
            (("POST",), f"{reg}/{{entry_id}}/terms/changes"): "manage_replication",
            (("POST",), f"{reg}/{{entry_id}}/terms/{{version_id}}/approve"): "approve_replication",
            (("POST",), f"{reg}/{{entry_id}}/terms/{{version_id}}/reject"): "approve_replication"}
    missing = [k for k in rep1 if k not in gn or k in gb]
    check(len(rep1) == 10 and not missing and all(k[1].startswith("/api/v1/replication/") for k in rep1),
          "10 new routes, all under /replication", f"{missing[:5]}")
    check(all(k in gn and rep1[k] in str(gn[k].get("after")) for k in rep1),
          "every new route names its replication gate",
          f"{[(k[1], gn.get(k, {}).get('after')) for k in rep1 if not (k in gn and rep1[k] in str(gn[k].get('after')))][:3]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node tests (no write controls without the flags); node --test tests/; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/replication/"], WEB_DIR, timeout=300)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/replication passes",
          (out + err)[-300:] if code else "")
    for name in ("registry: no Add control unless the envelope says can_write === true",
                 "terms: view-only envelope renders no Correct / Change / Approve / Reject (and no Refresh)",
                 "approve / reject: only with approve_replication AND not a maker of the draft",
                 "menu: Portfolio → Replication is gated on view_replication (strict, Super Admin first)"):
        check(re.search(rf"^ok \d+ - {re.escape(name)}", out, re.M) is not None, f"node test: {name}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_underlyingresolution.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_underlyingresolution.py")], REPO, timeout=1200,
                             env=env)
    m = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(m) and m[-1][1] == "0" and code == 0, "verify_underlyingresolution.py: 0 FAIL",
          f"{m[-1] if m else 'no TOTAL'}; {fails[:3]} {err[-200:] if not m else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def guard_prefix(conn) -> bool:
    """The reserved prefix must match no REAL security before anything is
    deleted or seeded (a fixture name is the only thing it may match)."""
    n = await platform(conn, "SELECT count(*) FROM portfolio.securities_global_identifiers i JOIN "
                             "portfolio.securities_global s ON s.id = i.global_security_id WHERE i.id_value LIKE $1 "
                             "AND NOT (s.name = ANY($2::text[]))", f"{PREFIX}%", list(FUND_NAMES), fetch="fetchval")
    return check(n == 0, f"the reserved test prefix {PREFIX} matches no real security-master row", f"{n}")


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
    pre = real = None
    try:
        if not await guard_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("seed", seed, conn)
        await run("ingestion", y_ingest, conn)
        if S.get("fund"):
            await run("dry run", y_dry_run, conn)
            await run("extraction", y_extraction, conn)
            await run("edits", y_edits, conn)
            await run("maker-checker", y_maker_checker, conn)
            await run("isolation", y_isolation, conn)
            await run("gates", y_gates, conn)
            await run("refresh", y_refresh, conn)
        else:
            check(False, "ingestion created the fixture fund", "every fund-scoped section skipped")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
        try:
            from services.database import close_pool

            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            log = await teardown(conn)
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if real is not None:
                after = await real_fingerprints(conn)
                for k in real:
                    check(after[k] == real[k], f"real {k} unchanged (count + checksum)", f"{real[k][0]} rows")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
        finally:
            await conn.close()

    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        await run("regression", regression)
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
