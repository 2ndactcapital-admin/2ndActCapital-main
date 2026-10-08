"""verify_goldset.py — GOLD SET: the stratified plan, propose with trap cases, the
gpt-5-mini pre-fill, the review API on the v3 registry, and the scorer.

WRITTEN BY THE SPRINT, RUN BY THE OPERATOR:

    python3 apps/api/scripts/verify_goldset.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. Hydrates secrets from Doppler over HTTPS at startup
(_db_bootstrap). EVERY model call is mocked (services.note_extraction.proxy.chat
and .jev are replaced before any section runs) and every R2 read is mocked
(documents._download serves the fixture HTML); nothing is fetched from EDGAR.
Real spend: $0.

FIXTURES — all tagged and all torn down at the start AND at the end:
  * edgar_index_filings with accession prefix ACC_PREFIX. Each points at a REAL
    issuer CIK of one of the seven banks (read live), so no issuer row is
    written.
  * reference_filings with ids in the 601d UUID block (full UUIDs, made from a
    counter — never a slice of a UUID).
  * edgar_cohorts named TAG...; their members go by ON DELETE CASCADE. A sealed
    cohort's trigger refuses a direct member delete, so the members are never
    deleted directly.
  * note_gold_candidates, note_gold_values, readings, runs and staging on the
    fixture filings / runs. Runs carry config->>'verify_tag' = TAG.
  * two platform_model_catalog rows (FIXTURE_MODELS).
  * a fixture org with a super admin and an org admin (users, roles, user_roles).
Teardown deletes in FK child-before-parent order, READ FROM pg_constraint. It
then re-reads every touched table and compares it with its pre-test count.
note_term_readings has an immutability trigger: around the scoped delete the
script tries DISABLE/ENABLE TRIGGER (in a savepoint, because the app role may
not own the table). It FAILS LOUDLY if any fixture reading remains.

RLS: every read and write runs with app.is_super_admin set LOCAL inside a
transaction (rls_* helpers). A read without it would silently return nothing.
Every fixture value is checked against the table's live CHECK constraints
(pg_constraint, contype read ::text) before it is written.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
DISCOVERY_DOC = REPO / "docs" / "GOLDSET_DISCOVERY.md"
STATUS_DOC = REPO / "docs" / "PROJECT_STATUS.md"
INDEX_MIGRATION = API_DIR / "migrations" / "goldset_drop_edgar_index_filings_indexes.sql"

TAG = "goldset-verify"
ACC_PREFIX = "9999999999-61-"          # no other verify uses -61- (grep'd)
SEED = 610061
BATCH_1 = f"{TAG}-batch-1"
BATCH_2 = f"{TAG}-batch-2"
PUBLIC_MODEL = "goldset-verify-public-model"
UNPRICED_MODEL = "goldset-verify-unpriced-model"
FIXTURE_MODELS = [PUBLIC_MODEL, UNPRICED_MODEL]
ORG = UUID("99000000-0000-0000-0000-0000601d0001")
U_SUPER = UUID("99000000-0000-0000-0000-0000601d0011")
U_ORGADMIN = UUID("99000000-0000-0000-0000-0000601d0012")
FIXTURE_USERS = [U_SUPER, U_ORGADMIN]
HEADERS = {"Authorization": "Bearer verify-token"}
SEVEN = ("JPMorgan", "Morgan Stanley", "UBS", "Goldman Sachs", "Citigroup", "Bank of America", "Barclays")


def filing_uuid(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-{0x601d1000 + n:012x}")


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


# ═══ RLS-scoped DB helpers ═════════════════════════════════════════════════
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


async def rls_row(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchrow(q, *a)


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


def _sub(uid: UUID) -> str:
    return f"goldset_{uid.hex}"


# ═══ Teardown — FK order from pg_constraint; counts as the backstop ═══════
TOUCHED = (
    "portfolio.note_gold_values", "portfolio.note_extraction_staged_fields", "portfolio.note_extraction_staging",
    "portfolio.note_term_readings", "portfolio.note_gold_candidates", "portfolio.note_extraction_runs",
    "portfolio.edgar_cohort_members", "portfolio.edgar_cohorts", "portfolio.edgar_index_filings",
    "portfolio.reference_filings", "public.platform_model_catalog",
)
COUNTED = TOUCHED + ("public.users", "public.organizations", "public.roles", "public.user_roles")
CASCADE_ONLY = {"portfolio.edgar_cohort_members"}   # deleted by ON DELETE CASCADE from edgar_cohorts
IMMUTABLE = {"portfolio.note_term_readings"}


async def fixture_keys(conn) -> dict:
    runs = await rls_fetch(conn, "SELECT id FROM portfolio.note_extraction_runs WHERE config->>'verify_tag' = $1", TAG)
    cohorts = await rls_fetch(conn, "SELECT id FROM portfolio.edgar_cohorts WHERE name LIKE $1", TAG + "%")
    filings = await rls_fetch(conn, "SELECT id FROM portfolio.reference_filings WHERE accession_number LIKE $1",
                              ACC_PREFIX + "%")
    return {"runs": [r["id"] for r in runs], "cohorts": [r["id"] for r in cohorts],
            "filings": [r["id"] for r in filings]}


def predicates(k: dict) -> dict[str, tuple[str, list]]:
    f, r, c = k["filings"], k["runs"], k["cohorts"]
    staging = ("SELECT id FROM portfolio.note_extraction_staging "
               "WHERE reference_filing_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])")
    return {
        "portfolio.note_gold_values": ("reference_filing_id = ANY($1::uuid[])", [f]),
        "portfolio.note_extraction_staged_fields": (f"staging_id IN ({staging})", [f, r]),
        "portfolio.note_extraction_staging": ("reference_filing_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])",
                                              [f, r]),
        "portfolio.note_term_readings": ("reference_filing_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])", [f, r]),
        "portfolio.note_gold_candidates": ("reference_filing_id = ANY($1::uuid[])", [f]),
        "portfolio.note_extraction_runs": ("id = ANY($1::uuid[])", [r]),
        "portfolio.edgar_cohort_members": ("cohort_id = ANY($1::uuid[])", [c]),
        "portfolio.edgar_cohorts": ("id = ANY($1::uuid[])", [c]),
        "portfolio.edgar_index_filings": ("accession_number LIKE $1", [ACC_PREFIX + "%"]),
        "portfolio.reference_filings": ("id = ANY($1::uuid[])", [f]),
        "public.platform_model_catalog": ("model_id = ANY($1::text[])", [FIXTURE_MODELS]),
    }


async def delete_order(conn) -> list[str]:
    """TOUCHED in child-before-parent order, from pg_constraint's FKs."""
    rows = await conn.fetch(
        """SELECT n1.nspname || '.' || c1.relname AS child, n2.nspname || '.' || c2.relname AS parent
             FROM pg_constraint k
             JOIN pg_class c1 ON c1.oid = k.conrelid JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
             JOIN pg_class c2 ON c2.oid = k.confrelid JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
            WHERE k.contype::text = 'f'""")
    # edgar_cohort_members goes by ON DELETE CASCADE when its cohort is deleted
    # (the frozen trigger refuses a direct delete), so its FKs bind the COHORT:
    # a cohort must go before any table its members reference. The fixture
    # manifest rows' own FK to edgar_cohorts (selected_by_cohort_id) is cleared
    # by teardown before any delete, so that edge is dropped.
    tables = set(TOUCHED) - CASCADE_ONLY
    children: dict[str, set[str]] = {t: set() for t in tables}
    for r in rows:
        child, parent = r["child"], r["parent"]
        if child in CASCADE_ONLY:
            child = "portfolio.edgar_cohorts"
        if (child, parent) == ("portfolio.edgar_index_filings", "portfolio.edgar_cohorts"):
            continue
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
    out["public.users"] = await rls_val(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) "
                                              "OR auth0_sub = ANY($2::text[])", FIXTURE_USERS,
                                        [_sub(u) for u in FIXTURE_USERS])
    out["public.organizations"] = await rls_val(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG)
    # readings the review path writes have run_id NULL — scoped by fixture filing above
    return out


async def teardown(conn) -> None:
    k = await fixture_keys(conn)
    preds = predicates(k)
    order = await delete_order(conn)
    await rls_exec(conn, "UPDATE portfolio.edgar_index_filings SET selected_by_cohort_id = NULL "
                         "WHERE accession_number LIKE $1 AND selected_by_cohort_id IS NOT NULL", ACC_PREFIX + "%")
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
                    disabled = False                        # the trigger is BEFORE UPDATE: DELETE is unaffected
            await conn.execute(f"DELETE FROM {t} WHERE {where}", *args)
            if disabled:
                await conn.execute(f"ALTER TABLE {t} ENABLE TRIGGER note_term_readings_no_update")
        if t in IMMUTABLE:
            left = await rls_val(conn, f"SELECT count(*) FROM {t} WHERE {where}", *args)
            if left:
                raise RuntimeError(f"{left} fixture rows REMAIN in {t} after the scoped delete")
    subs = [_sub(u) for u in FIXTURE_USERS]
    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM audit_log WHERE org_id = $1 OR user_id = ANY($2::uuid[])", ORG, FIXTURE_USERS)
        await conn.execute("DELETE FROM assistant_activities WHERE org_id = $1 OR user_id = ANY($2::uuid[])",
                           ORG, FIXTURE_USERS)
        await conn.execute("DELETE FROM agent_proposals WHERE org_id = $1 OR proposed_by = ANY($2::uuid[])",
                           ORG, FIXTURE_USERS)
        await conn.execute("DELETE FROM user_roles WHERE user_id = ANY($1::uuid[]) "
                           "OR role_id IN (SELECT id FROM roles WHERE org_id = $2)", FIXTURE_USERS, ORG)
        await conn.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE org_id = $1)", ORG)
        await conn.execute("DELETE FROM roles WHERE org_id = $1", ORG)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[])",
                           FIXTURE_USERS, subs)
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


async def table_counts(conn) -> dict[str, int]:
    return {t: await rls_val(conn, f"SELECT count(*) FROM {t}") for t in COUNTED}


# ═══ Mocks — installed before any section; real spend stays $0 ═════════════
CHAT_BODIES: list[dict] = []
READER_ANSWER: dict = {}
HTML_BY_KEY: dict[str, str] = {}


def install_mocks():
    from services.note_extraction import documents, proxy

    async def fake_chat(body, timeout=None):
        CHAT_BODIES.append(json.loads(json.dumps(body, default=str)))
        system = body["messages"][0]["content"]
        keys = list(json.loads(system.split("JSON schema:\n", 1)[1])["properties"])
        out = {k: READER_ANSWER.get(k, {"value": None, "quote": None}) for k in keys}
        content = json.dumps(out)
        return proxy.ProxyResponse(
            200, {"model": body["model"], "choices": [{"message": {"content": content}}],
                  "usage": {"prompt_tokens": 1000, "completion_tokens": 500}},
            content, {"x-litellm-attempted-fallbacks": "0"})

    async def no_jev(body, timeout=None):
        raise AssertionError("Jev must never be called by the gold pre-fill")

    def fake_download(r2_key, bucket):
        if r2_key not in HTML_BY_KEY:
            raise LookupError(f"verify: no fixture object at {r2_key}")
        return HTML_BY_KEY[r2_key].encode("utf-8")

    proxy.chat = fake_chat
    proxy.jev = no_jev
    documents._download = fake_download


def deployment(name: str, priced: bool):
    from services.note_extraction import proxy
    return proxy.Deployment(name, None, name, None, 1e-7 if priced else None, 4e-7 if priced else None,
                            None, True, None)


# ═══ Fixture filings ═══════════════════════════════════════════════════════
BASE_HTML = """<html><body>
<p>Pricing Supplement dated March 27, 2026</p>
<p>GOLDSET VERIFY Notes linked to the GSV Index</p>
<p>Pricing Date: March 27, 2026</p>
<p>Maturity Date: March 29, 2027</p>
<p>Buffer Amount: 15%</p>
<p>Alpha Securities LLC is the agent for the notes and receives a commission of $15.00 per $1,000 note.</p>
<p>TRAP_SLOT</p>
</body></html>"""
Q_PRICING = "Pricing Date: March 27, 2026"
Q_MATURITY = "Maturity Date: March 29, 2027"
Q_BUFFER = "Buffer Amount: 15%"
Q_DIST = "Alpha Securities LLC is the agent for the notes and receives a commission of $15.00 per $1,000 note."

TRAP_SNIPPETS = {
    "worst_of": "The payment at maturity depends on the Least Performing Underlying of the three.",
    "daily_or_continuous_barrier": "If the closing level on any trading day during the Monitoring Period is below the Trigger.",
    "issuer_call": "We may redeem the notes, in whole but not in part, at our option on any Call Date.",
    "digital_fixed_payout": "You will receive the Digital Return of 8.00% if the Final Level is at or above the Initial Level.",
    "buffer_vs_barrier": "The Downside Threshold is 70% of the Initial Value.",
    "price_return_underlying": "The Index is a price return index and does not reflect dividends paid on its components.",
    "up_to_commission": "The agent will receive a commission of up to $25.00 per $1,000 principal amount note.",
    "named_distribution_agent": "InspereX LLC will act as distribution agent for the notes.",
    "hypothetical_example_table": "Hypothetical Payments at Maturity\n-10.00% $1,000.00\n-30.00% $850.00\n20.00% $1,200.00",
}
CLEAN_SNIPPET = ("The notes pay interest at 5.00% per annum, payable semi-annually, and repay principal at "
                 "maturity. Minimum denominations of $1,000.")


def html_for(tag: str | None) -> str:
    snippet = TRAP_SNIPPETS[tag] if tag else CLEAN_SNIPPET
    return BASE_HTML.replace("<p>TRAP_SLOT</p>", "".join(f"<p>{line}</p>" for line in snippet.split("\n")))


# ═══ App calls (httpx.ASGITransport on THIS loop) ══════════════════════════
async def api(uid, method, path, body=None):
    import httpx
    import main

    sub = _sub(uid)
    main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": str(ORG)}
    kw = {"headers": HEADERS}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify") as client:
        res = await client.request(method, path, **kw)
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def _detail(body) -> str:
    return str(body.get("detail", body))[:200] if isinstance(body, dict) else str(body)[:200]


async def seed_users(conn) -> None:
    from services.rbac import ensure_role, grant_org_admin

    defs = await check_defs(conn, "users")
    roles_ok = allowed(defs, "role")
    check(not roles_ok or {"super_admin", "member"} <= roles_ok, "fixture users.role values satisfy the live CHECK",
          f"{sorted(roles_ok)}")
    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "GOLDSET Verify Fixture Org", f"{ORG}-goldset")
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""", uid, ORG, f"{sub}@test.local", sub, sub, role)
        member_role = await ensure_role(conn, ORG, "member", "goldset fixture: no permissions")
        await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                           U_SUPER, member_role)
        await grant_org_admin(conn, U_ORGADMIN, ORG)


# ═══ Sections ══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — the four discovery findings are reported")
    from services.note_extraction import traps

    text = DISCOVERY_DOC.read_text(encoding="utf-8") if DISCOVERY_DOC.exists() else ""
    heads = [h for h in ("## 1a.", "## 1b.", "## 1c.", "## 1d.") if h in text]
    check(len(heads) == 4, "docs/GOLDSET_DISCOVERY.md reports 1a, 1b, 1c and 1d", f"found {heads}")
    listed = set(re.findall(r"^\| `([a-z_]+)` \|", text, re.M))
    check(bool(listed) and listed == set(traps.DETECTORS),
          "1d: the doc's detector table lists exactly the code's trap tags", f"doc {sorted(listed)}")
    router = (API_DIR / "routers/note_extraction_admin.py").read_text()
    blocks = [b for b in router.split("@router.")[1:] if b.split("(", 1)[1].startswith('"/admin/note-extraction/gold')]
    all_gold = blocks
    gold_routes = []
    for b in blocks:
        body = b.split("):\n", 1)[1] if "):\n" in b else ""      # after the (possibly multi-line) signature
        first = body.strip().splitlines()[0] if body.strip() else ""
        if "_require_super_admin(request)" in first:
            gold_routes.append(b.split('"')[1])
    check(bool(all_gold) and len(gold_routes) == len(all_gold),
          "1a: every gold route checks _require_super_admin FIRST (the same gate as B1)",
          f"{len(gold_routes)} of {len(all_gold)}")
    scoring_src = (API_DIR / "services/note_extraction/scoring.py").read_text()
    check("normalize(spec, gold_value)" in scoring_src and "normalize(spec, run_value)" in scoring_src,
          "1b: the scorer compares through the cascade's own schema.normalize")
    uq = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint WHERE conrelid = 'portfolio.note_gold_candidates'::regclass "
        "AND contype::text = 'u' AND pg_get_constraintdef(oid) = 'UNIQUE (reference_filing_id)'")
    check(uq == 1, "1c: note_gold_candidates is UNIQUE on reference_filing_id (one batch per filing), live")
    groups = {r["issuer_group"] for r in await rls_fetch(
        conn, "SELECT DISTINCT issuer_group FROM portfolio.structured_note_issuers WHERE issuer_group = ANY($1::text[])",
        list(SEVEN))}
    check(groups == set(SEVEN), "1c: all seven banks exist as structured_note_issuers.issuer_group, by these names",
          f"missing {sorted(set(SEVEN) - groups)}")
    kinds = allowed(await check_defs(conn, "portfolio.edgar_index_filings"), "document_kind")
    check("pricing_supplement" in kinds and "preliminary_pricing_supplement" in kinds,
          "1c: document_kind distinguishes a final from a preliminary pricing supplement (live CHECK)")


async def y_index_migration(conn):
    section("[Y] The index-drop migration exists and is idempotent")
    sql = INDEX_MIGRATION.read_text() if INDEX_MIGRATION.exists() else ""
    stmts = [s.strip() for s in re.sub(r"--[^\n]*", "", sql).split(";") if s.strip()]
    check(len(stmts) == 2 and all(s.upper().startswith("DROP INDEX IF EXISTS") for s in stmts)
          and {"portfolio.edgar_index_filings_form_date_idx", "portfolio.edgar_index_filings_status_idx"}
          == {s.split()[-1] for s in stmts},
          "the migration is exactly two DROP INDEX IF EXISTS statements naming the two dropped indexes", f"{stmts}")
    ok = True
    err = ""
    for _ in range(2):
        try:
            await conn.execute(sql)
        except Exception as exc:  # noqa: BLE001
            ok, err = False, f"{type(exc).__name__}: {exc}"
    check(ok, "it runs twice cleanly", err)
    idx = {r["indexname"] for r in await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'portfolio' AND tablename = 'edgar_index_filings'")}
    check(not ({"edgar_index_filings_form_date_idx", "edgar_index_filings_status_idx"} & idx),
          "neither dropped index exists live")
    check({"edgar_index_filings_form_sort_idx", "edgar_index_filings_queue_idx"} <= idx,
          "the wider indexes that cover them are still there", f"{sorted(idx)}")


async def seed_manifest(conn) -> dict:
    """Fixture manifest rows: 3 per (bank, era) + decoys. Returns the layout."""
    from services.note_extraction import gold_set

    defs = await check_defs(conn, "portfolio.edgar_index_filings")
    statuses = allowed(defs, "pipeline_status")
    check({"selected", "not_selected", "discovered", "ready_for_extraction", "not_pricing_supplement"} <= statuses,
          "fixture pipeline_status values satisfy the live CHECK", f"{sorted(statuses)}")
    policy = await rls_val(conn, "SELECT version FROM portfolio.edgar_selection_policies WHERE is_active "
                                 "ORDER BY version DESC LIMIT 1")
    check(policy is not None, "an active selection policy exists to stamp fixture rows", f"{policy}")
    ciks = {}
    for b in SEVEN + ("Wells Fargo",):
        ciks[b] = await rls_val(conn, "SELECT filer_cik FROM portfolio.structured_note_issuers WHERE issuer_group = $1 "
                                      "ORDER BY (filer_role = 'issuer') DESC, filer_cik LIMIT 1", b)
    check(all(ciks.values()), "each bank (and the decoy Wells Fargo) has a real issuer CIK", json.dumps(ciks))
    rows, n = [], 0
    layout = {"stratified": [], "decoys": {}}
    for b in SEVEN:
        for e, y0, _y1 in gold_set.ERAS:
            for k in range(3):
                n += 1
                acc = f"{ACC_PREFIX}{n:06d}"
                d = date(y0 + (k % 2), 3, 2 + k)
                rows.append((acc, "424B2", d, "selected", policy, ciks[b]))
                layout["stratified"].append((acc, b, e))
    decoys = {
        "form_424B3": ("424B3", date(2023, 5, 1), "selected", policy, ciks["JPMorgan"]),
        "not_selected": ("424B2", date(2023, 5, 2), "not_selected", policy, ciks["JPMorgan"]),
        "discovered": ("424B2", date(2023, 5, 3), "discovered", None, ciks["JPMorgan"]),
        "other_bank": ("424B2", date(2023, 5, 4), "selected", policy, ciks["Wells Fargo"]),
        "pre_2019": ("424B2", date(2018, 6, 1), "selected", policy, ciks["JPMorgan"]),
    }
    for name, (form, d, st, pol, cik) in decoys.items():
        n += 1
        acc = f"{ACC_PREFIX}{n:06d}"
        rows.append((acc, form, d, st, pol, cik))
        layout["decoys"][name] = acc
    async with conn.transaction():
        await _super(conn)
        await conn.executemany(
            """INSERT INTO portfolio.edgar_index_filings
                 (accession_number, form_type, filing_date, index_quarter, submission_path, filer_count,
                  pipeline_status, selection_policy_version, primary_issuer_cik)
               VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $8)""",
            [(acc, form, d, f"{d.year}Q{(d.month - 1) // 3 + 1}", f"edgar/data/{cik}/{acc}.txt", st, pol, cik)
             for acc, form, d, st, pol, cik in rows])
    layout["all"] = [r[0] for r in rows]
    return layout


async def y_plan(conn, layout) -> str | None:
    section("[Y] --plan: 28 strata, equal allocation, seeded, selected 424B2s only")
    import build_gold_set
    from services.note_extraction import gold_set

    size = 2 * len(gold_set.strata())      # per stratum 2, each stratum has 3 available
    p1 = await gold_set.plan_members(conn, size=size, seed=SEED, accessions=layout["all"])
    p2 = await gold_set.plan_members(conn, size=size, seed=SEED, accessions=layout["all"])
    members = p1["members"]
    check(len(members) > 0, "the plan is non-empty", f"{len(members)}")
    per = Counter(st for _a, st in members)
    check(len(gold_set.strata()) == 28 and len(per) == 28,
          "28 strata (7 banks x 4 eras), every one represented", f"{len(per)}")
    check(set(per.values()) == {p1["per_stratum"]} and p1["per_stratum"] == size // 28,
          "equal allocation: every stratum gets exactly ceil(size/28)", f"{sorted(set(per.values()))}")
    accs = [a for a, _ in members]
    check(len(accs) == len(set(accs)), "no accession appears twice")
    check(not (set(layout["decoys"].values()) & set(accs)),
          "decoys excluded: a 424B3, a not_selected, a discovered (no policy), a non-top-7 bank, a pre-2019 filing")
    reread = await rls_fetch(conn, "SELECT accession_number, form_type, pipeline_status, selection_policy_version "
                                   "FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])", accs)
    check(len(reread) == len(accs) and all(r["form_type"] == "424B2" and r["selection_policy_version"] is not None
                                           and r["pipeline_status"] not in ("discovered", "not_selected")
                                           for r in reread),
          "every member is a SELECTED 424B2 (re-read from the manifest)")
    by_acc = {a: (b, e) for a, b, e in layout["stratified"]}
    check(all(gold_set.stratum_label(*by_acc[a]) == st for a, st in members),
          "each member's stratum label is its own bank x era")
    check(p1["members"] == p2["members"], "the same seed gives the same cohort (identical, in order)")
    p3 = await gold_set.plan_members(conn, size=size, seed=SEED + 1, accessions=layout["all"])
    find("a different seed", f"{'a different' if p3['members'] != members else 'the SAME (possible on 3-per-stratum fixtures)'} member list")
    short = await gold_set.plan_members(conn, size=4 * 28, seed=SEED, accessions=layout["all"])
    check(len(short["shortfall"]) == 28 and all(v == 1 for v in short["shortfall"].values())
          and len(short["members"]) == 3 * 28,
          "a size the pool cannot fill reports the shortfall per stratum and is not padded",
          f"{len(short['members'])} members, {len(short['shortfall'])} short strata")

    created = await gold_set.create_plan(conn, name=f"{TAG} plan", size=size, seed=SEED, accessions=layout["all"])
    cid = created["cohort_id"]
    row = await rls_row(conn, "SELECT kind, definition, member_count, sealed_at FROM portfolio.edgar_cohorts WHERE id = $1",
                        cid)
    d = json.loads(row["definition"]) if row and isinstance(row["definition"], str) else (row["definition"] if row else {})
    kinds = allowed(await check_defs(conn, "portfolio.edgar_cohorts"), "kind")
    check(row is not None and row["kind"] in kinds and row["sealed_at"] is not None,
          "the plan is a sealed (frozen) cohort with a kind the live CHECK allows", f"{row['kind'] if row else None}")
    check(d.get("seed") == SEED and d.get("per_stratum") == size // 28 and d.get("strata") == 28
          and d.get("banks") == list(SEVEN) and len(d.get("eras") or []) == 4,
          "the definition records the seed, banks, eras and per-stratum count", json.dumps(d)[:200])
    url = os.environ["DATABASE_URL"]
    c2 = await asyncpg.connect(url, statement_cache_size=0)
    try:
        stored = [(r["accession_number"], r["stratum"]) for r in await rls_fetch(
            c2, "SELECT accession_number, stratum FROM portfolio.edgar_cohort_members WHERE cohort_id = $1 "
                "ORDER BY position", cid)]
    finally:
        await c2.close()
    check(bool(stored) and stored == members, "the stored members equal the plan (independent re-read)",
          f"{len(stored)} stored")
    before = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_cohorts WHERE name LIKE $1", TAG + "%")
    rc = await build_gold_set.do_plan(conn, argparse.Namespace(name=f"{TAG} plan", size=size, seed=SEED, dry_run=False))
    after = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_cohorts WHERE name LIKE $1", TAG + "%")
    check(rc == 0 and after == before, "re-running --plan with the same name writes nothing", f"{before} -> {after}")
    return cid


async def fetch_cohort(conn, cid, layout) -> dict:
    """Simulate the pipeline's fetch on the plan cohort: per bank, one 2019-20
    member is a PRELIMINARY (fetched, not final); the rest are final pricing
    supplements with a stored document carrying a trap snippet."""
    from services.note_extraction import gold_set, traps

    defs = await check_defs(conn, "portfolio.edgar_index_filings")
    kinds = allowed(defs, "document_kind")
    check({"pricing_supplement", "preliminary_pricing_supplement"} <= kinds,
          "fixture document_kind values satisfy the live CHECK")
    rf_status = allowed(await check_defs(conn, "portfolio.reference_filings"), "extraction_status")
    check(not rf_status or "pending" in rf_status, "fixture reference_filings.extraction_status satisfies the live CHECK",
          f"{sorted(rf_status)}")
    members = [(r["accession_number"], r["stratum"]) for r in await rls_fetch(
        conn, "SELECT accession_number, stratum FROM portfolio.edgar_cohort_members WHERE cohort_id = $1 "
              "ORDER BY position", cid)]
    by_acc = {a: (b, e) for a, b, e in layout["stratified"]}
    tags = list(traps.DETECTORS)
    out = {"finals": {}, "prelims": [], "unfetched": []}
    prelim_banks = set()
    i = 0
    async with conn.transaction():
        await _super(conn)
        for n, (acc, _st) in enumerate(members, start=1):
            bank, era = by_acc[acc]
            fid = filing_uuid(n)
            d = await conn.fetchval("SELECT filing_date FROM portfolio.edgar_index_filings WHERE accession_number = $1",
                                    acc)
            r2_key = f"verify/goldset/{fid}.htm"
            is_prelim = era == gold_set.ERAS[0][0] and bank not in prelim_banks
            tag = None if is_prelim else tags[i % len(tags)]
            HTML_BY_KEY[r2_key] = html_for(tag)
            await conn.execute(
                """INSERT INTO portfolio.reference_filings
                     (id, cik, filer_name, form_type, accession_number, filing_date, primary_document, source_url,
                      r2_key, extraction_status)
                   VALUES ($1, '0', $2, '424B2', $3, $4, 'fixture.htm', 'https://example.invalid/goldset', $5, 'pending')""",
                fid, f"GOLDSET VERIFY {bank}", acc, d, r2_key)
            if is_prelim:
                prelim_banks.add(bank)
                await conn.execute(
                    """UPDATE portfolio.edgar_index_filings SET pipeline_status = 'not_pricing_supplement',
                              document_kind = 'preliminary_pricing_supplement', reference_filing_id = $2,
                              fetched_at = now() WHERE accession_number = $1""", acc, fid)
                out["prelims"].append(str(fid))
            else:
                i += 1
                await conn.execute(
                    """UPDATE portfolio.edgar_index_filings SET pipeline_status = 'ready_for_extraction',
                              document_kind = 'pricing_supplement', reference_filing_id = $2,
                              fetched_at = now() WHERE accession_number = $1""", acc, fid)
                out["finals"][str(fid)] = {"bank": bank, "era": era, "year": d.year, "tag": tag, "html": html_for(tag)}
    return out


async def y_propose(conn, cid, fetched) -> list[str]:
    section("[Y] --propose: fetched finals only, minimums met, shortfall reported, no re-proposal")
    from services.note_extraction import documents, gold, gold_set, traps

    base_tags = traps.detect(documents.document_from_html(html_for(None)).text)
    check(base_tags == [], "precondition: the fixture filing without a trap snippet fires no detector", f"{base_tags}")
    finals = fetched["finals"]
    pool_n = len(finals)
    per_bank_pool = Counter(v["bank"] for v in finals.values())
    target = min(pool_n - 4, 7 * gold_set.MIN_PER_BANK + 3)
    check(pool_n > 0 and target >= 7 * gold_set.MIN_PER_BANK and min(per_bank_pool.values()) >= gold_set.MIN_PER_BANK,
          "the fixture pool allows every minimum", f"pool {pool_n}, target {target}, per bank {dict(per_bank_pool)}")
    res = await gold_set.propose(conn, cid, target=target, batch=BATCH_1, seed=SEED)
    chosen = res["chosen"]
    ids = {n.reference_filing_id for n in chosen}
    check(len(chosen) == target and res["inserted"] == target, "the target is met and every proposal is written",
          f"{len(chosen)} chosen, {res['inserted']} inserted")
    check(bool(ids) and ids <= set(finals), "only FETCHED FINAL pricing supplements are proposed",
          f"{len(ids - set(finals))} outside the finals")
    check(not (ids & set(fetched["prelims"])), "no fetched preliminary is proposed")
    check(res["pool_counts"]["excluded"].get("document_kind preliminary_pricing_supplement", 0) == len(fetched["prelims"]),
          "the preliminaries are counted as excluded, by document_kind", json.dumps(res["pool_counts"]["excluded"]))
    by_bank = Counter(n.bank for n in chosen)
    check(all(by_bank[b] >= gold_set.MIN_PER_BANK for b in SEVEN), f"at least {gold_set.MIN_PER_BANK} per bank",
          json.dumps(dict(by_bank)))
    by_st = Counter((n.bank, n.era) for n in chosen)
    check(all(by_st[(b, e)] >= 1 for b in SEVEN for e, _a, _z in gold_set.ERAS), "at least 1 per era per bank")
    tagc = Counter(t for n in chosen for t in n.trap_tags)
    check(all(tagc[t] >= traps.TRAP_MINIMUMS[t] for t in traps.DETECTORS), "at least 3 notes per trap tag",
          json.dumps(dict(tagc)))
    s = res["shortfalls"]
    check(not s["per_bank"] and not s["per_stratum"] and not s["per_trap"] and s["total"]["missing"] == 0,
          "no shortfall is reported when the pool allows every minimum", json.dumps(s)[:200])
    rows = await rls_fetch(conn, "SELECT reference_filing_id, sample_batch, issuer_group, filing_year, product_type, "
                                 "trap_tags, status FROM portfolio.note_gold_candidates WHERE sample_batch = $1", BATCH_1)
    statuses = allowed(await check_defs(conn, "portfolio.note_gold_candidates"), "status")
    ok = len(rows) == target
    bad = []
    for r in rows:
        f = finals.get(str(r["reference_filing_id"]))
        text = documents.document_from_html(f["html"]).text if f else ""
        if not f or r["issuer_group"] != f["bank"] or r["filing_year"] != f["year"] \
                or list(r["trap_tags"]) != traps.detect(text) or r["product_type"] != gold.product_type(text) \
                or r["status"] != "proposed" or r["status"] not in statuses:
            bad.append(str(r["reference_filing_id"]))
    check(ok and not bad, "each candidate records issuer_group, filing_year, product_type and trap_tags (re-read)",
          f"{len(rows)} rows, {len(bad)} mismatched")

    # A second batch over the same cohort: only the leftovers remain -> shortfall, no padding, no re-proposal.
    left = pool_n - target
    res2 = await gold_set.propose(conn, cid, target=target, batch=BATCH_2, seed=SEED)
    ids2 = {n.reference_filing_id for n in res2["chosen"]}
    check(res2["pool_counts"]["already_in_a_gold_batch"] == target,
          "filings already in another batch are excluded from the pool", f"{res2['pool_counts']['already_in_a_gold_batch']}")
    check(not (ids2 & ids), "no filing from batch 1 is proposed again in batch 2")
    check(len(ids2) == left and res2["inserted"] == left and res2["shortfalls"]["total"]["missing"] == target - left,
          "a pool that cannot meet the target proposes only what it has and REPORTS the shortfall (no padding)",
          f"{len(ids2)} proposed of {target}; missing {res2['shortfalls']['total']['missing']}")
    check(bool(res2["shortfalls"]["per_bank"]), "...including per-bank shortfalls",
          ", ".join(res2["shortfalls"]["per_bank"])[:200])
    again = await gold_set.write_proposals(conn, [n for n in chosen][:1], BATCH_2)
    check(again == 0, "a direct re-write of a batch-1 filing into batch 2 inserts nothing (UNIQUE + ON CONFLICT)")
    n1 = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_candidates WHERE sample_batch = $1", BATCH_1)
    check(n1 == target, "batch 1 is unchanged by the second propose", f"{n1}")
    rep = await gold_set.batch_report(conn, BATCH_1)
    check(rep["notes"] == target and sum(sum(r.values()) for r in rep["bank_x_era"].values()) == target
          and rep["status"] == {"proposed": target},
          "--report covers bank x era, trap tags, product types and review status", json.dumps(rep["status"]))
    return sorted(ids)


async def y_traps():
    section("[Y] Each trap detector fires on a crafted snippet and stays quiet on a clean one")
    from services.note_extraction import traps

    for tag, fn in traps.DETECTORS.items():
        hit = bool(fn(TRAP_SNIPPETS[tag]))
        quiet = not bool(fn(CLEAN_SNIPPET))
        check(hit and quiet, f"trap '{tag}'", f"crafted {hit}, clean quiet {quiet}")
    for tag, why in traps.NEEDS_READINGS.items():
        find(f"'{tag}' is a rule proxy", why)


def reader_answer(rspecs) -> dict:
    a = {}
    keys = {s.key for s in rspecs}
    want = {
        "pricing_date": ("2026-03-27", Q_PRICING),
        "maturity_date": ("2027-03-29", Q_MATURITY),
        "protection_type": ("buffer", Q_BUFFER),
        "buffer_pct": (15, Q_BUFFER),
        "agent_commission_pct": ({"min": 1.5, "max": 1.5, "bound": "exact"}, Q_DIST),
        "distribution": ([{"name": "Alpha Securities LLC", "role": "dealer", "fee_min_pct": 1.5,
                           "fee_max_pct": 1.5}], Q_DIST),
    }
    for k, (v, q) in want.items():
        if k in keys:
            a[k] = {"value": v, "quote": q}
    return a


async def y_prefill(conn, specs, proposed: list[str]) -> str | None:
    section("[Y] Pre-fill (mocked): readings carry run_id under run_kind 'gold_prefill'; guards; dry run")
    from services.model_catalog import PublicDataOnlyError, assert_model_allowed_for_task
    from services.note_extraction import prefill, readers, schema
    from services.note_extraction.spend import UnpricedModelError

    kinds = allowed(await check_defs(conn, "portfolio.note_extraction_runs"), "run_kind")
    check("gold_prefill" in kinds, "run_kind 'gold_prefill' is allowed by the LIVE CHECK", f"{sorted(kinds)}")
    avail = allowed(await check_defs(conn, "platform_model_catalog"), "availability")
    check(not avail or "available" in avail, "fixture catalog availability satisfies the live CHECK")
    async with conn.transaction():
        await _super(conn)
        await conn.execute(
            """INSERT INTO platform_model_catalog (model_id, display_name, provider, availability, public_data_only,
                                                   manual_input_cost_per_mtok, manual_output_cost_per_mtok)
               VALUES ($1, 'GOLDSET VERIFY public-data model', 'goldsetverify', 'available', true, 0.25, 2.0),
                      ($2, 'GOLDSET VERIFY unpriced model', 'goldsetverify', 'available', false, NULL, NULL)""",
            PUBLIC_MODEL, UNPRICED_MODEL)
    cat = {PUBLIC_MODEL: deployment(PUBLIC_MODEL, False), UNPRICED_MODEL: deployment(UNPRICED_MODEL, False)}

    refused_org = False
    async with conn.transaction():
        await _super(conn)
        try:
            await assert_model_allowed_for_task(conn, PUBLIC_MODEL, task_key="chat", org_id=ORG)
        except PublicDataOnlyError:
            refused_org = True
    check(refused_org, "precondition: the fixture model IS flagged public_data_only (refused for org work)")
    try:
        priced = await prefill.check_model(conn, PUBLIC_MODEL, cat, max_tokens=32000)
        accepted = priced[PUBLIC_MODEL].price_source == "manual"
        err = ""
    except Exception as exc:  # noqa: BLE001
        accepted, priced, err = False, None, f"{type(exc).__name__}: {exc}"
    check(accepted, "a flagged public_data_only model is ACCEPTED for the gold pre-fill (manual price applied)", err)

    runs_before = await rls_val(conn, "SELECT count(*) FROM portfolio.note_extraction_runs")
    try:
        await prefill.check_model(conn, UNPRICED_MODEL, cat, max_tokens=32000)
        refused = False
    except UnpricedModelError as exc:
        refused = exc.models == [UNPRICED_MODEL]
    check(refused, "a model with no proxy price and no manual price is REFUSED, naming it")
    try:
        await prefill.check_model(conn, PUBLIC_MODEL, cat, max_tokens=4000)
        low = False
    except prefill.PrefillRefused:
        low = True
    check(low, "max_tokens below 32000 is refused (a reasoning model would answer empty)")
    try:
        await prefill.check_model(conn, "claude-sonnet", {"claude-sonnet": deployment("claude-sonnet", True)},
                                  max_tokens=32000)
        claude = False
    except prefill.PrefillRefused:
        claude = True
    check(claude, "a Claude model is refused")
    runs_mid = await rls_val(conn, "SELECT count(*) FROM portfolio.note_extraction_runs")
    check(runs_mid == runs_before, "no refusal wrote a run row", f"{runs_before} -> {runs_mid}")

    fids = proposed[:2]
    check(len(fids) == 2, "two proposed notes to pre-fill", f"{fids}")
    rspecs = schema.reader_specs(specs)
    READER_ANSWER.clear()
    READER_ANSWER.update(reader_answer(rspecs))
    cfg = readers.ReaderConfig(slot=prefill.SLOT, deployment=PUBLIC_MODEL, max_tokens=32000)
    rd_before = await rls_val(conn, "SELECT count(*) FROM portfolio.note_term_readings")
    calls_before = len(CHAT_BODIES)
    plan = await prefill.plan(conn, fids, specs, cfg, catalog=priced)
    rd_after = await rls_val(conn, "SELECT count(*) FROM portfolio.note_term_readings")
    runs_after = await rls_val(conn, "SELECT count(*) FROM portfolio.note_extraction_runs")
    check(len(plan.notes) == 2 and plan.est_input_tokens > 0 and plan.est_cost_usd > 0,
          "dry run prints notes, estimated tokens and cost", f"{len(plan.notes)} notes, ~{plan.est_input_tokens} tokens, "
                                                             f"~${plan.est_cost_usd:.4f}")
    check(rd_after == rd_before and runs_after == runs_before and len(CHAT_BODIES) == calls_before,
          "dry run writes nothing and calls no model",
          f"readings {rd_before}->{rd_after}, runs {runs_before}->{runs_after}, calls +{len(CHAT_BODIES) - calls_before}")

    res = await prefill.run_prefill(conn, batch=BATCH_1, filing_ids=fids, specs=specs, model=PUBLIC_MODEL,
                                    max_tokens=32000, spend_cap_usd=1.0, catalog=priced,
                                    extra_config={"verify_tag": TAG})
    run_id = res["run_id"]
    run = await rls_row(conn, "SELECT run_kind, status, notes_done, report, config FROM portfolio.note_extraction_runs "
                              "WHERE id = $1", run_id)
    check(run is not None and run["run_kind"] == "gold_prefill" and run["status"] == "completed" and run["notes_done"] == 2,
          "one run, run_kind 'gold_prefill', completed over both notes",
          f"{dict(run) if run else None}"[:200])
    cfg_row = json.loads(run["config"]) if run and isinstance(run["config"], str) else (run["config"] if run else {})
    check("biased upward" in (cfg_row.get("anchoring_note") or ""), "the run's config records the anchoring note")
    new_calls = CHAT_BODIES[calls_before:]
    check(len(new_calls) == 2 and all(b["model"] == PUBLIC_MODEL and b["max_tokens"] == 32000 for b in new_calls),
          "exactly one (mocked) reader call per note, to the requested model at max_tokens 32000",
          f"{[(b['model'], b['max_tokens']) for b in new_calls]}")
    rows = await rls_fetch(conn, "SELECT source, field_key, run_id FROM portfolio.note_term_readings "
                                 "WHERE reference_filing_id = ANY($1::uuid[]) AND source <> 'human'", fids)
    check(len(rows) > 0 and all(r["run_id"] is not None and str(r["run_id"]) == run_id for r in rows),
          "EVERY reading the pre-fill wrote carries its run_id", f"{len(rows)} readings")
    srcs = Counter(r["source"] for r in rows)
    check(srcs["model_1"] > 0 and srcs["derived"] > 0,
          "readings come from the model and from derivations (rules / EdgarTools where they fire)", json.dumps(srcs))
    staged = await rls_val(conn, "SELECT count(*) FROM portfolio.note_extraction_staging WHERE run_id = $1", run_id)
    check(staged == 0, "the pre-fill stages nothing")
    return run_id


async def y_review(conn, specs, proposed, run_id) -> None:
    section("[Y] Review API: confirm / correct (scalar, range, list) / absent; supersession")
    from services.note_extraction import gold

    fid = proposed[0]
    base = f"/api/v1/admin/note-extraction/gold/notes/{fid}"
    acts = allowed(await check_defs(conn, "portfolio.note_gold_values"), "action")
    check({"confirmed", "corrected", "absent"} <= acts, "the actions used satisfy the live CHECK", f"{sorted(acts)}")
    st0 = await rls_val(conn, "SELECT status FROM portfolio.note_gold_candidates WHERE reference_filing_id = $1", fid)
    check(st0 == "proposed", "the candidate starts 'proposed'", f"{st0}")

    s, note = await api(U_SUPER, "GET", base)
    check(s == 200, "the super admin opens the note", f"HTTP {s} {_detail(note) if s != 200 else ''}")
    live = {sp.key for sp in specs}
    fields = {f["key"] for f in note.get("fields", [])}
    sec_fields = [k for sct in note.get("sections", []) for k in sct["fields"]]
    check(fields == live and sorted(sec_fields) == sorted(live),
          "the note view lists exactly the LIVE registry fields, each in one section (retired hidden)",
          f"{len(fields)} fields, {len(live)} live")
    secs = note.get("sections", [])
    first_expanded = [x["key"] for x in secs if x["expanded"]]
    check(bool(secs) and secs[0]["expanded"] and {"economics", "distribution"} <= set(first_expanded)
          and all(x["expanded"] for x in secs[:len(first_expanded)]),
          "critical / economics / distribution sections come first and expanded; the rest collapsed",
          f"expanded {first_expanded}")
    lists = {f["key"]: f for f in note.get("fields", []) if f["kind"] == "list"}
    check(set(lists) == {"underlyings", "observation_schedule", "distribution"}
          and all(f.get("members") for f in lists.values()),
          "the three list fields carry their member definitions from the server")
    pf = (note.get("prefill") or {}).get("by_field") or {}
    check((note.get("prefill") or {}).get("run_id") == run_id and "pricing_date" in pf,
          "the pre-fill comes from the gold_prefill run", f"{(note.get('prefill') or {}).get('run_id')}")
    p_pd = pf.get("pricing_date") or {}
    text = note.get("text") or ""
    ts, te = p_pd.get("text_start"), p_pd.get("text_end")
    check(isinstance(ts, int) and isinstance(te, int) and "March 27, 2026" in text[ts:te],
          "the selected field's quote is located in the document text for highlighting", f"{ts}..{te}")
    check(p_pd.get("came_from") == "model" and (pf.get("tenor_months") or {}).get("came_from") == "derivation",
          "each field says whether it came from the model, the rules or a derivation",
          f"{p_pd.get('came_from')}, tenor {(pf.get('tenor_months') or {}).get('came_from')}")
    check("biased upward" in (note.get("anchoring_note") or ""), "the screen's payload carries the anchoring note")

    async def gold_rows(field):
        return await rls_fetch(conn, "SELECT id, value, action, source_reading_id, source_quote, valid_from, valid_to, "
                                     "(value IS NULL) AS sql_null FROM portfolio.note_gold_values "
                                     "WHERE reference_filing_id = $1 AND field_key = $2 ORDER BY valid_from, id",
                               fid, field)

    def val(r):
        v = r["value"]
        return json.loads(v) if isinstance(v, str) else v

    # confirm (a reading)
    s, b = await api(U_SUPER, "PUT", f"{base}/fields/pricing_date",
                     {"action": "confirmed", "value": p_pd.get("value"), "source_reading_id": p_pd.get("reading_id")})
    r = await gold_rows("pricing_date")
    check(s == 200 and len(r) == 1 and r[0]["action"] == "confirmed" and val(r[0]) == "2026-03-27"
          and str(r[0]["source_reading_id"]) == p_pd.get("reading_id") and r[0]["source_quote"] == Q_PRICING,
          "CONFIRM writes action 'confirmed' with source_reading_id and the reading's quote",
          f"HTTP {s} {_detail(b) if s != 200 else ''}")
    st1 = await rls_val(conn, "SELECT status FROM portfolio.note_gold_candidates WHERE reference_filing_id = $1", fid)
    check(st1 == "in_review", "the first saved field moves the candidate proposed -> in_review", f"{st1}")
    # correct: scalar
    s, b = await api(U_SUPER, "PUT", f"{base}/fields/maturity_date", {"action": "corrected", "value": "2027-03-30"})
    r = await gold_rows("maturity_date")
    check(s == 200 and len(r) == 1 and r[0]["action"] == "corrected" and val(r[0]) == "2027-03-30"
          and r[0]["source_reading_id"] is None, "CORRECT (scalar) writes the corrected value", f"HTTP {s}")
    # correct: range
    s, b = await api(U_SUPER, "PUT", f"{base}/fields/agent_commission_pct",
                     {"action": "corrected", "value": {"min": None, "max": 2.5}})
    r = await gold_rows("agent_commission_pct")
    check(s == 200 and len(r) == 1 and val(r[0]) == {"min": None, "max": 2.5, "bound": "up_to"},
          "CORRECT (range) stores {min, max, bound} with the bound inferred", f"{val(r[0]) if r else None}")
    n_before = len(r)
    s, b = await api(U_SUPER, "PUT", f"{base}/fields/agent_commission_pct",
                     {"action": "corrected", "value": {"min": 3, "max": 2}})
    check(s == 422 and len(await gold_rows("agent_commission_pct")) == n_before,
          "a range with min > max is refused (422) and nothing is written", f"HTTP {s}")
    # correct: list
    members = [{"name": "Alpha Securities LLC", "role": "dealer", "fee_max_pct": 1.5},
               {"name": "InspereX LLC", "role": "distribution_agent"}]
    s, b = await api(U_SUPER, "PUT", f"{base}/fields/distribution", {"action": "corrected", "value": members})
    r = await gold_rows("distribution")
    stored = val(r[0]) if r else None
    check(s == 200 and len(r) == 1 and isinstance(stored, list) and len(stored) == 2
          and stored[0]["name"] == "Alpha Securities LLC" and stored[0]["fee_min_pct"] is None
          and stored[1]["role"] == "distribution_agent",
          "CORRECT (list) stores the validated rows, one member per row", json.dumps(stored)[:200])
    s, b = await api(U_SUPER, "PUT", f"{base}/fields/distribution",
                     {"action": "corrected", "value": [{"name": "X LLC", "role": "boss"}]})
    check(s == 422 and len(await gold_rows("distribution")) == 1, "a list row with an invalid role is refused (422)")
    # absent
    s, b = await api(U_SUPER, "PUT", f"{base}/fields/cap_pct", {"action": "absent", "value": None})
    r = await gold_rows("cap_pct")
    check(s == 200 and len(r) == 1 and r[0]["action"] == "absent" and r[0]["sql_null"] is True,
          "ABSENT stores SQL NULL (not a JSON null)", f"HTTP {s}")
    # supersession
    s1, _ = await api(U_SUPER, "PUT", f"{base}/fields/buffer_pct", {"action": "corrected", "value": 15})
    s2, _ = await api(U_SUPER, "PUT", f"{base}/fields/buffer_pct", {"action": "corrected", "value": 20})
    r = await gold_rows("buffer_pct")
    old = [x for x in r if x["valid_to"] is not None]
    new = [x for x in r if x["valid_to"] is None]
    check(s1 == 200 and s2 == 200 and len(r) == 2 and len(old) == 1 and len(new) == 1 and val(old[0]) == 15
          and val(new[0]) == 20 and old[0]["valid_to"] == new[0]["valid_from"],
          "a correction supersedes by valid_to / valid_from; the old row is still readable",
          f"{[(val(x), x['valid_to'] is None) for x in r]}")
    retired = await rls_val(conn, "SELECT field_key FROM portfolio.note_terms_field_registry WHERE retired_at IS NOT NULL "
                                  "ORDER BY field_key LIMIT 1")
    if retired:
        s, _ = await api(U_SUPER, "PUT", f"{base}/fields/{retired}", {"action": "absent", "value": None})
        check(s == 422, "a retired field cannot be reviewed (422)", f"{retired}: HTTP {s}")

    section("[Y] Permission gate on the identical request")
    body = {"action": "corrected", "value": 25}
    path = f"{base}/fields/barrier_pct"
    before = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_values WHERE reference_filing_id = $1", fid)
    s_oa, b_oa = await api(U_ORGADMIN, "PUT", path, body)
    mid = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_values WHERE reference_filing_id = $1", fid)
    check(s_oa == 403, "an org admin is REFUSED (403)", f"HTTP {s_oa} {_detail(b_oa)}")
    check(mid == before, "...and the refusal leaves note_gold_values unchanged", f"{before} -> {mid}")
    s_g, _ = await api(U_ORGADMIN, "GET", base)
    s_k, _ = await api(U_ORGADMIN, "POST", f"{base}/skip", {"reason": "org admin tries to skip"})
    st_k = await rls_val(conn, "SELECT status FROM portfolio.note_gold_candidates WHERE reference_filing_id = $1", fid)
    check(s_g == 403 and s_k == 403 and st_k == "in_review", "...and is refused reading the note and skipping it",
          f"GET {s_g}, skip {s_k}, status {st_k}")
    s_sa, b_sa = await api(U_SUPER, "PUT", path, body)
    after = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_values WHERE reference_filing_id = $1", fid)
    check(s_sa == 200 and after == mid + 1, "the super admin is ADMITTED on the identical request",
          f"HTTP {s_sa} {_detail(b_sa) if s_sa != 200 else ''}; {mid} -> {after}")
    check((b_sa.get("permissions") or {}).get("can_write") is True
          and (b_sa.get("vocabularies") or {}).get("editable") == ["value", "action", "notes"],
          "the response carries the permission envelope")

    section("[Y] Candidate status: proposed -> in_review -> done; skip records a reason")
    # A missed and a false for the scorer: gold has a value the run lacks; gold absent where the run had one.
    s_m, _ = await api(U_SUPER, "PUT", f"{base}/fields/issuer_entity", {"action": "corrected", "value": "GSV Bank"})
    s_f, _ = await api(U_SUPER, "PUT", f"{base}/fields/protection_type", {"action": "absent", "value": None})
    check(s_m == 200 and s_f == 200, "two more reviewed fields saved (issuer_entity corrected, protection_type absent)")
    have = {r["field_key"] for r in await rls_fetch(
        conn, "SELECT field_key FROM portfolio.note_gold_values WHERE reference_filing_id = $1 AND valid_to IS NULL", fid)}
    remaining = [k for k in sorted(live) if k not in have]
    st_mid = await rls_val(conn, "SELECT status FROM portfolio.note_gold_candidates WHERE reference_filing_id = $1", fid)
    check(st_mid == "in_review" and len(remaining) > 0, "with fields left, the candidate stays in_review",
          f"{st_mid}, {len(remaining)} left")
    fails = []
    last = None
    for k in remaining:
        s, last = await api(U_SUPER, "PUT", f"{base}/fields/{k}", {"action": "absent", "value": None})
        if s != 200:
            fails.append((k, s))
    st_done = await rls_val(conn, "SELECT status FROM portfolio.note_gold_candidates WHERE reference_filing_id = $1", fid)
    prog = (last or {}).get("progress") or {}
    check(not fails and st_done == "done" and prog.get("fields_done") == prog.get("fields_total") == len(live),
          "when every live field has a current gold value the candidate is 'done'",
          f"{st_done}; progress {prog}; failures {fails[:3]}")
    s, lst = await api(U_SUPER, "GET", f"/api/v1/admin/note-extraction/gold/candidates?batch={BATCH_1}")
    pr = (lst or {}).get("progress") or {}
    n_batch = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_candidates WHERE sample_batch = $1", BATCH_1)
    check(s == 200 and pr.get("notes_done") == 1 and pr.get("notes_total") == n_batch,
          "batch progress reports notes done / total", f"{pr}")

    fid2 = proposed[1]
    s0, _ = await api(U_SUPER, "POST", f"/api/v1/admin/note-extraction/gold/notes/{fid2}/skip", {"reason": ""})
    s1, b1 = await api(U_SUPER, "POST", f"/api/v1/admin/note-extraction/gold/notes/{fid2}/skip",
                       {"reason": "verify: a plain fixed-rate note, not a structured payoff"})
    row = await rls_row(conn, "SELECT status, skip_reason, skipped_by FROM portfolio.note_gold_candidates "
                              "WHERE reference_filing_id = $1", fid2)
    check(s0 == 422, "skip without a reason is refused (422)", f"HTTP {s0}")
    check(s1 == 200 and row is not None and row["status"] == "skipped" and "fixed-rate" in (row["skip_reason"] or "")
          and row["skipped_by"] == U_SUPER, "skip sets 'skipped' and records the reason and who skipped",
          f"{dict(row) if row else None}")
    other = proposed[2]
    refused = False
    try:
        await rls_exec(conn, "UPDATE portfolio.note_gold_candidates SET status = 'skipped', skip_reason = NULL "
                             "WHERE reference_filing_id = $1", other)
    except asyncpg.CheckViolationError:
        refused = True
    check(refused, "the DATABASE refuses a skipped candidate without a reason (live CHECK)")
    find("the review path's own 'human' readings carry run_id NULL by design (no run); they are scoped by fixture "
         "filing id in teardown and counted to zero")
    _ = gold  # imported for symmetry with the B1 verify; the write path is gold.record_gold_value


def scorer_fixture(n_notes: int, outcomes: dict[str, list[str]]):
    """gold / run dicts producing the given outcome per note per field."""
    gold, run = {}, {}
    notes = {f"note-{i:03d}" for i in range(n_notes)}
    for fk, outs in outcomes.items():
        for i, o in enumerate(outs):
            nid = f"note-{i:03d}"
            if o == "correct":
                gold[(nid, fk)], run[(nid, fk)] = 1, 1
            elif o == "correct_absent":
                gold[(nid, fk)] = None
            elif o == "wrong":
                gold[(nid, fk)], run[(nid, fk)] = 1, 2
            elif o == "missed":
                gold[(nid, fk)] = 1
            elif o == "false":
                gold[(nid, fk)], run[(nid, fk)] = None, 1
    return gold, run, notes


async def y_scorer(conn, specs, run_id, proposed):
    section("[Y] Scorer: exact counts, ranges, reordered lists, too few, the 95% gate")
    from services.note_extraction import scoring
    from services.note_extraction.schema import FieldSpec

    num = FieldSpec("num_crit", "N", "number", "d", critical=True)
    num2 = FieldSpec("num_other", "M", "number", "d", critical=False)
    outs = {"num_crit": ["correct"] * 6 + ["correct_absent"] * 2 + ["wrong"] * 2 + ["missed"] * 3 + ["false"] * 1,
            "num_other": ["correct"] * 9}
    gold, run, notes = scorer_fixture(14, outs)
    rep = scoring.score([num, num2], gold, run, notes)
    exp = Counter("correct" if o.startswith("correct") else o for o in outs["num_crit"])
    f = rep["fields"]["num_crit"]
    check(len(outs["num_crit"]) > 0 and (f["correct"], f["wrong"], f["missed"], f["false"])
          == (exp["correct"], exp["wrong"], exp["missed"], exp["false"])
          and abs(f["accuracy"] - exp["correct"] / len(outs["num_crit"])) < 1e-12,
          "correct / wrong / missed / false are counted exactly; accuracy = correct / n",
          f"{(f['correct'], f['wrong'], f['missed'], f['false'], f['accuracy'])} vs {dict(exp)}")
    f2 = rep["fields"]["num_other"]
    check(f2["notes"] == 9 and f2["status"] == "too few" and f2["accuracy"] is None,
          "a field with fewer than 10 reviewed notes is 'too few' (not scored)", f"{f2['notes']} notes")

    rng = FieldSpec("rng", "R", "range", "d", critical=True, value_shape="range")
    dist = FieldSpec("distribution", "D", "list", "d", critical=True, value_shape="list")
    members = [{"name": "Alpha Securities LLC", "role": "dealer", "fee_max_pct": 1.5},
               {"name": "InspereX LLC", "role": "distribution_agent", "fee_max_pct": None}]
    check(scoring.classify(rng, {"min": None, "max": 2.5, "bound": "up_to"}, {"min": None, "max": 2.50}) == "correct",
          "range agreement (same min and max, bound unstated) scores correct")
    check(scoring.classify(rng, {"min": 1, "max": 2.5}, {"min": None, "max": 2.5}) == "wrong",
          "...and a different min scores wrong")
    check(scoring.classify(dist, members, list(reversed(members))) == "correct",
          "a reordered list of the same members scores correct")
    check(scoring.classify(dist, members, members[:1]) == "wrong", "...and a list missing a member scores wrong")

    crit = [FieldSpec("c1", "C1", "number", "d", critical=True), FieldSpec("c2", "C2", "number", "d", critical=True)]
    g, r_, n = scorer_fixture(40, {"c1": ["correct"] * 39 + ["wrong"], "c2": ["correct"] * 40})
    p = scoring.score(crit, g, r_, n)
    check(p["gate"]["result"] == "PASS" and p["reviewed_notes"] == 40, "the gate PASSES at >= 95% on 40 notes",
          f"{p['gate']}")
    g, r_, n = scorer_fixture(40, {"c1": ["correct"] * 37 + ["wrong"] * 3, "c2": ["correct"] * 40})
    q = scoring.score(crit, g, r_, n)
    check(q["gate"]["result"] == "FAIL" and [b["field"] for b in q["gate"]["below"]] == ["c1"],
          "the gate FAILS at 92.5% and names the field below it", f"{q['gate']['below']}")
    g, r_, n = scorer_fixture(39, {"c1": ["correct"] * 39, "c2": ["correct"] * 39})
    t = scoring.score(crit, g, r_, n)
    check(t["gate"]["result"] == "FAIL" and len(t["gate"]["below"]) == 2,
          "the gate FAILS with only 39 reviewed notes, however accurate", f"{[b['reason'] for b in t['gate']['below']]}")

    # DB: score the pre-fill run against the reviewed note; the report lands on the run.
    rep = await scoring.score_run(conn, run_id, specs, batch=BATCH_1, fields="all")
    fr = rep["field_results"]
    check(rep["reviewed_notes"] == 1, "on the DB: one reviewed note in common (the other was skipped, no gold)",
          f"{rep['reviewed_notes']}")
    check(fr["pricing_date"]["correct"] == 1 and fr["maturity_date"]["wrong"] == 1
          and fr["issuer_entity"]["missed"] == 1 and fr["protection_type"]["false"] == 1
          and fr["cap_pct"]["correct"] == 1,
          "on the DB: confirmed -> correct, corrected -> wrong, run-null -> missed, gold-absent -> false",
          json.dumps({k: {x: fr[k][x] for x in ("correct", "wrong", "missed", "false")}
                      for k in ("pricing_date", "maturity_date", "issuer_entity", "protection_type", "cap_pct")}))
    check(all(v["status"] == "too few" for v in fr.values()) and rep["gate"]["result"] == "FAIL",
          "with one note every field is 'too few' and the gate fails")
    check((rep.get("anchoring") or {}).get("anchored") is True, "the pre-fill run is marked anchored")
    url = os.environ["DATABASE_URL"]
    c2 = await asyncpg.connect(url, statement_cache_size=0)
    try:
        stored = await rls_val(c2, "SELECT report->'gold_score'->>'reviewed_notes' FROM portfolio.note_extraction_runs "
                                   "WHERE id = $1", run_id)
        kept = await rls_val(c2, "SELECT report ? 'gold_prefill' FROM portfolio.note_extraction_runs WHERE id = $1", run_id)
    finally:
        await c2.close()
    check(stored == "1" and kept is True, "the report is written to the run's report jsonb, keeping what was there "
                                          "(independent re-read)", f"{stored}, prefill kept {kept}")


def y_regression():
    section("[Y] Regression: verify_notefields.py, run ONCE as a flat subprocess")
    try:
        proc = subprocess.run([sys.executable, str(HERE.parent / "verify_notefields.py")], capture_output=True,
                              text=True, timeout=180, cwd=str(REPO))
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        out = f"TIMEOUT after 180s: {(exc.stdout or b'')[-300:]!r}"
    m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
    check(m is not None and int(m.group(1)) > 0 and int(m.group(2)) == 0,
          "verify_notefields.py still reports 0 FAIL", m.group(0) if m else out[-300:])


# ═══ Main ══════════════════════════════════════════════════════════════════
async def main_async() -> int:
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    conn = await asyncpg.connect(url, statement_cache_size=0)
    install_mocks()
    sgnt_before = None
    pre_counts = None
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
        from services.note_extraction import schema
        specs = await schema.load_specs(conn)
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

        await run("task1", y_task1, conn)
        await run("index migration", y_index_migration, conn)
        await run("traps", y_traps)
        await run("seed users", seed_users, conn)
        layout = await run("seed manifest", seed_manifest, conn)
        cid = await run("plan", y_plan, conn, layout) if layout else None
        fetched = await run("fetch (simulated)", fetch_cohort, conn, cid, layout) if cid else None
        proposed = await run("propose", y_propose, conn, cid, fetched) if fetched else None
        run_id = await run("prefill", y_prefill, conn, specs, proposed) if proposed else None
        if proposed and run_id:
            await run("review", y_review, conn, specs, proposed, run_id)
            await run("scorer", y_scorer, conn, specs, run_id, proposed)
        else:
            check(False, "review and scorer sections had their prerequisites", f"proposed={bool(proposed)} run={run_id}")
        await run("regression", y_regression)
        check(all(not str(b.get("model", "")).startswith(("gpt", "claude", "gemini")) for b in CHAT_BODIES),
              "every reader call went to the mocked fixture deployment (no real model named)",
              f"{len(CHAT_BODIES)} calls")

        section("[Y] securities_global_note_terms untouched")
        sgnt_after = await rls_val(conn, "SELECT count(*) FROM portfolio.securities_global_note_terms")
        check(sgnt_before is not None and sgnt_after == sgnt_before,
              "securities_global_note_terms row count unchanged", f"{sgnt_before} -> {sgnt_after}")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        section("[Y] Teardown: zero fixture rows; every touched table at its pre-test count")
        try:
            await teardown(conn)
            left = await fixture_counts(conn)
            check(sum(left.values()) == 0, "zero fixture rows remain", json.dumps(left))
            if pre_counts is not None:
                post = await table_counts(conn)
                diff = {t: (pre_counts[t], post[t]) for t in COUNTED if pre_counts[t] != post[t]}
                check(not diff, "every touched table is back at its exact pre-test count", json.dumps(diff))
            else:
                check(False, "pre-test counts were taken")
        except Exception as exc:  # noqa: BLE001
            check(False, "end-of-run teardown", f"{type(exc).__name__}: {exc}")
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        await conn.close()
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
