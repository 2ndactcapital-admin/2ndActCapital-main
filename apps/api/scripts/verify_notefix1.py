"""verify_notefix1.py — NOTEFIX1: fee fields become ranges, per-field reader
validation (+ coercion), a wider hypothetical-example trap detector, and the
gold verify's regression ordering.

WRITTEN BY THE SPRINT. THE OPERATOR RUNS IT:

    python3 apps/api/scripts/verify_notefix1.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. Hydrates secrets from Doppler over HTTPS at startup
(_db_bootstrap). EVERY model call is mocked (services.note_extraction.proxy.chat
and .jev are replaced before any section runs); nothing is fetched from EDGAR
or R2. Real spend: $0.

FIXTURES — all tagged and all torn down at the start AND at the end:
  * reference_filings with accession prefix ACC_PREFIX, ids in the 720f UUID
    block (full UUIDs built from a counter, never a slice of a UUID).
  * note_term_readings / note_extraction_runs tagged config->>'verify_tag' =
    TAG. note_term_readings has an immutability trigger: the scoped delete
    disables/re-enables it in a savepoint and FAILS LOUDLY if rows remain.
  * note_gold_candidates / note_gold_values on the fixture filings.
  * a fixture org + one super-admin user (FK target for note_gold_values.
    reviewer_id and note_extraction_runs.created_by only — no RBAC/HTTP layer
    is exercised here, so no roles are granted).
Teardown deletes in FK child-before-parent order, READ FROM pg_constraint
(contype read ::text). It runs at the START (in case a prior run left fixture
rows) and at the END, and re-reads every touched table to confirm its exact
pre-test count.

RLS: every read and write runs with app.is_super_admin set LOCAL inside a
transaction (the rls_* helpers). The connection's role is confirmed
rolbypassrls = False as its own assertion.

The nested verify_notefields.py and verify_goldset.py regressions run ONCE
each, as flat subprocesses, ONLY AFTER this script's own teardown has
completed and confirmed zero fixture rows remain — running them earlier would
let them see THIS script's own live fixtures (the exact self-contamination
Task 5 fixes in verify_goldset.py itself).
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
DISCOVERY_DOC = REPO / "docs" / "NOTEFIX1_DISCOVERY.md"
NOTE_FIELDS_DOC = REPO / "docs" / "NOTE_FIELDS.md"

TAG = "notefix1-verify"
ACC_PREFIX = "9999999999-72-"          # no other verify uses -72- (grep'd: -55-, -58-, -61-, -77- are taken)
ORG = UUID("99000000-0000-0000-0000-0000720f0001")
U_SUPER = UUID("99000000-0000-0000-0000-0000720f0011")
FIXTURE_USERS = [U_SUPER]
BATCH = f"{TAG}-batch-1"
RANGE_FIELDS = ("structuring_fee_pct", "other_fees_pct", "total_fees_pct", "proceeds_to_issuer_pct")


def filing_uuid(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-{0x720f1000 + n:012x}")


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


# ═══ RLS-scoped DB helpers (verify_goldset.py pattern) ═════════════════════
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


def _sub(uid: UUID) -> str:
    return f"notefix1_{uid.hex}"


# ═══ Teardown — FK order from pg_constraint; counts as the backstop ═══════
TOUCHED = (
    "portfolio.note_gold_values", "portfolio.note_term_readings", "portfolio.note_gold_candidates",
    "portfolio.note_extraction_runs", "portfolio.reference_filings",
)
COUNTED = TOUCHED + ("public.users", "public.organizations")
IMMUTABLE = {"portfolio.note_term_readings"}


async def fixture_keys(conn) -> dict:
    runs = await rls_fetch(conn, "SELECT id FROM portfolio.note_extraction_runs WHERE config->>'verify_tag' = $1", TAG)
    filings = await rls_fetch(conn, "SELECT id FROM portfolio.reference_filings WHERE accession_number LIKE $1",
                              ACC_PREFIX + "%")
    return {"runs": [r["id"] for r in runs], "filings": [r["id"] for r in filings]}


def predicates(k: dict) -> dict[str, tuple[str, list]]:
    f, r = k["filings"], k["runs"]
    return {
        "portfolio.note_gold_values": ("reference_filing_id = ANY($1::uuid[])", [f]),
        "portfolio.note_term_readings": ("reference_filing_id = ANY($1::uuid[]) OR run_id = ANY($2::uuid[])", [f, r]),
        "portfolio.note_gold_candidates": ("reference_filing_id = ANY($1::uuid[])", [f]),
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
    out["public.users"] = await rls_val(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) "
                                              "OR auth0_sub = ANY($2::text[])", FIXTURE_USERS,
                                        [_sub(u) for u in FIXTURE_USERS])
    out["public.organizations"] = await rls_val(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG)
    return out


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
    subs = [_sub(u) for u in FIXTURE_USERS]
    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[])",
                           FIXTURE_USERS, subs)
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


async def table_counts(conn) -> dict[str, int]:
    return {t: await rls_val(conn, f"SELECT count(*) FROM {t}") for t in COUNTED}


# ═══ Mocks — installed before any section; real spend stays $0 ═════════════
READER_ANSWER: dict = {}
CHAT_MODE = {"mode": "normal"}       # 'normal' | 'garbage' (unparseable response)


def install_mocks():
    from services.note_extraction import proxy

    async def fake_chat(body, timeout=None):
        if CHAT_MODE["mode"] == "garbage":
            content = "not json {"
        else:
            system = body["messages"][0]["content"]
            keys = list(json.loads(system.split("JSON schema:\n", 1)[1])["properties"])
            out = {k: READER_ANSWER.get(k, {"value": None, "quote": None}) for k in keys}
            content = json.dumps(out)
        return proxy.ProxyResponse(
            200, {"model": body["model"], "choices": [{"message": {"content": content}}],
                  "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            content, {"x-litellm-attempted-fallbacks": "0"})

    async def no_jev(body, timeout=None):
        raise AssertionError("Jev must never be called by this verify")

    proxy.chat = fake_chat
    proxy.jev = no_jev


def mock_deployment(name: str):
    from services.note_extraction import proxy
    return proxy.Deployment(name, None, name, None, 1e-7, 4e-7, None, True, None)


# ═══ Fixture setup ══════════════════════════════════════════════════════════
async def seed_user(conn) -> None:
    defs = await check_defs(conn, "users")
    roles_ok = allowed(defs, "role")
    check(not roles_ok or "super_admin" in roles_ok, "fixture users.role value satisfies the live CHECK",
          f"{sorted(roles_ok)}")
    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "NOTEFIX1 Verify Fixture Org", f"{ORG}-notefix1")
        sub = _sub(U_SUPER)
        await conn.execute(
            """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
               VALUES ($1, $2, $3, $4, $5, 'super_admin', true)""",
            U_SUPER, ORG, f"{sub}@test.local", sub, sub)


async def make_filing(conn, n: int, label: str) -> UUID:
    rf_form = allowed(await check_defs(conn, "portfolio.reference_filings"), "form_type")
    check(not rf_form or "424B2" in rf_form, "fixture reference_filings.form_type satisfies the live CHECK",
          f"{sorted(rf_form)}")
    fid = filing_uuid(n)
    async with conn.transaction():
        await _super(conn)
        await conn.execute(
            """INSERT INTO portfolio.reference_filings
                 (id, cik, filer_name, form_type, accession_number, filing_date, primary_document, source_url,
                  extraction_status)
               VALUES ($1, '0', $2, '424B2', $3, CURRENT_DATE, 'fixture.htm', 'https://example.invalid/notefix1',
                       'pending')""",
            fid, f"NOTEFIX1 VERIFY {label}", f"{ACC_PREFIX}{n:06d}")
    return fid


# ═══ Sections ════════════════════════════════════════════════════════════
def y_task1_discovery():
    section("[Y] Task 1 — the four discovery findings are reported")
    text = DISCOVERY_DOC.read_text(encoding="utf-8") if DISCOVERY_DOC.exists() else ""
    heads = [h for h in ("## 1a.", "## 1b.", "## 1c.", "## 1d.") if h in text]
    check(len(heads) == 4, "docs/NOTEFIX1_DISCOVERY.md reports 1a, 1b, 1c and 1d", f"found {heads}")

    check("parse_reader_output" in text and "ONE combined Pydantic model" in text,
          "1a: the whole-reading-failure root cause is named (parse_reader_output's old combined model)")
    check("started_at DESC" in text or "ORDER BY r.started_at DESC" in text,
          "1b: the review screen's newest-run selection is documented (no code change needed)")
    check("fees_reconcile" in text and "interval" in text.lower(),
          "1c: the self-check's interval-overlap rule is documented")
    m = re.search(r"New hit rate:\W*(\d+)/(\d+)", text)
    check(m is not None, "1d: the new 138-final hit rate is recorded in the discovery doc")
    if m:
        print(f"  138-final hypothetical-example-table hit rate: {m.group(1)}/{m.group(2)}")
        check(int(m.group(1)) > int(m.group(2)) * 0.5,
              "1d: the widened detector hits well over half of the 138 real finals",
              f"{m.group(1)}/{m.group(2)} (old detector: 1/138)")
    check("DEALER RESALE DISCOUNT" in text or "dealer resale discount" in text.lower(),
          "1d: price_to_public_pct's 'up to' hits are confirmed to describe something else "
          "(a dealer discount), so it correctly stays scalar")

    nf = NOTE_FIELDS_DOC.read_text(encoding="utf-8") if NOTE_FIELDS_DOC.exists() else ""
    check(all(f"`{k}`" in nf for k in RANGE_FIELDS), "the four widened fields are named in NOTE_FIELDS.md",
          f"{RANGE_FIELDS}")
    check("price_to_public_pct" in nf and "Stays scalar" in nf,
          "NOTE_FIELDS.md records that price_to_public_pct stays scalar")


async def y_task2_registry(conn, specs) -> None:
    section("[Y] Task 2 — the four economics fields are 'range' in the live registry")
    from services.note_extraction import schema

    by_key = {s.key: s for s in specs}
    for key in RANGE_FIELDS:
        s = by_key.get(key)
        check(s is not None and s.value_shape == "range" and s.kind == schema.KIND_RANGE,
              f"live registry: {key} is value_shape='range' (kind={schema.KIND_RANGE})",
              f"{s.value_shape if s else 'MISSING'}")
    p2p = by_key.get("price_to_public_pct")
    check(p2p is not None and p2p.value_shape == "scalar" and p2p.kind == schema.KIND_NUMBER,
          "live registry: price_to_public_pct stays scalar", f"{p2p.value_shape if p2p else 'MISSING'}")

    # A single stated amount -> min = max (decision C), proven through the real parser.
    sub = schema.subset(specs, ["structuring_fee_pct"])
    raw = json.dumps({"structuring_fee_pct": {"value": 0.40, "quote": "a structuring fee of 0.40%"}})
    fields, fatal, issues = schema.parse_reader_output(sub, raw)
    check(fatal is None and fields["structuring_fee_pct"]["value"] == {"min": 0.4, "max": 0.4, "bound": "exact"},
          "a single stated amount '0.40%' stores min = max = 0.40", f"{fields}")
    check(issues.get("structuring_fee_pct", {}).get("coerced") == "scalar_to_range",
          "the single amount is recorded as a scalar_to_range coercion, not silently reshaped")


async def y_reproduce_and_storage(conn, specs, filing_id: UUID, run_id: str) -> None:
    section("[Y] REPRODUCE FIRST, then prove the fix stores the value")
    from services.note_extraction import schema, store
    from services.note_extraction.schema import FieldSpec

    # The exact failure from run e6da8fcd-b4e7-4300-9bac-48dee6e54697, note
    # 6380efa7-5a87-4202-8b6c-3bcaf34030aa: the model correctly read a
    # range-shaped value for structuring_fee_pct.
    bad_shape_value = {"min": None, "max": 0.4, "bound": "up_to"}
    quote = "a structuring fee of up to 0.40%"

    old_spec = FieldSpec(key="structuring_fee_pct", label="Structuring Fee %", kind=schema.KIND_NUMBER,
                         description="pre-notefix1 scalar shape", value_shape="scalar")
    raw_old = json.dumps({"structuring_fee_pct": {"value": bad_shape_value, "quote": quote}})
    _, _, old_issues = schema.parse_reader_output([old_spec], raw_old)
    check(bool(old_issues.get("structuring_fee_pct", {}).get("error")),
          "under the OLD registry shape (scalar), this exact range-shaped answer fails ITS OWN field "
          "validation — and under the pre-notefix1 combined-model parser this failed the WHOLE reading "
          "(note 6380efa7-5a87-4202-8b6c-3bcaf34030aa lost every model-read field)",
          f"old shape={old_spec.value_shape}, error={old_issues['structuring_fee_pct']['error'][:120]}")

    sub = schema.subset(specs, ["structuring_fee_pct", "other_fees_pct", "price_to_public_pct", "cap_pct"])
    raw_new = json.dumps({
        "structuring_fee_pct": {"value": bad_shape_value, "quote": quote},
        "other_fees_pct": {"value": 0.40, "quote": "a referral fee of 0.40%"},
        "price_to_public_pct": {"value": 100.0, "quote": "100% of the principal amount"},
        "cap_pct": {"value": "not-a-number", "quote": "garbage"},
    })
    fields, fatal, issues = schema.parse_reader_output(sub, raw_new)
    check(fatal is None, "under the NEW registry shape (range), the response is accepted — no whole-reading failure",
          f"{fatal}")
    check(fields.get("structuring_fee_pct", {}).get("value") == bad_shape_value,
          "structuring_fee_pct is stored as a range with the bound wording preserved", f"{fields.get('structuring_fee_pct')}")
    check(fields.get("structuring_fee_pct", {}).get("quote") == quote,
          "the bound wording is kept as the field's quote")
    check(fields.get("other_fees_pct", {}).get("value") == {"min": 0.4, "max": 0.4, "bound": "exact"},
          "a single stated amount on other_fees_pct also sets min = max")
    check(fields.get("price_to_public_pct", {}).get("value") == 100.0,
          "price_to_public_pct (unaffected control, still scalar) parses normally alongside the range fields")
    check("cap_pct" not in fields and bool(issues.get("cap_pct", {}).get("error")),
          "the one deliberately malformed field (cap_pct) is dropped and recorded — every OTHER field above "
          "was still kept in the SAME response", f"issues={list(issues)}")

    # Real proof, not assumed: WRITE the reading, then re-read it from an
    # INDEPENDENT connection.
    sub_by_key = {s.key: s for s in sub}
    row = store.ReadingRow(
        reference_filing_id=str(filing_id), field_key="structuring_fee_pct", source="model_1",
        value=fields["structuring_fee_pct"]["value"],
        value_normalized=schema.normalize(sub_by_key["structuring_fee_pct"], fields["structuring_fee_pct"]["value"]),
        run_id=run_id, origin="cascade", source_quote=fields["structuring_fee_pct"]["quote"],
    )
    await store.insert_readings(conn, [row])
    independent = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        back = await independent.fetchrow(
            "SELECT value, source_quote FROM portfolio.note_term_readings WHERE id = $1", row.id)
    finally:
        await independent.close()
    stored_value = json.loads(back["value"]) if back and back["value"] is not None else None
    check(back is not None and stored_value == {"min": None, "max": 0.4, "bound": "up_to"},
          "the written reading round-trips from an INDEPENDENT connection with the range intact",
          f"{stored_value}")
    check(back is not None and back["source_quote"] == quote,
          "the bound wording survives as the persisted reading's quote")


async def y_task2_self_checks() -> None:
    section("[Y] Task 2/1c — self-checks on range-shaped fee fields")
    from services.note_extraction.checks import fees_reconcile

    consistent = {"price_to_public_pct": 100, "total_fees_pct": {"min": None, "max": 2.0, "bound": "up_to"},
                 "proceeds_to_issuer_pct": {"min": 98.0, "max": 98.0, "bound": "exact"}}
    check(fees_reconcile(consistent) is None,
          "a consistent range-shaped fee set (implied proceeds interval overlaps the stated one) stays quiet",
          f"{fees_reconcile(consistent)}")

    scalar_as_range = {"price_to_public_pct": 100, "total_fees_pct": 2.0, "proceeds_to_issuer_pct": 98.0}
    check(fees_reconcile(scalar_as_range) is None,
          "a wholly scalar note (every range's min = max) reduces to the old equality check and stays quiet")

    inconsistent = {"price_to_public_pct": 100, "total_fees_pct": {"min": 2.0, "max": 2.0, "bound": "exact"},
                    "proceeds_to_issuer_pct": {"min": 90.0, "max": 90.0, "bound": "exact"}}
    finding = fees_reconcile(inconsistent)
    check(finding is not None and finding.check == "fees_reconcile",
          "a crafted inconsistency (no feasible fee/proceeds combination) fires with a reason", f"{finding}")


async def y_task3_per_field_validation() -> None:
    section("[Y] Task 3 — one malformed field never rejects the whole note")
    from services.note_extraction import cascade, documents, readers
    from services.note_extraction.schema import KIND_NUMBER, METHOD_MODEL, FieldSpec

    specs = [
        FieldSpec(key="notefix1_good_field", label="Good", kind=KIND_NUMBER, description="d",
                  extraction_method=METHOD_MODEL),
        FieldSpec(key="notefix1_bad_field", label="Bad", kind=KIND_NUMBER, description="d",
                  extraction_method=METHOD_MODEL),
    ]
    doc = documents.document_from_html(
        "<p>The notes state that the good value is 42 per the filing.</p>",
        reference_filing_id="notefix1-verify-cascade-fixture")

    READER_ANSWER.clear()
    READER_ANSWER.update({
        "notefix1_good_field": {"value": 42.0, "quote": "the good value is 42"},
        "notefix1_bad_field": {"value": "not-a-number", "quote": "garbage"},
    })
    CHAT_MODE["mode"] = "normal"

    dep1, dep2 = "notefix1-verify-dep-1", "notefix1-verify-dep-2"
    catalog = {dep1: mock_deployment(dep1), dep2: mock_deployment(dep2)}
    slots = cascade.EnsembleSlots(
        model_1=readers.ReaderConfig(slot="model_1", deployment=dep1),
        model_2=readers.ReaderConfig(slot="model_2", deployment=dep2),
        jev_route=None, escalation=None)

    out = await cascade.run_note(doc, specs, slots, catalog=catalog, spend=None,
                                 run_id="notefix1-verify-cascade-run", origin="verify")
    check(out.status == "needs_review", "a reading with one malformed field is needs_review, NOT failed",
          f"status={out.status}")
    check(bool(out.status_reason) and "notefix1_bad_field" in out.status_reason,
          "the note's status_reason names the bad field", out.status_reason)

    by_key = {s.field_key: s for s in out.staged}
    good = by_key.get("notefix1_good_field")
    check(good is not None and good.resolved_value == 42.0 and good.resolution == "verified_agreement"
          and not good.needs_review,
          "the OTHER (good) field is fully kept and resolved, unaffected by the bad field", f"{good}")
    bad = by_key.get("notefix1_bad_field")
    check(bad is not None and bad.resolved_value is None and bad.needs_review,
          "the bad field itself is flagged needs_review on its own staged record", f"{bad}")
    check(bad is not None and bool((bad.metadata or {}).get("review_reasons")),
          "the bad field's metadata carries its own validation reason", f"{bad.metadata if bad else None}")

    issues = out.detail.get("field_issues") or []
    bad_issues = [d for d in issues if d["field_key"] == "notefix1_bad_field"]
    check(len(bad_issues) == 2 and all(d["error"] for d in bad_issues) and all(d["coerced"] is None for d in bad_issues),
          "both readers' field-level errors are recorded on the run's report (field, error, raw value)",
          f"{bad_issues}")
    check(all(d["raw_value"] == {"value": "not-a-number", "quote": "garbage"} for d in bad_issues),
          "the raw (unparsed) value is preserved in the report, not just the error text")

    # An unparseable response (not valid JSON at all) still fails the WHOLE note.
    CHAT_MODE["mode"] = "garbage"
    doc2 = documents.document_from_html("<p>Nothing special.</p>",
                                        reference_filing_id="notefix1-verify-cascade-fixture-2")
    out2 = await cascade.run_note(doc2, specs, slots, catalog=catalog, spend=None,
                                  run_id="notefix1-verify-cascade-run-2", origin="verify")
    check(out2.status == "failed", "a genuinely unparseable response still fails the whole note",
          f"status={out2.status}, reason={out2.status_reason}")
    CHAT_MODE["mode"] = "normal"


def y_task4_detector() -> None:
    section("[Y] Task 4 — widened hypothetical-example trap detector")
    from services.note_extraction import traps

    variants = {
        "Hypothetical Examples (all caps heading)": "HYPOTHETICAL EXAMPLES\nThe table below illustrates returns.",
        "Examples of Hypothetical Payments": "Examples of Hypothetical Payments at Maturity are shown below.",
        "Hypothetical Payout Profile (Bank of America)":
            "Hypothetical Payout Profile and Examples of Payments at Maturity",
        "Hypothetical Payment at Maturity": "The hypothetical payment at maturity depends on the final level.",
        "What Is the Total Return ... Assuming":
            "What Is the Total Return on the Notes, Assuming a Hypothetical Hold to Maturity?",
        "Hypothetical Initial Value column header (Barclays)":
            "Hypothetical Initial Value of each Reference Asset: 100",
        "Hypothetical underlying return column header (Citigroup)": "Hypothetical underlying return",
        "Hypothetical Examples of How the Notes Might Perform (UBS)":
            "Hypothetical Examples of How the Notes Might Perform",
    }
    for label, snippet in variants.items():
        check("hypothetical_example_table" in traps.detect(snippet), f"fires on: {label}", snippet[:70])

    clean = ("The notes pay interest at 5.00% per annum, payable semi-annually, and repay principal at "
             "maturity. Minimum denominations of $1,000.")
    check("hypothetical_example_table" not in traps.detect(clean),
          "stays quiet on a document with no hypothetical-example wording at all")

    text = DISCOVERY_DOC.read_text(encoding="utf-8") if DISCOVERY_DOC.exists() else ""
    m = re.search(r"New hit rate:\W*(\d+)/(\d+)", text)
    check(m is not None, "the 138-final hit rate is printed (from the discovery doc)",
          m.group(0) if m else "not found")


async def y_task5_supersede(conn, filing_id: UUID) -> None:
    section("[Y] Task 5 — prefill_gold.py --supersede")
    from services.note_extraction import gold, prefill, store
    from services.note_extraction.schema import load_specs
    from routers.note_extraction_admin import _prefill_by_field

    specs = await load_specs(conn)
    cap_spec = next(s for s in specs if s.key == "cap_pct")

    run_1 = await store.create_run(conn, run_kind="gold_prefill", spend_cap_usd=1.0,
                                   config={"batch": BATCH, "model": "notefix1-verify-model", "verify_tag": TAG},
                                   notes_planned=1)
    await rls_exec(conn, "UPDATE portfolio.note_extraction_runs SET started_at = started_at - interval '1 second' "
                         "WHERE id = $1", run_1)
    await store.insert_readings(conn, [store.ReadingRow(
        reference_filing_id=str(filing_id), field_key="cap_pct", source="model_1", value=10.0,
        value_normalized="10.0000", run_id=run_1, origin="cascade", metadata={"gold_prefill": True})])

    refused_no_flag = False
    try:
        await prefill.check_supersede(conn, BATCH, [str(filing_id)], supersede=False)
    except prefill.PrefillRefused:
        refused_no_flag = True
    check(refused_no_flag, "a second gold_prefill run is refused WITHOUT --supersede, when one already exists")

    allowed_with_flag = True
    try:
        await prefill.check_supersede(conn, BATCH, [str(filing_id)], supersede=True)
    except prefill.PrefillRefused as exc:
        allowed_with_flag = False
        find("unexpected refusal with --supersede and zero gold values", str(exc))
    check(allowed_with_flag, "--supersede is ALLOWED while the batch has zero gold values")

    run_2 = await store.create_run(conn, run_kind="gold_prefill", spend_cap_usd=1.0,
                                   config={"batch": BATCH, "model": "notefix1-verify-model", "verify_tag": TAG},
                                   notes_planned=1)
    await store.insert_readings(conn, [store.ReadingRow(
        reference_filing_id=str(filing_id), field_key="cap_pct", source="model_1", value=99.0,
        value_normalized="99.0000", run_id=run_2, origin="cascade", metadata={"gold_prefill": True})])

    runs_for_note = await gold.prefill_runs_for(conn, str(filing_id))
    check(bool(runs_for_note) and runs_for_note[0]["run_id"] == run_2,
          "the review screen's own run-selection (gold.prefill_runs_for) picks up the superseding run cleanly "
          "(newest first, no code change needed per 1b)", f"{runs_for_note}")

    readings = await gold.readings_for(conn, str(filing_id))
    by_field = _prefill_by_field([cap_spec], readings, run_2)
    check(by_field.get("cap_pct", {}).get("value") == 99.0,
          "the review API's own helper (_prefill_by_field) serves the NEWER run's value, not the superseded one",
          f"{by_field.get('cap_pct')}")

    runs_before = await rls_val(conn, "SELECT count(*) FROM portfolio.note_extraction_runs WHERE config->>'batch' = $1 "
                                     "AND run_kind = 'gold_prefill'", BATCH)
    await gold.record_gold_value(conn, reviewer_id=str(U_SUPER), reference_filing_id=str(filing_id),
                                 spec=cap_spec, action="confirmed", value=99.0)

    refused_after_gold = False
    try:
        await prefill.check_supersede(conn, BATCH, [str(filing_id)], supersede=True)
    except prefill.PrefillRefused:
        refused_after_gold = True
    check(refused_after_gold, "--supersede is REFUSED once the batch has a human gold value, even with the flag")

    runs_after = await rls_val(conn, "SELECT count(*) FROM portfolio.note_extraction_runs WHERE config->>'batch' = $1 "
                                    "AND run_kind = 'gold_prefill'", BATCH)
    check(runs_before == runs_after == 2, "nothing was written by the refused --supersede call",
          f"before={runs_before}, after={runs_after}")


async def y_regression() -> None:
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run: regression section skipped (parent runs it)")
        return
    section("[Y] Regression: verify_notefields.py and verify_goldset.py, run ONCE each as flat subprocesses")
    for name in ("verify_notefields.py", "verify_goldset.py"):
        try:
            proc = subprocess.run([sys.executable, str(HERE.parent / name)], capture_output=True,
                                  text=True, timeout=600, cwd=str(REPO), env=os.environ | {"VERIFY_NESTED": "1"})
            out = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as exc:
            out = f"TIMEOUT after 600s: {(exc.stdout or b'')[-300:]!r}"
        m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
        check(m is not None and int(m.group(1)) > 0 and int(m.group(2)) == 0,
              f"{name} still reports 0 FAIL", m.group(0) if m else out[-300:])


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
        await run("task2 registry", y_task2_registry, conn, specs)
        await run("task2 self-checks", y_task2_self_checks)

        await run("seed user", seed_user, conn)
        fid_repro = await run("make filing (reproduce)", make_filing, conn, 1, "reproduce")
        fid_gold = await run("make filing (gold batch)", make_filing, conn, 2, "gold-batch")

        run_id_repro = await run("create verify run", _create_verify_run, conn)
        if fid_repro and run_id_repro:
            await run("reproduce + storage", y_reproduce_and_storage, conn, specs, fid_repro, run_id_repro)

        await run("task3 per-field validation", y_task3_per_field_validation)
        await run("task4 detector", y_task4_detector)

        if fid_gold:
            await run("task5 --supersede", y_task5_supersede, conn, fid_gold)

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
        teardown_clean = False
        try:
            await teardown(conn)
            left = await fixture_counts(conn)
            teardown_clean = sum(left.values()) == 0
            check(teardown_clean, "zero fixture rows remain", json.dumps(left))
            if pre_counts is not None:
                post = await table_counts(conn)
                diff = {t: (pre_counts[t], post[t]) for t in COUNTED if pre_counts[t] != post[t]}
                check(not diff, "every touched table is back at its exact pre-test count", json.dumps(diff))
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

        # Task 5: the nested regressions run ONLY after THIS teardown is
        # confirmed clean — exactly the ordering fix this sprint made to
        # verify_goldset.py's own nested verify_notefields.py regression.
        if teardown_clean:
            await y_regression()
        else:
            check(False, "regression skipped: this run's own teardown did not leave zero fixture rows")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def _create_verify_run(conn) -> str:
    from services.note_extraction import store
    return await store.create_run(conn, run_kind="verify", spend_cap_usd=1.0,
                                   config={"verify_tag": TAG, "purpose": "notefix1 reproduce+storage"})


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
