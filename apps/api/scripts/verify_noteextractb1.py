"""verify_noteextractb1.py — note extraction B1: engine, gold set, harness.

WRITTEN BY THE SPRINT, RUN BY THE OPERATOR. Pass/fail only; prints
'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. Hydrates secrets
from Doppler over HTTPS at startup. Mocked provider responses everywhere a real
call is not the point; the only real model calls are one ~16-token call to
the existing claude-haiku deployment (proves the provider-reported model is
recorded through the real proxy) and one call to a deployment that does not
exist (proves it FAILS rather than being answered by another model) — total
real spend well under $0.01.

PREREQUISITE: run scripts/move_note_terms_disagreements.py --apply first (the
MCP tool cannot run a DELETE non-interactively). Until then the "29
disagreements" section FAILS, by design.

ASSERTIONS
  [Y] Task 1's findings reported
  [Y] The 29 disagreements: gone from document_field_corrections, present in
      the readings table with their original values and models; no other
      correction row changed
  [Y] Schema generated from the registry; buffer and barrier never share a
      field; Pydantic rejects a wrong type and accepts null
  [Y] Rules on fixture snippets; a 9-character string failing the CUSIP check
      digit is rejected
  [Y] Trimming keeps the plan-of-distribution section in full
  [Y] Distribution is a list (two participants, two fees); an unknown name is
      reported, not dropped
  [Y] Fee-based-account price and price to public are separate fields
  [Y] EdgarTools runs on stored HTML with zero SEC requests; version + license recorded
  [Y] Trimming keeps key terms / payoff / fee sentence, drops risk factors;
      the recall measure is correct on a fixture
  [Y] Two readers = two separate calls to two different deployments; an
      unavailable deployment FAILS; the provider-reported model is recorded
  [Y] Prompt prefix identical across notes, filing text last, one filing per call
  [Y] A fabricated quote is rejected; a real quote's offsets map to the exact raw substring
  [Y] Agreement + verified quote -> verified, no Jev; a disagreement -> exactly
      one Jev call carrying every disputed question
  [Y] No Jev criterion contains a candidate value or a model name
  [Y] Unresolved critical -> needs_review; unresolved non-critical -> not
  [Y] Gold: super-admin entry persists (independent re-read) with the reviewer;
      org admin and member get 403; no code path lets a model write gold
  [Y] Harness metrics on a fixture equal a hand calculation
  [Y] Spending cap stops a run cleanly; dry run makes zero provider calls
  [Y] securities_global and securities_global_note_terms row counts unchanged
  [Y] No provider key, OpenRouter route, or direct litellm SDK provider call in app code
  [Y] Teardown: fixture rows gone; exact before/after counts
  [Y] npm run build exits 0

Run:  python3 apps/api/scripts/verify_noteextractb1.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
WEB_DIR = REPO / "apps" / "web"
PREMOVE = HERE.parent / "fixtures" / "noteextractb1_disagreements_premove.json"

# ═══ Fixtures — the "8b1e" block is used by no other verify script (grep'd).
# Every unique value uses a FULL UUID, never a slice of one.
ORG = UUID("99000000-0000-0000-0000-00008b1e0001")
U_SUPER = UUID("99000000-0000-0000-0000-00008b1e0011")
U_ORGADMIN = UUID("99000000-0000-0000-0000-00008b1e0012")
U_MEMBER = UUID("99000000-0000-0000-0000-00008b1e0013")
FIXTURE_USERS = [U_SUPER, U_ORGADMIN, U_MEMBER]
F_GOLD = UUID("99000000-0000-0000-0000-00008b1e0101")
F_CAP = [UUID("99000000-0000-0000-0000-00008b1e0111"), UUID("99000000-0000-0000-0000-00008b1e0112"),
         UUID("99000000-0000-0000-0000-00008b1e0113")]
FIXTURE_FILINGS = [F_GOLD, *F_CAP]
FIXTURE_FILER = "NOTEEXTRACTB1 VERIFY FIXTURE ISSUER"
REAL_FILING = UUID("d5bd4af2-c3cd-4633-8287-f014553e0ab3")   # stored Barclays 424B2 (2025Q1 corpus)
HEADERS = {"Authorization": "Bearer verify-token"}
M1, M2, ESC = "nx-verify-model-one", "nx-verify-model-two", "nx-verify-escalation"

FIXTURE_HTML = """<html><body>
<p>Pricing Supplement dated March 7, 2025</p>
<p>NXVERIFY Bank Structured Notes linked to the NXVERIFY Index</p>
<table>
<tr><td>CUSIP:</td><td>06746BCK5</td></tr>
<tr><td>Pricing Date:</td><td>March 7, 2025</td></tr>
<tr><td>Maturity Date:</td><td>March 12, 2027</td></tr>
<tr><td>Barrier Value:</td><td>70.00% of the Initial Value</td></tr>
</table>
<table>
<tr><td></td><td>Price to Public</td><td>Fees and Commissions</td><td>Proceeds to Issuer</td></tr>
<tr><td>Per Note</td><td>$1,000</td><td>$22.50</td><td>$977.50</td></tr>
</table>
<p>The estimated value of the notes on the pricing date is $965.50 per $1,000 principal amount note.</p>
<p>Minimum denominations of $1,000 and integral multiples of $1,000 in excess thereof.</p>
<h2>Payment at Maturity</h2>
<p>If the Final Value is less than the Barrier Value, you will lose 1% of the principal amount for every 1% that the Final Value is below the Initial Value.</p>
<h2>Selected Risk Considerations</h2>
<p>NXRISKTEXT You may lose some or all of your principal. The notes are subject to the credit risk of the issuer.</p>
<h2>Tax Considerations</h2>
<p>NXTAXTEXT The notes should be treated as prepaid financial contracts for U.S. federal income tax purposes.</p>
<h2>Supplemental Plan of Distribution</h2>
<p>Alpha Securities LLC will act as agent for the notes and will receive a selling commission of $15.00 per $1,000 note.</p>
<h3>Structuring Fees</h3>
<p>Zeta Platform Markets LLC, a distribution platform, will receive a structuring fee of $7.50 per $1,000 note.</p>
<p>The public offering price for investors purchasing the notes in fee-based advisory accounts will be $980.00 per note.</p>
<h2>Validity of the Notes</h2>
<p>NXVALIDITYTEXT In the opinion of counsel, the notes are valid obligations.</p>
</body></html>"""
Q_EV = "The estimated value of the notes on the pricing date is $965.50 per $1,000 principal amount note."
Q_BARRIER = "If the Final Value is less than the Barrier Value, you will lose 1% of the principal amount"
Q_PRICE = "Per Note $1,000 $22.50 $977.50"
Q_DIST_A = "Alpha Securities LLC will act as agent for the notes and will receive a selling commission of $15.00 per $1,000 note."
Q_DIST_Z = "Zeta Platform Markets LLC, a distribution platform, will receive a structuring fee of $7.50 per $1,000 note."
Q_MAT = "Maturity Date: March 12, 2027"

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []
_blocked: list[str] = []


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} (type {type(passed).__name__}), not a bool, "
        f"for label={label!r} — reversed-argument guard")
    global _ok, _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


def blocked(label: str, detail: str = "") -> None:
    print(f"[BLOCKED] {label}" + (f"  — {detail}" if detail else ""))
    _blocked.append(label)


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ═══ DB helpers — RLS context on EVERY read and write ═══════════════════════
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


def _sub(uid: UUID) -> str:
    return f"noteextractb1_{uid.hex}"


COUNT_TABLES = (
    "portfolio.note_term_readings", "portfolio.note_extraction_runs", "portfolio.note_extraction_staging",
    "portfolio.note_extraction_staged_fields", "portfolio.note_gold_values", "portfolio.note_gold_candidates",
    "portfolio.distribution_participants", "portfolio.reference_filings", "public.users",
    "public.organizations",
)


async def table_counts(conn) -> dict:
    out = {}
    for t in COUNT_TABLES:
        out[t] = await rls_val(conn, f"SELECT count(*) FROM {t}")
    return out


async def fixture_run_ids(conn) -> list:
    rows = await rls_fetch(
        conn,
        """SELECT DISTINCT run_id FROM portfolio.note_term_readings
            WHERE reference_filing_id = ANY($1::uuid[]) AND run_id IS NOT NULL
           UNION SELECT run_id FROM portfolio.note_extraction_staging WHERE reference_filing_id = ANY($1::uuid[])
           UNION SELECT id FROM portfolio.note_extraction_runs
            WHERE run_kind = 'verify' AND config->>'fixture' = 'noteextractb1'""",
        FIXTURE_FILINGS)
    return [r["run_id"] for r in rows if r["run_id"] is not None]


async def teardown(conn) -> None:
    run_ids = await fixture_run_ids(conn)
    fixture_subs = [_sub(u) for u in FIXTURE_USERS]
    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM portfolio.note_gold_values WHERE reference_filing_id = ANY($1::uuid[])",
                           FIXTURE_FILINGS)
        await conn.execute("DELETE FROM portfolio.note_extraction_staging WHERE reference_filing_id = ANY($1::uuid[]) "
                           "OR run_id = ANY($2::uuid[])", FIXTURE_FILINGS, run_ids)
        await conn.execute("DELETE FROM portfolio.note_term_readings WHERE reference_filing_id = ANY($1::uuid[]) "
                           "OR run_id = ANY($2::uuid[])", FIXTURE_FILINGS, run_ids)
        await conn.execute("DELETE FROM portfolio.note_gold_candidates WHERE reference_filing_id = ANY($1::uuid[])",
                           FIXTURE_FILINGS)
        await conn.execute("DELETE FROM portfolio.note_extraction_runs WHERE id = ANY($1::uuid[])", run_ids)
        await conn.execute("DELETE FROM portfolio.reference_filings WHERE id = ANY($1::uuid[])", FIXTURE_FILINGS)
        # BEFORE deleting fixture users: everything that may reference them.
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
                           FIXTURE_USERS, fixture_subs)
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


async def seed(conn) -> None:
    from services.rbac import ensure_role, grant_org_admin

    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "NOTEEXTRACTB1 Fixture Org", f"{ORG}-noteextractb1")
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member"), (U_MEMBER, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""",
                uid, ORG, f"{sub}@test.local", sub, sub, role)
        member_role = await ensure_role(conn, ORG, "member", "noteextractb1 fixture: no permissions")
        for uid in (U_SUPER, U_MEMBER):
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                               uid, member_role)
        await grant_org_admin(conn, U_ORGADMIN, ORG)
        for fid in FIXTURE_FILINGS:
            await conn.execute(
                """INSERT INTO portfolio.reference_filings
                     (id, cik, filer_name, form_type, accession_number, filing_date, primary_document,
                      source_url, r2_key, extraction_status)
                   VALUES ($1, '0', $2, '424B2', $3, $4, 'fixture.htm', 'https://example.invalid/fixture',
                           $5, 'pending')""",
                fid, FIXTURE_FILER, f"VERIFY-NX-{fid}", date(2025, 3, 7), f"verify/noteextractb1/{fid}.htm")


# ═══ The app, on THIS event loop (httpx.ASGITransport) ═════════════════════
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


# ═══ Mocked provider ═════════════════════════════════════════════════════
class MockProxy:
    """Replaces services.note_extraction.proxy._post. Answers per model from a
    dict of {field_key: (value, quote)}; every request is recorded."""

    def __init__(self, answers: dict, *, reported: dict | None = None, jev_prob: float = 0.95,
                 cost: str = "0.01", jev_choice: str = "candidate_1"):
        self.answers = answers
        self.reported = reported or {M1: f"{M1}-2025-01-01", M2: M2, ESC: ESC}
        self.jev_prob = jev_prob
        self.cost = cost
        self.jev_choice = jev_choice
        self.requests: list[tuple[str, dict]] = []

    async def __call__(self, path, body, timeout):
        from services.note_extraction import proxy

        self.requests.append((path, json.loads(json.dumps(body, default=str))))
        if path.endswith("/systemone"):
            ans = {k: {"choice": self.jev_choice,
                       "probabilities": {self.jev_choice: self.jev_prob,
                                         ("not_stated" if self.jev_choice != "not_stated" else "candidate_1"):
                                         round(1 - self.jev_prob, 6)}}
                   for k in body["questions"]}
            return proxy.ProxyResponse(200, {"model": body["model"], "answers": ans, "usage": {"cost_usd": 0.0}},
                                       "", {}, 3)
        model = body["model"]
        system = body["messages"][0]["content"]
        keys = list(json.loads(system.split("JSON schema:\n", 1)[1])["properties"])
        a = self.answers.get(model, {})
        out = {k: {"value": a[k][0], "quote": a[k][1]} if k in a else {"value": None, "quote": None} for k in keys}
        return proxy.ProxyResponse(
            200, {"model": self.reported.get(model, model),
                  "choices": [{"message": {"content": json.dumps(out)}}],
                  "usage": {"prompt_tokens": 1200, "completion_tokens": 150,
                            "prompt_tokens_details": {"cached_tokens": 400}}},
            "", {"x-litellm-attempted-fallbacks": "0", "x-litellm-response-cost": self.cost}, 4)

    def chat_models(self):
        return [b["model"] for p, b in self.requests if p.endswith("/chat/completions")]

    def jev_requests(self):
        return [b for p, b in self.requests if p.endswith("/systemone")]


def mock_catalog():
    from services.note_extraction.proxy import Deployment
    # Output price chosen so the pre-call ESTIMATE (max_tokens x price) is at
    # least the mocked actual cost — the cap is enforced on estimates.
    return {n: Deployment(n, None, f"verifyprov/{n}", None, 1e-9, 2.5e-6, None, False, 100000)
            for n in (M1, M2, ESC)}


Q_BARRIER_LEVEL = "Barrier Value: 70.00% of the Initial Value"
AGREE = {
    "barrier_pct": (70, Q_BARRIER_LEVEL),
    "protection_type": ("barrier", Q_BARRIER),
    "maturity_date": ("2027-03-12", Q_MAT),
    "estimated_value_pct": (96.55, Q_EV),
    "total_commissions_fees_pct": (2.25, Q_PRICE),
    "price_to_public_pct": (100, Q_PRICE),
}


def answers_with(**overrides_m2):
    m1 = dict(AGREE)
    m2 = dict(AGREE)
    m2.update(overrides_m2)
    return {M1: m1, M2: m2, ESC: dict(AGREE)}


# ═══ Sections ═══════════════════════════════════════════════════════════
async def task1_findings(conn, specs) -> None:
    section("[Y] Task 1 findings (live)")
    reported = []
    tables = await rls_val(conn, """SELECT count(*) FROM information_schema.tables WHERE (table_schema, table_name) IN
        (('portfolio','edgar_index_filings'),('portfolio','reference_filings'),
         ('portfolio','securities_global_note_terms'),('portfolio','note_terms_field_registry'))""")
    ready = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_index_filings WHERE pipeline_status = 'ready_for_extraction'")
    check(tables == 4, "1a. sprint A's objects exist", f"{tables}/4 tables")
    find("1a. ready_for_extraction", f"{ready} manifest rows are ready_for_extraction (0 at sprint time: the 200 "
         f"'fetched' rows are the 2025Q1 corpus, never classified) — the pilot needs --include-corpus or the nightly job")
    reported.append("1a")

    reg = await rls_fetch(conn, "SELECT field_key FROM portfolio.note_terms_field_registry")
    src = (API_DIR / "services" / "note_terms_extraction.py").read_text()
    check(len(reg) > 0 and "call_claude_json" in src, "1b. registry read live; the old extractor calls call_claude_json",
          f"{len(reg)} registry fields")
    find("1b. how models are called today", "services/note_terms_extraction.py -> services.extraction.call_claude_json "
         "(Anthropic /v1/messages through the proxy, WITH the app fallback chain) — unusable for an ensemble slot; "
         "B1 adds services/note_extraction/proxy.py (one deployment, no fallback)")
    reported.append("1b")

    try:
        from register_note_extraction_models import CANDIDATES, lite_llm_key_names
        names = lite_llm_key_names()
        needed = sorted({c[2] for c in CANDIDATES})
        missing = [k for k in needed if k not in names]
        check(True, "1c. prd_lite_llm key names read (names only)")
        find("1c. provider keys in prd_lite_llm", f"needed {needed}; missing {missing}"
             + ("" if not missing else " — those models are BLOCKED until the operator adds the key"))
        for k in missing:
            blocked(f"1c. {k} missing from prd_lite_llm")
        reported.append("1c")
    except Exception as exc:  # noqa: BLE001
        check(False, "1c. prd_lite_llm key names read", f"{type(exc).__name__}: {exc}")

    from services.note_extraction import edgartools_reader as er
    req = (API_DIR / "requirements.txt").read_text()
    check(er.installed_version() == er.EDGARTOOLS_PINNED_VERSION and f"edgartools=={er.EDGARTOOLS_PINNED_VERSION}" in req,
          "1d. EdgarTools pinned: installed == requirements pin", f"installed={er.installed_version()}")
    check(er.installed_license() == "MIT", "1d. EdgarTools license recorded (MIT)", f"{er.installed_license()}")
    reported.append("1d")

    import urllib.request
    try:
        with urllib.request.urlopen(os.environ["LITELLM_BASE_URL"].rstrip("/") + "/openapi.json", timeout=60) as r:
            version = json.load(r).get("info", {}).get("version")
    except Exception as exc:  # noqa: BLE001
        version = f"unreachable ({type(exc).__name__})"
    check(version == "1.96.2", "1e. proxy is LiteLLM 1.96.2 (pinned)", f"version={version}")
    find("1e. no-fallback + provider model", "disable_fallbacks:true is honoured per request; the body's 'model' is "
         "REWRITTEN to the requested alias unless metadata._complexity_router_return_raw_model_name=true, which "
         "returns the provider-reported model; x-litellm-model-id / x-litellm-attempted-fallbacks come back as headers. "
         "Also: the proxy ACCEPTED a raw upstream id (anthropic/claude-haiku-4-5-20251001) — the 'raw ids return 400' "
         "premise is not true on 1.96.2")
    reported.append("1e")
    check(len(reported) == 5, "Task 1's findings reported", f"{reported}")


async def disagreements(conn) -> None:
    section("[Y] The 29 model disagreements moved into readings")
    snap = json.loads(PREMOVE.read_text())
    ids = [UUID(r["id"]) for r in snap["rows"]]
    check(len(ids) == 29, "pre-move snapshot holds the 29 disagreements", f"{len(ids)}")
    left = await rls_val(conn, "SELECT count(*) FROM document_field_corrections WHERE id = ANY($1::uuid[])", ids)
    check(left == 0, "none of the 29 remain in document_field_corrections",
          f"{left} remain" + (" — run scripts/move_note_terms_disagreements.py --apply" if left else ""))
    rows = await rls_fetch(
        conn, """SELECT legacy_correction_id, source, value, provider_model, origin, deployment_name
                   FROM portfolio.note_term_readings WHERE legacy_correction_id = ANY($1::uuid[])""", ids)
    by = {(str(r["legacy_correction_id"]), r["source"]): r for r in rows}
    bad = []
    for s in snap["rows"]:
        for src, raw, model in (("model_1", s["original_value"], s["model_1"]),
                                ("model_2", s["corrected_value"], s["model_2"])):
            r = by.get((s["id"], src))
            if r is None:
                bad.append(f"{s['id']}:{src} missing")
                continue
            val = json.loads(r["value"]) if r["value"] is not None else None
            if (val is None) != (raw is None) or (raw is not None and str(val).lower() != str(raw).lower()):
                bad.append(f"{s['id']}:{src} value {val!r} != {raw!r}")
            if r["provider_model"] != model or r["origin"] != "migrated_correction":
                bad.append(f"{s['id']}:{src} model {r['provider_model']!r}/{r['origin']}")
    check(len(rows) == 2 * len(ids) and not bad,
          "each moved row is two readings (Model 1 = original + its model, Model 2 = corrected + its model)",
          f"{len(rows)} readings; problems: {bad[:4]}")
    captured = datetime.fromisoformat(snap["captured_at"])
    others = await rls_fetch(
        conn, """SELECT md5(coalesce(string_agg(c::text, '|' ORDER BY id), '')) AS h, count(*) AS n
                   FROM document_field_corrections c WHERE corrected_at <= $1 AND NOT (id = ANY($2::uuid[]))""",
        captured, ids)
    check(others[0]["n"] == snap["other_rows_count"] and others[0]["h"] == snap["other_rows_md5"],
          "no other correction row changed (same count and md5 as the pre-move snapshot)",
          f"n={others[0]['n']} (was {snap['other_rows_count']})")
    remaining = await rls_val(conn, "SELECT count(*) FROM document_field_corrections WHERE target_type = 'note_terms' "
                                    "AND corrected_by IS NULL")
    check(remaining == 0, "document_field_corrections holds no model disagreements (corrected_by NULL)", f"{remaining}")


def schema_section(specs) -> None:
    section("[Y] Schema generated from the registry; buffer/barrier separate; Pydantic")
    from services.note_extraction import schema

    js = schema.json_schema(specs)
    reg_keys = [s.key for s in specs if s.origin == "registry"]
    missing = [k for k in reg_keys if k not in js["properties"]]
    check(bool(reg_keys) and not missing, "every registry field is present in the generated schema",
          f"{len(reg_keys)} registry fields, missing {missing}")
    fake = [{"field_key": "nxverify_new_field", "display_label": "NX New", "data_type": "numeric"}]
    gen = schema.build_field_specs(fake)
    check("nxverify_new_field" in schema.json_schema(gen)["properties"],
          "a field added to the registry appears in the schema with no code change")
    keys = {s.key for s in specs}
    try:
        schema.assert_no_shared_protection_field(specs)
        sep = True
    except ValueError:
        sep = False
    pt = next(s for s in specs if s.key == "protection_type")
    check(sep and {"buffer_pct", "barrier_pct", "protection_pct"} <= keys
          and {"buffer", "barrier", "full", "none"} <= set(pt.enum or ()),
          "a buffer and a barrier cannot share a field (separate level fields; type keeps full/buffer/barrier/none)")
    nulls = json.dumps({k: {"value": None, "quote": None} for k in keys})
    parsed, err = schema.parse_reader_output(specs, nulls)
    check(err is None and parsed["barrier_pct"]["value"] is None, "Pydantic accepts null", f"{err}")
    parsed, err = schema.parse_reader_output(specs, json.dumps({"barrier_pct": {"value": "seventy", "quote": "x"}}))
    check(parsed is None and err is not None and "validation" in err,
          "Pydantic rejects a wrong type (a word for a number) — a failed reading, not a crash", f"{err and err[:80]}")
    parsed, err = schema.parse_reader_output(specs, json.dumps({"maturity_date": {"value": True, "quote": None}}))
    check(parsed is None and err is not None, "Pydantic rejects a boolean for a date")


def rules_section(doc) -> None:
    section("[Y] Rules on fixture snippets")
    from services.note_extraction import documents, rules, trim

    tr = trim.trim(doc.text)
    r = rules.run_rules(doc.text, distribution_spans=tr.distribution_spans)
    expect = {"cusip": "06746BCK5", "pricing_date": "2025-03-07", "maturity_date": "2027-03-12",
              "estimated_value_pct": 96.55, "price_to_public_pct": 100, "total_commissions_fees_pct": 2.25,
              "agent_commission_pct": 1.5, "fee_based_account_price_pct": 98, "denomination_amount": 1000}
    for k, v in expect.items():
        got = r.hits.get(k)
        check(got is not None and got.value == v, f"rule: {k} = {v!r}", f"got {got.value if got else None!r}")
        if got:
            loc = documents.locate_quote(doc, got.quote)
            check(loc is not None, f"rule: {k}'s quote is found in the filing")
    bad = documents.document_from_html("<p>CUSIP:</p><p>06746BCK4</p>")
    rb = rules.run_rules(bad.text)
    check("cusip" not in rb.hits and any(x["candidate"] == "06746BCK4" for x in rb.rejected),
          "a labeled 9-character string that fails the CUSIP check digit is REJECTED (and reported)",
          f"rejected={rb.rejected}")
    bare = documents.document_from_html("<p>Reference number 06746BCK5 appears without its label.</p>")
    check("cusip" not in rules.run_rules(bare.text).hits, "a bare valid-looking token with no CUSIP label is not taken")


def trim_section(doc) -> None:
    section("[Y] Trimming: plan of distribution in full; keeps terms/payoff/fees; drops risk; recall")
    from services.note_extraction import trim

    tr = trim.trim(doc.text)
    a = doc.text.index("Supplemental Plan of Distribution")
    b = doc.text.index("Validity of the Notes")
    pod = doc.text[a:b].strip()
    check(pod in tr.text, "the plan-of-distribution section is kept IN FULL (incl. its 'Structuring Fees' sub-heading)",
          f"{len(pod)} chars")
    check("Zeta Platform Markets LLC" in tr.text and "fee-based advisory accounts" in tr.text,
          "the PoD's distributor and fee-based-account price survive trimming")
    check(Q_EV in tr.text and "CUSIP" in tr.text and Q_BARRIER in tr.text and "$22.50" in tr.text,
          "trimming keeps the key terms, the payoff and the estimated-value / fee sentences")
    check("NXRISKTEXT" not in tr.text and "NXTAXTEXT" not in tr.text and "NXVALIDITYTEXT" not in tr.text,
          "trimming drops risk factors, tax and validity boilerplate")
    check(tr.kept_chars < tr.full_chars and tr.trimmed_tokens_est == trim.estimate_tokens(tr.text),
          "the trimmed token count is recorded", f"{tr.trimmed_tokens_est} of {tr.full_tokens_est}")
    rec = trim.recall(tr.text, [Q_EV, "NXRISKTEXT You may lose some or all", "", Q_DIST_Z])
    check(rec["total"] == 3 and rec["found"] == 2 and abs(rec["recall"] - 2 / 3) < 1e-12,
          "recall measure = hand calculation (2 of 3 non-empty quotes kept)", f"{rec}")


def distribution_section(doc, specs) -> None:
    section("[Y] Distribution is a list; unknown participant reported; fee-based price separate")
    from services.note_extraction import participants, rules, schema, trim

    tr = trim.trim(doc.text)
    names = [p["name"] for p in rules.run_rules(doc.text, distribution_spans=tr.distribution_spans).participant_names]
    check(names == ["Alpha Securities LLC", "Zeta Platform Markets LLC"],
          "rules find both participant names in the plan of distribution", f"{names}")
    two = [
        {"name": "Alpha Securities LLC", "role": "distribution_agent", "fee_type": "selling_commission",
         "fee_pct": 1.5, "fee_per_unit": 15.0, "quote": Q_DIST_A},
        {"name": "Zeta Platform Markets LLC", "role": "placement_agent", "fee_type": "structuring_fee",
         "fee_pct": 0.75, "fee_per_unit": 7.5, "quote": Q_DIST_Z},
    ]
    raw = json.dumps({"distribution": {"value": two, "quote": Q_DIST_A}})
    parsed, err = schema.parse_reader_output(specs, raw)
    dist = (parsed or {}).get("distribution", {}).get("value")
    check(err is None and isinstance(dist, list) and len(dist) == 2
          and [p["role"] for p in dist] == ["distribution_agent", "placement_agent"]
          and [p["fee_type"] for p in dist] == ["selling_commission", "structuring_fee"]
          and [p["fee_pct"] for p in dist] == [1.5, 0.75],
          "two participants with different fees -> two entries, each with its role and fee", f"{dist} {err}")
    single = json.dumps({"distribution": {"value": "Alpha Securities LLC and Zeta Platform Markets LLC", "quote": None}})
    check(schema.parse_reader_output(specs, single)[0] is None,
          "a single distribution-agent STRING is refused (the field is a list)")
    known = [{"id": uuid4(), "canonical_name": "Alpha Securities LLC", "participant_type": "dealer",
              "aliases": ["Alpha Securities"], "status": "active"}]
    m = participants.match_names(dist, known)
    check([x["name"] for x in m.matched] == ["Alpha Securities LLC"]
          and [x["name"] for x in m.unmatched] == ["Zeta Platform Markets LLC"],
          "the unknown participant is REPORTED as unmatched, not dropped", f"matched={len(m.matched)} unmatched={m.unmatched}")
    keys = {s.key for s in specs}
    r = rules.run_rules(doc.text)
    check({"fee_based_account_price_pct", "price_to_public_pct"} <= keys
          and r.hits["price_to_public_pct"].value == 100 and r.hits["fee_based_account_price_pct"].value == 98,
          "fee-based-account price (98) and price to public (100) are separate fields with separate values")


async def edgartools_section(conn) -> None:
    section("[Y] EdgarTools on STORED HTML, zero SEC requests")
    import socket
    from services.note_extraction import documents, edgartools_reader as er

    try:
        doc = await documents.load_document(conn, REAL_FILING)
    except Exception as exc:  # noqa: BLE001
        check(False, "a real stored filing loads from R2 (hollisworks-docs)", f"{type(exc).__name__}: {exc}")
        return
    res = await asyncio.to_thread(er.read_html, doc.html)
    check(res.ok and len(res.fields) > 0, "EdgarTools parses the stored HTML of a real 424B2",
          f"fields={sorted(res.fields)} tables={res.tables_found} err={res.error}")
    check(res.network_attempts == [], "EdgarTools made ZERO network attempts (socket/DNS instrumented)",
          f"{res.network_attempts}")
    check(res.version == er.EDGARTOOLS_PINNED_VERSION and res.license == "MIT",
          "the reading records EdgarTools' version and license", f"{res.version} {res.license}")
    attempts: list = []
    refused = False
    with er.network_blocked(attempts):
        try:
            socket.create_connection(("www.sec.gov", 443), timeout=3)
        except er.NetworkBlocked:
            refused = True
    check(refused and len(attempts) == 1, "the block is real: a deliberate SEC connection inside it is refused and recorded")


async def readers_section(conn, specs, doc) -> None:
    section("[Y] Two readers, two deployments; unavailable deployment fails; provider model recorded")
    from services.note_extraction import cascade, proxy, readers
    from services.note_extraction.spend import SpendTracker

    mock = MockProxy(answers_with())
    real_post = proxy._post
    proxy._post = mock
    try:
        slots = cascade.EnsembleSlots(readers.ReaderConfig("model_1", M1), readers.ReaderConfig("model_2", M2),
                                      "jev-1.13.0", None)
        out = await cascade.run_note(doc, specs, slots, catalog=mock_catalog(), spend=SpendTracker(10.0),
                                     participant_rows=[])
    finally:
        proxy._post = real_post
    models = mock.chat_models()
    check(models[:2] in ([M1, M2], [M2, M1]) and len(set(models[:2])) == 2,
          "Model 1 and Model 2 are two SEPARATE calls to two different deployments", f"{models}")
    bodies = [b for p, b in mock.requests if p.endswith("/chat/completions")]
    check(all(b.get("disable_fallbacks") is True and (b.get("metadata") or {}).get(proxy.RAW_MODEL_METADATA_KEY) is True
              and "fallbacks" not in b for b in bodies),
          "every reader call carries disable_fallbacks:true + the raw-model flag, and no fallbacks list")
    m1_rows = [r for r in out.readings if r.source == "model_1" and r.field_key != "__call__"]
    check(bool(m1_rows) and all(r.provider_model == f"{M1}-2025-01-01" for r in m1_rows),
          "the provider-reported model is recorded on every Model 1 reading", f"{m1_rows[0].provider_model if m1_rows else None}")
    try:
        with pytest_raises(ValueError):
            cascade.EnsembleSlots(readers.ReaderConfig("model_1", M1), readers.ReaderConfig("model_2", M1), None, None)
        same_refused = True
    except AssertionError:
        same_refused = False
    check(same_refused, "an ensemble with the same deployment twice is refused")

    # A mismatch between asked-for and provider-reported model is a FAILED reading.
    mis = MockProxy(answers_with(), reported={M1: "some-other-model", M2: M2})
    proxy._post = mis
    try:
        call = await readers.read(readers.ReaderConfig("model_1", M1), specs, "text", filer="x", form_type="424B2",
                                  catalog=mock_catalog(), spend=None)
    finally:
        proxy._post = real_post
    check(call.status == "model_mismatch" and not call.usable and call.fields == {},
          "a provider-reported model that differs from the requested deployment is a failed reading", f"{call.error}")

    # REAL calls through the real proxy.
    catalog = await asyncio.to_thread(proxy.deployment_catalog)
    sub = [s for s in specs if s.key == "cusip"]
    gone = await readers.read(readers.ReaderConfig("model_1", "nx-verify-deployment-that-does-not-exist", None, 16),
                              sub, "CUSIP: 06746BCK5", filer="x", form_type="424B2", catalog=catalog, spend=None)
    check(gone.status == "failed" and gone.provider_model is None and (gone.error or "").startswith("HTTP 4"),
          "REAL: a call to an unavailable deployment FAILS — no other model answers it", f"{gone.error and gone.error[:120]}")
    if "claude-haiku" in catalog:
        live = await readers.read(readers.ReaderConfig("model_1", "claude-haiku", None, 200), sub,
                                  "CUSIP: 06746BCK5", filer="x", form_type="424B2", catalog=catalog, spend=None)
        check(live.provider_model is not None and proxy.reported_model_matches(catalog["claude-haiku"].upstream,
                                                                               live.provider_model)
              and live.status in ("ok", "invalid"),
              "REAL: the provider-reported model comes back through the proxy and is recorded",
              f"reported={live.provider_model!r} status={live.status} cost={live.cost_usd}")
    else:
        blocked("REAL provider-model call", "no claude-haiku deployment on the proxy")


class pytest_raises:  # tiny stand-in, no pytest dependency
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError("expected an exception")
        return issubclass(et, self.exc)


def prompt_section(specs) -> None:
    section("[Y] Prompt prefix identical; filing text last; one filing per call")
    from services.note_extraction import readers

    a_msgs, a_hash = readers.build_messages(specs, "FILING-TEXT-ALPHA body", filer="Issuer A", form_type="424B2")
    b_msgs, b_hash = readers.build_messages(specs, "FILING-TEXT-BRAVO body", filer="Issuer B", form_type="FWP")
    check(a_hash == b_hash and a_msgs[0] == b_msgs[0], "the fixed prefix (instructions + schema) hashes the same across notes",
          a_hash[:12])
    check(a_msgs[-1]["content"].endswith("FILING-TEXT-ALPHA body") and b_msgs[-1]["content"].endswith("FILING-TEXT-BRAVO body"),
          "the filing text is the LAST thing in the call")
    check("FILING-TEXT-BRAVO" not in json.dumps(a_msgs) and json.dumps(a_msgs).count("FILING TEXT:") == 1,
          "a call carries exactly one filing")
    src = (API_DIR / "services" / "note_extraction" / "readers.py").read_text()
    check(not re.search(r"def\s+read\s*\([^)]*filings\s*:", src), "the reader API has no multi-filing parameter")


def quotes_section(doc) -> None:
    section("[Y] Fabricated quote rejected; real quote maps to the exact raw substring")
    from services.note_extraction import compare, documents, schema

    spec = schema.FieldSpec("estimated_value_pct", "EV", schema.KIND_NUMBER, "")
    fab = "The estimated value of the notes is $999.00 per $1,000 according to our invented model."
    check(documents.locate_quote(doc, fab) is None, "a fabricated quote is not found in the filing")
    e = compare.evidence(doc, spec, "model_1", 99.9, fab)
    check(e.quote_verified is False and e.supports is False, "a fabricated quote cannot support a value")
    loc = documents.locate_quote(doc, Q_EV)
    check(loc is not None and loc.raw_start is not None and doc.html[loc.raw_start:loc.raw_end] == Q_EV,
          "a real quote's offsets map to the EXACT substring of the raw HTML",
          f"{loc and (loc.raw_start, loc.raw_end)}")
    e2 = compare.evidence(doc, spec, "model_1", 96.55, Q_EV)
    check(e2.quote_verified is True and e2.value_in_quote is True, "the value appears in its real quote ($965.50 per $1,000 = 96.55)")


async def jev_section(specs, doc) -> None:
    section("[Y] Agreement verified with no Jev; disagreement -> ONE Jev call with every disputed question")
    from services.note_extraction import cascade, proxy, readers
    from services.note_extraction.spend import SpendTracker

    real_post = proxy._post
    slots = cascade.EnsembleSlots(readers.ReaderConfig("model_1", M1), readers.ReaderConfig("model_2", M2),
                                  "jev-1.13.0", None)
    agree = MockProxy(answers_with())
    proxy._post = agree
    try:
        out = await cascade.run_note(doc, specs, slots, catalog=mock_catalog(), spend=SpendTracker(10.0),
                                     participant_rows=[])
    finally:
        proxy._post = real_post
    staged = {f.field_key: f for f in out.staged}
    check(staged["estimated_value_pct"].resolution == "verified_agreement"
          and staged["barrier_pct"].resolution == "verified_agreement",
          "both readers agree + the quote verifies -> 'verified_agreement'")
    check(len(agree.jev_requests()) == 0 and out.jev_calls == 0, "...and Jev is NOT called")

    dis = MockProxy(answers_with(barrier_pct=(75, Q_BARRIER_LEVEL), estimated_value_pct=(96.0, Q_EV)))
    proxy._post = dis
    try:
        out2 = await cascade.run_note(doc, specs, slots, catalog=mock_catalog(), spend=SpendTracker(10.0),
                                      participant_rows=[])
    finally:
        proxy._post = real_post
    jr = dis.jev_requests()
    check(len(jr) == 1, "a note with disagreements gets EXACTLY ONE Jev call", f"{len(jr)} calls")
    qs = set(jr[0]["questions"]) if jr else set()
    check(qs == {"barrier_pct", "estimated_value_pct"},
          "that one call carries EVERY disputed question (and nothing verified)", f"{sorted(qs)}")
    check(len(jr[0]["state"]) <= 80_000 if jr else False, "the Jev state is the relevant clauses, inside the 32K budget",
          f"{len(jr[0]['state']) if jr else None} chars")

    section("[Y] No Jev criterion contains a candidate value or a model name")
    forbidden = {"75", "70", "96.0", "96", "96.55", M1, M2, f"{M1}-2025-01-01", "model_1", "model_2",
                 "claude", "gpt", "gemini", "rules", "edgartools"}
    leaks = []
    instr_has_values = True
    for key, q in (jr[0]["questions"].items() if jr else []):
        for opt, text in q["criteria"].items():
            for f in forbidden:
                if re.search(rf"(?<![\w.]){re.escape(f)}(?![\w])", text, re.I):
                    leaks.append(f"{key}.{opt}:{f}")
        if key == "barrier_pct" and not ("75" in q["instructions"] and "70" in q["instructions"]):
            instr_has_values = False
    check(bool(jr) and not leaks, "no criterion contains a candidate value, a source name or a model name", f"{leaks[:5]}")
    check(bool(jr) and instr_has_values, "the candidates are offered in the QUESTION about the document (so the check is not vacuous)")
    check(bool(jr) and all("model" not in q["instructions"].lower() for q in jr[0]["questions"].values()),
          "no question asks which model was right")

    section("[Y] Unresolved critical -> needs_review; unresolved non-critical -> not")
    low = MockProxy({**answers_with(barrier_pct=(75, Q_BARRIER_LEVEL)),
                     M1: {**AGREE, "cap_pct": (25, Q_EV)}, M2: {**AGREE, "barrier_pct": (75, Q_BARRIER_LEVEL),
                                                                 "cap_pct": (30, Q_EV)}}, jev_prob=0.5)
    proxy._post = low
    try:
        out3 = await cascade.run_note(doc, specs, slots, catalog=mock_catalog(), spend=SpendTracker(10.0),
                                      participant_rows=[])
    finally:
        proxy._post = real_post
    st = {f.field_key: f for f in out3.staged}
    check(st["barrier_pct"].resolution == "unresolved" and st["barrier_pct"].needs_review is True
          and out3.status == "needs_review",
          "an unresolved CRITICAL field (barrier) sends the note to needs_review", f"{out3.status_reason}")
    check(st["cap_pct"].resolution == "unresolved" and st["cap_pct"].needs_review is False,
          "an unresolved NON-critical field (cap) does not")


async def gold_section(conn, specs) -> None:
    section("[Y] Gold values: super admin only; reviewer recorded; no model path")
    path = f"/api/v1/admin/note-extraction/gold/notes/{F_GOLD}/fields/barrier_pct"
    body = {"action": "corrected", "value": 70.0, "notes": "verify fixture"}
    before = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_values WHERE reference_filing_id = $1", F_GOLD)
    s_oa, b_oa = await api(U_ORGADMIN, "PUT", path, body)
    s_m, b_m = await api(U_MEMBER, "PUT", path, body)
    mid = await rls_val(conn, "SELECT count(*) FROM portfolio.note_gold_values WHERE reference_filing_id = $1", F_GOLD)
    check(s_oa == 403 and s_m == 403, "org admin and member get 403 on the identical request",
          f"org_admin {s_oa} {_detail(b_oa)} | member {s_m} {_detail(b_m)}")
    check(mid == before, "...and no gold row was written by them", f"{before} -> {mid}")
    s, b = await api(U_SUPER, "PUT", path, body)
    check(s == 200 and (b.get("saved") or {}).get("action") == "corrected", "super admin's gold entry is accepted",
          f"HTTP {s} {_detail(b) if s != 200 else ''}")
    check(isinstance(b, dict) and (b.get("permissions") or {}).get("can_write") is True
          and (b.get("vocabularies") or {}).get("editable") == ["value", "action", "notes"],
          "the response publishes the permission envelope (editable from the server)")
    url = os.environ["DATABASE_URL"]
    c2 = await asyncpg.connect(url, statement_cache_size=0)
    try:
        row = await rls_row(c2, """SELECT g.value, g.reviewer_id, g.reviewed_at, u.auth0_sub
                                     FROM portfolio.note_gold_values g JOIN users u ON u.id = g.reviewer_id
                                    WHERE g.reference_filing_id = $1 AND g.field_key = 'barrier_pct' AND g.valid_to IS NULL""",
                            F_GOLD)
        human = await rls_val(c2, """SELECT count(*) FROM portfolio.note_term_readings
                                      WHERE reference_filing_id = $1 AND field_key = 'barrier_pct' AND source = 'human'""",
                              F_GOLD)
    finally:
        await c2.close()
    check(row is not None and json.loads(row["value"]) == 70 and row["auth0_sub"] == _sub(U_SUPER)
          and row["reviewed_at"] is not None,
          "the gold value persists (INDEPENDENT connection re-read) with the reviewer and timestamp recorded",
          f"{dict(row) if row else None}")
    check(human == 1, "the same write appended one 'human' reading (readings hold every source)", f"{human}")
    s2, _ = await api(U_SUPER, "PUT", path, {"action": "confirmed", "value": 70.0})
    counts = await rls_row(conn, """SELECT count(*) AS total, count(*) FILTER (WHERE valid_to IS NULL) AS open_
                                      FROM portfolio.note_gold_values WHERE reference_filing_id = $1 AND field_key = 'barrier_pct'""",
                           F_GOLD)
    check(s2 == 200 and counts["total"] == 2 and counts["open_"] == 1,
          "a re-review closes the old row and inserts a new one (bi-temporal, never edited in place)", f"{dict(counts)}")

    # The database refuses a write that did not come through the human path.
    refused = False
    try:
        async with conn.transaction():
            await _super(conn)
            await conn.execute("""INSERT INTO portfolio.note_gold_values (reference_filing_id, field_key, value, action, reviewer_id)
                                  VALUES ($1, 'cap_pct', '25'::jsonb, 'corrected', $2)""", F_GOLD, U_SUPER)
    except asyncpg.InsufficientPrivilegeError:
        refused = True
    check(refused, "the DATABASE refuses a gold write with super-admin context but no signed-in reviewer")
    refused2 = False
    try:
        async with conn.transaction():
            await _super(conn)
            await conn.execute("SELECT set_config('app.gold_reviewer_id', $1, true)", str(U_MEMBER))
            await conn.execute("""INSERT INTO portfolio.note_gold_values (reference_filing_id, field_key, value, action, reviewer_id)
                                  VALUES ($1, 'cap_pct', '25'::jsonb, 'corrected', $2)""", F_GOLD, U_SUPER)
    except asyncpg.InsufficientPrivilegeError:
        refused2 = True
    check(refused2, "...and one whose reviewer_id differs from the signed-in reviewer")

    hits_guc, hits_insert, hits_call = [], [], []
    for p in API_DIR.rglob("*.py"):
        rel = p.relative_to(API_DIR).as_posix()
        if rel.startswith(("venv/", "migrations/")) or "__pycache__" in rel or rel == "scripts/verify_noteextractb1.py":
            continue
        t = p.read_text(errors="replace")
        if "app.gold_reviewer_id" in t:
            hits_guc.append(rel)
        if re.search(r"(INSERT\s+INTO|UPDATE)\s+portfolio\.note_gold_values", t, re.I):
            hits_insert.append(rel)
        if re.search(r"\brecord_gold_value\s*\(", t) and rel != "services/note_extraction/gold.py":
            hits_call.append(rel)
    check(hits_guc == ["services/note_extraction/gold.py"] and hits_insert == ["services/note_extraction/gold.py"]
          and hits_call == ["routers/note_extraction_admin.py"],
          "no code path lets a model write gold: only gold.py writes it, called only by the super-admin router",
          f"guc={hits_guc} writes={hits_insert} callers={hits_call}")

    # Client-side proof: the REAL envelope through the screen's own gate.
    s_get, env = await api(U_SUPER, "GET", f"/api/v1/admin/note-extraction/gold/notes/{F_GOLD}")
    s_get_m, _ = await api(U_MEMBER, "GET", f"/api/v1/admin/note-extraction/gold/notes/{F_GOLD}")
    payloads = {
        "super_admin": env if s_get == 200 else None,
        "member_403": None,
        "lost_envelope": {k: v for k, v in (env or {}).items() if k not in ("permissions", "vocabularies")},
        "string_true": {**(env or {}), "permissions": {**((env or {}).get("permissions") or {}), "can_write": "true"}},
        "editable_empty": {**(env or {}), "vocabularies": {"editable": [], "inline_editable": []}},
    }
    proc = subprocess.run(["node", str(HERE.parent / "noteextractb1_gold_gate_harness.mjs")],
                          input=json.dumps(payloads), capture_output=True, text=True, timeout=60)
    try:
        gates = json.loads(proc.stdout)
    except ValueError:
        gates = {}
    check(s_get == 200 and s_get_m == 403 and gates.get("super_admin") is True,
          "UI: the super admin's real envelope renders write controls", f"{gates} {proc.stderr[:200]}")
    check(all(gates.get(k) is False for k in ("member_403", "lost_envelope", "string_true", "editable_empty")),
          "UI: a 403, a lost envelope, a non-boolean can_write or an empty editable list render NO write control",
          f"{gates}")


def metrics_section() -> None:
    section("[Y] Harness metrics equal a hand calculation")
    from services.note_extraction import metrics

    gold = {("n1", "a"): "1", ("n1", "b"): None, ("n2", "a"): "2", ("n2", "b"): "x"}
    readings = {("n1", "a"): {"normalized": "1", "quote_verified": True},
                ("n1", "b"): {"normalized": "y", "quote_verified": False},
                ("n2", "a"): {"normalized": None, "quote_verified": None}}
    fm = metrics.field_metrics(gold, readings)
    o = fm["overall"]
    check(o["accuracy"] == 0.25 and abs(o["null_rate"] - 2 / 3) < 1e-12 and o["invented_rate"] == 0.5,
          "field metrics: accuracy 1/4, null rate 2/3, invented rate 1/2", f"{o}")
    a = fm["by_field"]["a"]
    check(a["accuracy"] == 0.5 and a["null_rate"] == 0.5 and a["invented_rate"] == 0.0,
          "per-field metrics (field a): 1/2, 1/2, 0", f"{a}")
    cm = metrics.call_metrics([{"cost_usd": 0.01, "latency_ms": 100, "input_tokens": 1000, "cached_tokens": 250},
                               {"cost_usd": 0.03, "latency_ms": 300, "input_tokens": 1000, "cached_tokens": 0}], 2)
    check(abs(cm["cost_per_note"] - 0.02) < 1e-12 and cm["latency_ms_median"] == 200 and cm["cache_share"] == 0.125,
          "cost per note $0.02, median latency 200 ms, cached share 0.125", f"{cm}")
    sk = metrics.skip_second_reader([True, None, False, True])
    check(sk["skip_would_apply"] == 3 and sk["safe"] == 2 and abs(sk["safe_rate"] - 2 / 3) < 1e-12,
          "skip-second-reader: applies 3, safe 2", f"{sk}")
    ja = metrics.jev_accuracy({("n1", "a"): {"normalized": "1", "accepted": True},
                               ("n2", "a"): {"normalized": "3", "accepted": False}}, gold)
    check(ja["accuracy"] == 0.5 and ja["accepted"] == 1 and ja["accepted_accuracy"] == 1.0, "Jev accuracy 1/2", f"{ja}")
    tr = metrics.trim_recall([{"total": 2, "found": 1}, {"total": 3, "found": 3}])
    check(tr["recall"] == 0.8, "pooled trim recall 4/5", f"{tr}")
    cov = metrics.source_coverage(gold, {("n1", "a"): {"normalized": "1"}, ("n2", "b"): {"normalized": "z"}},
                                  {"n1": "I1", "n2": "I2"})
    check(abs(cov["by_field"]["a"]["coverage"] - 0.5) < 1e-12 and cov["by_field"]["a"]["accuracy"] == 1.0
          and cov["by_field"]["b"]["accuracy"] == 0.0, "EdgarTools-style coverage/accuracy per field", f"{cov['by_field']}")
    base = {"underlyings": "spx", "maturity_date": "2027-03-12", "barrier_pct": "70", "coupon_rate": "9",
            "autocall_barrier_pct": "100"}
    same = metrics.identical_terms_across_cusips([
        {"note": "a", "issuer": "X", "cusip": "C1", "fields": base, "total_commissions_fees_pct": 2.0},
        {"note": "b", "issuer": "X", "cusip": "C2", "fields": base, "total_commissions_fees_pct": 0.5},
        {"note": "c", "issuer": "X", "cusip": "C3", "fields": {**base, "barrier_pct": "60"}}])
    check(len(same) == 1 and same[0]["cusips"] == ["C1", "C2"],
          "identical terms under different CUSIPs are grouped (a pricing comparison)", f"{same}")


async def spend_section(conn, specs, doc) -> None:
    section("[Y] Spending cap stops a run cleanly; dry run makes zero provider calls")
    from services.note_extraction import cascade, proxy, readers, runner
    from services.note_extraction.spend import Plan, SpendTracker

    real_post = proxy._post
    slots = cascade.EnsembleSlots(readers.ReaderConfig("model_1", M1), readers.ReaderConfig("model_2", M2), None, None)
    mock = MockProxy(answers_with())
    proxy._post = mock
    try:
        one = await cascade.run_note(doc, specs, slots, catalog=mock_catalog(), spend=SpendTracker(100.0),
                                     participant_rows=[])
        per_note = one.cost_usd
        cap = per_note + 0.015
        summary = await runner.run_notes(
            conn, [str(f) for f in F_CAP], specs, slots, catalog=mock_catalog(), spend_cap_usd=cap,
            run_kind="verify", config={"fixture": "noteextractb1"}, participant_rows=[],
            downloader=lambda key, bucket: FIXTURE_HTML.encode())
    finally:
        proxy._post = real_post
    run = await rls_row(conn, "SELECT status, notes_done, spent_usd, stop_reason FROM portfolio.note_extraction_runs WHERE id = $1",
                        summary.run_id)
    staged = await rls_val(conn, "SELECT count(*) FROM portfolio.note_extraction_staging WHERE run_id = $1", summary.run_id)
    recorded = await rls_val(conn, "SELECT COALESCE(SUM(cost_usd), 0) FROM portfolio.note_term_readings WHERE run_id = $1",
                             summary.run_id)
    check(run is not None and run["status"] == "stopped_spend_cap" and run["notes_done"] == 1 and staged == 1,
          "the run STOPS at the cap: status stopped_spend_cap, the finished note staged, nothing after it",
          f"{dict(run) if run else None} staged={staged} per_note=${per_note:.4f} cap=${cap:.4f}")
    check(run is not None and abs(float(run["spent_usd"]) - float(recorded)) < 1e-9 and float(recorded) <= cap + 1e-9
          and float(recorded) > per_note,
          "spend is enforced from RECORDED costs: spent = SUM(cost_usd), never above the cap, and the interrupted "
          "note's completed call is recorded too", f"spent={run and run['spent_usd']} recorded={recorded}")
    nss = await rls_val(conn, "SELECT count(*) FROM portfolio.note_extraction_staging s WHERE s.run_id = $1 "
                              "AND s.reference_filing_id = ANY($2::uuid[])", summary.run_id, F_CAP[1:])
    check(nss == 0, "the note in flight when the cap hit is NOT staged", f"{nss}")

    calls_before = dict(proxy.CALLS)
    counting = MockProxy(answers_with())
    proxy._post = counting
    try:
        plan = Plan()
        runner.plan_note(plan, doc, specs, cascade.EnsembleSlots(
            readers.ReaderConfig("model_1", M1), readers.ReaderConfig("model_2", M2), "jev-1.13.0",
            readers.ReaderConfig("escalation", ESC)), mock_catalog())
    finally:
        proxy._post = real_post
    check(proxy.CALLS == calls_before and counting.requests == [] and len(plan.calls) >= 2 and plan.total_usd > 0,
          "DRY RUN: planned calls + an estimated cost, ZERO provider calls", f"{plan.summary()['by_slot']}")
    for script in ("run_note_extraction_pilot.py", "run_note_extraction_eval.py"):
        t = (HERE.parent / script).read_text()
        check("--dry-run" in t and "assert proxy.CALLS == before" in t and 'required=True' in t,
              f"{script}: has --dry-run (asserting zero calls) and a REQUIRED --spend-cap")


def source_section() -> None:
    section("[Y] No provider key, OpenRouter route or direct litellm SDK call in application code")
    files = [p for p in API_DIR.rglob("*.py")
             if not p.relative_to(API_DIR).as_posix().startswith(("venv/", "migrations/"))
             and "__pycache__" not in p.parts]
    new_files = [p for p in files if "note_extraction" in p.as_posix() or p.name in (
        "register_note_extraction_models.py", "run_note_extraction_pilot.py", "run_note_extraction_eval.py",
        "sample_gold_set.py", "seed_distribution_participants.py", "_note_extraction_common.py",
        "move_note_terms_disagreements.py")]
    key_re = re.compile(r"(sk-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{30,}|\"api_key\"\s*:\s*\"(?!os\.environ/)[^\"]+\")")
    leaks = [p.name for p in new_files if key_re.search(p.read_text(errors="replace"))]
    check(bool(new_files) and not leaks, "no provider key literal in the sprint's code (keys are os.environ/NAME)",
          f"{len(new_files)} files scanned; leaks={leaks}")
    orr = [p.name for p in new_files if re.search(r"openrouter/", p.read_text(errors="replace"), re.I)]
    check(not orr, "no OpenRouter route in the sprint's code", f"{orr}")
    sdk = []
    for p in files:
        rel = p.relative_to(API_DIR).as_posix()
        if rel.startswith("scripts/verify_"):
            continue
        t = p.read_text(errors="replace")
        if re.search(r"^\s*(import\s+litellm\b|from\s+litellm\b)", t, re.M) or re.search(r"\blitellm\.(a?completion|acompletion)\(", t):
            sdk.append(rel)
    check(not sdk, "no direct litellm SDK provider call anywhere in application code", f"{sdk}")
    from register_note_extraction_models import CANDIDATES
    check(all(c[2].endswith("_API_KEY") and "openrouter" not in c[1] for c in CANDIDATES),
          "every registration references its key by NAME only, none via OpenRouter")


def npm_build() -> None:
    section("[Y] npm run build")
    proc = subprocess.run(["npm", "run", "build"], cwd=WEB_DIR, capture_output=True, text=True, timeout=1500)
    check(proc.returncode == 0, "npm run build exits 0", f"rc={proc.returncode} {proc.stderr[-300:] if proc.returncode else ''}")


async def main_async() -> int:
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    conn = await asyncpg.connect(url, statement_cache_size=0)
    from services.note_extraction import documents, schema

    sec_before = None
    counts_before = None
    try:
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the verify connection's role does NOT bypass RLS", f"{await conn.fetchval('SELECT current_user')}")
        await teardown(conn)                     # leftovers from an aborted earlier run
        counts_before = await table_counts(conn)
        sec_before = (await rls_val(conn, "SELECT count(*) FROM portfolio.securities_global"),
                      await rls_val(conn, "SELECT count(*) FROM portfolio.securities_global_note_terms"))
        await seed(conn)
        specs = schema.build_field_specs(await rls_fetch(
            conn, "SELECT field_key, display_label, data_type FROM portfolio.note_terms_field_registry ORDER BY field_key"))
        doc = documents.document_from_html(FIXTURE_HTML, reference_filing_id=str(F_GOLD),
                                           filer_name=FIXTURE_FILER, form_type="424B2")

        async def run(name, coro):
            try:
                if asyncio.iscoroutine(coro):
                    await coro
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")

        def sync(name, fn, *a):
            try:
                fn(*a)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")

        await run("task1", task1_findings(conn, specs))
        await run("disagreements", disagreements(conn))
        sync("schema", schema_section, specs)
        sync("rules", rules_section, doc)
        sync("trim", trim_section, doc)
        sync("distribution", distribution_section, doc, specs)
        await run("edgartools", edgartools_section(conn))
        await run("readers", readers_section(conn, specs, doc))
        sync("prompt", prompt_section, specs)
        sync("quotes", quotes_section, doc)
        await run("jev", jev_section(specs, doc))
        from services.database import get_pool
        await get_pool()                         # the app's pool on THIS loop
        await run("gold", gold_section(conn, specs))
        sync("metrics", metrics_section)
        await run("spend", spend_section(conn, specs, doc))
        sync("source", source_section)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "the verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            section("[Y] securities_global / note_terms unchanged; teardown exact")
            if sec_before is not None:
                sec_after = (await rls_val(conn, "SELECT count(*) FROM portfolio.securities_global"),
                             await rls_val(conn, "SELECT count(*) FROM portfolio.securities_global_note_terms"))
                check(sec_after == sec_before, "securities_global and securities_global_note_terms row counts unchanged",
                      f"{sec_before} -> {sec_after}")
            await teardown(conn)
            left = await rls_val(conn, "SELECT count(*) FROM portfolio.reference_filings WHERE id = ANY($1::uuid[])",
                                 FIXTURE_FILINGS)
            left_r = await rls_val(conn, "SELECT count(*) FROM portfolio.note_term_readings WHERE reference_filing_id = ANY($1::uuid[])",
                                   FIXTURE_FILINGS)
            check(left == 0 and left_r == 0, "teardown: fixture filings and their readings are gone")
            if counts_before is not None:
                after = await table_counts(conn)
                diff = {t: (counts_before[t], after[t]) for t in COUNT_TABLES if counts_before[t] != after[t]}
                check(not diff, "teardown: exact before/after row counts on every touched table", f"{diff}")
        finally:
            from services.database import close_pool
            await close_pool()
            await conn.close()

    npm_build()

    print(f"\n{'=' * 70}")
    if _finds:
        print(f"FINDINGS: {len(_finds)}")
    if _blocked:
        print(f"BLOCKED (not counted as FAIL): {len(_blocked)} — {_blocked}")
    print(f"TOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    print("=" * 70)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
