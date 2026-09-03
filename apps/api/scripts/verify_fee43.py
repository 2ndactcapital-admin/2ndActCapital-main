"""Sprint fee43 verification — invoices, reconciliation, GL posting.

Pass/fail only, no prompts. Run:

    python3 apps/api/scripts/verify_fee43.py

Every table this script writes to is counted before the first insert and again
after the last delete; a difference of even one row fails the run, reported
AFTER the tests so a teardown bug never masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[1] proves the pre-existing entry is untouched by CONTENT, not by count.**
  Every ``journal_entries`` row that existed before this script ran is captured
  as a full row image (every column, cast to text) plus its lines, and compared
  field-for-field at the end. A count-only check would pass while this sprint
  silently rewrote the one entry the platform already had. ``vehicle_kind`` is
  asserted to be exactly ``'SPV'`` on it — the additive-first proof fee42 set
  the standard for.

* **[3] proves BOTH routing directions on ONE run.** The mixed run carries an
  ASSET_MANAGEMENT line and a CLUB_DUES line. Two separate single-product runs
  would pass even if routing ignored ``product_type`` entirely and keyed on
  something per-run. The same run producing two entries, into two DIFFERENT
  ledger_books, is what proves the routing table is actually consulted per line.
  Each entry's account codes and its debit/credit sides are asserted, because
  "an entry exists" would pass for one booked to the wrong account.

* **[5] forces a REAL failure rather than simulating one.** The CLUB_DUES
  posting template is deactivated for the duration of one ``post_run`` call, so
  the GL genuinely cannot resolve a template — one of the exact failure modes
  ``post_to_ledger``'s docstring names. The run must then be found still
  COMPLIANCE_APPROVED with ``posted_at`` NULL, no journal entry, AND no
  ``revenue_events`` row: the last of those is what proves the rollback covered
  the whole transaction rather than just the status UPDATE. The template's
  ``is_active`` is restored in a ``finally`` and re-asserted in [9].

* **[6] compares OUTPUT, not the code path.** The invoice's disclosure text is
  compared against an independent, direct ``fee_narratives.render_narrative``
  call made by this script. Asserting that ``fee_invoices`` imports fee41 would
  prove an import; asserting the two strings are equal proves the text a client
  receives is the text fee41 produced. The negative is proven too: with the
  template removed, generation must RAISE rather than emit fallback prose —
  which is what rules out a second generator hiding behind a default.

* **[7] proves the exception path is not "everything is an exception".** A
  statement that ties produces MATCHED receipts and a statement that does not
  tie produces EXCEPTION receipts, on the SAME run and the same billed amounts,
  with only the stated total differing. The non-tying statement's per-line
  variances are exactly zero — so the lines would each look perfect — and every
  receipt must still be EXCEPTION. That is the "never silently posts" claim.

* **[8] runs cross-org isolation under app_service, whose ``rolbypassrls`` is
  asserted False first.** Under ``postgres`` every policy is inert and the
  whole check passes vacuously.

* **Teardown disables the immutability triggers.** A POSTED fee_run, carry run
  and journal entry cannot be deleted or updated while they are on — that IS
  the trigger. [9] asserts every one of them is back ON before the script
  exits, so a crash between the two cannot leave a table unguarded without the
  run failing.
"""

from __future__ import annotations

import asyncio
import glob
import json
import pathlib
import sys
import traceback
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncpg  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "fee43verify"
P = "99000000-0000-0000-0000-00004c43"

U_CREATE = f"{P}0001"
U_ADVISOR = f"{P}0002"
U_COMPLY = f"{P}0003"
U_REVIEW = f"{P}0004"
USERS = [U_CREATE, U_ADVISOR, U_COMPLY, U_REVIEW]

E_HH_A = f"{P}0011"
E_HH_B = f"{P}0012"
E_SPV_VEH = f"{P}0013"
E_XORG = f"{P}0014"
ENTITIES = [E_HH_A, E_HH_B, E_SPV_VEH, E_XORG]

HH_A = f"{P}0021"
HH_B = f"{P}0022"
HOUSEHOLDS = [HH_A, HH_B]

ACC_A = f"{P}0031"
ACC_B = f"{P}0032"
ACCOUNTS = [ACC_A, ACC_B]
MASK_A = "***4301"
MASK_B = "***4302"

FS_AM = f"{P}0041"
FS_CD = f"{P}0042"
SCHEDULES = [FS_AM, FS_CD]

RUN_MIX = f"{P}0051"
RUN_FAIL = f"{P}0052"
RUN_XORG = f"{P}0053"
RUNS = [RUN_MIX, RUN_FAIL, RUN_XORG]

L_AM_A = f"{P}0061"
L_CD_B = f"{P}0062"
L_FAIL = f"{P}0063"
L_XORG = f"{P}0064"
LINES = [L_AM_A, L_CD_B, L_FAIL, L_XORG]

DEAL = f"{P}0071"
SPV = f"{P}0081"
CRUN = f"{P}0091"
CLINE = f"{P}0092"

DOC_TIE = f"{P}0101"
DOC_NOTIE = f"{P}0102"
DOCS = [DOC_TIE, DOC_NOTIE]
EXTR_TIE = f"{P}0111"
EXTR_NOTIE = f"{P}0112"
EXTRACTIONS = [EXTR_TIE, EXTR_NOTIE]

TMPL = f"{P}0121"
TEMPLATE_CODE = "FEE_DISCLOSURE"

BOOK_XORG = f"{P}0131"
INV_XORG = f"{P}0132"
RCPT_XORG = f"{P}0133"
JE_XORG = f"{P}0134"

# Billed amounts. Chosen so the two product types are distinguishable in the
# ledger at a glance and neither total can be mistaken for the other.
AMT_AM = D("1000.00")
AMT_CD = D("250.00")
AMT_TOTAL = AMT_AM + AMT_CD
AMT_FAIL = D("500.00")
CARRY_GAIN = D("10000.00")
CARRY_GP = D("2000.00")
CARRY_LP = CARRY_GAIN - CARRY_GP

#: Tables whose row counts must be identical before and after.
COUNTED = [
    "public.journal_entries", "public.journal_lines", "public.ledger_books",
    "public.fee_invoices", "public.fee_receipts", "public.fee_runs",
    "public.fee_run_lines", "public.fee_schedules", "public.spv_carry_runs",
    "public.spv_carry_run_lines", "public.revenue_events",
    "public.fee_narratives", "public.fee_narrative_templates",
    "public.chart_of_accounts", "public.posting_templates",
    "public.posting_template_lines", "public.documents",
    "public.document_extractions", "public.assistant_activities",
    "public.households", "public.accounts", "public.entities",
    "public.users", "public.deals", "public.spvs",
]

#: (table, trigger) pairs teardown must switch off and [9] must find back on.
TRIGGER_NAMES = [
    ("public.fee_runs", "fee_runs_immutable_once_posted"),
    ("public.fee_run_lines", "fee_run_lines_immutable_once_posted"),
    ("public.spv_carry_runs", "spv_carry_runs_immutable_once_posted"),
    ("public.spv_carry_run_lines", "spv_carry_run_lines_immutable_once_posted"),
    ("public.journal_lines", "trg_guard_posted_lines"),
]

PASS: list[str] = []
FAIL: list[str] = []
FIND: list[str] = []


def ok(label: str, condition: bool, detail: str = "") -> bool:
    (PASS if condition else FAIL).append(f"{label}: {detail}" if detail else label)
    print(f"{'[PASS]' if condition else '[FAIL]'} {label}"
          + (f" — {detail}" if detail else ""))
    return condition


def find(label: str, detail: str) -> None:
    FIND.append(f"{label}: {detail}")
    print(f"[FIND] {label} — {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def _set_triggers(conn, enabled: bool) -> None:
    verb = "ENABLE" if enabled else "DISABLE"
    for table, trig in TRIGGER_NAMES:
        await conn.execute(f"ALTER TABLE {table} {verb} TRIGGER {trig}")


async def teardown(conn) -> None:
    """By fixture id, in FK order. Never a TRUNCATE."""
    await _set_triggers(conn, False)
    try:
        # Journal lines/entries this script's postings created, located by the
        # run ids in source_event_id — the only link back (fee_gl.entries_for_run).
        await conn.execute(
            """DELETE FROM public.journal_lines
               WHERE entry_id IN (SELECT id FROM public.journal_entries
                                  WHERE source_event_id = ANY($1::uuid[])
                                     OR id = ANY($2::uuid[]))""",
            RUNS + [CRUN], [JE_XORG])
        await conn.execute(
            """DELETE FROM public.journal_entries
               WHERE source_event_id = ANY($1::uuid[]) OR id = ANY($2::uuid[])""",
            RUNS + [CRUN], [JE_XORG])
        await conn.execute(
            "DELETE FROM public.spv_carry_run_lines WHERE id = ANY($1::uuid[]) "
            "OR spv_carry_run_id = ANY($2::uuid[])", [CLINE], [CRUN])
        await conn.execute(
            "DELETE FROM public.spv_carry_runs WHERE id = ANY($1::uuid[])", [CRUN])
        await conn.execute(
            "DELETE FROM public.fee_receipts WHERE fee_run_line_id = ANY($1::uuid[]) "
            "OR id = ANY($2::uuid[])", LINES, [RCPT_XORG])
        await conn.execute(
            "DELETE FROM public.fee_invoices WHERE fee_run_id = ANY($1::uuid[]) "
            "OR id = ANY($2::uuid[])", RUNS, [INV_XORG])
        await conn.execute(
            "DELETE FROM public.fee_run_lines WHERE id = ANY($1::uuid[]) "
            "OR fee_run_id = ANY($2::uuid[])", LINES, RUNS)
        await conn.execute(
            "DELETE FROM public.fee_runs WHERE id = ANY($1::uuid[])", RUNS)
    finally:
        await _set_triggers(conn, True)

    await conn.execute(
        "DELETE FROM public.revenue_events WHERE source_id = ANY($1::uuid[])",
        RUNS + LINES + [CRUN])
    await conn.execute(
        "DELETE FROM public.fee_narratives WHERE fee_schedule_id = ANY($1::uuid[]) "
        "OR template_id = ANY($2::uuid[])", SCHEDULES, [TMPL])
    await conn.execute(
        "DELETE FROM public.fee_narrative_templates WHERE id = ANY($1::uuid[])",
        [TMPL])
    await conn.execute(
        "DELETE FROM public.assistant_activities WHERE related_id = ANY($1::uuid[])",
        RUNS + [CRUN])
    await conn.execute(
        "DELETE FROM public.document_extractions WHERE id = ANY($1::uuid[]) "
        "OR document_id = ANY($2::uuid[])", EXTRACTIONS, DOCS)
    await conn.execute(
        "DELETE FROM public.documents WHERE id = ANY($1::uuid[])", DOCS)
    await conn.execute(
        "DELETE FROM public.fee_schedules WHERE id = ANY($1::uuid[])", SCHEDULES)
    await conn.execute(
        "DELETE FROM public.ledger_books WHERE id = ANY($1::uuid[])", [BOOK_XORG])
    await conn.execute(
        "DELETE FROM public.spvs WHERE id = ANY($1::uuid[])", [SPV])
    await conn.execute("DELETE FROM public.deals WHERE id = ANY($1::uuid[])", [DEAL])
    await conn.execute(
        "DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", ACCOUNTS)
    await conn.execute(
        "DELETE FROM public.households WHERE id = ANY($1::uuid[])", HOUSEHOLDS)
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", ENTITIES)
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def _activity(conn, related_type: str, related_id: str, action_key: str,
                    title: str) -> None:
    """An APPROVED maker-checker row. proposed_by <> approved_by, per the CHECK."""
    await conn.execute(
        """INSERT INTO public.assistant_activities
             (org_id, user_id, action_key, title, status, related_type,
              related_id, proposed_by, approved_by)
           VALUES ($1::uuid, $2::uuid, $3, $4, 'approved', $5, $6::uuid,
                   $2::uuid, $7::uuid)""",
        ORG, U_ADVISOR, action_key, title, related_type, related_id, U_COMPLY)


async def setup(conn) -> None:
    for uid, email in zip(USERS, ("create", "advisor", "comply", "review")):
        await conn.execute(
            "INSERT INTO public.users (id, org_id, email, full_name) "
            "VALUES ($1::uuid, $2::uuid, $3, $4)",
            uid, ORG, f"{TAG}.{email}@example.invalid", f"{TAG} {email}")

    for eid, org, etype, name in (
        (E_HH_A, ORG, "household", f"{TAG} household A"),
        (E_HH_B, ORG, "household", f"{TAG} household B"),
        (E_SPV_VEH, ORG, "spv", f"{TAG} SPV vehicle"),
        (E_XORG, OTHER_ORG, "household", f"{TAG} other org"),
    ):
        await conn.execute(
            "INSERT INTO public.entities (id, org_id, entity_type, display_name) "
            "VALUES ($1::uuid, $2::uuid, $3::entity_type, $4)", eid, org, etype, name)

    for hid, name in ((HH_A, f"{TAG} Household A"), (HH_B, f"{TAG} Household B")):
        await conn.execute(
            "INSERT INTO public.households (id, org_id, name) "
            "VALUES ($1::uuid, $2::uuid, $3)", hid, ORG, name)

    for aid, mask, ent, hh in ((ACC_A, MASK_A, E_HH_A, HH_A),
                               (ACC_B, MASK_B, E_HH_B, HH_B)):
        await conn.execute(
            """INSERT INTO public.accounts
                 (id, org_id, account_number_masked, account_number_hash,
                  custodian_code, registration_type, tax_status,
                  primary_entity_id, household_id)
               VALUES ($1::uuid, $2::uuid, $3, $4, 'ALTRUIST', 'INDIVIDUAL',
                       'TAXABLE', $5::uuid, $6::uuid)""",
            aid, ORG, mask, f"{TAG}-{mask}", ent, hh)

    for sid, code, product in ((FS_AM, f"{TAG}_AM", "ASSET_MANAGEMENT"),
                               (FS_CD, f"{TAG}_CD", "CLUB_DUES")):
        await conn.execute(
            """INSERT INTO public.fee_schedules
                 (id, org_id, code, name, product_type, rate_type,
                  billing_frequency, billing_timing, valuation_method, status)
               VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'FLAT', 'QUARTERLY',
                       'ARREARS', 'PERIOD_END', 'APPROVED')""",
            sid, ORG, code, f"{TAG} {product}", product)

    for rid, org in ((RUN_MIX, ORG), (RUN_FAIL, ORG), (RUN_XORG, OTHER_ORG)):
        await conn.execute(
            """INSERT INTO public.fee_runs
                 (id, org_id, period_start, period_end, billing_frequency,
                  run_type, status, calculation_snapshot_hash, created_by)
               VALUES ($1::uuid, $2::uuid, DATE '2026-04-01', DATE '2026-06-30',
                       'QUARTERLY', 'SCHEDULED', 'COMPLIANCE_APPROVED',
                       $3, $4::uuid)""",
            rid, org, f"{TAG}-snapshot", U_CREATE)

    detail = json.dumps({"source": TAG})
    for lid, run, org, hh, ent, acct, sched, product, net in (
        (L_AM_A, RUN_MIX, ORG, HH_A, E_HH_A, ACC_A, FS_AM, "ASSET_MANAGEMENT", AMT_AM),
        (L_CD_B, RUN_MIX, ORG, HH_B, E_HH_B, ACC_B, FS_CD, "CLUB_DUES", AMT_CD),
        (L_FAIL, RUN_FAIL, ORG, HH_B, E_HH_B, ACC_B, FS_CD, "CLUB_DUES", AMT_FAIL),
        (L_XORG, RUN_XORG, OTHER_ORG, None, E_XORG, None, FS_AM, "ASSET_MANAGEMENT",
         D("77.00")),
    ):
        await conn.execute(
            """INSERT INTO public.fee_run_lines
                 (id, org_id, fee_run_id, account_id, household_id, entity_id,
                  product_type, fee_schedule_id, billable_value, valuation_method,
                  gross_fee, net_fee, calc_detail)
               VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6::uuid,
                       $7, $8::uuid, $9, 'PERIOD_END', $9, $10, $11::jsonb)""",
            lid, org, run, acct, hh, ent, product, sched,
            net * 100, net, detail)

    for rid in (RUN_MIX, RUN_FAIL):
        await _activity(conn, "fee_run", rid, "fee_run.advisor_approve",
                        "Advisor approval of fee run")
        await _activity(conn, "fee_run", rid, "fee_run.compliance_approve",
                        "Compliance approval of fee run")

    await conn.execute(
        "INSERT INTO public.deals (id, org_id, name) VALUES ($1::uuid, $2::uuid, $3)",
        DEAL, ORG, f"{TAG} deal")
    await conn.execute(
        """INSERT INTO public.spvs (id, org_id, deal_id, vehicle_entity_id, name,
                                    carry_pct, currency)
           VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 20, 'USD')""",
        SPV, ORG, DEAL, E_SPV_VEH, f"{TAG} SPV")
    await conn.execute(
        """INSERT INTO public.spv_carry_runs
             (id, org_id, spv_id, status, carry_basis,
              calculation_snapshot_hash, created_by)
           VALUES ($1::uuid, $2::uuid, $3::uuid, 'COMPLIANCE_APPROVED',
                   'DEAL_BY_DEAL', $4, $5::uuid)""",
        CRUN, ORG, SPV, f"{TAG}-carry-snapshot", U_CREATE)
    await conn.execute(
        """INSERT INTO public.spv_carry_run_lines
             (id, org_id, spv_carry_run_id, entity_id, gross_gain_allocated,
              return_of_capital, preferred_return, gp_catchup, carry_to_gp,
              net_to_lp, calc_detail)
           VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 0, 0, 0, $6, $7,
                   $8::jsonb)""",
        CLINE, ORG, CRUN, E_HH_A, CARRY_GAIN, CARRY_GP, CARRY_LP, detail)
    await _activity(conn, "spv_carry_run", CRUN, "spv_carry_run.advisor_approve",
                    "Advisor approval of SPV carry run")
    await _activity(conn, "spv_carry_run", CRUN, "spv_carry_run.compliance_approve",
                    "Compliance approval of SPV carry run")

    # Two omnibus statements over the SAME billed amounts. Only the stated total
    # differs — that is what makes [7]'s two directions a controlled comparison.
    tie_rows = [[MASK_A, str(AMT_AM)], [MASK_B, str(AMT_CD)],
                ["Statement Total", str(AMT_TOTAL)]]
    notie_rows = [[MASK_A, str(AMT_AM)], [MASK_B, str(AMT_CD)],
                  ["Statement Total", str(AMT_TOTAL + D("400.00"))]]
    for did, eid, rows, fname in ((DOC_TIE, EXTR_TIE, tie_rows, "omnibus_ties.pdf"),
                                  (DOC_NOTIE, EXTR_NOTIE, notie_rows,
                                   "omnibus_does_not_tie.pdf")):
        await conn.execute(
            """INSERT INTO public.documents
                 (id, org_id, original_filename, source, mime_type, storage_key,
                  status, doc_family, created_by)
               VALUES ($1::uuid, $2::uuid, $3, 'upload', 'application/pdf', $4,
                       'processed', 'statement', $5::uuid)""",
            did, ORG, fname, f"{TAG}/{fname}", U_CREATE)
        await conn.execute(
            """INSERT INTO public.document_extractions
                 (id, org_id, document_id, extraction_method, extracted_tables)
               VALUES ($1::uuid, $2::uuid, $3::uuid, 'textract', $4::jsonb)""",
            eid, ORG, did, json.dumps([{"rows": rows}]))

    # fee_narrative_templates is EMPTY on the deployed database, so the template
    # fee41 renders from is a fixture. Tokens are schedule-scoped only: the
    # precedence tokens resolve through positions, which a fixture household
    # does not own (fee41's own finding).
    await conn.execute(
        """INSERT INTO public.fee_narrative_templates
             (id, org_id, template_code, body_template, version)
           VALUES ($1::uuid, $2::uuid, $3, $4, 1)""",
        TMPL, ORG, TEMPLATE_CODE,
        "Fees under {{schedule.name}} ({{schedule.code}} v{{schedule.version}}) "
        "are billed {{schedule.billing_frequency_label}} in "
        "{{schedule.billing_timing_label}}, in {{schedule.currency}}.")

    # Other-org rows for [8]. The journal entry points at the other org's own
    # ledger_book, so it is a structurally real row and not a malformed one.
    await conn.execute(
        """INSERT INTO public.ledger_books (id, org_id, book_code, name)
           VALUES ($1::uuid, $2::uuid, $3, $4)""",
        BOOK_XORG, OTHER_ORG, "RIA_OPERATING", f"{TAG} other-org book")
    await conn.execute(
        """INSERT INTO public.fee_invoices
             (id, org_id, fee_run_id, invoice_number, status, total_amount)
           VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'DRAFT', 10)""",
        INV_XORG, OTHER_ORG, RUN_XORG, f"INV-{TAG}-XORG")
    await conn.execute(
        """INSERT INTO public.fee_receipts
             (id, org_id, fee_run_line_id, received_amount, received_on, source)
           VALUES ($1::uuid, $2::uuid, $3::uuid, 10, DATE '2026-07-01', 'MANUAL')""",
        RCPT_XORG, OTHER_ORG, L_XORG)
    await conn.execute(
        """INSERT INTO public.journal_entries
             (id, org_id, vehicle_id, vehicle_kind, entry_date, transaction_type_code)
           VALUES ($1::uuid, $2::uuid, $3::uuid, 'LEDGER_BOOK', DATE '2026-06-30',
                   'ADVISORY_FEE_REVENUE')""",
        JE_XORG, OTHER_ORG, BOOK_XORG)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


async def je_image(conn) -> dict[str, dict]:
    """Every journal entry, as a comparable all-text image, with its lines."""
    rows = await conn.fetch(
        "SELECT to_jsonb(je)::text AS img, id::text AS id FROM public.journal_entries je")
    out = {}
    for r in rows:
        lines = await conn.fetch(
            "SELECT to_jsonb(jl)::text AS img FROM public.journal_lines jl "
            "WHERE jl.entry_id = $1::uuid ORDER BY line_no", r["id"])
        out[r["id"]] = {"entry": r["img"], "lines": [x["img"] for x in lines]}
    return out


async def entry_lines(conn, entry_id: str) -> list[dict]:
    rows = await conn.fetch(
        """SELECT jl.line_no, coa.code, jl.debit, jl.credit
           FROM public.journal_lines jl
           JOIN public.chart_of_accounts coa ON coa.id = jl.account_id
           WHERE jl.entry_id = $1::uuid ORDER BY jl.line_no""", entry_id)
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


async def test_1_objects_and_untouched(conn, before_img: dict) -> None:
    print("\n── [1] deployed objects + the pre-existing entry is untouched ──")

    cols = {}
    for table in ("ledger_books", "fee_invoices", "fee_receipts", "journal_entries"):
        rows = await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=$1", table)
        cols[table] = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in rows}

    ok("[1a] ledger_books deployed with book_code/name + system axis",
       {"id", "org_id", "book_code", "name", "system_to", "valid_to"}
       <= set(cols["ledger_books"]),
       f"{len(cols['ledger_books'])} columns")
    ok("[1b] journal_entries.vehicle_kind deployed NOT NULL",
       cols["journal_entries"].get("vehicle_kind") == ("text", "NO"),
       str(cols["journal_entries"].get("vehicle_kind")))

    check = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='journal_entries_vehicle_kind_check'")
    ok("[1c] vehicle_kind CHECK admits exactly SPV and LEDGER_BOOK",
       check is not None and "'SPV'" in check and "'LEDGER_BOOK'" in check,
       str(check))

    ok("[1d] fee_invoices deployed",
       {"invoice_number", "status", "total_amount", "fee_run_id", "household_id"}
       <= set(cols["fee_invoices"]), f"{len(cols['fee_invoices'])} columns")
    ok("[1e] fee_receipts deployed with variance + paired review columns",
       {"variance", "reconciliation_status", "reviewed_by", "reviewed_at"}
       <= set(cols["fee_receipts"]), f"{len(cols['fee_receipts'])} columns")

    pair = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='fee_receipts_reviewed_pair_check'")
    ok("[1f] reviewed_by/reviewed_at are a paired CHECK", pair is not None, str(pair))

    # The additive-first proof. Every entry that predates this script, compared
    # by full row image rather than by count.
    pre = {k: v for k, v in before_img.items() if k != JE_XORG}
    ok("[1g] exactly one journal_entries row predates this sprint's postings",
       len(pre) == 1, f"{len(pre)} row(s)")
    if pre:
        eid = next(iter(pre))
        row = await conn.fetchrow(
            "SELECT vehicle_kind, vehicle_id::text AS vehicle_id "
            "FROM public.journal_entries WHERE id=$1::uuid", eid)
        ok("[1h] the pre-existing entry's vehicle_kind is backfilled to 'SPV'",
           row is not None and row["vehicle_kind"] == "SPV",
           f"{eid} -> {row['vehicle_kind'] if row else 'MISSING'}")
        is_spv = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.spvs WHERE id=$1::uuid)",
            row["vehicle_id"]) if row else False
        ok("[1i] its vehicle_id really resolves to an spvs row",
           bool(is_spv), f"vehicle_id={row['vehicle_id'] if row else None}")


async def test_2_books_and_accounts(conn) -> None:
    print("\n── [2] ledger_books + chart_of_accounts follow the deployed convention ──")

    books = await conn.fetch(
        "SELECT id::text AS id, book_code, name, description FROM public.ledger_books "
        "WHERE org_id=$1::uuid AND system_to IS NULL ORDER BY book_code", ORG)
    by_code = {b["book_code"]: b for b in books}
    ok("[2a] both RIA_OPERATING and CLUB_DUES books exist",
       {"RIA_OPERATING", "CLUB_DUES"} <= set(by_code), str(sorted(by_code)))
    if {"RIA_OPERATING", "CLUB_DUES"} <= set(by_code):
        r, c = by_code["RIA_OPERATING"], by_code["CLUB_DUES"]
        ok("[2b] the two books are genuinely distinguishable",
           r["id"] != c["id"] and r["name"] != c["name"]
           and r["description"] != c["description"],
           f"{r['name']!r} vs {c['name']!r}")

    uq = await conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname='ledger_books_code_uq'")
    ok("[2c] book_code is unique per org on the live system axis",
       uq is not None and "system_to IS NULL" in uq, str(uq))

    # The convention, measured rather than assumed: 4-digit code banded by
    # account_type, flat (parent_code NULL on every row).
    accounts = await conn.fetch(
        "SELECT code, name, account_type, normal_balance, parent_code "
        "FROM public.chart_of_accounts WHERE org_id=$1::uuid AND system_to IS NULL "
        "ORDER BY code", ORG)
    by_code_a = {a["code"]: a for a in accounts}
    needed = {"1210": ("ASSET", "D"), "1220": ("ASSET", "D"),
              "4400": ("INCOME", "C"), "4500": ("INCOME", "C"),
              "4600": ("INCOME", "C"), "4700": ("INCOME", "C"),
              "5400": ("EXPENSE", "D"), "5500": ("EXPENSE", "D")}
    ok("[2d] the sprint's revenue/receivable/expense accounts all exist",
       set(needed) <= set(by_code_a),
       f"missing {sorted(set(needed) - set(by_code_a))}" if not set(needed) <= set(by_code_a)
       else f"{len(needed)} accounts")

    typed = all(by_code_a[c]["account_type"] == t
                and by_code_a[c]["normal_balance"].strip() == nb
                for c, (t, nb) in needed.items() if c in by_code_a)
    ok("[2e] each carries the account_type/normal_balance its band implies",
       typed, "1xxx ASSET/D, 4xxx INCOME/C, 5xxx EXPENSE/D")

    band = {"1": "ASSET", "2": "LIABILITY", "3": "EQUITY", "4": "INCOME",
            "5": "EXPENSE", "9": "MEMO"}
    banded = all(band.get(a["code"][0]) == a["account_type"] for a in accounts)
    ok("[2f] the new codes do not clash with the existing banding",
       banded and all(len(a["code"]) == 4 for a in accounts),
       f"{len(accounts)} accounts, every code 4 digits in its type's band")
    ok("[2g] the chart is flat, and the new rows match that",
       all(a["parent_code"] is None for a in accounts),
       "parent_code NULL on every row — no hierarchy to slot into")


async def test_3_fee_run_posting(conn) -> None:
    print("\n── [3] a MIXED fee run routes each product_type to its own book ──")
    from services.fee_runs import post_run

    result = await post_run(conn, ORG, RUN_MIX, posted_by=U_COMPLY)
    ok("[3a] the mixed run reached POSTED", result["status"] == "POSTED",
       str(result["status"]))

    entries = await conn.fetch(
        """SELECT je.id::text AS id, je.vehicle_kind, je.vehicle_id::text AS vehicle_id,
                  je.transaction_type_code, je.posted_at, lb.book_code
           FROM public.journal_entries je
           LEFT JOIN public.ledger_books lb ON lb.id = je.vehicle_id
           WHERE je.source_event_id = $1::uuid
           ORDER BY je.transaction_type_code""", RUN_MIX)
    ok("[3b] the ONE run produced TWO journal entries", len(entries) == 2,
       f"{len(entries)} entries: {[e['transaction_type_code'] for e in entries]}")

    by_txn = {e["transaction_type_code"]: e for e in entries}
    adv = by_txn.get("ADVISORY_FEE_REVENUE")
    club = by_txn.get("CLUB_DUES_REVENUE")

    ok("[3c] the ASSET_MANAGEMENT line booked to the RIA_OPERATING book",
       adv is not None and adv["vehicle_kind"] == "LEDGER_BOOK"
       and adv["book_code"] == "RIA_OPERATING",
       f"{adv['vehicle_kind']}/{adv['book_code']}" if adv else "no entry")
    ok("[3d] the CLUB_DUES line booked to the CLUB_DUES book",
       club is not None and club["vehicle_kind"] == "LEDGER_BOOK"
       and club["book_code"] == "CLUB_DUES",
       f"{club['vehicle_kind']}/{club['book_code']}" if club else "no entry")
    ok("[3e] both directions proven on the SAME run, into DIFFERENT books",
       adv is not None and club is not None
       and adv["vehicle_id"] != club["vehicle_id"],
       "one run, two ledger_books")

    if adv:
        lines = await entry_lines(conn, adv["id"])
        got = {l["code"]: (D(str(l["debit"])), D(str(l["credit"]))) for l in lines}
        ok("[3f] advisory entry debits 1210 Fees Receivable and credits 4400",
           got.get("1210") == (AMT_AM, D(0)) and got.get("4400") == (D(0), AMT_AM),
           str(got))
    if club:
        lines = await entry_lines(conn, club["id"])
        got = {l["code"]: (D(str(l["debit"])), D(str(l["credit"]))) for l in lines}
        ok("[3g] club entry debits 1220 Club Dues Receivable and credits 4600",
           got.get("1220") == (AMT_CD, D(0)) and got.get("4600") == (D(0), AMT_CD),
           str(got))

    ok("[3h] both entries are actually POSTED, not left as drafts",
       all(e["posted_at"] is not None for e in entries),
       f"{sum(1 for e in entries if e['posted_at'])} of {len(entries)} posted")

    bal = await conn.fetchval(
        """SELECT bool_and(d = c) FROM (
             SELECT sum(jl.debit) d, sum(jl.credit) c
             FROM public.journal_lines jl
             JOIN public.journal_entries je ON je.id = jl.entry_id
             WHERE je.source_event_id = $1::uuid GROUP BY je.id) s""", RUN_MIX)
    ok("[3i] every entry balances", bool(bal), "sum(debit) = sum(credit)")

    rev = await conn.fetchval(
        "SELECT count(*) FROM public.revenue_events WHERE source_id = ANY($1::uuid[])",
        [L_AM_A, L_CD_B])
    ok("[3j] fee39's revenue emission still ran, unchanged", rev == 2,
       f"{rev} revenue_events")


async def test_4_carry_posting(conn) -> None:
    print("\n── [4] a POSTED carry run books carry_to_gp ──")
    from services.spv_carry_runs import post_run as post_carry

    result = await post_carry(conn, ORG, CRUN, posted_by=U_COMPLY)
    ok("[4a] the carry run reached POSTED", result["status"] == "POSTED",
       str(result["status"]))

    entries = await conn.fetch(
        "SELECT id::text AS id, vehicle_kind, vehicle_id::text AS vehicle_id, "
        "transaction_type_code, posted_at FROM public.journal_entries "
        "WHERE source_event_id = $1::uuid", CRUN)
    ok("[4b] it produced exactly one journal entry", len(entries) == 1,
       f"{len(entries)} entries")

    if entries:
        e = entries[0]
        ok("[4c] booked inside the SPV's OWN book, not a ledger_book",
           e["vehicle_kind"] == "SPV" and e["vehicle_id"] == SPV,
           f"{e['vehicle_kind']} vehicle_id={e['vehicle_id']}")
        ok("[4d] through the CARRY_ALLOCATION template",
           e["transaction_type_code"] == "CARRY_ALLOCATION",
           str(e["transaction_type_code"]))
        lines = await entry_lines(conn, e["id"])
        got = {l["code"]: (D(str(l["debit"])), D(str(l["credit"]))) for l in lines}
        ok("[4e] debits 5500 Carried Interest and credits 2100 Due to Affiliate",
           got.get("5500") == (CARRY_GP, D(0)) and got.get("2100") == (D(0), CARRY_GP),
           str(got))
        ok("[4f] the entry is POSTED", e["posted_at"] is not None, str(e["posted_at"]))

    # The GP-entity question, measured rather than asserted from the prompt.
    gp_cols = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='spvs' "
        "AND (column_name ILIKE '%gp%' OR column_name ILIKE '%manager%' "
        "     OR column_name ILIKE '%sponsor%')")
    gp_rows = await conn.fetchval(
        "SELECT count(*) FROM public.entities WHERE entity_type='gp'")
    ok("[4g] spvs carries no GP/manager/sponsor reference column",
       len(gp_cols) == 0, f"{[c['column_name'] for c in gp_cols]}")
    find("[4h] GP entity — the honest answer",
         f"entity_type has a 'gp' enum value but {gp_rows} entities rows use it, "
         f"and spvs has no column pointing at one. Carry therefore books inside "
         f"the SPV's own book as an expense credited to 2100 Due to Affiliate, "
         f"not as an equity allocation to a GP capital account. A GP legal-entity "
         f"model is a REAL gap, deferred — it was not invented here")


async def test_5_atomicity(conn) -> None:
    print("\n── [5] a GL failure leaves the run un-POSTED ──")
    from services.fee_runs import post_run

    before = await conn.fetchrow(
        "SELECT status, posted_at FROM public.fee_runs WHERE id=$1::uuid", RUN_FAIL)
    ok("[5a] the run starts COMPLIANCE_APPROVED",
       before["status"] == "COMPLIANCE_APPROVED", str(before["status"]))

    tmpl_id = await conn.fetchval(
        "SELECT id FROM public.posting_templates WHERE org_id=$1::uuid "
        "AND transaction_type_code='CLUB_DUES_REVENUE'", ORG)
    raised = None
    try:
        # A REAL failure mode, not a simulated one: the template the run's only
        # line needs is deactivated, so the GL genuinely cannot resolve it.
        await conn.execute(
            "UPDATE public.posting_templates SET is_active=false WHERE id=$1", tmpl_id)
        try:
            await post_run(conn, ORG, RUN_FAIL, posted_by=U_COMPLY)
        except Exception as exc:  # noqa: BLE001
            raised = exc
    finally:
        await conn.execute(
            "UPDATE public.posting_templates SET is_active=true WHERE id=$1", tmpl_id)

    ok("[5b] posting raised rather than silently skipping the GL",
       raised is not None, type(raised).__name__ if raised else "no exception")

    after = await conn.fetchrow(
        "SELECT status, posted_at FROM public.fee_runs WHERE id=$1::uuid", RUN_FAIL)
    ok("[5c] the run is STILL COMPLIANCE_APPROVED — the status change rolled back",
       after["status"] == "COMPLIANCE_APPROVED", str(after["status"]))
    ok("[5d] posted_at was not stamped", after["posted_at"] is None,
       str(after["posted_at"]))

    je = await conn.fetchval(
        "SELECT count(*) FROM public.journal_entries WHERE source_event_id=$1::uuid",
        RUN_FAIL)
    ok("[5e] no journal entry survives the failed posting", je == 0, f"{je} entries")

    # The check that proves the rollback covered the WHOLE transaction: fee39's
    # revenue emission ran before the GL step and must have unwound too.
    rev = await conn.fetchval(
        "SELECT count(*) FROM public.revenue_events WHERE source_id=$1::uuid", L_FAIL)
    ok("[5f] fee39's revenue_events rolled back too, not just the status",
       rev == 0, f"{rev} revenue_events")

    restored = await conn.fetchval(
        "SELECT is_active FROM public.posting_templates WHERE id=$1", tmpl_id)
    ok("[5g] the posting template was restored", restored is True, str(restored))


async def test_6_disclosure_is_fee41(conn) -> None:
    print("\n── [6] the invoice disclosure IS fee41's output ──")
    from services.fee_invoices import (InvoiceError, generate_invoices_for_run)
    from services.fee_narratives import render_narrative

    result = await generate_invoices_for_run(
        conn, ORG, RUN_MIX, created_by=U_CREATE, template_code=TEMPLATE_CODE)
    ok("[6a] one invoice per household on the POSTED run",
       result["invoice_count"] == 2, f"{result['invoice_count']} invoices")

    by_hh = {i["household_id"]: i for i in result["invoices"]}
    ok("[6b] invoice totals equal the household's billed lines",
       by_hh.get(HH_A, {}).get("total_amount") == AMT_AM
       and by_hh.get(HH_B, {}).get("total_amount") == AMT_CD,
       f"A={by_hh.get(HH_A, {}).get('total_amount')} "
       f"B={by_hh.get(HH_B, {}).get('total_amount')}")

    # The proof is a comparison of OUTPUT against an independent render.
    independent = await render_narrative(
        conn, ORG, fee_schedule_id=FS_AM, household_id=HH_A,
        template_code=TEMPLATE_CODE)
    invoice_text = by_hh.get(HH_A, {}).get("disclosure_text")
    ok("[6c] the invoice's disclosure text is byte-identical to fee41's render",
       invoice_text == independent.rendered_text,
       f"{invoice_text!r:.90}")
    ok("[6d] and it is real rendered prose, not an empty or token-laden string",
       bool(invoice_text) and "{{" not in invoice_text
       and TAG in invoice_text,
       f"{len(invoice_text or '')} chars")

    saved = await conn.fetchval(
        "SELECT count(*) FROM public.fee_narratives WHERE template_id=$1::uuid "
        "AND rendered_text=$2", TMPL, invoice_text)
    ok("[6e] the render was persisted where fee41 persists renders",
       saved >= 1, f"{saved} fee_narratives row(s)")

    # The negative: no second generator, no fallback string. With the template
    # gone there is nothing to fall back TO, and generation must raise.
    body = await conn.fetchval(
        "SELECT body_template FROM public.fee_narrative_templates WHERE id=$1::uuid",
        TMPL)
    await conn.execute(
        "UPDATE public.fee_narrative_templates SET system_to=now() WHERE id=$1::uuid",
        TMPL)
    raised = None
    try:
        await generate_invoices_for_run(
            conn, ORG, RUN_MIX, created_by=U_CREATE, template_code=TEMPLATE_CODE)
    except Exception as exc:  # noqa: BLE001
        raised = exc
    finally:
        await conn.execute(
            "UPDATE public.fee_narrative_templates SET system_to=NULL "
            "WHERE id=$1::uuid", TMPL)
    ok("[6f] with fee41's template gone, invoicing RAISES — no fallback prose",
       isinstance(raised, InvoiceError),
       f"{type(raised).__name__}" if raised else "no exception")
    ok("[6g] the template body was restored",
       await conn.fetchval("SELECT body_template FROM "
                           "public.fee_narrative_templates WHERE id=$1::uuid",
                           TMPL) == body, "unchanged")


async def test_7_reconciliation(conn) -> None:
    print("\n── [7] reconciliation: a statement that does not tie never posts ──")
    from services.fee_invoices import (ReconciliationError, close_exception,
                                       list_exceptions,
                                       reconcile_omnibus_statement)
    from datetime import date

    # Direction one: the statement ties. Same billed amounts, correct total.
    tie = await reconcile_omnibus_statement(
        conn, ORG, run_id=RUN_MIX, document_id=DOC_TIE,
        received_on=date(2026, 7, 15))
    ok("[7a] a tying statement is recognised as tying", tie["ties"] is True,
       f"allocated={tie['allocated_total']} stated={tie['stated_total']}")
    ok("[7b] its receipts are MATCHED, not blanket-EXCEPTION",
       tie["matched_count"] == 2 and tie["exception_count"] == 0,
       f"{tie['matched_count']} matched / {tie['exception_count']} exception")

    await conn.execute(
        "DELETE FROM public.fee_receipts WHERE fee_run_line_id = ANY($1::uuid[])",
        [L_AM_A, L_CD_B])

    # Direction two: identical allocations, only the stated total is wrong.
    notie = await reconcile_omnibus_statement(
        conn, ORG, run_id=RUN_MIX, document_id=DOC_NOTIE,
        received_on=date(2026, 7, 15))
    ok("[7c] a non-tying statement is caught", notie["ties"] is False,
       f"allocated={notie['allocated_total']} stated={notie['stated_total']}")
    ok("[7d] a real, named reason is recorded",
       bool(notie["tie_break_reason"]), str(notie["tie_break_reason"])[:110])

    variances = [D(str(r["variance"])) for r in notie["receipts"]]
    ok("[7e] every per-line variance is zero — the lines each look perfect",
       all(v == 0 for v in variances), str(variances))
    ok("[7f] and yet EVERY receipt is EXCEPTION — nothing silently posts",
       notie["exception_count"] == len(notie["receipts"])
       and notie["matched_count"] == 0,
       f"{notie['exception_count']} exception / {notie['matched_count']} matched")

    queue = await list_exceptions(conn, ORG, run_id=RUN_MIX)
    ok("[7g] the exception queue surfaces them as open work",
       len(queue) == len(notie["receipts"]), f"{len(queue)} open exceptions")

    receipt_id = queue[0]["id"] if queue else None

    raised = None
    try:
        await close_exception(conn, ORG, receipt_id, reviewed_by="",
                              resolution="no reviewer")
    except Exception as exc:  # noqa: BLE001
        raised = exc
    ok("[7h] closing without a reviewer is refused",
       isinstance(raised, ReconciliationError),
       type(raised).__name__ if raised else "no exception")

    # The database, not just the service, refuses a half-review.
    db_raised = None
    try:
        await conn.execute(
            "UPDATE public.fee_receipts SET reviewed_by=$2::uuid WHERE id=$1::uuid",
            receipt_id, U_REVIEW)
    except Exception as exc:  # noqa: BLE001
        db_raised = exc
    ok("[7i] a direct UPDATE setting only reviewed_by is refused by the CHECK",
       db_raised is not None,
       type(db_raised).__name__ if db_raised else "ACCEPTED — pair not enforced")

    closed = await close_exception(
        conn, ORG, receipt_id, reviewed_by=U_REVIEW,
        resolution="custodian re-issued the statement with the correct total")
    ok("[7j] closing records BOTH reviewed_by and reviewed_at",
       closed["reviewed_by"] == U_REVIEW and closed["reviewed_at"] is not None,
       f"{closed['reviewed_by']} at {closed['reviewed_at']}")

    still_open = await list_exceptions(conn, ORG, run_id=RUN_MIX)
    ok("[7k] the closed exception leaves the open queue",
       len(still_open) == len(queue) - 1,
       f"{len(still_open)} open, was {len(queue)}")


async def test_8_cross_org(app_conn) -> None:
    print("\n── [8] cross-org isolation under app_service ──")

    bypass = await app_conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")
    if not ok("[8a] the test role does NOT bypass RLS", bypass is False,
              f"rolbypassrls={bypass}"):
        return

    async def visible(org: str, sql: str, *args) -> int:
        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.current_org_id', $1, true)", org)
            await app_conn.execute(
                "SELECT set_config('app.is_super_admin', 'false', true)")
            return await app_conn.fetchval(sql, *args)

    probes = [
        ("ledger_books", "SELECT count(*) FROM public.ledger_books WHERE id=$1::uuid",
         BOOK_XORG),
        ("fee_invoices", "SELECT count(*) FROM public.fee_invoices WHERE id=$1::uuid",
         INV_XORG),
        ("fee_receipts", "SELECT count(*) FROM public.fee_receipts WHERE id=$1::uuid",
         RCPT_XORG),
        ("journal_entries",
         "SELECT count(*) FROM public.journal_entries WHERE id=$1::uuid", JE_XORG),
    ]
    for name, sql, arg in probes:
        as_ours = await visible(ORG, sql, arg)
        as_theirs = await visible(OTHER_ORG, sql, arg)
        ok(f"[8b] {name}: the other org's row is invisible to us, visible to them",
           as_ours == 0 and as_theirs == 1,
           f"as ORG={as_ours}, as OTHER_ORG={as_theirs}")

    # journal_lines has no org_id of its own — its policy walks the parent entry.
    ours = await visible(
        ORG, "SELECT count(*) FROM public.journal_lines jl "
             "JOIN public.journal_entries je ON je.id=jl.entry_id "
             "WHERE je.source_event_id=$1::uuid", RUN_MIX)
    theirs = await visible(
        OTHER_ORG, "SELECT count(*) FROM public.journal_lines jl "
                   "JOIN public.journal_entries je ON je.id=jl.entry_id "
                   "WHERE je.source_event_id=$1::uuid", RUN_MIX)
    ok("[8c] journal_lines inherits isolation through its parent entry",
       ours == 4 and theirs == 0, f"as ORG={ours}, as OTHER_ORG={theirs}")

    blocked = None
    try:
        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.current_org_id', $1, true)", ORG)
            await app_conn.execute(
                "SELECT set_config('app.is_super_admin', 'false', true)")
            await app_conn.execute(
                """INSERT INTO public.ledger_books (org_id, book_code, name)
                   VALUES ($1::uuid, $2, $3)""",
                OTHER_ORG, f"{TAG}_LEAK", f"{TAG} leak attempt")
    except Exception as exc:  # noqa: BLE001
        blocked = exc
    ok("[8d] writing a ledger_book into another org is refused by WITH CHECK",
       blocked is not None,
       type(blocked).__name__ if blocked else "ACCEPTED — isolation broken")


async def test_9_hygiene(conn, before: dict, before_img: dict) -> None:
    print("\n── [9] triggers restored + the pre-existing entry byte-identical ──")

    states = await conn.fetch(
        """SELECT c.relname||'.'||t.tgname AS name, t.tgenabled
           FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
           WHERE NOT t.tgisinternal AND t.tgname = ANY($1::text[])""",
        [t for _, t in TRIGGER_NAMES])
    disabled = [s["name"] for s in states if s["tgenabled"] == "D"]
    ok("[9a] every immutability trigger is back ON", not disabled,
       f"disabled: {disabled}" if disabled else f"{len(states)} triggers enabled")

    after_img = await je_image(conn)
    pre_ids = [k for k in before_img if k != JE_XORG]
    identical = all(k in after_img and after_img[k] == before_img[k] for k in pre_ids)
    ok("[9b] every pre-existing journal entry is BYTE-IDENTICAL, lines included",
       identical,
       f"{len(pre_ids)} pre-existing entr(y/ies) compared field-for-field")

    after = await counts(conn)
    drift = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
    ok("[9c] no table's row count changed", not drift, str(drift) if drift else "clean")


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    admin, admin_prov = await admin_dsn()
    app, app_prov = await app_service_dsn()
    if not admin:
        print(f"[FAIL] no working admin DSN: {admin_prov}")
        return 1
    if not app:
        print(f"[FAIL] no working app_service DSN: {app_prov}")
        return 1
    print(f"admin via {admin_prov}\napp_service via {app_prov}")

    conn = await connect(admin)
    app_conn = await connect(app)
    try:
        await teardown(conn)  # a previous crashed run must not poison the counts
        before = await counts(conn)
        before_img = await je_image(conn)

        await setup(conn)
        try:
            await test_1_objects_and_untouched(conn, before_img)
            await test_2_books_and_accounts(conn)
            await test_3_fee_run_posting(conn)
            await test_4_carry_posting(conn)
            await test_5_atomicity(conn)
            await test_6_disclosure_is_fee41(conn)
            await test_7_reconciliation(conn)
            await test_8_cross_org(app_conn)
        except Exception:  # noqa: BLE001
            FAIL.append(f"unhandled: {traceback.format_exc()}")
            print(f"[FAIL] unhandled exception\n{traceback.format_exc()}")
        finally:
            await teardown(conn)

        await test_9_hygiene(conn, before, before_img)
    finally:
        await conn.close()
        await app_conn.close()

    print(f"\n{'=' * 70}\nfee43: {len(PASS)} PASS, {len(FAIL)} FAIL, {len(FIND)} FIND")
    for f in FAIL:
        print(f"  FAIL {f}")
    for f in FIND:
        print(f"  FIND {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
