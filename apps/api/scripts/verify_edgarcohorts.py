"""verify_edgarcohorts.py — EDGAR cohorts, cohort-targeted fetch, --cohort for
B1's tools, the template-study preset and the inventory pass.

WHAT WAS BUILT (edgarcohorts.structural; this script proves it):
  * migrations/edgarcohorts_cohorts_inventory.sql — portfolio.edgar_cohorts +
    edgar_cohort_members (frozen by trigger once sealed; members keyed by
    accession), edgar_pipeline_runs.cohort_id / run_kind,
    edgar_index_filings.selected_by_cohort_id (+ the lifecycle CHECK now
    accepting a cohort instead of a policy version), and the inventory tables
    (runs, documents, items, concepts). Four RLS policies per new table.
  * services/edgar_cohorts.py (definitions, sampling, preview, frozen save,
    copy-and-edit, reads, launch), services/edgar_pipeline.py (cohort queue in
    cohort order, selected-by-cohort marking, run_kind), edgar_pipeline_job.py
    (--cohort), routers/edgar_pipeline_admin.py (cohort + inventory endpoints;
    the default run relabelled), services/edgar_inventory.py +
    scripts/run_edgar_inventory.py (terms pages, model choice, quote check,
    concepts, label dictionary, spending cap, dry run), --cohort on
    run_note_extraction_pilot.py / run_note_extraction_eval.py /
    sample_gold_set.py, and the web page (Filings ticks + cohort builder,
    Cohorts tab, relabelled default run).

Hydrates secrets from Doppler over HTTPS at startup (_db_bootstrap). RLS
context is set on EVERY read (app.is_super_admin LOCAL inside a transaction).

REAL DATA IS NOT CHANGED. Every manifest row this script touches is its own
fixture (accession prefix 9999999999-58-, CIKs 9999999581..3); the real
manifest is fingerprinted (row count + md5 of every row) before and after. The
template-study preset and the size limit READ real data only. No Render job
is launched (start_render_job is replaced in-process), no SEC request and no
R2 call is made (fetch_one is replaced), and every model response is MOCKED
at services.note_extraction.proxy._post — total real spend $0.

ASSERTIONS:
  [Y] Task 1's findings reported
  [Y] A cohort built from a filter, one built by hand, and one stratified each
      freeze exactly the expected members; co-listed filings appear once
  [Y] Random sampling with the same seed yields the same members; a different
      seed yields different ones
  [Y] The stratified preview's per-stratum counts equal the saved members
  [Y] A saved cohort cannot be edited; "copy and edit" creates a new one
  [Y] The size limit refuses an oversized cohort
  [Y] Super-admin 200; org admin and member 403 on the IDENTICAL request, for
      create, read and run
  [Y] Fetching a cohort targets exactly its members in cohort order; a member
      the default policy did not select is fetched and records selection by
      cohort with the cohort id; filings outside the cohort are untouched
  [Y] A cohort run is refused while another job holds the lease
  [Y] The run records its cohort id; the Cohorts tab's counts by status match
      the database
  [Y] The default policy's behavior is unchanged
  [Y] B1's pilot and harness in dry-run with --cohort plan exactly the cohort's
      ready members
  [Y] The template-study preset covers every 'yes' issuer and all three eras
  [Y] Inventory dry-run makes zero model calls; with mocked responses, items are
      stored with provenance; a fabricated quote is rejected and counted; terms
      pages stop before risk factors
  [Y] Concept grouping keeps every original label and quote attached, and the
      per-issuer label dictionary is produced
  [Y] The spending cap stops a run cleanly
  [Y] No Claude model, OpenRouter route, provider key or direct provider call in
      application code
  [Y] Teardown: fixtures gone; manifest rows restored exactly
  [Y] npm run build exits 0

Pass/fail only. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on failure.

Run:  python3 apps/api/scripts/verify_edgarcohorts.py
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import date
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
WEB_DIR = REPO / "apps" / "web"

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — the "ed0c" UUID block, accession prefix 9999999999-58- and CIKs
# 9999999581..3 are unused by any other verify script (grep'd). Every unique
# value is a FULL literal, never a slice of a UUID.
# ═══════════════════════════════════════════════════════════════════════════
ORG = UUID("99000000-0000-0000-0000-0000ed0c0001")
U_SUPER = UUID("99000000-0000-0000-0000-0000ed0c0011")     # users.role super_admin
U_ORGADMIN = UUID("99000000-0000-0000-0000-0000ed0c0012")  # real org_admin GRANT
U_MEMBER = UUID("99000000-0000-0000-0000-0000ed0c0013")    # only 'member'
FIXTURE_USERS = [U_SUPER, U_ORGADMIN, U_MEMBER]

ACC_PREFIX = "9999999999-58-"
A_ISS, A_GUAR, B_ISS = "9999999581", "9999999582", "9999999583"
FIXTURE_CIKS = [A_ISS, A_GUAR, B_ISS]
GROUP_A, GROUP_B = "VERIFY Cohorts A", "VERIFY Cohorts B"
COHORT_PREFIX = "VERIFY edgarcohorts"
VERIFY_LEASE = "verify-edgarcohorts"
VERIFY_BUCKET = "verify-edgarcohorts-bucket"
CATALOG_MODEL = "verify-edgarcohorts-model"
CATALOG_UPSTREAM = "verifyco/verify-edgarcohorts-model"
CATALOG_DEPLOYMENT_ID = "verify-edgarcohorts-deployment"
HEADERS = {"Authorization": "Bearer verify-token"}


def A(n: int) -> str:
    return f"{ACC_PREFIX}5800{n:02d}"


# (accession, [filer ciks], form, filing date). Group A is the issuer A_ISS;
# A(11) is CO-LISTED by A's guarantor and A's issuer (two filer rows, one filing).
FX = [
    (A(1), [A_ISS], "424B2", date(2020, 3, 2)),
    (A(2), [A_ISS], "424B2", date(2020, 6, 1)),
    (A(3), [A_ISS], "424B2", date(2021, 1, 4)),
    (A(4), [A_ISS], "424B2", date(2022, 2, 1)),
    (A(5), [A_ISS], "424B2", date(2023, 5, 1)),
    (A(6), [A_ISS], "424B2", date(2023, 7, 3)),
    (A(7), [A_ISS], "424B2", date(2024, 2, 1)),
    (A(8), [A_ISS], "424B2", date(2025, 3, 3)),
    (A(9), [A_ISS], "424B2", date(2025, 6, 2)),
    (A(10), [A_ISS], "FWP", date(2025, 6, 3)),             # policy: not_selected
    (A(11), [A_GUAR, A_ISS], "424B2", date(2025, 7, 1)),   # co-listed
    (A(12), [B_ISS], "424B2", date(2020, 5, 1)),
    (A(13), [B_ISS], "424B2", date(2022, 5, 2)),
    (A(14), [B_ISS], "424B2", date(2022, 8, 1)),
    (A(15), [B_ISS], "424B2", date(2024, 9, 3)),
    (A(16), [B_ISS], "424B2", date(2026, 1, 5)),
    (A(17), [A_ISS], "424B2", date(2024, 11, 1)),          # left 'discovered'
]
FX_BY = {acc: (ciks, form, d) for acc, ciks, form, d in FX}
GROUP_OF_CIK = {A_ISS: GROUP_A, A_GUAR: GROUP_A, B_ISS: GROUP_B}
DISCOVERED_ONLY = [A(17)]

# Documents (ready for extraction) used by the inventory and the B1 tools.
INV_PRICING = [A(4), A(8), A(9), A(15)]
INV_PRODUCT = [A(5)]
FETCH_COHORT_ORDER = [A(6), A(10), A(17), A(1), A(8)]   # A(8) is past fetch -> skipped

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []
_blocked: list[str] = []
STATE: dict = {"pipeline_runs": set(), "inventory_runs": set()}


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} (type {type(passed).__name__}), "
        f"not a bool, for label={label!r} — reversed-argument guard"
    )
    global _ok, _n_pass, _n_fail
    line = f"{'[PASS]' if passed else '[FAIL]'} {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ═══════════════════════════════════════════════════════════════════════════
# DB helpers — every read and write sets super-admin context in its own txn
# ═══════════════════════════════════════════════════════════════════════════
async def _super(conn) -> None:
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def rls_fetch(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetch(q, *a)


async def rls_row(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchrow(q, *a)


async def rls_val(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchval(q, *a)


async def rls_exec(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.execute(q, *a)


async def manifest_fingerprint(conn) -> tuple:
    row = await rls_row(
        conn,
        """SELECT count(*) AS n, md5(string_agg(md5(f::text), '' ORDER BY f.accession_number)) AS h
           FROM portfolio.edgar_index_filings f WHERE f.accession_number NOT LIKE $1""",
        ACC_PREFIX + "%")
    return int(row["n"]), row["h"]


async def table_fingerprint(conn, table: str, key: str) -> str:
    return await rls_val(conn, f"SELECT md5(string_agg(md5(t::text), '' ORDER BY t.{key})) FROM {table} t")


async def member_list(conn, cohort_id) -> list[tuple]:
    rows = await rls_fetch(conn, """SELECT accession_number, position, stratum FROM portfolio.edgar_cohort_members
                                    WHERE cohort_id = $1 ORDER BY position""", cohort_id)
    return [(r["accession_number"], r["position"], r["stratum"]) for r in rows]


async def fixture_rows(conn, accessions) -> dict:
    rows = await rls_fetch(conn, "SELECT * FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])",
                           list(accessions))
    return {r["accession_number"]: dict(r) for r in rows}


def _sub(uid: UUID) -> str:
    return f"edgarcohorts_{uid.hex}"


# ═══════════════════════════════════════════════════════════════════════════
# HTTP through the real app, on THIS event loop (httpx.ASGITransport)
# ═══════════════════════════════════════════════════════════════════════════
async def api(uid, method, path, body=None):
    import httpx
    import main

    sub = _sub(uid)
    main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": str(ORG)}
    kw = {"headers": HEADERS}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=120.0) as client:
        res = await client.request(method, path, **kw)
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def _detail(body) -> str:
    return str(body.get("detail", body))[:200] if isinstance(body, dict) else str(body)[:200]


BASE = "/api/v1/admin/edgar"


# ═══════════════════════════════════════════════════════════════════════════
# Fixture documents (HTML) — terms, payout examples, then risk + tax sentinels
# ═══════════════════════════════════════════════════════════════════════════
RISK_SENTINEL = "RISKSENTINEL you may lose some or all of your principal amount"
TAX_SENTINEL = "TAXSENTINEL the notes should be treated as prepaid derivative contracts"
LICENSE_SENTINEL = "LICENSESENTINEL the index sponsor makes no representation whatsoever"


def pricing_html(acc: str, *, date_label: str, examples_after_risk: bool) -> str:
    n = int(acc[-2:])
    terms = [
        f"Pricing Supplement No. {n} dated March 3, 2025",
        f"VERIFY Bank Auto-Callable Contingent Coupon Notes {n} Linked to the Verify Index",
        "Key Terms",
        f"{date_label}: March {n}, 2025",
        f"Coupon Barrier: {60 + n}.00% of the Initial Level",
        f"Redemption Barrier: {50 + n}.00% of the Initial Level, the level at which the fixed payment is made",
        f"Aggregate Principal Amount: ${n},250,000",
        "The notes are issued under product supplement no. VERIFY-1 dated January 2, 2024.",
    ]
    example = [f"Hypothetical Examples",
               f"Example {n}: the final level is 120% of the initial level and you receive $1,{n:03d} per $1,000 note."]
    risk = ["Selected Risk Considerations", RISK_SENTINEL + "."]
    tax = ["Material U.S. Federal Income Tax Consequences", TAX_SENTINEL + "."]
    lic = ["License Agreement", LICENSE_SENTINEL + "."]
    lines = terms + (risk + example if examples_after_risk else example + risk) + lic + tax
    return "<html><body>" + "".join(f"<p>{line}</p>" for line in lines) + "</body></html>"


def product_html(acc: str) -> str:
    lines = ["Product Supplement No. VERIFY-1 dated January 2, 2024",
             "General Terms of the Notes",
             "Contingent Coupon: a coupon paid on each Coupon Payment Date if the closing level is at or above "
             "the Coupon Barrier.",
             "Risk Factors", RISK_SENTINEL + "."]
    return "<html><body>" + "".join(f"<p>{line}</p>" for line in lines) + "</body></html>"


DOC_HTML: dict[str, str] = {}
for _i, _acc in enumerate(INV_PRICING):
    DOC_HTML[_acc] = pricing_html(_acc, date_label="Pricing Date" if _i % 2 == 0 else "Trade Date",
                                  examples_after_risk=(_i == 1))
for _acc in INV_PRODUCT:
    DOC_HTML[_acc] = product_html(_acc)
REF_OF: dict[str, str] = {}      # accession -> reference_filing id (filled at seed)
ACC_OF_REF: dict[str, str] = {}


async def fake_load_document(_conn, reference_filing_id, downloader=None):
    from services.note_extraction import documents

    acc = ACC_OF_REF[str(reference_filing_id)]
    ciks, form, d = FX_BY[acc]
    return documents.document_from_html(
        DOC_HTML[acc], reference_filing_id=str(reference_filing_id), form_type=form,
        filer_name=GROUP_OF_CIK[ciks[-1]], cik=ciks[-1], filing_date=d, accession_number=acc)


# ═══════════════════════════════════════════════════════════════════════════
# Seed / teardown
# ═══════════════════════════════════════════════════════════════════════════
async def fixture_cohort_ids(conn) -> list:
    rows = await rls_fetch(conn, "SELECT id FROM portfolio.edgar_cohorts WHERE name LIKE $1", COHORT_PREFIX + "%")
    return [r["id"] for r in rows]


async def teardown_fixtures(conn) -> None:
    cids = await fixture_cohort_ids(conn)
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
                           FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS])
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)

        await conn.execute("DELETE FROM portfolio.edgar_inventory_runs WHERE cohort_id = ANY($1::uuid[]) "
                           "OR id = ANY($2::uuid[])", cids, list(STATE["inventory_runs"]))
        await conn.execute("DELETE FROM portfolio.edgar_pipeline_runs WHERE cohort_id = ANY($1::uuid[]) "
                           "OR id = ANY($2::uuid[]) OR trigger_source = 'verify'", cids, list(STATE["pipeline_runs"]))
        # Release the fixture rows' cohort reference so the cohorts can go.
        await conn.execute(
            """UPDATE portfolio.edgar_index_filings
                  SET pipeline_status = 'discovered', selected_by_cohort_id = NULL,
                      selection_policy_version = NULL, reference_filing_id = NULL
                WHERE accession_number LIKE $1""", ACC_PREFIX + "%")
        await conn.execute("UPDATE portfolio.edgar_index_filings SET selected_by_cohort_id = NULL "
                           "WHERE selected_by_cohort_id = ANY($1::uuid[])", cids)
        await conn.execute("DELETE FROM portfolio.edgar_cohorts WHERE id = ANY($1::uuid[])", cids)
        await conn.execute("DELETE FROM portfolio.reference_filings WHERE accession_number LIKE $1", ACC_PREFIX + "%")
        await conn.execute("DELETE FROM portfolio.edgar_index_filing_filers WHERE accession_number LIKE $1",
                           ACC_PREFIX + "%")
        await conn.execute("DELETE FROM portfolio.edgar_index_filings WHERE accession_number LIKE $1", ACC_PREFIX + "%")
        await conn.execute("DELETE FROM portfolio.structured_note_issuers WHERE filer_cik = ANY($1::text[])", FIXTURE_CIKS)
        await conn.execute("DELETE FROM portfolio.edgar_pipeline_lease WHERE lease_name = $1", VERIFY_LEASE)
        await conn.execute("DELETE FROM platform_model_catalog WHERE model_id = $1", CATALOG_MODEL)


def _line(cik, name, form, day: date, acc):
    return f"{cik}|{name}|{form}|{day.strftime('%Y%m%d')}|edgar/data/{cik}/{acc}.txt"


async def seed(conn) -> None:
    from services import edgar_index as ix
    from services import edgar_pipeline as p
    from services.rbac import ensure_role, grant_org_admin

    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "EDGARCOHORTS Fixture Org", f"{ORG}-edgarcohorts")
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member"), (U_MEMBER, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""", uid, ORG, f"{sub}@test.local", sub, sub, role)
        member_role = await ensure_role(conn, ORG, "member", "edgarcohorts fixture: no permissions")
        for uid in (U_SUPER, U_MEMBER):
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                               uid, member_role)
        await grant_org_admin(conn, U_ORGADMIN, ORG)
        # Fixture issuers are 'review' (selected by policy v1, but NOT in the
        # template-study preset, which takes 'yes' issuers only).
        for cik, group, role in ((A_ISS, GROUP_A, "issuer"), (A_GUAR, GROUP_A, "guarantor"), (B_ISS, GROUP_B, "issuer")):
            await conn.execute(
                """INSERT INTO portfolio.structured_note_issuers
                       (filer_cik, issuer_group, filer_name, filer_role, credit_entity, include_status, notes)
                   VALUES ($1, $2, $3, $4, $5, 'review', 'edgarcohorts fixture')""",
                cik, group, f"{group} filer {cik}", role, f"{group} credit")
        await conn.execute(
            """INSERT INTO platform_model_catalog (model_id, display_name, provider, availability)
               VALUES ($1, 'Verify edgarcohorts model', 'verifyco', 'available')""", CATALOG_MODEL)
    lines = [_line(cik, GROUP_OF_CIK[cik] + " filer", form, d, acc) for acc, ciks, form, d in FX for cik in ciks]
    parsed = ix.parse_index_lines(lines)
    await ix.load_records(conn, parsed.filing_records(None), parsed.filer_records())
    decided = [acc for acc, *_ in FX if acc not in DISCOVERED_ONLY]
    STATE["policy_select"] = await p.select_stage(conn, accessions=decided)

    # Stored documents for the inventory / B1 fixtures (no R2 object: the
    # loaders are replaced in this script).
    async with conn.transaction():
        await _super(conn)
        for acc in INV_PRICING + INV_PRODUCT:
            ciks, form, d = FX_BY[acc]
            rid = await conn.fetchval(
                """INSERT INTO portfolio.reference_filings
                       (cik, filer_name, form_type, accession_number, filing_date, primary_document,
                        source_url, extraction_status, retention_classification, content_encoding)
                   VALUES ($1, $2, $3, $4, $5, 'verify.htm', $6, 'fetched', 'public_reference', 'identity')
                   RETURNING id""",
                ciks[-1], GROUP_OF_CIK[ciks[-1]] + " filer", form, acc, d,
                f"https://verify.invalid/{acc}/verify.htm")
            REF_OF[acc] = str(rid)
            ACC_OF_REF[str(rid)] = acc
            status, kind = (("ready_for_extraction", "pricing_supplement") if acc in INV_PRICING
                            else ("not_pricing_supplement", "product_supplement"))
            await conn.execute(
                """UPDATE portfolio.edgar_index_filings
                      SET pipeline_status = $2, document_kind = $3, reference_filing_id = $4,
                          fetched_at = now(), status_reason = 'edgarcohorts fixture document'
                    WHERE accession_number = $1""", acc, status, kind, rid)


# ═══════════════════════════════════════════════════════════════════════════
# Sections
# ═══════════════════════════════════════════════════════════════════════════
async def task1_findings(conn) -> None:
    section("[Y] Task 1 findings")
    from services import edgar_pipeline as p
    from services import edgar_pipeline_admin as admin

    sig = inspect.signature(p.select_stage)
    check("accessions" in sig.parameters, "1a: select_stage takes an explicit accession list")
    find("1a: the default fetch queue only takes 'selected' rows (and due 'fetch_failed'); the lifecycle CHECK "
         "required a policy version on every decided row. A cohort-chosen filing is now marked "
         "selected_by_cohort_id with selection_policy_version cleared, and the CHECK accepts either one "
         "(never both: edgar_index_filings_one_selector_chk)")
    chk = await rls_val(conn, """SELECT pg_get_constraintdef(oid) FROM pg_constraint
                                 WHERE conname = 'edgar_index_filings_decided_has_policy_chk'""")
    check(bool(chk) and "selected_by_cohort_id" in chk, "1a: the decided-row CHECK accepts a cohort id", chk or "")
    keys = set(inspect.signature(admin._where).parameters)
    check("filters" in keys, "1b: the Filings endpoint's filters go through one where-builder the cohort builder reuses")
    find("1b: Filings tab filters = issuer_group, form_type, status, document_kind, quarter, date_from, date_to "
         "(GET /admin/edgar/filings, server-side). Ticks are kept client-side as a Set of accession numbers, "
         "independent of the page, so they survive paging, sorting and filtering")
    rows = await rls_fetch(conn, "SELECT model_id, provider, availability FROM platform_model_catalog "
                                 "WHERE model_id <> $1 ORDER BY model_id", CATALOG_MODEL)
    non_claude = [r["model_id"] for r in rows if r["availability"] == "available"
                  and "claude" not in r["model_id"].lower() and (r["provider"] or "").lower() not in ("anthropic", "voyage")]
    find(f"1c: available non-Claude chat models in platform_model_catalog: {non_claude or 'NONE'}",
         "the inventory pass reports BLOCKED until one is registered" if not non_claude else "")
    for script in ("run_note_extraction_pilot.py", "run_note_extraction_eval.py", "sample_gold_set.py"):
        text = (HERE.parent / script).read_text()
        check("--cohort" in text, f"1d: {script} accepts --cohort")


async def build_section(conn) -> dict:
    section("[Y] Cohorts from a filter, by hand, stratified freeze exactly the expected members")
    from services import edgar_cohorts as c
    out = {}

    # ── filter: everything in group A (all forms) ──
    expected_a = sorted([acc for acc, ciks, _f, _d in FX if GROUP_OF_CIK[ciks[-1]] == GROUP_A],
                        key=lambda a: (FX_BY[a][2], a), reverse=True)
    co = await c.create_cohort(conn, name=f"{COHORT_PREFIX} filter A", purpose="verify",
                               definition={"source": "filter", "filters": {"issuer_groups": [GROUP_A]},
                                           "sampling": {"method": "all"}})
    out["filter"] = co["id"]
    got = [m[0] for m in await member_list(conn, co["id"])]
    check(got == expected_a, "filter cohort freezes exactly group A's filings, newest first",
          f"expected {len(expected_a)} got {len(got)}")
    filer_rows = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_index_filing_filers WHERE accession_number = $1", A(11))
    check(filer_rows == 2 and got.count(A(11)) == 1,
          "the co-listed filing (two filer rows) appears exactly once", f"filer rows={filer_rows}")
    check(co["member_count"] == len(expected_a) and co["sealed_at"] is not None,
          "the saved cohort records its member_count and is sealed")
    check(co["definition"]["filters"] == {"issuer_groups": [GROUP_A]} and co["definition"]["sampling"] == {"method": "all"},
          "the definition that built it is stored")

    # ── by hand, with a duplicate tick ──
    ticks = [A(3), A(12), A(3), A(11), A(12)]
    expected_h = [A(3), A(12), A(11)]
    ch = await c.create_cohort(conn, name=f"{COHORT_PREFIX} hand", purpose=None,
                               definition={"source": "hand", "accessions": ticks})
    out["hand"] = ch["id"]
    got = [m[0] for m in await member_list(conn, ch["id"])]
    check(got == expected_h, "hand cohort freezes the ticked filings once each, in the order ticked", f"{got}")
    check(ch["kind"] == "hand_picked", "a hand-built cohort is recorded as hand_picked")

    # ── stratified: N per issuer group x era ──
    defn = {"source": "filter", "filters": {"issuer_groups": [GROUP_A, GROUP_B], "form_type": "424B2"},
            "sampling": {"method": "stratified", "stratify_by": "issuer_era", "per_stratum": 2, "seed": 7}}
    avail: dict[str, list[str]] = {}
    for acc, ciks, form, d in FX:
        if form == "424B2":
            avail.setdefault(f"{GROUP_OF_CIK[ciks[-1]]} | {c.era_of(d)}", []).append(acc)
    expected_strata = {k: min(2, len(v)) for k, v in avail.items()}
    pv = await c.preview(conn, defn)
    pv_strata = {s["stratum"]: s["members"] for s in pv["strata"]}
    check(pv_strata == expected_strata, "stratified preview: per-stratum counts = min(N, available) per group x era",
          f"preview={pv_strata} expected={expected_strata}")
    cs = await c.create_cohort(conn, name=f"{COHORT_PREFIX} stratified", purpose=None, definition=defn)
    out["stratified"] = cs["id"]
    mem = await member_list(conn, cs["id"])
    saved_strata: dict[str, int] = {}
    for _a, _p, st in mem:
        saved_strata[st] = saved_strata.get(st, 0) + 1
    check(saved_strata == pv_strata, "the stratified preview's per-stratum counts equal the saved members",
          f"saved={saved_strata}")
    check(all(a in avail.get(st, []) for a, _p, st in mem), "every stratified member belongs to the stratum it is labelled with")
    check(len({a for a, _p, _s in mem}) == len(mem), "stratified members are unique by accession")
    pv2 = await c.preview(conn, defn)
    check([r["accession_number"] for r in pv2["sample"]] == [a for a, _p, _s in mem][:len(pv2["sample"])],
          "re-previewing the same definition picks the same members the save froze")

    # ── newest / oldest ──
    newest = await c.build_members(conn, c.normalise_definition(
        {"source": "filter", "filters": {"issuer_groups": [GROUP_B]}, "sampling": {"method": "newest", "n": 3}}))
    exp_new = sorted([a for a, ciks, _f, _d in FX if GROUP_OF_CIK[ciks[-1]] == GROUP_B],
                     key=lambda a: FX_BY[a][2], reverse=True)[:3]
    check([m[0] for m in newest["members"]] == exp_new, "newest N takes the N newest", f"{exp_new}")
    oldest = await c.build_members(conn, c.normalise_definition(
        {"source": "filter", "filters": {"issuer_groups": [GROUP_B]}, "sampling": {"method": "oldest", "n": 2}}))
    exp_old = sorted([a for a, ciks, _f, _d in FX if GROUP_OF_CIK[ciks[-1]] == GROUP_B], key=lambda a: FX_BY[a][2])[:2]
    check([m[0] for m in oldest["members"]] == exp_old, "oldest N takes the N oldest", f"{exp_old}")
    return out


async def random_section(conn) -> None:
    section("[Y] Random sampling: same seed -> same members; different seed -> different")
    from services import edgar_cohorts as c

    def d(seed):
        return {"source": "filter", "filters": {"issuer_groups": [GROUP_A, GROUP_B]},
                "sampling": {"method": "random", "n": 4, "seed": seed}}
    r1 = await c.create_cohort(conn, name=f"{COHORT_PREFIX} random 11a", purpose=None, definition=d(11))
    r2 = await c.create_cohort(conn, name=f"{COHORT_PREFIX} random 11b", purpose=None, definition=d(11))
    r3 = await c.create_cohort(conn, name=f"{COHORT_PREFIX} random 12", purpose=None, definition=d(12))
    m1 = [m[0] for m in await member_list(conn, r1["id"])]
    m2 = [m[0] for m in await member_list(conn, r2["id"])]
    m3 = [m[0] for m in await member_list(conn, r3["id"])]
    pool = {a for a, ciks, _f, _d in FX}
    check(len(m1) == 4 and set(m1) <= pool, "random N=4 draws 4 fixture filings", f"{m1}")
    check(m1 == m2, "the same seed gives the same members, in the same order")
    check(set(m3) != set(m1), "a different seed gives a different member set", f"seed11={sorted(m1)} seed12={sorted(m3)}")
    check(r1["definition"]["sampling"]["seed"] == 11, "the seed is recorded in the definition")


async def frozen_section(conn, ids: dict) -> None:
    section("[Y] A saved cohort cannot be edited; 'copy and edit' creates a new one")
    from services import edgar_cohorts as c
    cid = ids["hand"]
    before = await member_list(conn, cid)
    before_row = dict(await rls_row(conn, "SELECT * FROM portfolio.edgar_cohorts WHERE id = $1", cid))
    attempts = {
        "rename the cohort": ("UPDATE portfolio.edgar_cohorts SET name = name || ' x' WHERE id = $1", (cid,)),
        "change a member's stratum": ("UPDATE portfolio.edgar_cohort_members SET stratum = 'x' WHERE cohort_id = $1", (cid,)),
        "add a member": ("INSERT INTO portfolio.edgar_cohort_members (cohort_id, accession_number, position) "
                         "VALUES ($1, $2, 99)", (cid, A(16))),
        "remove a member": ("DELETE FROM portfolio.edgar_cohort_members WHERE cohort_id = $1 AND accession_number = $2",
                            (cid, A(12))),
    }
    for what, (sql, args) in attempts.items():
        try:
            await rls_exec(conn, sql, *args)
            refused, msg = False, "accepted"
        except asyncpg.PostgresError as exc:
            refused, msg = "frozen" in str(exc), str(exc)[:120]
        check(refused, f"the database refuses to {what} on a saved cohort", msg)
    s, b = await api(U_SUPER, "PATCH", f"{BASE}/cohorts/{cid}", {"name": "renamed"})
    check(s == 409, "PATCH on a saved cohort is refused with 409 (even for super-admin)", f"HTTP {s} {_detail(b)}")
    after = await member_list(conn, cid)
    after_row = dict(await rls_row(conn, "SELECT * FROM portfolio.edgar_cohorts WHERE id = $1", cid))
    check(before == after and before_row == after_row, "the cohort and its members are unchanged after every attempt")

    s, b = await api(U_SUPER, "POST", f"{BASE}/cohorts/{cid}/copy",
                     {"name": f"{COHORT_PREFIX} hand copy", "remove": [A(12)], "add": [A(13), A(3)]})
    new = (b or {}).get("cohort") or {}
    check(s == 201 and new.get("id") not in (None, str(cid)), "copy and edit returns a NEW cohort", f"HTTP {s} {_detail(b)}")
    if s == 201:
        got = [m[0] for m in await member_list(conn, new["id"])]
        exp = [a for a, _p, _s in before if a != A(12)] + [A(13)]
        check(got == exp, "the copy = source members minus removed plus added (a duplicate add ignored)", f"{got}")
        check(str(new.get("copied_from")) == str(cid) and new.get("kind") == "copy",
              "the copy records what it was copied from")
        check(await member_list(conn, cid) == before, "the source cohort is untouched by the copy")


async def size_section(conn) -> None:
    section("[Y] The size limit refuses an oversized cohort")
    from services import edgar_cohorts as c
    big = [f"9999999998-{i // 1000000:02d}-{i % 1000000:06d}" for i in range(c.MAX_COHORT_SIZE + 1)]
    try:
        c.normalise_definition({"source": "hand", "accessions": big})
        refused = False
    except c.CohortTooLarge:
        refused = True
    check(refused, f"a hand list of {c.MAX_COHORT_SIZE + 1:,} filings is refused before any query")
    n_before = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_cohorts")
    real = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_index_filings WHERE accession_number NOT LIKE $1",
                         ACC_PREFIX + "%")
    s, b = await api(U_SUPER, "POST", f"{BASE}/cohorts",
                     {"name": f"{COHORT_PREFIX} too big", "definition": {"source": "filter", "filters": {},
                                                                         "sampling": {"method": "all"}}})
    n_after = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_cohorts")
    check(real > c.MAX_COHORT_SIZE, "the real manifest is larger than the limit (so 'all' must be refused)", f"{real:,}")
    check(s == 422 and n_before == n_after, "saving 'all filings' is refused (422) and creates nothing",
          f"HTTP {s} {_detail(b)}")
    s, b = await api(U_SUPER, "POST", f"{BASE}/cohorts/preview",
                     {"definition": {"source": "filter", "filters": {}, "sampling": {"method": "all"}}})
    check(s == 200 and b.get("too_large") is True, "the preview of the same definition says too_large", f"HTTP {s}")


def _fake_starter_factory():
    calls = []

    async def fake_start(run_id, *, http=None):
        calls.append(str(run_id))
        return "verify-service", f"verify-job-{len(calls)}"
    return fake_start, calls


async def permission_section(conn) -> str | None:
    section("[Y] Super-admin 200; org admin and member 403 on the IDENTICAL request (create, read, run)")
    bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bypass is False, "the verify connection's role does NOT bypass RLS", f"rolbypassrls={bypass}")
    body = {"name": f"{COHORT_PREFIX} permissions", "purpose": "verify",
            "definition": {"source": "hand", "accessions": [A(3)]}}
    n0 = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_cohorts")
    res = {}
    for name, uid in (("org_admin", U_ORGADMIN), ("member", U_MEMBER), ("super", U_SUPER)):
        res[name] = await api(uid, "POST", f"{BASE}/cohorts", body)
    n1 = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_cohorts")
    check(res["org_admin"][0] == 403 and res["member"][0] == 403 and res["super"][0] == 201,
          "CREATE: super 201 / org admin 403 / member 403", f"{[(k, v[0]) for k, v in res.items()]}")
    check(n1 == n0 + 1, "only the super-admin's request created a cohort", f"{n0}->{n1}")
    cid = ((res["super"][1] or {}).get("cohort") or {}).get("id")
    if not cid:
        return None
    s, b = await api(U_SUPER, "POST", f"{BASE}/cohorts", {**body, "org_id": str(ORG)})
    check(s == 422, "a body carrying org_id is refused (extra='forbid')", f"HTTP {s}")

    for path in (f"{BASE}/cohorts", f"{BASE}/cohorts/{cid}", f"{BASE}/cohorts/{cid}/members"):
        r = {name: await api(uid, "GET", path) for name, uid in
             (("super", U_SUPER), ("org_admin", U_ORGADMIN), ("member", U_MEMBER))}
        check(r["super"][0] == 200 and r["org_admin"][0] == 403 and r["member"][0] == 403,
              f"READ {path.replace(str(cid), '{id}')}: super 200 / org admin 403 / member 403",
              f"{[(k, v[0]) for k, v in r.items()]}")
        check("rows" not in r["member"][1] and "cohort" not in r["member"][1], "the 403 carries no data")
    env = (await api(U_SUPER, "GET", f"{BASE}/cohorts"))[1]
    check(env.get("vocabularies", {}).get("editable") == [] and env.get("permissions", {}).get("can_write") is True,
          "the cohorts envelope publishes editable [] (a cohort is never edited) and can_write for super-admin")

    from services import edgar_pipeline as p
    fake, calls = _fake_starter_factory()
    original = p.start_render_job
    p.start_render_job = fake
    try:
        run_body = {"run_kind": "fetch", "fetch_cap": 1}
        before = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE cohort_id = $1", cid)
        r = {}
        for name, uid in (("org_admin", U_ORGADMIN), ("member", U_MEMBER), ("super", U_SUPER)):
            r[name] = await api(uid, "POST", f"{BASE}/cohorts/{cid}/runs", run_body)
        after = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE cohort_id = $1", cid)
        check(r["org_admin"][0] == 403 and r["member"][0] == 403 and r["super"][0] == 202,
              "RUN: super 202 / org admin 403 / member 403", f"{[(k, v[0]) for k, v in r.items()]}")
        run = (r["super"][1] or {}).get("run") or {}
        if run.get("id"):
            STATE["pipeline_runs"].add(UUID(str(run["id"])))
        check(after == before + 1, "only the super-admin's request recorded a run", f"{before}->{after}")
        check(str(run.get("cohort_id")) == str(cid) and run.get("run_kind") == "fetch"
              and list(run.get("stages") or []) == ["fetch"],
              "the run records its cohort id, run kind 'fetch' and the single fetch stage",
              f"status={run.get('status')} {run.get('stop_reason') or ''}")
        s, b = await api(U_SUPER, "POST", f"{BASE}/cohorts/{cid}/runs", {"run_kind": "extract"})
        check(s == 422, "run kind 'extract' is accepted by the API shape but refused until B2 (422)", f"HTTP {s} {_detail(b)}")
        if run.get("status") == "launched":
            check(len(calls) == 1, "the launch went through the (replaced) Render starter exactly once")
        else:
            find("the super-admin's cohort run was not launched (another job was in progress); the "
                 "permission and cohort-id assertions above still hold", str(run.get("stop_reason")))
    finally:
        p.start_render_job = original
    return cid


class _FakeFetch:
    """Replaces edgar_pipeline.fetch_one: records the order and moves the row
    past fetch the way a real fetch would (no SEC, no R2)."""

    def __init__(self):
        self.order: list[str] = []

    async def __call__(self, conn, row, *, client, bucket, r2_prefix=None):
        from services.database import platform_scope
        self.order.append(row["accession_number"])
        async with platform_scope(conn):
            await conn.execute(
                """UPDATE portfolio.edgar_index_filings
                      SET pipeline_status = 'not_pricing_supplement', status_reason = 'verify fake fetch',
                          document_kind = 'other', fetched_at = now(), attempt_count = attempt_count + 1,
                          last_attempt_at = now(), next_attempt_at = NULL
                    WHERE accession_number = $1""", row["accession_number"])
        return {"accession_number": row["accession_number"], "status": "not_pricing_supplement",
                "document_kind": "other", "cusip": None, "reference_filing_id": None,
                "bytes_uploaded": 0, "upload_skipped": True, "raw_key": None, "text_key": None}


async def fetch_section(conn) -> str | None:
    section("[Y] Fetching a cohort: exactly its members, in cohort order; a not-selected member is fetched "
            "and records selection by cohort; filings outside the cohort untouched")
    import httpx
    from services import edgar_cohorts as c
    from services import edgar_pipeline as p

    co = await c.create_cohort(conn, name=f"{COHORT_PREFIX} fetch", purpose="verify",
                               definition={"source": "hand", "accessions": FETCH_COHORT_ORDER})
    cid = co["id"]
    before = await fixture_rows(conn, [a for a, *_ in FX])
    statuses = {a: before[a]["pipeline_status"] for a in FETCH_COHORT_ORDER}
    check(statuses[A(10)] == "not_selected" and statuses[A(17)] == "discovered"
          and statuses[A(6)] == "selected" and statuses[A(8)] == "ready_for_extraction",
          "pre-state: an FWP the policy did not select, an undecided filing, a policy-selected one, one past fetch",
          f"{statuses}")
    eligible = [a for a in FETCH_COHORT_ORDER if statuses[a] in ("discovered", "selected", "not_selected")]
    not_by_policy = [a for a in eligible if statuses[a] in ("discovered", "not_selected")]

    fake = _FakeFetch()
    original_fetch, original_env = p.fetch_one, os.environ.get("EDGAR_R2_BUCKET")
    p.fetch_one = fake
    os.environ["EDGAR_R2_BUCKET"] = VERIFY_BUCKET     # a VALID name; the fake never touches R2
    client = httpx.AsyncClient()
    try:
        run_id = await p.create_run(conn, trigger_source="verify", fetch_cap=len(FETCH_COHORT_ORDER),
                                    stages=["fetch"], status="launched", cohort_id=cid)
        STATE["pipeline_runs"].add(run_id)
        row = await p.run_job(conn, run_id, lease_name=VERIFY_LEASE, client=client)
    finally:
        p.fetch_one = original_fetch
        if original_env is None:
            os.environ.pop("EDGAR_R2_BUCKET", None)
        else:
            os.environ["EDGAR_R2_BUCKET"] = original_env
        await client.aclose()

    check(fake.order == eligible, "the fetch stage attempted exactly the eligible members, in cohort order",
          f"order={fake.order} expected={eligible}")
    check(A(8) not in fake.order, "a member already past fetch is skipped")
    after = await fixture_rows(conn, [a for a, *_ in FX])
    for a in not_by_policy:
        r = after[a]
        check(str(r["selected_by_cohort_id"]) == str(cid) and r["selection_policy_version"] is None
              and r["pipeline_status"] == "not_pricing_supplement",
              f"{a} ({statuses[a]} by the policy) was fetched and records selected_by_cohort_id = the cohort, "
              "no policy version", f"{r['selected_by_cohort_id']} v{r['selection_policy_version']}")
    r6 = after[A(6)]
    check(r6["selected_by_cohort_id"] is None and r6["selection_policy_version"] == before[A(6)]["selection_policy_version"],
          "a member the POLICY selected keeps its policy provenance (no cohort mark)")
    outside = [a for a, *_ in FX if a not in FETCH_COHORT_ORDER]
    check(all(before[a] == after[a] for a in outside) and before[A(8)] == after[A(8)],
          "every fixture filing outside the cohort (and the skipped member) is unchanged, all columns",
          f"changed={[a for a in outside if before[a] != after[a]]}")
    check(str(row["cohort_id"]) == str(cid) and row["status"] == "succeeded"
          and row["fetch_attempted"] == len(eligible) and row["fetched"] == len(eligible),
          "the run row records its cohort id, succeeded, attempted/fetched = the eligible members",
          f"{row['status']} attempted={row['fetch_attempted']} {row['stop_reason']}")
    details = row["details"] if isinstance(row["details"], dict) else json.loads(row["details"])
    check((details.get("cohort") or {}).get("marked_by_cohort") == len(not_by_policy),
          "the run's details count the members marked as selected by the cohort",
          f"{details.get('cohort')}")

    # Cohorts tab counts == the database.
    s, b = await api(U_SUPER, "GET", f"{BASE}/cohorts/{cid}")
    sql = {r["pipeline_status"]: r["n"] for r in await rls_fetch(conn, """
        SELECT f.pipeline_status, count(*)::int AS n FROM portfolio.edgar_cohort_members m
        JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
        WHERE m.cohort_id = $1 GROUP BY 1""", cid)}
    check(s == 200 and b.get("status_counts") == sql, "the Cohorts tab's counts by status match the database",
          f"api={b.get('status_counts')} sql={sql}")
    check(any(str(r["id"]) == str(run_id) and str(r.get("cohort_id", cid)) == str(cid) for r in b.get("runs", [])),
          "the Cohorts tab lists the run under its cohort")
    s, m = await api(U_SUPER, "GET", f"{BASE}/cohorts/{cid}/members?page=1&page_size=2")
    check(s == 200 and m.get("total") == len(FETCH_COHORT_ORDER)
          and [r["accession_number"] for r in m.get("rows", [])] == FETCH_COHORT_ORDER[:2],
          "the members grid pages server-side in cohort order", f"total={m.get('total')}")
    return cid


async def lease_section(conn, cid) -> None:
    section("[Y] A cohort run is refused while another job holds the lease")
    import httpx
    from services import edgar_cohorts as c
    from services import edgar_pipeline as p

    snap = await fixture_rows(conn, [a for a, *_ in FX])
    took = await p.acquire_lease(conn, "verify-other-job", lease_name=VERIFY_LEASE)
    check(took, "another holder takes the (verify) lease")
    fake = _FakeFetch()
    original = p.fetch_one
    p.fetch_one = fake
    client = httpx.AsyncClient()
    try:
        run_id = await p.create_run(conn, trigger_source="verify", fetch_cap=5, stages=["fetch"],
                                    status="launched", cohort_id=cid)
        STATE["pipeline_runs"].add(run_id)
        row = await p.run_job(conn, run_id, lease_name=VERIFY_LEASE, client=client)
    finally:
        p.fetch_one = original
        await client.aclose()
        await p.release_lease(conn, "verify-other-job", lease_name=VERIFY_LEASE)
    check(row["status"] == "refused" and "lease" in (row["stop_reason"] or ""),
          "the cohort job is refused while the lease is held", f"{row['status']}: {row['stop_reason']}")
    check(fake.order == [] and await fixture_rows(conn, [a for a, *_ in FX]) == snap,
          "nothing was fetched and no manifest row changed")

    # The launch path: a recent launched run (the permission section's) blocks a second launch.
    blocking = await p._blocking_run(conn)
    fake_start, calls = _fake_starter_factory()
    row2 = await c.launch_cohort_run(conn, cid, trigger_source="verify", starter=fake_start)
    STATE["pipeline_runs"].add(UUID(str(row2["id"])))
    if blocking:
        check(row2["status"] == "refused" and calls == [] and str(row2["cohort_id"]) == str(cid),
              "launching a cohort run while another job is in progress is refused (recorded, with the cohort id)",
              f"{row2['status']}: {row2['stop_reason']}")
    else:
        find("no other job was in progress at the launch check, so the launch-path refusal could not be shown "
             "here; the job-level refusal above is the lease proof")


async def default_policy_section(conn, policy_fp_before) -> None:
    section("[Y] The default policy's behavior is unchanged")
    import httpx
    from services import edgar_pipeline as p

    sel = STATE.get("policy_select") or {}
    rows = await fixture_rows(conn, [a for a, *_ in FX])
    expected_sel = {acc: ("selected" if form == "424B2" else "not_selected")
                    for acc, _c, form, _d in FX if acc not in DISCOVERED_ONLY}
    decided = await rls_fetch(conn, """SELECT accession_number, pipeline_status, selection_policy_version,
                                              selected_by_cohort_id, status_reason
                                       FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1::text[])""",
                              list(expected_sel))
    policy_v = sel.get("policy_version")
    check(sel.get("selected", 0) == sum(1 for v in expected_sel.values() if v == "selected")
          and sel.get("not_selected", 0) == sum(1 for v in expected_sel.values() if v == "not_selected"),
          "select_stage (at seed) decided the fixtures exactly as policy v1's rules say", f"{sel}")
    check(all(r["status_reason"] and r["status_reason"].startswith(f"policy v{policy_v}") for r in decided
              if r["selected_by_cohort_id"] is None and r["accession_number"] not in INV_PRICING + INV_PRODUCT
              and r["pipeline_status"] in ("selected", "not_selected")),
          "policy decisions still carry the policy's own reason and version")
    fp_after = await table_fingerprint(conn, "portfolio.edgar_selection_policies", "version")
    check(fp_after == policy_fp_before, "the selection policies table is unchanged")

    # The default queue (no cohort): selected rows, newest first, no cohort marking.
    targets = [A(2), A(12), A(13)]
    check(all(rows[a]["pipeline_status"] == "selected" for a in targets), "pre-state: three policy-selected fixtures")
    fake = _FakeFetch()
    original = p.fetch_one
    p.fetch_one = fake
    try:
        stats = await p.fetch_stage(conn, cap=10, client=httpx.AsyncClient(), bucket=VERIFY_BUCKET, accessions=targets)
    finally:
        p.fetch_one = original
    exp = sorted(targets, key=lambda a: (FX_BY[a][2], a), reverse=True)
    check(fake.order == exp, "the default fetch order is still newest first", f"{fake.order}")
    after = await fixture_rows(conn, targets)
    check(stats.marked_by_cohort == 0 and all(after[a]["selected_by_cohort_id"] is None for a in targets),
          "a default (non-cohort) fetch never marks selection by cohort")

    fake_start, _calls = _fake_starter_factory()
    original_start = p.start_render_job
    p.start_render_job = fake_start
    try:
        s, b = await api(U_SUPER, "POST", f"{BASE}/runs", {"fetch_cap": 0})
    finally:
        p.start_render_job = original_start
    run = (b or {}).get("run") or {}
    if run.get("id"):
        STATE["pipeline_runs"].add(UUID(str(run["id"])))
    check(s == 202 and run.get("cohort_id") is None and run.get("run_kind") == "fetch"
          and list(run.get("stages") or []) == list(p.STAGES),
          "'Run default policy now' still records the full discover/select/fetch run with no cohort",
          f"HTTP {s} {run.get('status')}")
    s, prog = await api(U_SUPER, "GET", f"{BASE}/progress")
    check(s == 200 and (prog.get("vocabularies", {}).get("run_now") or {}).get("label")
          == "Run default policy now (newest first)",
          "the Progress tab's button label comes from the envelope: 'Run default policy now (newest first)'")


async def b1_section(conn) -> None:
    section("[Y] B1's pilot and harness in dry-run with --cohort plan exactly the cohort's ready members")
    import _note_extraction_common as common
    import run_note_extraction_eval as harness
    import run_note_extraction_pilot as pilot
    import sample_gold_set as gold_script
    from services import edgar_cohorts as c
    from services.note_extraction import cascade, documents, proxy, schema
    from services.note_extraction.proxy import Deployment

    members = [A(1), A(9), A(5), A(15), A(4), A(8)]   # A(1): not fetched; A(5): product supplement
    co = await c.create_cohort(conn, name=f"{COHORT_PREFIX} b1", purpose=None,
                               definition={"source": "hand", "accessions": members})
    cid = co["id"]
    expected = [REF_OF[a] for a in members if a in INV_PRICING]
    sql = [str(r["reference_filing_id"]) for r in await rls_fetch(conn, """
        SELECT f.reference_filing_id FROM portfolio.edgar_cohort_members m
        JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
        WHERE m.cohort_id = $1 AND f.pipeline_status = 'ready_for_extraction' AND f.reference_filing_id IS NOT NULL
        ORDER BY m.position""", cid)]
    check(sql == expected, "the cohort's ready members (SQL, cohort order) are the expected fixtures")

    def dep(name):
        return Deployment(name=name, deployment_id=f"{name}-id", upstream=f"verifyco/{name}", price_key=name,
                          input_cost_per_token=1e-7, output_cost_per_token=4e-7, cache_read_cost_per_token=None,
                          supports_response_schema=False, max_input_tokens=128000)
    fake_catalog = {"verify-m1": dep("verify-m1"), "verify-m2": dep("verify-m2")}

    async def fake_context(conn_):
        specs = schema.build_field_specs(await schema.load_registry_rows(conn_))
        return specs, fake_catalog, []

    async def fake_ensemble(_conn):
        return {"id": UUID("99000000-0000-0000-0000-0000ed0c0e01"), "model_1": "verify-m1",
                "model_2": "verify-m2", "jev_route": None}

    saved = (common.load_context, cascade.active_ensemble, documents.load_document)
    common.load_context, cascade.active_ensemble, documents.load_document = fake_context, fake_ensemble, fake_load_document
    before_calls = dict(proxy.CALLS)
    try:
        for label, mod, argv in (
            ("pilot", pilot, ["--dry-run", "--spend-cap", "1", "--cohort", str(cid), "--escalation-model="]),
            ("harness", harness, ["--dry-run", "--spend-cap", "1", "--cohort", str(cid), "--candidates", "verify-m1",
                                  "--no-jev"]),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = await mod.main(argv)
            out = buf.getvalue()
            m = re.search(r"^PLANNED_NOTES (.*)$", out, re.M)
            planned = json.loads(m.group(1)) if m else None
            check(rc == 0 and planned == expected, f"{label} --dry-run --cohort plans exactly the cohort's ready "
                  "members, in cohort order", f"rc={rc} planned={planned} tail={out[-300:]!r}" if planned != expected else "")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = await gold_script.main(["--cohort", str(cid)])
        m = re.search(r"pool: (\d+) eligible notes \((\d+) ready", buf.getvalue())
        check(rc == 0 and m is not None and int(m.group(2)) == len(expected),
              "the gold-set sampler proposes candidates from the cohort's ready members only",
              buf.getvalue()[-200:] if not m else f"pool {m.group(1)}")
    finally:
        common.load_context, cascade.active_ensemble, documents.load_document = saved
    check(proxy.CALLS == before_calls, "the dry runs made zero proxy calls")


async def template_study_section(conn) -> None:
    section("[Y] The template-study preset covers every 'yes' issuer and all three eras")
    from services import edgar_cohorts as c
    s, b = await api(U_SUPER, "POST", f"{BASE}/cohorts/presets/template-study/preview", {})
    check(s == 200, "the preset previews", f"HTTP {s} {_detail(b)}")
    if s != 200:
        return
    got = {x["stratum"]: x["members"] for x in b["strata"]}
    rows = await rls_fetch(conn, f"""
        SELECT i.issuer_group, {c._era_sql('f.filing_date')} AS era, count(*)::int AS n
        FROM portfolio.edgar_index_filings f
        JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
        WHERE f.form_type = '424B2' AND i.include_status = 'yes' AND f.accession_number NOT LIKE $1
        GROUP BY 1, 2""", ACC_PREFIX + "%")
    expected = {f"{r['issuer_group']} | {r['era']}": min(c.TEMPLATE_STUDY_PER_STRATUM, r["n"]) for r in rows}
    check(got == expected, "per stratum: min(8, available) for every 'yes' issuer group x era (derived in SQL)",
          f"strata={len(got)} expected={len(expected)}")
    yes_groups = {r["issuer_group"] for r in await rls_fetch(
        conn, "SELECT DISTINCT issuer_group FROM portfolio.structured_note_issuers WHERE include_status = 'yes'")}
    covered = {k.split(" | ")[0] for k in got}
    with_filings = {r["issuer_group"] for r in rows}
    check(with_filings <= covered, "every 'yes' issuer group with 424B2 filings is in the preset",
          f"missing={sorted(with_filings - covered)}")
    if yes_groups - with_filings:
        find("'yes' issuer groups with no 424B2 filings in the manifest (nothing to sample)",
             f"{sorted(yes_groups - with_filings)}")
    eras = {k.split(" | ")[1] for k in got}
    check({label for label, _a, _b in c.ERAS} <= eras, "all three eras appear", f"{sorted(eras)}")
    check(b["count"] == sum(got.values()) and not any(a.startswith(ACC_PREFIX) for a in
                                                      (r["accession_number"] for r in b["sample"])),
          "the preview count is the sum of its strata and excludes the test's fixtures")


# ── Mocked model (services.note_extraction.proxy._post is the one network function) ──
class FakeProxy:
    def __init__(self, catalog, registry_key: str | None):
        self.catalog = catalog
        self.registry_key = registry_key
        self.calls = {"inventory": 0, "grouping": 0}
        self.fabricated: dict[str, str] = {}

    async def __call__(self, path, body, timeout):
        from services import edgar_inventory as inv
        from services.note_extraction.proxy import ProxyResponse
        from services.note_extraction.spend import estimate_call_cost

        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        chars = sum(len(m["content"]) for m in body["messages"])
        cost = estimate_call_cost(self.catalog.get(body["model"]), chars, body["max_tokens"])
        if system.startswith(inv.INSTRUCTIONS[:60]):
            self.calls["inventory"] += 1
            acc = re.search(r"Accession: (\S+)", user).group(1)
            content = self._inventory(acc)
        else:
            self.calls["grouping"] += 1
            content = self._grouping(user)
        resp_body = {"model": CATALOG_MODEL, "choices": [{"message": {"content": json.dumps(content)}}],
                     "usage": {"prompt_tokens": chars // 4, "completion_tokens": 500}}
        headers = {"x-litellm-model-id": CATALOG_DEPLOYMENT_ID, "x-litellm-attempted-fallbacks": "0",
                   "x-litellm-response-cost": repr(cost)}
        return ProxyResponse(200, resp_body, json.dumps(resp_body)[:2000], headers, 5)

    def _inventory(self, acc):
        n = int(acc[-2:])
        fabricated = f"VERIFY FABRICATED QUOTE {n} that appears nowhere in this filing at all"
        self.fabricated[acc] = fabricated
        if acc in INV_PRODUCT:
            return {"document": {"product_family": "product supplement", "program_supplement": None,
                                 "has_hypothetical_payout_table": False, "issue_size": None},
                    "items": [{"label": "Contingent Coupon", "value": None,
                               "quote": "Contingent Coupon: a coupon paid on each Coupon Payment Date",
                               "section": "General Terms of the Notes", "maps_to": "NEW",
                               "proposed_field_key": "contingent_coupon_definition"},
                              {"label": "Phantom", "value": "x", "quote": fabricated, "section": None,
                               "maps_to": "NEW"}]}
        date_label = "Pricing Date" if INV_PRICING.index(acc) % 2 == 0 else "Trade Date"
        return {
            "document": {"product_family": "auto-callable contingent coupon notes",
                         "program_supplement": "product supplement no. VERIFY-1",
                         "has_hypothetical_payout_table": True, "issue_size": f"${n},250,000"},
            "items": [
                {"label": date_label, "value": f"March {n}, 2025", "quote": f"{date_label}: March {n}, 2025",
                 "section": "Key Terms", "maps_to": self.registry_key or "NEW", "proposed_field_key": None},
                {"label": "Coupon Barrier", "value": f"{60 + n}.00%", "quote": f"Coupon Barrier: {60 + n}.00% of the Initial Level",
                 "section": "Key Terms", "maps_to": "NEW", "proposed_field_key": "coupon_barrier_pct"},
                {"label": "Redemption Barrier", "value": f"{50 + n}.00%",
                 "quote": f"Redemption Barrier: {50 + n}.00% of the Initial Level",
                 "section": "Key Terms", "maps_to": "NEW", "proposed_field_key": "fixed_payment_threshold_pct",
                 "misleading_label": True,
                 "misleading_note": "Only the threshold for the fixed payment; it protects nothing."},
                {"label": "Aggregate Principal Amount", "value": f"${n},250,000",
                 "quote": f"Aggregate Principal Amount: ${n},250,000", "section": "Key Terms",
                 "maps_to": "NEW", "proposed_field_key": "issue_size"},
                {"label": "Phantom Field", "value": "1", "quote": fabricated, "section": "Key Terms", "maps_to": "NEW"},
            ],
        }

    @staticmethod
    def _grouping(user):
        ids = {}
        for line in user.splitlines():
            m = re.match(r"^(\d+)\. (.+?) — ", line)
            if m:
                ids[m.group(2)] = int(m.group(1))
        groups = []
        if "pricing date" in ids and "trade date" in ids:
            groups.append({"concept": "Pricing date", "label_ids": [ids["pricing date"], ids["trade date"]],
                           "maps_to": "NEW", "proposed_field_key": "pricing_date"})
        return {"groups": groups}


async def inventory_section(conn, inv_cohort) -> None:
    section("[Y] Inventory: dry run makes zero model calls; mocked run stores items with provenance; "
            "a fabricated quote is rejected and counted; terms pages stop before risk factors")
    import run_edgar_inventory as inv_script
    from services import edgar_inventory as inv
    from services.note_extraction import proxy, schema
    from services.note_extraction.proxy import Deployment

    # Terms pages.
    tp1 = inv.terms_pages((await fake_load_document(conn, REF_OF[INV_PRICING[0]])).text)
    tp2 = inv.terms_pages((await fake_load_document(conn, REF_OF[INV_PRICING[1]])).text)
    check(RISK_SENTINEL not in tp1.text and TAX_SENTINEL not in tp1.text and LICENSE_SENTINEL not in tp1.text
          and "Example 4" in tp1.text and "Coupon Barrier" in tp1.text,
          "terms pages keep the terms and payout examples and stop before risk factors, licence text and tax",
          f"stopped_at={tp1.stopped_at}")
    check(RISK_SENTINEL not in tp2.text and TAX_SENTINEL not in tp2.text and "Example 8" in tp2.text,
          "payout examples placed AFTER the risk section are still kept; the risk text is not", f"{tp2.sections}")
    check(tp1.tokens_est == (tp1.chars + 3) // 4 and tp1.chars < tp1.full_chars,
          "the terms-page size and token estimate are recorded")

    registry_rows = await schema.load_registry_rows(conn)
    registry_key = registry_rows[0]["field_key"] if registry_rows else None
    fake_catalog = {
        CATALOG_MODEL: Deployment(name=CATALOG_MODEL, deployment_id=CATALOG_DEPLOYMENT_ID, upstream=CATALOG_UPSTREAM,
                                  price_key=CATALOG_MODEL, input_cost_per_token=1e-6, output_cost_per_token=1e-6,
                                  cache_read_cost_per_token=None, supports_response_schema=False, max_input_tokens=200000),
        "claude-haiku": Deployment(name="claude-haiku", deployment_id="x", upstream="anthropic/claude-haiku-4-5",
                                   price_key=None, input_cost_per_token=1e-6, output_cost_per_token=1e-6,
                                   cache_read_cost_per_token=None, supports_response_schema=True, max_input_tokens=None),
    }

    # Model choice.
    chosen, report = await inv.choose_model(conn, fake_catalog)
    check(chosen == CATALOG_MODEL, "the run-time model choice takes the available, non-Claude, proxy-served entry",
          f"chosen={chosen}")
    haiku = next((r for r in report if r["model_id"] == "claude-haiku"), None)
    check(haiku is not None and haiku["eligible"] is False and "Claude" in (haiku["why_not"] or ""),
          "a Claude catalog entry is never eligible, even when the proxy serves it")
    blocked, _r = await inv.choose_model(conn, fake_catalog, "claude-haiku")
    check(blocked is None, "asking for a Claude model by name is refused (BLOCKED)")
    try:
        real_catalog = await asyncio.to_thread(proxy.deployment_catalog)
    except Exception as exc:  # noqa: BLE001
        real_catalog = None
        find("the live proxy catalogue could not be read; eligibility below ignores proxy registration", type(exc).__name__)
    real_ok, real_report = await inv.eligible_models(conn, real_catalog)
    real_ok = [m for m in real_ok if m != CATALOG_MODEL]
    find(f"live eligible inventory models: {real_ok or 'NONE — a real run reports BLOCKED'}",
         "; ".join(f"{r['model_id']}: {r['why_not']}" for r in real_report if not r["eligible"]
                   and r["model_id"] != CATALOG_MODEL)[:400])

    expected_docs = sorted(INV_PRICING + INV_PRODUCT)
    # Dry run (the CLI itself), zero model calls, nothing written.
    runs_before = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_inventory_runs")
    calls_before = (dict(proxy.CALLS), dict(inv.CALLS))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = await inv_script.main(["--cohort", str(inv_cohort), "--dry-run", "--spend-cap", "1",
                                    "--model", CATALOG_MODEL], catalog=fake_catalog, loader=fake_load_document)
    out = buf.getvalue()
    m = re.search(r"^PLANNED_DOCUMENTS (.*)$", out, re.M)
    planned = sorted(json.loads(m.group(1))) if m else None
    check(rc == 0 and planned == expected_docs,
          "the dry run plans every ready pricing supplement (fewer than 4 per issuer here) plus the product supplement",
          f"rc={rc} planned={planned} tail={out[-300:]!r}" if planned != expected_docs else "")
    check((dict(proxy.CALLS), dict(inv.CALLS)) == calls_before, "the dry run made ZERO model calls")
    runs_after = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_inventory_runs")
    check(runs_after == runs_before, "the dry run wrote no inventory run")

    # Mocked run.
    fake = FakeProxy(fake_catalog, registry_key)
    original_post = proxy._post
    proxy._post = fake
    try:
        summary = await inv.run_inventory(conn, inv_cohort, catalog=fake_catalog, spend_cap_usd=5.0, dry_run=False,
                                          loader=fake_load_document, registry_rows=registry_rows,
                                          deployment=CATALOG_MODEL)
    finally:
        proxy._post = original_post
    if summary.run_id:
        STATE["inventory_runs"].add(UUID(summary.run_id))
    check(summary.status == "completed" and summary.documents_done == len(expected_docs),
          "the mocked run completes every planned document", f"{summary.status} {summary.stop_reason}")
    check(fake.calls == {"inventory": len(expected_docs), "grouping": 1},
          "one inventory call per document and ONE grouping call", f"{fake.calls}")
    run = dict(await rls_row(conn, "SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", summary.run_id))
    docs = [dict(r) for r in await rls_fetch(conn, "SELECT * FROM portfolio.edgar_inventory_documents WHERE run_id = $1",
                                             summary.run_id)]
    items = [dict(r) for r in await rls_fetch(conn, "SELECT * FROM portfolio.edgar_inventory_items WHERE run_id = $1",
                                              summary.run_id)]
    expected_items = 4 * len(INV_PRICING) + 1 * len(INV_PRODUCT)
    check(len(items) == expected_items and run["items_accepted"] == expected_items,
          "every item with a real quote is stored", f"items={len(items)} expected={expected_items}")
    check(run["items_rejected"] == len(expected_docs) and sum(d["items_rejected"] for d in docs) == len(expected_docs),
          "each fabricated quote is REJECTED and counted (one per document)", f"rejected={run['items_rejected']}")
    fabricated = set(fake.fabricated.values())
    check(not any(i["quote"] in fabricated for i in items), "no stored item carries a fabricated quote")
    rej = [json.loads(d["rejected_items"]) if isinstance(d["rejected_items"], str) else d["rejected_items"] for d in docs]
    check(all(any(r.get("reason") == "quote not found in the filing" and r.get("quote") in fabricated for r in rr)
              for rr in rej), "each rejection is recorded on its document with the quote and the reason")
    check(all(i["deployment_name"] == CATALOG_MODEL and i["provider_model"] == CATALOG_MODEL and i["call_id"]
              for i in items), "every item records the model, the provider-reported model and its call id")
    check(all(d["status"] == "ok" and d["provider_model"] == CATALOG_MODEL and d["proxy_model_id"] == CATALOG_DEPLOYMENT_ID
              and d["cost_usd"] is not None and d["cost_usd"] > 0 for d in docs),
          "every document records provider-reported model, the serving deployment and its cost")
    doc_cost = sum(float(d["cost_usd"]) for d in docs)
    check(abs(float(run["spent_usd"]) - summary.spent_usd) < 1e-9 and float(run["spent_usd"]) > doc_cost,
          "the run's spend = its documents' costs plus the grouping call", f"run={run['spent_usd']} docs={doc_cost}")
    check(all(d["terms_tokens_est"] > 0 and d["terms_chars"] > 0 for d in docs), "token counts are recorded per document")
    pr = [d for d in docs if d["accession_number"] in INV_PRICING]
    check(all(d["has_payout_table"] is True and d["program_supplement"] and d["product_family"] and d["issue_size"]
              for d in pr), "per-document facts stored: product family, program supplement, payout table, issue size")
    by_text = {}
    for i in items:
        doc = await fake_load_document(conn, REF_OF[i["accession_number"]])
        by_text[i["id"]] = doc.text[i["quote_char_start"]:i["quote_char_end"]]
    check(all(re.sub(r"\s+", " ", by_text[i["id"]]).strip() == re.sub(r"\s+", " ", i["quote"]).strip() for i in items),
          "every stored quote's offsets point at that quote in the filing text")
    mis = [i for i in items if i["misleading_label"]]
    check(len(mis) == len(INV_PRICING) and all("protects nothing" in (i["misleading_note"] or "") for i in mis),
          "misleading-label flags are stored with their note")
    if registry_key:
        check(any(i["mapped_field_key"] == registry_key for i in items)
              and all(i["mapped_field_key"] is None or i["mapped_field_key"] == registry_key for i in items),
              "a mapping to an existing registry field is kept; NEW leaves the mapping empty")

    section("[Y] Concept grouping keeps every original label and quote attached; the label dictionary is produced")
    concepts = [dict(r) for r in await rls_fetch(conn, "SELECT * FROM portfolio.edgar_inventory_concepts WHERE run_id = $1",
                                                 summary.run_id)]
    check(all(i["concept_id"] is not None for i in items), "every item is attached to a concept")
    ok_labels = True
    for cpt in concepts:
        its = [i for i in items if i["concept_id"] == cpt["id"]]
        if sorted(cpt["labels"]) != sorted({i["label"] for i in its}) or cpt["frequency"] != len(its):
            ok_labels = False
    check(ok_labels, "each concept's labels are exactly its items' original labels, and its frequency their count")
    pd = next((cp for cp in concepts if set(cp["labels"]) == {"Pricing Date", "Trade Date"}), None)
    check(pd is not None and pd["grouping_method"] == "model",
          "the model-assisted grouping merged 'Pricing Date' and 'Trade Date' into one concept, keeping both labels")
    check(sum(cp["frequency"] for cp in concepts) == len(items), "no item is counted twice or lost across concepts")
    check(all(i["quote"] and i["label"] for i in items), "every item still carries its original label and quote")
    mb = next((cp for cp in concepts if "Redemption Barrier" in cp["labels"]), None)
    flags = mb and (json.loads(mb["misleading_flags"]) if isinstance(mb["misleading_flags"], str) else mb["misleading_flags"])
    check(bool(flags) and len(flags) == len(INV_PRICING), "the concept carries every misleading-label flag")
    s, b = await api(U_SUPER, "GET", f"{BASE}/inventory/{summary.run_id}")
    dictionary = (b or {}).get("label_dictionary") or {}
    expected_pairs = {(i["issuer_group"], i["label"]) for i in items}
    got_pairs = {(iss, lab) for iss, labs in dictionary.items() for lab in labs}
    check(s == 200 and got_pairs == expected_pairs, "the per-issuer label dictionary covers every issuer x label",
          f"HTTP {s} got={len(got_pairs)} expected={len(expected_pairs)}")
    check(len(b.get("rows", [])) == len(concepts), "the Cohorts-tab concepts grid lists every concept")
    s_o, _ = await api(U_ORGADMIN, "GET", f"{BASE}/inventory/{summary.run_id}")
    check(s_o == 403, "the inventory grid is super-admin only (org admin 403)")
    md = inv.render_markdown(run, docs, await inv.inventory_concepts(conn, summary.run_id),
                             await inv.run_label_dictionary(conn, summary.run_id))
    check("## Concepts" in md and "Pricing Date; Trade Date" in md and "## Per-issuer label dictionary" in md,
          "docs/TEMPLATE_STUDY.md renders from the stored run (concepts + label dictionary)")

    section("[Y] The spending cap stops a run cleanly")
    dry = await inv.run_inventory(conn, inv_cohort, catalog=fake_catalog, spend_cap_usd=1.0, dry_run=True,
                                  loader=fake_load_document, registry_rows=registry_rows, deployment=CATALOG_MODEL)
    first_est = dry.plan[0]["est_cost_usd"]
    cap = first_est + 1e-6
    fake2 = FakeProxy(fake_catalog, registry_key)
    proxy._post = fake2
    try:
        capped = await inv.run_inventory(conn, inv_cohort, catalog=fake_catalog, spend_cap_usd=cap, dry_run=False,
                                         loader=fake_load_document, registry_rows=registry_rows,
                                         deployment=CATALOG_MODEL)
        raised = False
    except Exception as exc:  # noqa: BLE001
        raised, capped = True, None
        print(f"  raised {type(exc).__name__}: {exc}")
    finally:
        proxy._post = original_post
    check(not raised, "the capped run returns normally (no exception)")
    if capped:
        if capped.run_id:
            STATE["inventory_runs"].add(UUID(capped.run_id))
        crow = dict(await rls_row(conn, "SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", capped.run_id))
        n_docs = await rls_val(conn, "SELECT count(*) FROM portfolio.edgar_inventory_documents WHERE run_id = $1",
                               capped.run_id)
        check(crow["status"] == "stopped_spend_cap" and "spending cap" in (crow["stop_reason"] or "")
              and crow["finished_at"] is not None,
              "the run stops as 'stopped_spend_cap' with the reason, and is finished", f"{crow['status']}")
        check(crow["documents_done"] == 1 and n_docs == 1 and fake2.calls["inventory"] == 1 and fake2.calls["grouping"] == 0,
              "exactly the documents that fit under the cap were read; the call that would cross it was never made",
              f"done={crow['documents_done']} calls={fake2.calls}")
        check(float(crow["spent_usd"]) <= cap, "recorded spend never exceeds the cap", f"{crow['spent_usd']} <= {cap}")
    blk = await inv.run_inventory(conn, inv_cohort, catalog={}, spend_cap_usd=1.0, dry_run=False,
                                  loader=fake_load_document, registry_rows=registry_rows)
    check(blk.status == "blocked" and blk.run_id is None and "BLOCKED" in (blk.stop_reason or ""),
          "with no eligible model the run reports BLOCKED and writes nothing")


def static_scan() -> None:
    section("[Y] No Claude model, OpenRouter route, provider key or direct provider call in application code")
    files = [API_DIR / "services" / "edgar_inventory.py", API_DIR / "services" / "edgar_cohorts.py",
             API_DIR / "services" / "edgar_pipeline.py", API_DIR / "routers" / "edgar_pipeline_admin.py",
             API_DIR / "scripts" / "run_edgar_inventory.py", API_DIR / "edgar_pipeline_job.py",
             API_DIR / "services" / "note_extraction" / "selection.py",
             WEB_DIR / "components" / "admin" / "EdgarCohorts.jsx",
             WEB_DIR / "components" / "admin" / "EdgarPipelineMonitor.jsx"]
    patterns = {
        "a Claude model id": re.compile(r"claude-(?:haiku|sonnet|opus|fable|\d)", re.I),
        "an OpenRouter route": re.compile(r"openrouter/|openrouter\.ai", re.I),
        "a provider SDK import": re.compile(r"^\s*(?:import|from)\s+(?:anthropic|openai|litellm|google\.generativeai|mistralai|cohere)\b", re.M),
        "a provider API host": re.compile(r"api\.(?:anthropic|openai|mistral|deepseek|together)\.(?:com|ai|xyz)|generativelanguage\.googleapis", re.I),
        "a provider key name": re.compile(r"(?:ANTHROPIC|OPENAI|GEMINI|GOOGLE|MISTRAL|DEEPSEEK|OPENROUTER|DEEPINFRA|GROQ)_API_KEY"),
    }
    for f in files:
        text = f.read_text()
        hits = [name for name, rx in patterns.items() if rx.search(text)]
        check(not hits, f"{f.relative_to(REPO)}: none found", f"{hits}")
    inv_text = (API_DIR / "services" / "edgar_inventory.py").read_text()
    check("httpx" not in inv_text and "proxy.chat(" in inv_text,
          "the inventory pass makes its calls only through the proxy chokepoint (proxy.chat); no HTTP client of its own")


async def teardown_and_restore(conn, fp_before) -> None:
    section("[Y] Teardown: fixtures gone; manifest rows restored exactly")
    cids = await fixture_cohort_ids(conn)
    await teardown_fixtures(conn)
    left = await rls_row(conn, """SELECT
        (SELECT count(*) FROM portfolio.edgar_index_filings WHERE accession_number LIKE $1) AS filings,
        (SELECT count(*) FROM portfolio.edgar_index_filing_filers WHERE accession_number LIKE $1) AS filers,
        (SELECT count(*) FROM portfolio.reference_filings WHERE accession_number LIKE $1) AS refs,
        (SELECT count(*) FROM portfolio.structured_note_issuers WHERE filer_cik = ANY($2::text[])) AS issuers,
        (SELECT count(*) FROM portfolio.edgar_cohorts WHERE id = ANY($3::uuid[]) OR name LIKE $4) AS cohorts,
        (SELECT count(*) FROM portfolio.edgar_cohort_members WHERE cohort_id = ANY($3::uuid[])) AS members,
        (SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE id = ANY($5::uuid[])) AS runs,
        (SELECT count(*) FROM portfolio.edgar_inventory_runs WHERE id = ANY($6::uuid[])) AS inventory_runs,
        (SELECT count(*) FROM portfolio.edgar_inventory_items WHERE run_id = ANY($6::uuid[])) AS items,
        (SELECT count(*) FROM portfolio.edgar_pipeline_lease WHERE lease_name = $7) AS lease,
        (SELECT count(*) FROM platform_model_catalog WHERE model_id = $8) AS catalog,
        (SELECT count(*) FROM users WHERE id = ANY($9::uuid[])) AS users""",
        ACC_PREFIX + "%", FIXTURE_CIKS, cids, COHORT_PREFIX + "%", list(STATE["pipeline_runs"]),
        list(STATE["inventory_runs"]), VERIFY_LEASE, CATALOG_MODEL, FIXTURE_USERS)
    check(all(v == 0 for v in dict(left).values()), "zero fixture rows remain", f"{dict(left)}")
    fp_after = await manifest_fingerprint(conn)
    check(fp_after == fp_before, "the real manifest's fingerprint (row count + md5 of every row) is unchanged",
          f"before={fp_before} after={fp_after}")


def npm_build() -> None:
    section("[Y] npm run build exits 0")
    try:
        proc = subprocess.run(["npm", "run", "build"], cwd=WEB_DIR, capture_output=True, text=True, timeout=1500)
    except Exception as exc:  # noqa: BLE001
        check(False, "npm run build exits 0", type(exc).__name__)
        return
    ok = proc.returncode == 0
    check(ok, "npm run build exits 0", "" if ok else (proc.stdout + proc.stderr)[-600:])
    if ok:
        check("/admin/edgar-pipeline" in proc.stdout, "the build emitted /admin/edgar-pipeline")


async def main_async() -> int:
    dsn = await bootstrap_async()
    if not dsn:
        print("[SKIP] no working DATABASE_URL — nothing can be proven")
        return 2

    from services.action_registry import REGISTRY
    from services.assistant_actions import register_all

    register_all()
    original_sync = REGISTRY.sync_catalog

    async def _noop_sync(pool, org_id):
        return None

    REGISTRY.sync_catalog = _noop_sync   # main.py's startup hook writes the REAL org's catalog

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    fp_before = None
    try:
        await teardown_fixtures(conn)
        fp_before = await manifest_fingerprint(conn)
        policy_fp = await table_fingerprint(conn, "portfolio.edgar_selection_policies", "version")
        print(f"manifest fingerprint before: rows={fp_before[0]}")
        await seed(conn)
        # The app's pool must live on THIS loop, the one api() calls the app on.
        from services.database import get_pool
        await get_pool()

        async def run(name, coro):
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001 — one section's crash never skips the rest
                import traceback
                traceback.print_exc()
                check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")
                return None

        await run("task1", task1_findings(conn))
        ids = await run("build", build_section(conn)) or {}
        await run("random", random_section(conn))
        if ids.get("hand"):
            await run("frozen", frozen_section(conn, ids))
        await run("size", size_section(conn))
        await run("permissions", permission_section(conn))
        fetch_cid = await run("fetch", fetch_section(conn))
        if fetch_cid:
            await run("lease", lease_section(conn, fetch_cid))
        await run("default_policy", default_policy_section(conn, policy_fp))
        await run("b1", b1_section(conn))
        await run("template_study", template_study_section(conn))
        from services import edgar_cohorts as c
        inv_cohort = await c.create_cohort(conn, name=f"{COHORT_PREFIX} inventory", purpose="verify",
                                           definition={"source": "hand", "accessions": INV_PRICING + INV_PRODUCT + [A(1)]})
        await run("inventory", inventory_section(conn, inv_cohort["id"]))
        static_scan()
    except Exception as exc:  # noqa: BLE001 — report, then still tear down
        import traceback
        traceback.print_exc()
        check(False, "the verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if fp_before is not None:
                await teardown_and_restore(conn, fp_before)
            else:
                await teardown_fixtures(conn)
        finally:
            REGISTRY.sync_catalog = original_sync
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
