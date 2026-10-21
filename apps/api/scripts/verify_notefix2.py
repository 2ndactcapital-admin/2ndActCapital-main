"""verify_notefix2.py — NOTEFIX2: the seven systematic errors found by
checking all 50 gold-1 notes against their own SEC filings, plus
second-reader findings surfaced in the review screen.

WRITTEN BY THE SPRINT. THE OPERATOR RUNS IT:

    python3 apps/api/scripts/verify_notefix2.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. Hydrates secrets from Doppler over HTTPS at startup
(_db_bootstrap). EVERY model call is mocked (services.note_extraction.proxy.chat
and .jev are replaced before any section runs); nothing is fetched from EDGAR
or R2. Real spend: $0.

FIXTURES — all tagged and all torn down at the start AND at the end:
  * one reference_filings row (accession prefix ACC_PREFIX, id in the 842f
    UUID block — a full UUID built from a counter, never a slice of a UUID)
    carrying a hand-built filing TEXT (no R2 read: the fixture FilingDocument
    is built directly with doc.text == doc.html, which is enough for every
    assertion here — locate_quote only reads doc.text/doc.index; raw HTML
    offsets are not exercised).
  * note_term_readings / note_extraction_runs tagged config->>'verify_tag' =
    TAG, run_kind 'verify' (the live CHECK's own allowed value — never
    invented). note_term_readings has an immutability trigger: the scoped
    delete disables/re-enables it in a savepoint and FAILS LOUDLY if rows
    remain.
  * a fixture org + one super-admin user and one non-super ('member') user,
    for the permission-gate proof (both ids, not slices, in the 842f block).

NEVER TORN DOWN — real reference data, seeded live by this sprint's own
migrations, not fixtures:
  * the 10 distribution_participants rows this sprint seeded
    (notefix2_affiliated_agents.sql).
  * the 237 portfolio.note_gold_second_reader rows this sprint's loader
    inserted from docs/gold/gold1_second_reader_v1.json.
This script only READS both, plus re-runs the loader (idempotent, so running
it again from this script is itself a real proof, not a side effect).

Teardown deletes in FK child-before-parent order, READ FROM pg_constraint
(contype read ::text). It runs at the START (in case a prior run left fixture
rows) and at the END, and re-reads every touched table to confirm its exact
pre-test count.

RLS: every read and write runs with app.is_super_admin set LOCAL inside a
transaction (the rls_* helpers). The connection's role is confirmed
rolbypassrls = False as its own assertion.

The nested verify_notefields.py, verify_goldset.py and verify_notefix1.py
regressions run ONCE each, as flat subprocesses, ONLY AFTER this script's own
teardown has completed and confirmed zero fixture rows remain.
"""
from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: in-process test identity only; set before main is imported

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

REPO = HERE.parents[3]
STATUS_DOC = REPO / "docs" / "PROJECT_STATUS.md"
NOTE_FIELDS_DOC = REPO / "docs" / "NOTE_FIELDS.md"
SECOND_READER_FILE = REPO / "docs" / "gold" / "gold1_second_reader_v1.json"

TAG = "notefix2-verify"
ACC_PREFIX = "9999999999-84-"          # no other verify uses -84- (grep'd: -55-, -58-, -61-, -72-, -77- are taken)
ORG = UUID("99000000-0000-0000-0000-0000842f0001")
U_SUPER = UUID("99000000-0000-0000-0000-0000842f0011")
U_MEMBER = UUID("99000000-0000-0000-0000-0000842f0012")
FIXTURE_USERS = [U_SUPER, U_MEMBER]
# auth0_sub MUST equal the literal "sub" the mocked token carries (str(uid), see
# y_permission_gate) — ensure_user's lookup (both the auth0_sub path and its
# UUID-id fallback) is RLS-scoped to rows whose own auth0_sub matches the
# caller's sub, so a mismatched auth0_sub silently misses even when id matches,
# falling through to auto-provisioning a fresh 'member' row instead.
USER_ROLE = {U_SUPER: "super_admin", U_MEMBER: "member"}
USER_EMAIL = {U_SUPER: "notefix2-super@test.local", U_MEMBER: "notefix2-member@test.local"}
FIXTURE_EMAIL_LIKE = "notefix2-%@test.local"
RUN_PURPOSE = "notefix2 integration fixture"


def filing_uuid(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-{0x842f1000 + n:012x}")


FID_MAIN = filing_uuid(1)

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} ({type(passed).__name__}), not a bool, for {label!r}")
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


# ═══ RLS-scoped DB helpers (verify_goldset.py / verify_notefix1.py pattern) ═
async def _super(conn) -> None:
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def rls_fetch(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetch(q, *a)


async def rls_val(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchval(q, *a)


async def rls_exec(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.execute(q, *a)


async def check_defs(conn, table: str) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = $1::regclass AND contype::text = 'c'", table)
    return {r["conname"]: r["def"] for r in rows}


def allowed(defs: dict[str, str], column: str) -> set[str]:
    out: set[str] = set()
    for d in defs.values():
        if re.search(rf"\(\(?{column}\b", d) and "ARRAY[" in d:
            out |= set(re.findall(r"'([^']+)'::text", d))
    return out


# ═══ Teardown — FK order from pg_constraint; counts as the backstop ═══════
TOUCHED = (
    "portfolio.note_term_readings", "portfolio.note_extraction_runs", "portfolio.reference_filings",
)
COUNTED = TOUCHED + ("public.users", "public.organizations",
                     "portfolio.distribution_participants", "portfolio.note_gold_second_reader")
IMMUTABLE = {"portfolio.note_term_readings"}


async def fixture_keys(conn) -> dict:
    runs = await rls_fetch(conn, "SELECT id FROM portfolio.note_extraction_runs WHERE config->>'verify_tag' = $1", TAG)
    filings = await rls_fetch(conn, "SELECT id FROM portfolio.reference_filings WHERE accession_number LIKE $1",
                              ACC_PREFIX + "%")
    return {"runs": [r["id"] for r in runs], "filings": [r["id"] for r in filings]}


def predicates(k: dict) -> dict[str, tuple[str, list]]:
    f, r = k["filings"], k["runs"]
    return {
        "portfolio.note_term_readings": ("reference_filing_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])", [f, r]),
        "portfolio.note_extraction_runs": ("id = ANY($1::uuid[])", [r]),
        "portfolio.reference_filings": ("id = ANY($1::uuid[])", [f]),
    }


async def delete_order(conn) -> list[str]:
    """TOUCHED in child-before-parent order, from pg_constraint's real FKs."""
    rows = await conn.fetch(
        """SELECT n1.nspname || '.' || c1.relname AS child, n2.nspname || '.' || c2.relname AS parent
             FROM pg_constraint k
             JOIN pg_class c1 ON c1.oid = k.conrelid JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
             JOIN pg_class c2 ON c2.oid = k.confrelid JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
            WHERE k.contype::text = 'f'""")
    tables = set(TOUCHED)
    children: dict[str, set[str]] = {t: set() for t in tables}
    for r in rows:
        child, parent = r["child"], r["parent"]
        if child in tables and parent in tables and child != parent:
            children[parent].add(child)
    order: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(t for t in remaining if not (children[t] & remaining))
        if not ready:
            raise RuntimeError(f"FK cycle among {sorted(remaining)}")
        order += ready
        remaining -= set(ready)
    return order


async def fixture_counts(conn) -> dict[str, int]:
    k = await fixture_keys(conn)
    out = {}
    for t, (where, args) in predicates(k).items():
        out[t] = await rls_val(conn, f"SELECT count(*) FROM {t} WHERE {where}", *args)
    out["public.users"] = await rls_val(
        conn,
        "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) "
        "OR email LIKE $3",
        FIXTURE_USERS, [str(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE)
    out["public.organizations"] = await rls_val(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG)
    return out


async def users_fk_children(conn) -> list[tuple[str, str]]:
    """(table, column) for every live FK pointing at public.users(id) — read
    from pg_constraint, same convention as delete_order. Self-referential FKs
    on users itself (e.g. invited_by) are excluded: a leaked fixture user is
    never legitimately the inviter/referrer of an unrelated real row, and
    cascading into other users() rows is not what "dependent rows" means here."""
    rows = await conn.fetch(
        """SELECT n.nspname || '.' || c.relname AS table_name, a.attname AS column_name
             FROM pg_constraint k
             JOIN pg_class c ON c.oid = k.conrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
             JOIN unnest(k.conkey) AS col(attnum) ON true
             JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = col.attnum
            WHERE k.contype::text = 'f' AND k.confrelid = 'public.users'::regclass""")
    return [(r["table_name"], r["column_name"]) for r in rows if r["table_name"] != "public.users"]


async def delete_fixture_users(conn) -> None:
    """Find fixture users by id, by auth0_sub, AND by email LIKE
    'notefix2-%@test.local' — a leaked auto-provisioned row (DB-generated id,
    auth0_sub == the mocked token's raw sub) matches by neither id nor the
    seeded auth0_sub, only by its email. Delete dependent rows first, then
    the user rows themselves."""
    rows = await rls_fetch(
        conn,
        "SELECT id FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) OR email LIKE $3",
        FIXTURE_USERS, [str(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE)
    uids = [r["id"] for r in rows]
    if not uids:
        return
    for table, column in await users_fk_children(conn):
        async with conn.transaction():
            await _super(conn)
            await conn.execute(f"DELETE FROM {table} WHERE {column} = ANY($1::uuid[])", uids)
    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", uids)


async def teardown(conn) -> None:
    k = await fixture_keys(conn)
    preds = predicates(k)
    order = await delete_order(conn)
    for t in order:
        where, args = preds[t]
        async with conn.transaction():
            await _super(conn)
            disabled = False
            if t in IMMUTABLE:
                try:
                    async with conn.transaction():          # savepoint: the app role may not own the table
                        await conn.execute(f"ALTER TABLE {t} DISABLE TRIGGER note_term_readings_no_update")
                    disabled = True
                except asyncpg.PostgresError:
                    disabled = False
            await conn.execute(f"DELETE FROM {t} WHERE {where}", *args)
            if disabled:
                await conn.execute(f"ALTER TABLE {t} ENABLE TRIGGER note_term_readings_no_update")
        if t in IMMUTABLE:
            left = await rls_val(conn, f"SELECT count(*) FROM {t} WHERE {where}", *args)
            if left:
                raise RuntimeError(f"{left} fixture rows REMAIN in {t} after the scoped delete")
    await delete_fixture_users(conn)
    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


async def table_counts(conn) -> dict[str, int]:
    return {t: await rls_val(conn, f"SELECT count(*) FROM {t}") for t in COUNTED}


# ═══ Mocks — installed before any section; real spend stays $0 ════════════
def install_mocks():
    from services.note_extraction import proxy

    async def no_chat(body, timeout=None):
        raise AssertionError("proxy.chat must never be called by this verify except inside the one "
                             "mocked integration fixture, which replaces it itself")

    async def no_jev(*a, **k):
        raise AssertionError("Jev must never be called by this verify")

    proxy.chat = no_chat
    proxy.jev = no_jev


def mock_deployment(name: str):
    from services.note_extraction import proxy
    return proxy.Deployment(name, None, name, None, 1e-7, 4e-7, None, True, None)


# ═══ Fixture setup ══════════════════════════════════════════════════════════
async def seed_users(conn) -> None:
    """Seed BOTH fixture users BEFORE any request reaches them. auth0_sub is the
    literal str(uid) — the exact "sub" the mocked token in y_permission_gate
    carries — not a derived string, because ensure_user's lookup is RLS-scoped
    to a row whose own auth0_sub matches the caller's sub."""
    defs = await check_defs(conn, "users")
    roles_ok = allowed(defs, "role")
    for role in USER_ROLE.values():
        check(not roles_ok or role in roles_ok, f"fixture users.role {role!r} satisfies the live CHECK",
              f"{sorted(roles_ok)}")
    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "NOTEFIX2 Verify Fixture Org", f"{ORG}-notefix2")
        for uid, role in USER_ROLE.items():
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""",
                uid, ORG, USER_EMAIL[uid], f"NOTEFIX2 fixture ({role})", str(uid), role)


# The combined integration fixture's filing text. Deliberately carries, in one
# note, the exact shapes from the real second-reader findings: a filing-own
# typo pricing-date sentence (note #1), an affiliated agent under a generic
# title (note #1), a junk merged-underlyings sentence, a per-period coupon
# amount that disagrees with the headline rate, an "up to" commission with no
# stated total, and a hypothetical-example section with a placeholder number.
FIXTURE_TEXT = """PRICING SUPPLEMENT

CUSIP No.: 06745QAB1

Pricing Date: January 28, 2019
Issue Date: January 31, 2019
Maturity Date: February 2, 2026

The estimated value of the notes on the Pricing Date, January 28, 2018 (the pricing date), was $985.00 per $1,000 principal amount.

Underlier: The S&P 500(R) Index (Bloomberg ticker: "SPX") and the Russell 2000(R) Index (Bloomberg ticker: "RTY")

Selling Agent: MLPF&S, an affiliate of BofA Finance Corporation (the "Issuer").

Threshold Value: 60% of the Starting Value.

If the Final Value of the Underlying is greater than or equal to its Threshold Value, you will receive the principal amount. If the Final Value of the Underlying is less than its Threshold Value, you will lose 1% of your principal for each 1% that the Underlying's Final Value is below its Starting Value.

Contingent Coupon Rate: 7.50% per annum, payable quarterly.

The agents will receive a commission of up to 2.00% of the principal amount.

Hypothetical Examples of Payments at Maturity

The following examples use a HYPOTHETICAL coupon rate of 12.00% per annum for illustration only and are not the actual Contingent Coupon Rate.

Hypothetical Underlying Return    Hypothetical Payment at Maturity
-40.01%                           $599.90
-20.00%                           $800.00
0.00%                             $1,000.00
20.00%                            $1,000.00
"""

# The gold-1-style WRONG pre-fix model answers (taken from the real
# second-reader findings, note #1 and note #6's shapes) — this is what the
# fixture proves the ENGINE now corrects or flags, not what a model "should"
# answer in general.
FIXTURE_MODEL_ANSWERS = {
    "pricing_date": {"value": "2018-01-28", "quote": "January 28, 2018 (the pricing date)"},
    "maturity_date": {"value": "2026-02-02", "quote": "Maturity Date: February 2, 2026"},
    "issue_date": {"value": "2019-01-31", "quote": "Issue Date: January 31, 2019"},
    "underlyings": {
        "value": [{"name": 'The S&P 500(R) Index (Bloomberg ticker: "SPX") and the Russell 2000(R) '
                            'Index (Bloomberg ticker: "RTY")'}],
        "quote": 'Underlier: The S&P 500(R) Index (Bloomberg ticker: "SPX") and the Russell 2000(R) '
                 'Index (Bloomberg ticker: "RTY")'},
    "distribution": {
        "value": [{"name": "MLPF&S", "role": "distribution_agent", "fee_min_pct": None, "fee_max_pct": None}],
        "quote": "Selling Agent: MLPF&S, an affiliate of BofA Finance Corporation"},
    "calculation_agent": {"value": "MLPF&S", "quote": "Selling Agent: MLPF&S"},
    "protection_type": {"value": "buffer", "quote": "lose 1% of your principal for each 1% that the "
                                                     "Underlying's Final Value is below its Starting Value"},
    "buffer_pct": {"value": 40, "quote": "Threshold Value: 60% of the Starting Value"},
    "barrier_pct": {"value": None, "quote": None},
    "coupon_rate_pa": {"value": 6.0, "quote": "Contingent Coupon Rate: 7.50% per annum"},
    "coupon_frequency": {"value": "quarterly", "quote": "payable quarterly"},
    "agent_commission_pct": {"value": {"min": None, "max": 2.0, "bound": "up_to"},
                             "quote": "a commission of up to 2.00%"},
    "total_fees_pct": {"value": None, "quote": None},
    "currency": {"value": None, "quote": None},
    "call_type": {"value": None, "quote": None},
    "coupon_memory": {"value": None, "quote": None},
    "coupon_type": {"value": "contingent", "quote": "Contingent Coupon Rate"},
    "observation_schedule": {
        "value": [{"observation_date": "2019-04-28", "coupon_barrier_pct": None, "coupon_amount": 18.75,
                   "call_level_pct": None, "call_amount": None}],
        "quote": None},
}


def install_fixture_chat():
    """Replace proxy.chat with a canned responder for THIS fixture's reader
    call only — installed by the integration section, removed (restored to
    the no-op refusal) immediately after."""
    from services.note_extraction import proxy

    async def fake_chat(body, timeout=None):
        system = body["messages"][0]["content"]
        keys = list(json.loads(system.split("JSON schema:\n", 1)[1])["properties"])
        out = {k: FIXTURE_MODEL_ANSWERS.get(k, {"value": None, "quote": None}) for k in keys}
        content = json.dumps(out)
        return proxy.ProxyResponse(
            200, {"model": body["model"], "choices": [{"message": {"content": content}}],
                  "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            content, {"x-litellm-attempted-fallbacks": "0"})

    proxy.chat = fake_chat


async def make_fixture_filing(conn) -> UUID:
    rf_form = allowed(await check_defs(conn, "portfolio.reference_filings"), "form_type")
    check(not rf_form or "424B2" in rf_form, "fixture reference_filings.form_type satisfies the live CHECK",
          f"{sorted(rf_form)}")
    async with conn.transaction():
        await _super(conn)
        await conn.execute(
            """INSERT INTO portfolio.reference_filings
                 (id, cik, filer_name, form_type, accession_number, filing_date, primary_document, source_url,
                  extraction_status)
               VALUES ($1, '70858', 'BofA Finance Corporation', '424B2', $2, CURRENT_DATE, 'fixture.htm',
                       'https://example.invalid/notefix2', 'pending')""",
            FID_MAIN, f"{ACC_PREFIX}000001")
    return FID_MAIN


async def create_verify_run(conn) -> str:
    from services.note_extraction import store
    return await store.create_run(conn, run_kind="verify", spend_cap_usd=1.0,
                                  config={"verify_tag": TAG, "purpose": RUN_PURPOSE})


# ═══ Sections ════════════════════════════════════════════════════════════
def y_task1_discovery() -> None:
    section("[Y] Task 1 — the six discovery findings are reported")
    text = STATUS_DOC.read_text(encoding="utf-8") if STATUS_DOC.exists() else ""
    start = text.find("NOTEFIX2")
    chunk = text[start:start + 12000] if start != -1 else ""
    heads = [h for h in ("**1a.**", "**1b.**", "**1c.**", "**1d.**", "**1e.**", "**1f.**") if h in chunk]
    check(len(heads) == 6, "docs/PROJECT_STATUS.md's NOTEFIX2 section reports 1a through 1f",
          f"found {heads}")
    check("map_terms" in chunk and "08da83c9" in chunk,
          "1a: the EdgarTools junk-underlying root cause names the real note it was found on")
    check("0 rows live" in chunk or "0 rows" in chunk,
          "1b: distribution_participants' empty-table starting state is recorded")
    check("tenor_months" in chunk and "2018" in chunk and "2019" in chunk,
          "1c: the gold-1 tenor_months typo-date mechanism is documented")
    check("hypothetical_span" in chunk,
          "1d: the widened hypothetical-section span capture is documented")
    check("json_schema" in chunk or "embedded in the system message" in chunk,
          "1e: how registry descriptions reach the reader prompt is documented")
    check("payoff_matches_examples" in chunk,
          "1f: the pre-existing payoff self-check is documented as the Task 7 starting point")

    nf = NOTE_FIELDS_DOC.read_text(encoding="utf-8") if NOTE_FIELDS_DOC.exists() else ""
    check("notefix2" in nf.lower(), "docs/NOTE_FIELDS.md records the notefix2 changes too (kept in sync)")


async def y_f1_affiliated_agents(conn) -> None:
    section("[Y] F1 — affiliated distribution agents")
    from services.note_extraction import participants

    parts = await rls_fetch(conn, "SELECT id, canonical_name, participant_type, aliases, "
                                  "affiliated_issuer_group, status, observed_count "
                                  "FROM portfolio.distribution_participants WHERE status <> 'retired' "
                                  "ORDER BY canonical_name")
    parts = [dict(r) for r in parts]
    check(len(parts) >= 10, "at least the 10 F1 participants are seeded live", f"{len(parts)} rows")
    affiliates = [p for p in parts if p["participant_type"] == "issuer_affiliate"]
    check(all(p.get("affiliated_issuer_group") for p in affiliates),
          "every seeded issuer_affiliate carries affiliated_issuer_group", f"{len(affiliates)} affiliates")

    groups = await rls_fetch(conn, "SELECT DISTINCT issuer_group FROM portfolio.structured_note_issuers")
    live_groups = {r["issuer_group"] for r in groups}
    mismatched = [p["canonical_name"] for p in affiliates if p["affiliated_issuer_group"] not in live_groups]
    check(not mismatched, "every affiliated_issuer_group matches a live structured_note_issuers.issuer_group",
          f"mismatched: {mismatched}")

    mlpfs = next((p for p in parts if p["canonical_name"].startswith("Merrill Lynch")), None)
    check(mlpfs is not None and "MLPF&S" in (mlpfs.get("aliases") or []),
          "Merrill Lynch, Pierce, Fenner & Smith Incorporated is seeded with alias MLPF&S")

    bofas_member = [{"name": "BofAS", "role": "distribution_agent", "fee_max_pct": 3.25}]
    matched, unmatched = participants.match_distribution(bofas_member, parts)
    resolved = participants.resolve_distribution_roles(matched, "Bank of America")
    check(bool(resolved) and resolved[0]["role"] == "issuer_affiliated_agent",
          "F1: a BofA note listing 'BofAS' as distribution_agent resolves to issuer_affiliated_agent",
          f"{resolved}")
    check(resolved[0].get("role_source") == "rule" and resolved[0].get("role_corrected_from") == "distribution_agent",
          "the correction is recorded as a rule correction, naming the reader's original (wrong) role")

    unaffiliated_issuer = participants.resolve_distribution_roles(matched, "JPMorgan")
    check(unaffiliated_issuer[0]["role"] == "distribution_agent",
          "the SAME participant on a DIFFERENT issuer's note is left exactly as the reader answered it "
          "(an affiliate of BofA is not an affiliate of every issuer)")

    unknown = [{"name": "Totally Unknown Regional Dealer LLC", "role": "dealer"}]
    _m2, u2 = participants.match_distribution(unknown, parts)
    check(len(u2) == 1 and u2[0]["name"] == "Totally Unknown Regional Dealer LLC",
          "an unknown dealer is reported in unmatched, never silently dropped")

    cal = participants.calculation_agent_affiliation("MS & Co.", "Morgan Stanley", parts)
    check(cal is not None and cal["affiliated"] is True,
          "calculation_agent is matched the same way: MS & Co. on a Morgan Stanley note is affiliated")
    cal_unaffiliated = participants.calculation_agent_affiliation("MS & Co.", "Barclays", parts)
    check(cal_unaffiliated is not None and cal_unaffiliated["affiliated"] is False,
          "the same calculation agent on a DIFFERENT issuer's note is not flagged affiliated")


def y_f2_list_hygiene() -> None:
    section("[Y] F2 — list hygiene drops junk, keeps real members")
    from services.note_extraction import rules

    members = [
        {"name": "NDX", "ticker": "NDX", "initial_level": 13112.65},
        {"name": "SPX", "ticker": "SPX", "initial_level": 3750.77},
        {"name": "CUSIP"},
        {"name": "Barrier Value"},
        {"name": "The closing level of the underlying on the final valuation date"},
        {"name": "The Nasdaq-100 Index, the Russell 2000 Index and the iShares 20+ Year Treasury Bond ETF"},
    ]
    kept, dropped = rules.filter_list_members("underlyings", members)
    kept_names = sorted(m["name"] for m in kept)
    check(kept_names == ["NDX", "SPX"], "real members (NDX 13,112.65, SPX 3,750.77) are kept",
          f"kept: {kept_names}")
    check(len(dropped) == 4, "all 4 junk members are dropped (table header x2, definition sentence, "
          "one-sentence list)", f"dropped: {len(dropped)}")
    for d in dropped:
        check(bool(d.get("drop_reason")), f"dropped member {d.get('name')!r} carries its reason",
              d.get("drop_reason", ""))

    dist_junk = [{"name": "Each dealer participating in the offering of the notes through the agent"}]
    _k, d2 = rules.filter_list_members("distribution", dist_junk)
    check(len(d2) == 1, "the same hygiene applies to distribution members (a sentence, not a participant name)")


def y_f2_f7_hypothetical_rejection() -> None:
    section("[Y] F2/F7 — a reading quoted ONLY from inside a hypothetical section is rejected")
    from services.note_extraction import compare as cmp, documents, rules, schema

    rr = rules.RuleResult()
    rules.find_hypothetical_rows(FIXTURE_TEXT, rr)
    span = rr.extras.get("hypothetical_span")
    check(span is not None, "a hypothetical_span extra is recorded even though this is a real worked "
          "numeric table (span capture no longer depends on row-parsing success)")
    rows = rr.extras.get("hypothetical_rows")
    check(rows is not None and len(rows.value) == 4, "the filing's real hypothetical rows still parse",
          f"{len(rows.value) if rows else 0} rows")

    doc = documents.FilingDocument(reference_filing_id="fixture", html=FIXTURE_TEXT, text=FIXTURE_TEXT,
                                   segments=[], encoding="utf-8")
    specs_by_key = {"coupon_rate_pa": schema.FieldSpec("coupon_rate_pa", "Coupon", schema.KIND_NUMBER, "desc")}

    inside_quote = "a HYPOTHETICAL coupon rate of 12.00% per annum"
    e_rejected = cmp.evidence(doc, specs_by_key["coupon_rate_pa"], "model_1", 12.0, inside_quote,
                              hypothetical_span=span.value)
    check(e_rejected.value is None and bool(e_rejected.dropped)
          and e_rejected.dropped[0].get("drop_reason") == "hypothetical_section",
          "a coupon_rate_pa quoted only from inside the hypothetical section is rejected",
          f"{e_rejected.dropped}")

    outside_quote = "Contingent Coupon Rate: 7.50% per annum, payable quarterly"
    e_kept = cmp.evidence(doc, specs_by_key["coupon_rate_pa"], "model_1", 7.5, outside_quote,
                          hypothetical_span=span.value)
    check(e_kept.value == 7.5 and not e_kept.dropped,
          "the SAME field quoted from OUTSIDE the section is kept", f"{e_kept.value}")

    underlyings_spec = schema.FieldSpec("underlyings", "Underlyings", schema.KIND_LIST, "desc", value_shape="list")
    members = [{"name": "NDX", "initial_level": 26000}, {"name": "RTY", "initial_level": 2500}]
    e_list = cmp.evidence(doc, underlyings_spec, "model_1", members, inside_quote, hypothetical_span=span.value)
    stripped_levels = [m.get("initial_level") for m in (e_list.value or [])]
    check(e_list.value is not None and all(v is None for v in stripped_levels),
          "underlyings.initial_level is stripped (not the whole list) when quoted from inside the section",
          f"{e_list.value}")
    check(all(m.get("name") for m in (e_list.value or [])),
          "the member NAMES survive the strip — only initial_level is suspect")


async def y_f3_buffer_barrier(conn, specs) -> None:
    section("[Y] F3 — buffer vs barrier field definitions and the extended payoff check")
    from services.note_extraction import checks
    from services.note_extraction.schema import KIND_NUMBER, assert_no_shared_protection_field

    by_key = {s.key: s for s in specs}
    bar, buf = by_key.get("barrier_pct"), by_key.get("buffer_pct")
    check(bar is not None and "threshold value" in bar.description.lower()
          and "trigger buffer level" in bar.description.lower(),
          "barrier_pct's live description covers 'threshold value' (BofA) and 'trigger buffer level' "
          "(Goldman) as real filing aliases")
    check(buf is not None and "buffer rate" in buf.description.lower() and "111.11" in buf.description,
          "buffer_pct's live description explicitly rules out the buffer RATE (111.11% etc.)")
    assert_no_shared_protection_field(specs)
    check(True, "assert_no_shared_protection_field passes even with the quoted alias "
          "'trigger buffer level' inside barrier_pct's own description")

    mismarked = {"protection_type": "buffer", "buffer_pct": 60}
    rows = [{"underlying_return_pct": -40.01, "payment_per_1000": 599.90}]
    f1 = checks.payoff_matches_at_protection_levels(mismarked, rows)
    check(f1 is not None, "a 'Threshold Value 60%' note still marked protection_type='buffer' is flagged "
          "by the extended payoff check", f"{f1.reason if f1 else None}")

    corrected = {"protection_type": "barrier", "barrier_pct": 60}
    f2 = checks.payoff_matches_at_protection_levels(corrected, rows)
    check(f2 is None, "the SAME level correctly marked 'barrier' does not fire", f"{f2}")

    bar_spec = by_key.get("barrier_pct")
    check(bar_spec is not None and bar_spec.kind == KIND_NUMBER, "barrier_pct stays a scalar number")


def y_f4_digital_payouts() -> None:
    section("[Y] F4 — digital payouts never become participation or cap")
    from services.note_extraction import readers

    few_shot = readers.FEW_SHOT
    check("Digital Percentage: 40.00%" in few_shot and "1400" in few_shot,
          "the few-shot block carries the real Barclays digital example with its correct value")
    check("never upside participation" in few_shot.lower() or "never a digital" in few_shot.lower(),
          "the few-shot block states the participation/digital distinction explicitly")


def y_f5_defaults() -> None:
    section("[Y] F5 — blank-field defaults, each named, never overriding a stated value")
    from services.note_extraction import derive

    c = derive.default_currency(None)
    check(bool(c is not None and c.value == "USD" and c.basis), "currency defaults to USD when unresolved",
          f"{c.basis if c else None}")
    check(bool(derive.default_currency("EUR") is None),
          "a EUR note stays EUR — the default never overrides a stated value")

    ct_none = derive.default_call_type(None, False)
    check(bool(ct_none is not None and ct_none.value == "none"),
          "call_type defaults to 'none' when no call/redemption wording exists anywhere in the filing")
    check(bool(derive.default_call_type(None, True) is None),
          "call_type is NOT defaulted when call/redemption wording DOES exist (ambiguous, not absent)")
    check(bool(derive.default_call_type("issuer", False) is None),
          "call_type is never overridden when the filing actually states one")

    cm = derive.default_coupon_memory("contingent", None, False)
    check(bool(cm is not None and cm.value is False),
          "coupon_memory defaults to false for a contingent coupon with no memory wording")
    check(bool(derive.default_coupon_memory("contingent", None, True) is None),
          "coupon_memory is NOT defaulted when memory/accrual wording DOES exist")
    check(bool(derive.default_coupon_memory("fixed", None, False) is None),
          "coupon_memory is never defaulted for a fixed (non-contingent) coupon")

    sched = [{"observation_date": "2019-04-28", "call_level_pct": None, "call_amount": None},
            {"observation_date": "2020-01-31", "call_level_pct": 100.0, "call_amount": None}]
    ncm = derive.no_call_months("2019-01-31", sched)
    check(bool(ncm is not None and ncm.value == 12),
          "no_call_months derives from the first schedule date that actually checks a call", f"{ncm}")
    check(bool(derive.no_call_months("2019-01-31", [{"observation_date": "2019-04-28",
                                                "call_level_pct": None, "call_amount": None}]) is None),
          "no_call_months is None when no schedule row checks a call at all")


def y_f6_fee_totals() -> None:
    section("[Y] F6 — fee totals: derived from a lone commission, or proposed on a failed reconcile")
    from services.note_extraction import checks, derive

    commission = {"min": None, "max": 3.75, "bound": "up_to"}
    d = derive.default_total_fees(None, commission, None, None)
    check(d is not None and d.value == {"min": None, "max": 3.75, "bound": "up_to"},
          "total_fees_pct derives from the stated commission when it is the ONLY fee component", f"{d.value if d else None}")
    check(derive.default_total_fees(None, commission, {"min": 1.0, "max": 1.0, "bound": "exact"}, None) is None,
          "the default DECLINES to guess when a second fee component is independently stated")
    check(derive.default_total_fees({"min": 9.26, "max": 9.26, "bound": "exact"}, commission, None, None) is None,
          "the default never overrides an already-stated total_fees_pct")

    values = {"price_to_public_pct": 100, "total_fees_pct": {"min": 9.26, "max": 9.26, "bound": "exact"},
             "proceeds_to_issuer_pct": {"min": 96.25, "max": 96.25, "bound": "exact"}}
    f = checks.fees_reconcile(values)
    check(f is not None and "proposed_total_fees_pct" in (f.detail or {}),
          "a stated total that fails price - fees ~= proceeds proposes the corrected total",
          f"{f.detail if f else None}")
    if f:
        proposed = f.detail["proposed_total_fees_pct"]
        check(abs(float(proposed["min"]) - 3.75) < 0.01 and abs(float(proposed["max"]) - 3.75) < 0.01,
              "the proposed total equals price - proceeds (3.75%), matching the real 3.75% commission",
              f"{proposed}")


def y_f7_wrong_reading_numbers() -> None:
    section("[Y] F7 — numbers lifted from the wrong reading")
    from services.note_extraction import checks, compare as cmp, documents, schema

    values = {"coupon_rate_pa": 6.0, "coupon_frequency": "quarterly",
             "observation_schedule": [{"observation_date": "2019-04-28", "coupon_amount": 18.75}]}
    f = checks.coupon_cross_check(values)
    check(f is not None and "7.5" in f.reason,
          "coupon 6% vs a stated $18.75 quarterly on $1,000 -> needs_review naming 7.50%", f"{f.reason if f else None}")
    check(checks.coupon_cross_check({**values, "coupon_rate_pa": 7.5}) is None,
          "the same schedule with the CORRECT 7.5% headline rate does not fire")

    bad = checks.maturity_after_pricing({"pricing_date": "2019-01-28", "maturity_date": "2018-01-28"})
    check(bad is not None, "a maturity BEFORE pricing is flagged", f"{bad.reason if bad else None}")
    check(checks.maturity_after_pricing({"pricing_date": "2019-01-28", "maturity_date": "2026-02-02"}) is None,
          "a real, later maturity does not fire")

    doc = documents.FilingDocument(reference_filing_id="fixture", html="x", text="x", segments=[], encoding="utf-8")
    maturity_spec = schema.FieldSpec("maturity_date", "Maturity", schema.KIND_DATE, "desc")
    e = cmp.evidence(doc, maturity_spec, "model_1", "2019-01-28", "x", reject_if_equals="2019-01-28")
    check(e.value is None and bool(e.dropped), "a maturity_date LITERALLY equal to pricing_date is rejected "
          "at the evidence level, before it can even become a candidate", f"{e.dropped}")


def y_readers_prompt() -> None:
    section("[Y] Task 6 — the reader prompt carries the new definitions and the few-shot block")
    from services.note_extraction import readers

    tokens = readers.few_shot_token_estimate()
    print(f"  FEW_SHOT token estimate: {tokens} (chars={len(readers.FEW_SHOT)})")
    check(tokens < 1500, "the few-shot block stays under ~1,500 tokens", f"{tokens}")
    for marker in ("Threshold Value 60%", "buffer rate, which equals approximately 111.11%",
                  "Digital Percentage: 40.00%", "issuer_affiliated_agent"):
        check(marker in readers.FEW_SHOT, f"the few-shot block contains: {marker!r}")
    messages, _ = readers.build_messages([], "FILING TEXT", filer="x", form_type="424B2")
    system = messages[0]["content"]
    check(readers.FEW_SHOT in system, "build_messages actually splices FEW_SHOT into the system prompt")


async def y_integration_fixture(conn, specs, fid) -> None:
    section("[Y] Integration — one fixture note through prefill_note, persisted and read back")
    from services.note_extraction import participants, prefill, readers, store
    from services.note_extraction.documents import FilingDocument

    doc = FilingDocument(reference_filing_id=str(fid), html=FIXTURE_TEXT, text=FIXTURE_TEXT, segments=[],
                         encoding="utf-8", form_type="424B2", filer_name="BofA Finance Corporation",
                         cik="70858", filing_date=None, accession_number=f"{ACC_PREFIX}000001")

    parts = await rls_fetch(conn, "SELECT id, canonical_name, participant_type, aliases, "
                                  "affiliated_issuer_group, status, observed_count "
                                  "FROM portfolio.distribution_participants WHERE status <> 'retired'")
    parts = [dict(r) for r in parts]
    groups_rows = await rls_fetch(conn, "SELECT filer_cik, issuer_group FROM portfolio.structured_note_issuers")
    issuer_groups = {r["filer_cik"]: r["issuer_group"] for r in groups_rows}
    check(issuer_groups.get("70858") == "Bank of America", "the fixture's CIK resolves to a real issuer_group",
          f"{issuer_groups.get('70858')}")

    run_id = await create_verify_run(conn)
    install_fixture_chat()
    try:
        cfg = readers.ReaderConfig(slot="model_1", deployment="gpt-5-mini", max_tokens=32000)
        catalog = {"gpt-5-mini": mock_deployment("gpt-5-mini")}
        note = await prefill.prefill_note(doc, specs, cfg, catalog=catalog, spend=None, run_id=run_id,
                                          participant_rows=parts, issuer_groups=issuer_groups)
    finally:
        install_mocks()   # restore the raising stub immediately

    check(note.status == "ok", "the fixture note's reader call completed", f"{note.status} {note.error}")
    check(note.input_disagreements == ["pricing_date"],
          "pricing_date is left UNRESOLVED (rules read 2019-01-28, the mocked model read the filing's own "
          "2018 typo sentence) rather than trusting either reading alone", f"{note.input_disagreements}")
    check(note.values.get("pricing_date") is None, "pricing_date is absent from the resolved values")
    check(note.values.get("tenor_months") is None,
          "tenor_months is correctly left null + needs_review (its input disagreed) rather than computing "
          "96.18 months from the wrong 2018 date — the exact gold-1 note #1 bug")

    dist = note.values.get("distribution") or []
    check(bool(dist) and dist[0]["role"] == "issuer_affiliated_agent",
          "MLPF&S, read by the model as 'distribution_agent', resolves to issuer_affiliated_agent", f"{dist}")
    check(dist[0].get("role_corrected_from") == "distribution_agent",
          "the correction records what the reader originally said")
    cal_aff = note.calculation_agent_affiliation
    check(cal_aff is not None and cal_aff["affiliated"] is True,
          "calculation_agent (also MLPF&S) is flagged as an issuer affiliate", f"{cal_aff}")

    check(note.values.get("underlyings") is None,
          "the merged-sentence underlyings reading is dropped by list hygiene before it can resolve")
    dropped_readings = [r for r in note.readings if r.metadata.get("dropped_members")]
    check(any(r.field_key == "underlyings" for r in dropped_readings),
          "the drop is recorded on the persisted reading's own metadata, not just in memory")

    check(note.values.get("currency") == "USD", "currency defaulted to USD")
    check(note.values.get("call_type") == "none", "call_type defaulted to 'none' (no call wording in the filing)")
    check(note.values.get("coupon_memory") is False, "coupon_memory defaulted to false")
    check(note.values.get("total_fees_pct") == {"min": None, "max": 2.0, "bound": "up_to"},
          "total_fees_pct derived from the lone 'up to 2.00%' commission", f"{note.values.get('total_fees_pct')}")

    cc = next((f for f in note.self_checks if f["check"] == "coupon_cross_check"), None)
    check(cc is not None, "the coupon cross-check fires end-to-end on the fixture's 6%-vs-$18.75-quarterly note",
          f"{cc['reason'] if cc else None}")

    await store.insert_readings(conn, note.readings)
    persisted = await rls_fetch(conn, "SELECT field_key, source, origin, run_id FROM portfolio.note_term_readings "
                                      "WHERE run_id = $1", UUID(run_id))
    check(len(persisted) == len(note.readings), "every reading the test writes is actually persisted",
          f"{len(persisted)} of {len(note.readings)}")
    check(all(str(r["run_id"]) == run_id for r in persisted), "every persisted reading carries the test's run_id")
    check(all(r["origin"] == "cascade" for r in persisted),
          "every persisted reading carries origin 'cascade' — the live CHECK's own allowed value")


async def y_second_reader(conn) -> None:
    section("[Y] Task 8 — second-reader findings, loaded and surfaced")
    from load_second_reader_findings import load as load_second_reader, rows_from_file
    from services.note_extraction import gold

    check(SECOND_READER_FILE.exists(), "docs/gold/gold1_second_reader_v1.json is present")
    rows = rows_from_file(SECOND_READER_FILE, source="gold1_second_reader_v1")
    check(len(rows) == 237, "the source file parses to 237 findings", f"{len(rows)}")

    before = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_second_reader "
                                 "WHERE source = 'gold1_second_reader_v1'")
    result = await load_second_reader(conn, rows)
    after = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_second_reader "
                                "WHERE source = 'gold1_second_reader_v1'")
    check(after == 237, "the live table holds exactly the 237 real findings", f"{after}")
    check(result["inserted"] == 0, "re-running the loader against the SAME file inserts 0 new rows (idempotent)",
          f"{result}")
    check(before == after, "row count is unchanged by the idempotent re-run", f"{before} -> {after}")

    fid_with_findings = await rls_val(conn, "SELECT reference_filing_id FROM portfolio.note_gold_second_reader "
                                            "WHERE severity = 'critical' LIMIT 1")
    check(fid_with_findings is not None, "a real note with a critical second-reader finding exists live")
    if fid_with_findings:
        findings = await gold.second_reader_for(conn, fid_with_findings)
        check(len(findings) > 0, "gold.second_reader_for returns findings for that note", f"{len(findings)}")
        counts = await gold.second_reader_counts(conn, [str(fid_with_findings)])
        c = counts.get(str(fid_with_findings))
        check(c is not None and c["critical"] >= 1, "gold.second_reader_counts reports its critical count",
              f"{c}")
        check(result["attempted"] == 237, "the loader's own report matches the source file's finding count")


async def y_permission_gate(conn) -> None:
    section("[Y] Permission gate on the review API's second-reader data — identical request, two callers")
    fid = await rls_val(conn, "SELECT reference_filing_id FROM portfolio.note_gold_second_reader LIMIT 1")
    if fid is None:
        check(False, "a real note with second-reader data exists to test the gate against")
        return
    try:
        import main
        from starlette.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        check(False, "FastAPI app imports for the TestClient check", f"{type(exc).__name__}: {exc}")
        return

    path = f"/api/v1/admin/note-extraction/gold/notes/{fid}"
    with TestClient(main.app, raise_server_exceptions=False) as c:
        main.verify_token = lambda _token: {"sub": str(U_MEMBER), "email": USER_EMAIL[U_MEMBER]}
        r_member = c.get(path, headers={"Authorization": "Bearer stub"})
        check(r_member.status_code == 403, "a non-super-admin ('member') caller is refused on the review "
              "API's second-reader-carrying endpoint", f"got {r_member.status_code}: {r_member.text[:200]}")

        main.verify_token = lambda _token: {"sub": str(U_SUPER), "email": USER_EMAIL[U_SUPER]}
        r_super = c.get(path, headers={"Authorization": "Bearer stub"})
        check(r_super.status_code == 200, "the SAME request is admitted for a super admin",
              f"got {r_super.status_code}: {r_super.text[:200]}")
        if r_super.status_code == 200:
            body = r_super.json()
            check("second_reader" in body and "by_field" in (body.get("second_reader") or {}),
                  "the super admin's response carries second_reader.by_field", f"{list(body.keys())}")
            check((body.get("second_reader") or {}).get("total_count", 0) > 0,
                  "the response's second_reader.total_count is real, not zero",
                  f"{(body.get('second_reader') or {}).get('total_count')}")


# ═══ Main ══════════════════════════════════════════════════════════════════
async def main_async() -> int:
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    conn = await asyncpg.connect(url, statement_cache_size=0)
    install_mocks()
    pre_counts = None
    sgnt_before = None
    try:
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the verify connection's role does NOT bypass RLS",
              f"{await conn.fetchval('SELECT current_user')}")
        try:
            await teardown(conn)
        except Exception as exc:  # noqa: BLE001
            check(False, "start-of-run teardown", f"{type(exc).__name__}: {exc}")
        left0 = await fixture_counts(conn)
        check(sum(left0.values()) == 0, "no fixture rows before the run", json.dumps(left0))
        pre_counts = await table_counts(conn)
        sgnt_before = await rls_val(conn, "SELECT count(*) FROM portfolio.securities_global_note_terms")

        from services.note_extraction.schema import load_specs
        specs = await load_specs(conn)
        check(len(specs) > 0, "the live registry yields field specs", f"{len(specs)}")

        async def run(name, fn, *a):
            try:
                res = fn(*a)
                if asyncio.iscoroutine(res):
                    res = await res
                return res
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")
                return None

        await run("task1 discovery", y_task1_discovery)
        await run("f1 affiliated agents", y_f1_affiliated_agents, conn)
        await run("f2 list hygiene", y_f2_list_hygiene)
        await run("f2/f7 hypothetical rejection", y_f2_f7_hypothetical_rejection)
        await run("f3 buffer vs barrier", y_f3_buffer_barrier, conn, specs)
        await run("f4 digital payouts", y_f4_digital_payouts)
        await run("f5 defaults", y_f5_defaults)
        await run("f6 fee totals", y_f6_fee_totals)
        await run("f7 wrong-reading numbers", y_f7_wrong_reading_numbers)
        await run("readers prompt", y_readers_prompt)

        await run("seed users", seed_users, conn)
        fid = await run("make fixture filing", make_fixture_filing, conn)
        if fid:
            await run("integration fixture", y_integration_fixture, conn, specs, fid)

        await run("second reader", y_second_reader, conn)
        await run("permission gate", y_permission_gate, conn)

        section("[Y] securities_global_note_terms untouched")
        sgnt_after = await rls_val(conn, "SELECT count(*) FROM portfolio.securities_global_note_terms")
        check(sgnt_before is not None and sgnt_after == sgnt_before,
              "securities_global_note_terms row count unchanged", f"{sgnt_before} -> {sgnt_after}")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        section("[Y] Teardown: zero fixture rows; seeded affiliates and second-reader rows REMAIN; every "
                "touched table at its pre-test count otherwise")
        teardown_clean = False
        try:
            await teardown(conn)
            left = await fixture_counts(conn)
            teardown_clean = sum(left.values()) == 0
            check(teardown_clean, "zero fixture rows remain", json.dumps(left))
            if pre_counts is not None:
                post = await table_counts(conn)
                diff = {t: (pre_counts[t], post[t]) for t in COUNTED if pre_counts[t] != post[t]}
                check(not diff, "every touched table is back at its exact pre-test count "
                      "(distribution_participants and note_gold_second_reader included, unchanged "
                      "because they were only READ, never written by teardown)", json.dumps(diff))
                teardown_clean = teardown_clean and not diff
            else:
                check(False, "pre-test counts were taken")
                teardown_clean = False
        except Exception as exc:  # noqa: BLE001
            check(False, "end-of-run teardown", f"{type(exc).__name__}: {exc}")
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        await conn.close()

        if teardown_clean:
            await y_regression()
        else:
            check(False, "regression skipped: this run's own teardown did not leave zero fixture rows")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def y_regression() -> None:
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run: regression section skipped (parent runs it)")
        return
    section("[Y] Regression: verify_notefields.py, verify_goldset.py and verify_notefix1.py, "
            "run ONCE each as flat subprocesses, AFTER this script's own teardown")
    for name in ("verify_notefields.py", "verify_goldset.py", "verify_notefix1.py"):
        try:
            proc = subprocess.run([sys.executable, str(HERE.parent / name)], capture_output=True,
                                  text=True, timeout=600, cwd=str(REPO), env=os.environ | {"VERIFY_NESTED": "1"})
            out = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as exc:
            out = f"TIMEOUT after 600s: {(exc.stdout or b'')[-300:]!r}"
        m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
        check(m is not None and int(m.group(1)) > 0 and int(m.group(2)) == 0,
              f"{name} still reports 0 FAIL", m.group(0) if m else out[-300:])


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
