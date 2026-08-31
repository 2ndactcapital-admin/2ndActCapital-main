"""Sprint fee40 verification — the fee chat interface.

Pass/fail only, no prompts. Run:

    python3 scripts/verify_fee40.py

Every table this script writes to is counted before the first insert and again
after the last delete; a difference of even one row fails the run, reported
AFTER the tests so a teardown bug never masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[2] does not rely on the model actually behaving.** "The model was not
  permitted to silently fill in a valuation method" is proved twice: once
  against a REAL model call on a description that never states one, and once —
  the load-bearing half — against a HAND-WRITTEN response that guesses
  PERIOD_END and fabricates a citation for it. The second proof holds on every
  model version and on a day the API is down. A test that only ran the first
  would be measuring this month's model, not this sprint's guard.

* **[3] simulates the malformed response rather than waiting for one.**
  ``propose_fee_spec`` takes a ``transport`` injection seam, so prose, empty
  output, a JSON array and a total absence of any response are each driven
  deliberately and each asserted to raise its OWN typed error. Relying on a real
  model to misbehave would make the check unrunnable on demand.

* **[4] proves the number is fee35's by IDENTITY, not by equality.**
  ``calculate_account_fee`` is wrapped for one call and the ``FeeCalcResult`` it
  returned is captured; the worked example's ``amount`` must be *the same
  Decimal object*. Equality would also pass for a separate computation that
  happens to agree today. The same figure is ALSO reached by the ordinary
  saved-schedule path through ``load_account_calc_request``, and hand-computed
  as a literal away from the code, so three independent routes must agree.

* **[5] compares fee34's error objects field by field**, not their rendered
  text. A paraphrase with the right ``code`` would pass a message check; a
  message match with a drifted ``field`` would fail the form. Both are asserted,
  against ``validate_schedule`` called directly on the same rows.

* **[8] runs the isolation checks on app_service, whose ``rolbypassrls`` is
  asserted False FIRST.** Without that assertion every isolation check below it
  is vacuous. Isolation is proved in BOTH directions: a name that exists only in
  the other org must not resolve, and a name that exists in BOTH must resolve to
  THIS org's row — a resolver that returned nothing at all would pass a
  one-directional check.

* **[8e] reproduces the leak the migration closed.** The pre-migration RLS
  predicate (``target_type <> 'document'``) is evaluated against the fixture row
  to show it WOULD have matched, before showing that the deployed predicate does
  not. A test that only checked the fixed policy would prove the fix works
  without ever showing there was anything to fix.

* Teardown is by fixture id and fixture tag, in FK order, never a TRUNCATE.
  ``ai_decision_log`` rows are reaped by this sprint's own ``task_type``, which
  the real model call writes — production rows for other tasks are untouched.
"""

from __future__ import annotations

import glob
import json
import os
import pathlib
import subprocess
import sys
import traceback
from datetime import date
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

# LiteLLM's Phase A deployment has ZERO models registered, so the intended
# Phase-B transport answers every request with "Invalid model name". The
# documented ops rollback is the only path that reaches a model today. Set
# BEFORE services.extraction is imported so the transport resolves once, and
# reported as a FIND rather than hidden — sprint code never sets this.
os.environ.setdefault("LITELLM_ROUTING_DISABLED", "1")

from services import fee_calc as FC  # noqa: E402
from services import fee_spec as FS  # noqa: E402
from services import fee_spec_corrections as FSC  # noqa: E402
from services import fee_worked_example as FWE  # noqa: E402
from services.fee_run_inputs import load_account_calc_request  # noqa: E402
from services.fee_spec_diff import build_diff  # noqa: E402
from services.fee_spec_resolver import (  # noqa: E402
    resolve_reference,
    resolve_spec_references,
)
from services.fee_validation import validate_schedule  # noqa: E402

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "fee40verify"

U_ADVISOR = "99000000-0000-0000-0000-0000fee40001"
USERS = [U_ADVISOR]

HH_MAIN = "99000000-0000-0000-0000-0000fee40011"
OTHER_HH = "99000000-0000-0000-0000-0000fee40012"

ACC_MAIN = "99000000-0000-0000-0000-0000fee40021"
OTHER_ACC = "99000000-0000-0000-0000-0000fee40022"

ENT_MAIN = "99000000-0000-0000-0000-0000fee40031"      # unique to ORG
ENT_SHARED_A = "99000000-0000-0000-0000-0000fee40032"  # same NAME in both orgs
ENT_SHARED_B = "99000000-0000-0000-0000-0000fee40033"
ENT_OTHER_ONLY = "99000000-0000-0000-0000-0000fee40034"  # exists ONLY in OTHER_ORG
ORG_ENTITIES = [ENT_MAIN, ENT_SHARED_A]
OTHER_ENTITIES = [ENT_SHARED_B, ENT_OTHER_ONLY]

SCH_SAVED = "99000000-0000-0000-0000-0000fee40041"
CONV_ID = "99000000-0000-0000-0000-0000fee40051"

#: Names carrying the tag so teardown can find them and no production row can
#: collide with them.
NAME_UNIQUE = f"{TAG} Marchetti Family Trust"
NAME_SHARED = f"{TAG} Ambiguous Holdings"
NAME_OTHER_ONLY = f"{TAG} Offshore Only Trust"

PERIOD_START = date(2026, 4, 1)
PERIOD_END = date(2026, 6, 30)

#: $2,000,000 through a 100bps / 75bps graduated ladder, quarterly, no proration.
#:   first  1,000,000 @ 100bps = 10,000.00
#:   next   1,000,000 @  75bps =  7,500.00
#:                      annual = 17,500.00  ->  / 4 quarters = 4,375.00
#: Computed here by a person, never by calling the thing under test.
BALANCE = D("2000000.00")
EXPECTED_FEE = D("4375.00")

#: The description [1] sends to a real model. Every field it must extract is
#: stated in words a person would use, and the numbers are unambiguous.
GOOD_DESCRIPTION = (
    f"Set up a fee schedule for the {NAME_UNIQUE}. Charge 100 basis points on "
    f"the first 1,000,000 of assets and 75 basis points on everything above "
    f"that, graduated so each tier only applies to the money in its own band. "
    f"Bill it quarterly in arrears, valued on the period-end market value. "
    f"There is a minimum fee of 2,500 per household."
)

#: [2]'s description. Deliberately silent on the valuation method — and on
#: everything else that would let a model infer one.
AMBIGUOUS_DESCRIPTION = (
    "Charge the Kessler family 85 basis points on everything, billed quarterly "
    "in arrears."
)

COUNTED = (
    "public.document_field_corrections",
    "public.assistant_conversations",
    "public.ai_decision_log",
    "public.fee_schedule_tiers",
    "public.fee_schedules",
    "public.fee_assignments",
    "public.account_balances_daily",
    "public.accounts",
    "public.households",
    "public.entities",
    "public.users",
)


# ═══════════════════════════════════════════════════════════════════════════
# Harness
# ═══════════════════════════════════════════════════════════════════════════


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def ok(self, ref, msg):
        self.rows.append(("PASS", ref, msg))
        print(f"[PASS] {ref}  {msg}")

    def bad(self, ref, msg, detail=""):
        self.rows.append(("FAIL", ref, f"{msg} — {detail}" if detail else msg))
        print(f"[FAIL] {ref}  {msg}" + (f"\n         {detail}" if detail else ""))

    def find(self, ref, msg):
        self.rows.append(("FIND", ref, msg))
        print(f"[FIND] {ref}  {msg}")

    def blocked(self, ref, msg):
        self.rows.append(("BLOCKED", ref, msg))
        print(f"[BLOCKED] {ref}  {msg}")

    def expect(self, ref, condition, msg, detail=""):
        if condition:
            self.ok(ref, msg)
        else:
            self.bad(ref, msg, detail)
        return bool(condition)

    @property
    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]

    def summary(self):
        counts: dict[str, int] = {}
        for kind, _, _ in self.rows:
            counts[kind] = counts.get(kind, 0) + 1
        total = len(self.rows)
        print("\n" + "=" * 78)
        print(f"fee40: {counts.get('PASS', 0)}/{total} PASS" + "".join(
            f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def teardown(conn) -> None:
    """By fixture id and fixture tag, in FK order. Never a TRUNCATE.

    ``ai_decision_log`` is reaped by this sprint's own ``task_type`` AND by the
    probe task_type below it. Production rows for other tasks — 277 of them at
    sprint start — are never in range of either predicate.
    """
    await conn.execute(
        "DELETE FROM public.document_field_corrections "
        "WHERE target_type = $1 AND target_id = ANY($2::uuid[])",
        FSC.TARGET_TYPE, [CONV_ID])
    await conn.execute(
        "DELETE FROM public.document_field_corrections "
        "WHERE target_type = $1 AND org_id = ANY($2::uuid[])",
        FSC.TARGET_TYPE, [ORG, OTHER_ORG])
    await conn.execute(
        "DELETE FROM public.assistant_conversations WHERE id = ANY($1::uuid[])", [CONV_ID])
    await conn.execute(
        "DELETE FROM public.assistant_conversations WHERE user_id = ANY($1::uuid[])", USERS)
    await conn.execute(
        "DELETE FROM public.ai_decision_log WHERE task_type = ANY($1::text[])",
        [FS.TASK_TYPE, f"{TAG}_probe"])
    await conn.execute(
        "DELETE FROM public.fee_assignments WHERE fee_schedule_id = ANY($1::uuid[])",
        [SCH_SAVED])
    await conn.execute(
        "DELETE FROM public.fee_schedule_tiers WHERE fee_schedule_id = ANY($1::uuid[])",
        [SCH_SAVED])
    await conn.execute(
        "DELETE FROM public.fee_schedules WHERE id = ANY($1::uuid[]) OR "
        "(org_id = ANY($2::uuid[]) AND code LIKE $3)",
        [SCH_SAVED], [ORG, OTHER_ORG], f"%{TAG.upper()}%")
    await conn.execute(
        "DELETE FROM public.account_balances_daily WHERE account_id = ANY($1::uuid[])",
        [ACC_MAIN, OTHER_ACC])
    await conn.execute(
        "DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", [ACC_MAIN, OTHER_ACC])
    await conn.execute(
        "DELETE FROM public.households WHERE id = ANY($1::uuid[])", [HH_MAIN, OTHER_HH])
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])",
        ORG_ENTITIES + OTHER_ENTITIES)
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def build_fixtures(conn) -> None:
    await conn.execute(
        """INSERT INTO public.users (id, org_id, email, auth0_sub)
           VALUES ($1::uuid,$2::uuid,$3,$4)""",
        U_ADVISOR, ORG, f"advisor@{TAG}.local", f"auth0|{TAG}-advisor")

    # ORG entities. ENT_SHARED_A has the SAME NAME as ENT_SHARED_B in the other
    # org — the pair that makes [8b] a real test rather than a lookup that
    # happens to find nothing.
    for eid, name in ((ENT_MAIN, NAME_UNIQUE), (ENT_SHARED_A, NAME_SHARED)):
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
               VALUES ($1::uuid,$2::uuid,'individual',$3)""",
            eid, ORG, name)
    for eid, name in ((ENT_SHARED_B, NAME_SHARED), (ENT_OTHER_ONLY, NAME_OTHER_ONLY)):
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
               VALUES ($1::uuid,$2::uuid,'individual',$3)""",
            eid, OTHER_ORG, name)

    await conn.execute(
        "INSERT INTO public.households (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
        HH_MAIN, ORG, f"{TAG} Marchetti household")
    await conn.execute(
        "INSERT INTO public.households (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
        OTHER_HH, OTHER_ORG, f"{TAG} other-org household")

    for aid, org, ent, hh in ((ACC_MAIN, ORG, ENT_MAIN, HH_MAIN),
                              (OTHER_ACC, OTHER_ORG, ENT_OTHER_ONLY, OTHER_HH)):
        await conn.execute(
            """INSERT INTO public.accounts
                 (id, org_id, account_number_masked, account_number_hash, custodian_code,
                  registration_type, tax_status, primary_entity_id, household_id,
                  is_billable, opened_on)
               VALUES ($1::uuid,$2::uuid,$3,$4,'TEST','individual','taxable',
                       $5::uuid,$6::uuid,true,'2024-01-01')""",
            aid, org, f"***{aid[-3:]}", f"{TAG}-{aid[-3:]}", ent, hh)

    # The balance the worked example is computed from. PERIOD_END valuation
    # reads the last balance in the period, so it is dated the period's end.
    await conn.execute(
        """INSERT INTO public.account_balances_daily
             (org_id, account_id, as_of_date, total_market_value, cash_value,
              source_system, is_billing_source, is_final)
           VALUES ($1::uuid,$2::uuid,$3::date,$4::numeric,0,'PRIMARY',true,true)""",
        ORG, ACC_MAIN, PERIOD_END, BALANCE)

    # The SAVED twin of the proposed schedule — byte-identical in every field
    # fee35 reads, so [4b] compares the override path against the ordinary
    # database path rather than against a differently-shaped schedule.
    await conn.execute(
        """INSERT INTO public.fee_schedules
             (id, org_id, code, name, product_type, rate_type, tier_method,
              billing_frequency, billing_timing, valuation_method, proration_method,
              status, day_weight_flows, minimum_fee, minimum_fee_scope)
           VALUES ($1::uuid,$2::uuid,$3,$4,'ASSET_MANAGEMENT','BPS','GRADUATED',
                   'QUARTERLY','ARREARS','PERIOD_END','NONE','APPROVED',false,
                   NULL,NULL)""",
        SCH_SAVED, ORG, f"{TAG.upper()}-SAVED", f"{TAG} saved twin")
    for seq, lo, hi, bps in ((1, 0, 1000000, 100), (2, 1000000, None, 75)):
        await conn.execute(
            """INSERT INTO public.fee_schedule_tiers
                 (org_id, fee_schedule_id, tier_seq, lower_bound, upper_bound, rate_bps)
               VALUES ($1::uuid,$2::uuid,$3,$4::numeric,$5::numeric,$6::numeric)""",
            ORG, SCH_SAVED, seq, lo, hi, bps)

    await conn.execute(
        """INSERT INTO public.assistant_conversations
             (id, org_id, user_id, context_ref, messages, status, title)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::jsonb,'[]'::jsonb,'active',$5)""",
        CONV_ID, ORG, U_ADVISOR,
        json.dumps({"type": "fee_schedule_spec", "id": None}),
        f"{TAG} draft")


# ═══════════════════════════════════════════════════════════════════════════
# Hand-written model responses — the deterministic half of every model check
# ═══════════════════════════════════════════════════════════════════════════


def make_transport(payload):
    """A stand-in for ``call_claude_text`` returning exactly ``payload``.

    Not a mock library: the seam is a plain callable, so what is injected here
    has the same shape as the real function and cannot drift from it silently.
    """
    async def transport(system, messages, max_tokens, *, org_id=None, task_type=None):
        return payload
    return transport


#: A model that GUESSES a valuation method and invents a citation for it. The
#: citation is fluent, plausible, and absent from the description — which is
#: exactly the failure the grounding check exists to catch.
GUESSING_RESPONSE = json.dumps({
    "schedule": {
        "code": "KESSLER_2026", "name": "Kessler Family",
        "product_type": "ASSET_MANAGEMENT", "rate_type": "BPS",
        "billing_frequency": "QUARTERLY", "billing_timing": "ARREARS",
        "valuation_method": "PERIOD_END",
    },
    "tiers": [{"tier_seq": 1, "lower_bound": "0", "upper_bound": None, "rate_bps": "85"}],
    "evidence": {
        "billing_frequency": "billed quarterly in arrears",
        "billing_timing": "billed quarterly in arrears",
        "valuation_method": "valued at the end of each billing period",
    },
})

#: The valid fixture [1] falls back to when no model is reachable, and the
#: schedule [4] prices. Every grounded field cites GOOD_DESCRIPTION verbatim.
VALID_RESPONSE = json.dumps({
    "schedule": {
        "code": f"{TAG.upper()}-PROPOSED", "name": "Marchetti Family Trust",
        "product_type": "ASSET_MANAGEMENT", "rate_type": "BPS",
        "tier_method": "GRADUATED", "billing_frequency": "QUARTERLY",
        "billing_timing": "ARREARS", "valuation_method": "PERIOD_END",
        "proration_method": "NONE", "day_weight_flows": False,
        "currency": "USD",
    },
    "tiers": [
        {"tier_seq": 1, "lower_bound": "0", "upper_bound": "1000000", "rate_bps": "100"},
        {"tier_seq": 2, "lower_bound": "1000000", "upper_bound": None, "rate_bps": "75"},
    ],
    "references": [{"ref": "r1", "kind": "ENTITY", "name": NAME_UNIQUE}],
    "evidence": {
        "billing_frequency": "Bill it quarterly in arrears",
        "billing_timing": "Bill it quarterly in arrears",
        "valuation_method": "valued on the period-end market value",
        "tier_method": "graduated so each tier only applies to the money in its own band",
        "proration_method": "quarterly in arrears",
    },
})


def valid_spec() -> FS.NormalisedSpec:
    parsed, _ = FS.parse_fee_spec(VALID_RESPONSE)
    return FS.normalise_fee_spec(parsed, GOOD_DESCRIPTION)


# ═══════════════════════════════════════════════════════════════════════════
# [1] a well-formed description produces a spec fee34's REAL validator passes
# ═══════════════════════════════════════════════════════════════════════════


async def check_1(conn):
    spec = valid_spec()

    errors = validate_schedule({**spec.schedule, "status": "DRAFT"}, spec.tiers)
    R.expect("1a", not errors,
             "the fixture spec passes fee34's REAL validate_schedule",
             str([e.as_dict() for e in errors]))

    tiers = sorted(spec.tiers, key=lambda t: t["tier_seq"])
    matches = (
        len(tiers) == 2
        and tiers[0]["lower_bound"] == D("0")
        and tiers[0]["upper_bound"] == D("1000000")
        and tiers[0]["rate_bps"] == D("100")
        and tiers[1]["lower_bound"] == D("1000000")
        and tiers[1].get("upper_bound") is None
        and tiers[1]["rate_bps"] == D("75")
    )
    R.expect("1b", matches,
             "the declared tiers/rates match what the description said "
             "(100bps to 1,000,000 then 75bps, open-ended)", str(tiers))

    R.expect("1c",
             all(isinstance(t[k], Decimal)
                 for t in tiers for k in ("lower_bound", "rate_bps")),
             "every tier bound and rate is a Decimal — no float reached fee34")

    # The REAL model, on the real path. BLOCKED (not FAIL) when no model
    # answers: an unreachable API is a deployment fact, not a defect in this
    # sprint's code, and the deterministic proofs above already ran.
    try:
        live, raw = await FS.propose_fee_spec(GOOD_DESCRIPTION, org_id=ORG)
    except FS.FeeSpecError as exc:
        R.blocked("1d", f"no live model answered ({type(exc).__name__}: {exc}); "
                        f"[1a-c] proved the pipeline deterministically")
        return spec, None

    live_errors = validate_schedule({**live.schedule, "status": "DRAFT"}, live.tiers)
    R.expect("1d", not live_errors,
             "a REAL model call on a well-formed description produced a spec "
             "fee34's validator passes",
             str([e.as_dict() for e in live_errors]) + f" spec={live.schedule}")

    live_tiers = sorted(live.tiers, key=lambda t: (t.get("tier_seq") or 0))
    rates = [t.get("rate_bps") for t in live_tiers]
    R.expect("1e", rates == [D("100"), D("75")],
             "the live model's tiers carry the rates the description stated",
             f"got {rates}")
    R.expect("1f", not any(isinstance(v, float) for v in live.schedule.values()),
             "no float survived anywhere in the live model's schedule")
    return spec, raw


# ═══════════════════════════════════════════════════════════════════════════
# [2] an ambiguous description yields `unresolved`, never a guessed default
# ═══════════════════════════════════════════════════════════════════════════


async def check_2(conn):
    # The load-bearing proof: a model that DOES guess, refused deterministically.
    parsed, _ = FS.parse_fee_spec(GUESSING_RESPONSE)
    spec = FS.normalise_fee_spec(parsed, AMBIGUOUS_DESCRIPTION)

    R.expect("2a", "valuation_method" not in spec.schedule,
             "a model that guessed PERIOD_END had the value DISCARDED — it is "
             "not in the resolved schedule at all",
             str(spec.schedule.get("valuation_method")))
    R.expect("2b", "valuation_method" in spec.unresolved_fields,
             "valuation_method is reported as unresolved instead",
             str(spec.unresolved))
    discarded = {d["field"]: d for d in spec.discarded}
    R.expect("2c",
             "valuation_method" in discarded
             and "does not appear in the description" in discarded["valuation_method"]["reason"],
             "the refusal names the real reason — the cited evidence is not in "
             "the advisor's text", str(spec.discarded))
    R.expect("2d", not spec.is_priceable,
             "the spec is not priceable, so no worked example can be produced "
             "from a guessed valuation method")

    # A grounded field in the SAME response survived, so [2a] is a refusal of
    # the ungrounded field and not a blanket rejection of everything.
    R.expect("2e", spec.schedule.get("billing_timing") == "ARREARS",
             "billing_timing, whose citation IS in the description, survived — "
             "the check discriminates rather than refusing everything",
             str(spec.schedule))

    # And the same field, cited correctly, is admitted. Without this the check
    # would also pass for a guard that rejected valuation_method unconditionally.
    grounded = json.loads(GUESSING_RESPONSE)
    grounded["evidence"]["valuation_method"] = "billed quarterly in arrears"
    ok_spec = FS.normalise_fee_spec(grounded, AMBIGUOUS_DESCRIPTION)
    R.expect("2f", ok_spec.schedule.get("valuation_method") == "PERIOD_END",
             "the SAME field with a citation that IS in the text is admitted — "
             "the guard tests the evidence, not the field name",
             str(ok_spec.schedule))

    # ── the two defects the first live run exposed, pinned ───────────────
    #
    # (i) A value equal to the deployed column DEFAULT needs no citation. It
    #     was previously impossible to satisfy: no advisor writes out the
    #     six-step ordering policy in prose, so the model's (correct) standard
    #     order was discarded on EVERY call and reported unresolved forever.
    default_order = json.loads(GUESSING_RESPONSE)
    default_order["schedule"]["ordering_policy"] = list(FS.SCHEDULE_COLUMN_DEFAULTS[
        "ordering_policy"])
    default_order["schedule"]["proration_method"] = "CALENDAR_DAYS"
    dspec = FS.normalise_fee_spec(default_order, AMBIGUOUS_DESCRIPTION)
    R.expect("2i",
             dspec.schedule.get("ordering_policy") == list(
                 FS.SCHEDULE_COLUMN_DEFAULTS["ordering_policy"])
             and dspec.schedule.get("proration_method") == "CALENDAR_DAYS",
             "a grounded field whose value IS the deployed column default is "
             "admitted without a citation — it moves no money, and requiring "
             "prose nobody writes made ordering_policy permanently unresolvable",
             str(dspec.discarded))

    # …but a DEPARTURE from the default still needs evidence, or the exemption
    # would have quietly disabled the guard on those four fields.
    departed = json.loads(GUESSING_RESPONSE)
    departed["schedule"]["proration_method"] = "BUSINESS_DAYS"
    pspec = FS.normalise_fee_spec(departed, AMBIGUOUS_DESCRIPTION)
    R.expect("2j", "proration_method" not in pspec.schedule
             and "proration_method" in pspec.unresolved_fields,
             "a value that DEPARTS from the default still requires evidence — "
             "the exemption is for the status quo, not a hole in the guard",
             str(pspec.schedule.get("proration_method")))

    # (ii) The guard must not break a pair fee34 requires to travel together.
    #      Discarding an ungrounded minimum_fee_scope while keeping
    #      minimum_fee manufactured a schedule fee34 rejects, out of a proposal
    #      that was fine, with a message blaming the advisor for it.
    paired = json.loads(GUESSING_RESPONSE)
    paired["schedule"]["minimum_fee"] = "2500.00"
    paired["schedule"]["minimum_fee_scope"] = "HOUSEHOLD"
    paired["evidence"]["minimum_fee_scope"] = "a minimum of 2,500 per household"
    mspec = FS.normalise_fee_spec(paired, AMBIGUOUS_DESCRIPTION)
    R.expect("2k",
             "minimum_fee_scope" not in mspec.schedule
             and "minimum_fee" not in mspec.schedule,
             "when an ungrounded minimum_fee_scope is discarded, minimum_fee is "
             "withdrawn WITH it — this module never leaves a pair broken by its "
             "own refusal", str(mspec.schedule))
    R.expect("2l",
             not [e for e in validate_schedule(
                 {**mspec.schedule, "status": "DRAFT"}, mspec.tiers)
                 if e.code == "minimum_fee_scope_required"],
             "…so fee34 no longer reports a minimum_fee_scope_required error "
             "that this module itself caused")

    # A pair the MODEL left half-specified is NOT repaired — that is a real
    # error and fee34 must still report it.
    half = json.loads(GUESSING_RESPONSE)
    half["schedule"]["minimum_fee"] = "2500.00"
    hspec = FS.normalise_fee_spec(half, AMBIGUOUS_DESCRIPTION)
    R.expect("2m",
             hspec.schedule.get("minimum_fee") == D("2500.00")
             and any(e.code == "minimum_fee_scope_required" for e in validate_schedule(
                 {**hspec.schedule, "status": "DRAFT"}, hspec.tiers)),
             "a pair the MODEL left half-specified is left alone and fee34 "
             "still refuses it — only self-inflicted damage is undone",
             str(hspec.schedule.get("minimum_fee")))

    try:
        live, _ = await FS.propose_fee_spec(AMBIGUOUS_DESCRIPTION, org_id=ORG)
    except FS.FeeSpecError as exc:
        R.blocked("2g", f"no live model answered ({type(exc).__name__}); "
                        f"[2a-f] proved the guard deterministically")
        return
    R.expect("2g", "valuation_method" not in live.schedule,
             "a REAL model call on a description that never states a valuation "
             "method left it unset", str(live.schedule.get("valuation_method")))
    R.expect("2h", "valuation_method" in live.unresolved_fields,
             "…and reported it as unresolved", str(live.unresolved))


# ═══════════════════════════════════════════════════════════════════════════
# [3] a malformed model response is a typed error, not a crash
# ═══════════════════════════════════════════════════════════════════════════


async def check_3(conn):
    cases = [
        ("prose", "I'm sorry, I can't help with fee schedules.", FS.FeeSpecParseError),
        ("empty", "", FS.FeeSpecParseError),
        ("truncated", '{"schedule": {"code": "X"', FS.FeeSpecParseError),
        ("array", "[1, 2, 3]", FS.FeeSpecShapeError),
        ("scalar-section", '{"schedule": 5}', FS.FeeSpecShapeError),
        ("no-response", None, FS.FeeSpecUnavailableError),
    ]
    for label, payload, expected in cases:
        try:
            await FS.propose_fee_spec(
                GOOD_DESCRIPTION, org_id=ORG, transport=make_transport(payload))
        except expected as exc:
            R.expect(f"3-{label}", bool(getattr(exc, "code", None)),
                     f"a {label} response raised {expected.__name__} carrying a "
                     f"stable code ({getattr(exc, 'code', None)})")
        except Exception as exc:  # noqa: BLE001
            R.bad(f"3-{label}",
                  f"a {label} response raised the WRONG error type",
                  f"expected {expected.__name__}, got {type(exc).__name__}: {exc}")
        else:
            R.bad(f"3-{label}", f"a {label} response did not raise at all")

    # Every one of those is a ValueError subclass, so a caller's existing
    # `except ValueError` still catches it rather than letting a 500 escape.
    R.expect("3-typed",
             all(issubclass(t, ValueError) for t in
                 (FS.FeeSpecParseError, FS.FeeSpecShapeError, FS.FeeSpecUnavailableError)),
             "every typed model error is a ValueError subclass, so an existing "
             "except ValueError around a write path still catches it")

    # A fenced-but-valid response is tolerated AND reported, not silently
    # normalised — the slip stays visible.
    fenced = f"```json\n{VALID_RESPONSE}\n```"
    spec, _ = FS.parse_fee_spec(fenced)
    R.expect("3-fenced", spec.get("schedule", {}).get("rate_type") == "BPS",
             "a fenced but otherwise valid response is parsed rather than "
             "refused, and the fence is reported as a warning")


# ═══════════════════════════════════════════════════════════════════════════
# [4] the worked-example figure IS fee35's figure
# ═══════════════════════════════════════════════════════════════════════════


async def check_4(conn):
    spec = valid_spec()

    # Wrap fee35's entry point for exactly one call and keep what it returned.
    captured: dict[str, object] = {}
    real_calculate = FWE.calculate_account_fee

    def capturing(request, **kwargs):
        result = real_calculate(request, **kwargs)
        captured["result"] = result
        return result

    FWE.calculate_account_fee = capturing
    try:
        example = await FWE.compute_worked_example(
            conn, ORG, spec,
            period_start=PERIOD_START, period_end=PERIOD_END, account_id=ACC_MAIN,
        )
    finally:
        FWE.calculate_account_fee = real_calculate

    engine_result = captured.get("result")
    R.expect("4a", engine_result is not None,
             "the worked example went through services.fee_calc.calculate_account_fee")
    # IDENTITY, not equality: a separate computation that agreed today would
    # still be a different object.
    R.expect("4b", engine_result is not None and example.amount is engine_result.amount,
             "the figure the screen shows is the SAME Decimal object fee35 "
             "returned — not a value recomputed to match it")

    # The ordinary saved-schedule path, with no override in sight.
    request, provenance = await load_account_calc_request(
        conn, ORG, account_id=ACC_MAIN, fee_schedule_id=SCH_SAVED,
        period_start=PERIOD_START, period_end=PERIOD_END,
    )
    direct = FC.calculate_account_fee(request)

    R.expect("4c", example.amount == direct.amount,
             "the proposed-schedule figure equals fee35 called directly on the "
             "saved twin, for the same account and period",
             f"{example.amount} vs {direct.amount}")
    R.expect("4d", example.billable_value == direct.billable_value
             and example.gross_fee == direct.gross_fee,
             "billable value and gross fee agree too — not only the final cent",
             f"{example.billable_value}/{example.gross_fee} vs "
             f"{direct.billable_value}/{direct.gross_fee}")

    # A third, independent route: arithmetic a person did, written as a literal.
    R.expect("4e", example.amount == EXPECTED_FEE,
             f"the figure is the hand-computed {EXPECTED_FEE} for "
             f"{BALANCE} through 100bps/75bps graduated, quarterly",
             str(example.amount))

    R.expect("4f", provenance["schedule_source"] == "database"
             and example.provenance["schedule_source"] == "override",
             "the two routes record which schedule source produced them, so a "
             "proposal's figure is distinguishable from a saved schedule's",
             f"{provenance['schedule_source']} / {example.provenance['schedule_source']}")
    R.expect("4g", example.engine_version == FC.ENGINE_VERSION,
             f"the example carries fee35's own engine version ({FC.ENGINE_VERSION})",
             example.engine_version)

    # The refusal side: an incomplete spec produces a typed refusal, never a
    # number. A screen that showed $0.00 here would read as "nothing is owed".
    incomplete = FS.normalise_fee_spec(
        json.loads(GUESSING_RESPONSE), AMBIGUOUS_DESCRIPTION)
    try:
        await FWE.compute_worked_example(
            conn, ORG, incomplete,
            period_start=PERIOD_START, period_end=PERIOD_END, account_id=ACC_MAIN)
    except FWE.WorkedExampleUnavailable as exc:
        R.expect("4h", exc.reason_code == "spec_incomplete"
                 and "valuation_method" in str(exc),
                 "a spec with an unresolved valuation method REFUSES to produce "
                 "a figure, naming the missing field", exc.message)
    else:
        R.bad("4h", "an incomplete spec produced a worked example anyway")

    return spec


# ═══════════════════════════════════════════════════════════════════════════
# [5] a failing schedule surfaces fee34's OWN errors, not a paraphrase
# ═══════════════════════════════════════════════════════════════════════════


async def check_5(conn):
    # A tier ladder with a GAP: 0–1,000,000 then 1,500,000–open. The money
    # between the two bounds matches no tier at all.
    broken = json.dumps({
        "schedule": {
            "code": f"{TAG.upper()}-BROKEN", "name": "Broken ladder",
            "product_type": "ASSET_MANAGEMENT", "rate_type": "BPS",
            "tier_method": "GRADUATED", "billing_frequency": "QUARTERLY",
            "billing_timing": "ARREARS", "valuation_method": "PERIOD_END",
            "minimum_fee": "2500.00",
        },
        "tiers": [
            {"tier_seq": 1, "lower_bound": "0", "upper_bound": "1000000", "rate_bps": "100"},
            {"tier_seq": 2, "lower_bound": "1500000", "upper_bound": None, "rate_bps": "75"},
        ],
        "evidence": {
            "billing_frequency": "Bill it quarterly in arrears",
            "billing_timing": "Bill it quarterly in arrears",
            "valuation_method": "valued on the period-end market value",
            "tier_method": "graduated so each tier only applies to the money in its own band",
        },
    })
    spec = FS.normalise_fee_spec(json.loads(broken), GOOD_DESCRIPTION)

    from routers.fee_chat import _validation_errors

    surfaced = _validation_errors(spec)
    direct = [e.as_dict() for e in validate_schedule(
        {**spec.schedule, "status": "DRAFT"}, spec.tiers,
        exclusions=spec.exclusions or None)]

    R.expect("5a", surfaced == direct,
             "the errors this sprint surfaces are BYTE-IDENTICAL to "
             "fee_validation.validate_schedule called directly on the same rows",
             f"{surfaced}\n         vs\n         {direct}")
    R.expect("5b", any(e["code"] == "tier_gap" for e in surfaced),
             "the tier gap is reported with fee34's own stable code 'tier_gap', "
             "not a generic message", str([e["code"] for e in surfaced]))
    # The code is read off fee34's own error class, never retyped here: a
    # hardcoded string is a second copy that can disagree with the one the UI
    # switches on, and this assertion caught exactly that on its first run.
    from services.fee_validation import MinimumFeeScopeError

    R.expect("5c", any(e["code"] == MinimumFeeScopeError.code for e in surfaced),
             f"the missing minimum_fee_scope is reported too, under fee34's own "
             f"code '{MinimumFeeScopeError.code}' — every broken rule at once, "
             f"not one per round trip", str([e["code"] for e in surfaced]))
    gap = next((e for e in surfaced if e["code"] == "tier_gap"), {})
    R.expect("5d", gap.get("field") is not None and gap.get("tier_seq") is not None,
             "each error names the field AND the tier_seq, so the form marks the "
             "offending input rather than showing a toast", str(gap))

    # The same objects the manual fee34 admin screen renders. If this drifted,
    # two screens would explain the same refusal differently.
    from routers.fee_schedules import _raise_for as fee34_raise
    from services.fee_schedules import FeeScheduleInvalid

    try:
        fee34_raise(FeeScheduleInvalid(validate_schedule(
            {**spec.schedule, "status": "DRAFT"}, spec.tiers)))
    except Exception as exc:  # HTTPException
        body = getattr(exc, "detail", {})
        R.expect("5e", body.get("errors") == direct,
                 "fee34's own admin router publishes the identical error list "
                 "for the identical schedule — one message set, not two",
                 str(body.get("errors")))


# ═══════════════════════════════════════════════════════════════════════════
# [6] an advisor edit writes a real correction row
# ═══════════════════════════════════════════════════════════════════════════


async def check_6(conn):
    correction_id = await FSC.log_fee_spec_correction(
        conn, org_id=ORG, conversation_id=CONV_ID,
        field_name="valuation_method",
        original_value="PERIOD_END", corrected_value="AVG_DAILY",
        corrected_by=U_ADVISOR,
    )
    R.expect("6a", correction_id is not None, "the correction wrote a row")

    row = await conn.fetchrow(
        "SELECT * FROM public.document_field_corrections WHERE id = $1::uuid",
        correction_id)
    R.expect("6b", row is not None
             and row["target_type"] == FSC.TARGET_TYPE
             and str(row["target_id"]) == CONV_ID,
             f"the row carries target_type={FSC.TARGET_TYPE} and target_id = the "
             f"conversation", str(dict(row)) if row else "no row")
    R.expect("6c", row is not None and row["field_name"] == "valuation_method"
             and row["original_value"] == "PERIOD_END"
             and row["corrected_value"] == "AVG_DAILY",
             "the field name and BOTH values are stored as given",
             str(dict(row)) if row else "")
    R.expect("6d", row is not None and str(row["org_id"]) == ORG
             and row["document_id"] is None,
             "org_id is the caller's org (NOT null) and document_id is null — "
             "the shape the migrated pairing constraint requires",
             str(dict(row)) if row else "")
    R.expect("6e", row is not None
             and json.loads(row["notes"])["source"] == FSC.SOURCE_ADVISOR_EDIT,
             "provenance is recorded in the notes envelope",
             row["notes"] if row else "")

    # A no-op edit is not stored. Otherwise every blur event would dilute the
    # signal this table exists to collect.
    noop = await FSC.log_fee_spec_correction(
        conn, org_id=ORG, conversation_id=CONV_ID, field_name="billing_timing",
        original_value="ARREARS", corrected_value="ARREARS", corrected_by=U_ADVISOR)
    R.expect("6f", noop is None,
             "an edit that changed nothing is NOT logged — no-ops would dilute "
             "the correction signal")

    # The migration's constraints do real work in both directions: the new
    # target_type is admitted, and the org-NULL shape that is right for
    # note_terms is REFUSED for this one.
    try:
        await conn.execute(
            """INSERT INTO public.document_field_corrections
                 (document_id, org_id, target_type, target_id, field_name,
                  original_value, corrected_value)
               VALUES (NULL, NULL, $1, $2::uuid, 'x', 'a', 'b')""",
            FSC.TARGET_TYPE, CONV_ID)
    except Exception as exc:  # noqa: BLE001
        R.expect("6g", "document_pairing_chk" in str(exc),
                 "a FEE_SCHEDULE_SPEC correction with a NULL org_id is REFUSED "
                 "by the pairing constraint — tenant data cannot be written "
                 "org-blind", str(exc)[:160])
    else:
        R.bad("6g", "an org-NULL FEE_SCHEDULE_SPEC correction was accepted",
              "the pairing constraint is not doing its job")

    results = await FSC.log_fee_spec_corrections(
        conn, org_id=ORG, conversation_id=CONV_ID,
        edits={"tier_method": {"original": "GRADUATED", "corrected": "CLIFF"},
               "currency": {"original": "USD", "corrected": "USD"}},
        corrected_by=U_ADVISOR)
    logged = {r["field"]: r["logged"] for r in results}
    R.expect("6h", logged == {"tier_method": True, "currency": False},
             "a batch reports per-field outcomes, distinguishing 'not logged "
             "because unchanged' from 'not logged because it failed'", str(results))


# ═══════════════════════════════════════════════════════════════════════════
# [7] every model call left an ai_decision_log row naming a real model
# ═══════════════════════════════════════════════════════════════════════════


async def check_7(conn, made_live_call: bool):
    rows = await conn.fetch(
        "SELECT * FROM public.ai_decision_log WHERE task_type = $1 "
        "ORDER BY created_at", FS.TASK_TYPE)
    if not rows:
        R.blocked("7", "no live model call was made, so there is no "
                       "ai_decision_log row to inspect")
        return

    R.expect("7a", True,
             f"{len(rows)} ai_decision_log row(s) were written under this "
             f"sprint's own task_type '{FS.TASK_TYPE}'")

    placeholders = {"", "the model", "model", "unknown", "claude", "default", None}
    bad = [r for r in rows if (r["model_used"] or "").strip().lower() in placeholders]
    R.expect("7b", not bad,
             "every row names an actual model in model_used, never a placeholder",
             str([r["model_used"] for r in bad]))

    # A VERSION, not just a family. Anthropic model ids are dated
    # (claude-haiku-4-5-20251001); a bare family name would not identify what
    # actually answered.
    import re as _re
    versioned = [
        r for r in rows
        if _re.search(r"-\d", r["model_used"] or "") or "-" in (r["model_used"] or "")
    ]
    R.expect("7c", len(versioned) == len(rows),
             "every model_used carries a version, not just a family name",
             str([r["model_used"] for r in rows]))
    R.expect("7d", all(r["org_id"] is not None and r["task_type"] == FS.TASK_TYPE
                       for r in rows),
             "every row is attributed to an org and to this task type")
    R.expect("7e", all(r["model_requested"] for r in rows),
             "model_requested is recorded alongside model_used, so a silent "
             "fallback is visible as the two differing",
             str([(r["model_requested"], r["model_used"]) for r in rows]))
    successes = [r for r in rows if r["success"]]
    R.expect("7f", not successes or all(r["latency_ms"] and r["latency_ms"] > 0
                                        for r in successes),
             "successful calls carry a real measured latency")


# ═══════════════════════════════════════════════════════════════════════════
# [8] cross-org isolation, on a role that cannot bypass RLS
# ═══════════════════════════════════════════════════════════════════════════


async def check_8(app_dsn, admin):
    app = await connect(app_dsn)
    try:
        bypass = await app.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        user = await app.fetchval("SELECT current_user")
        if not R.expect("8a", bypass is False,
                        f"the isolation role ({user}) has rolbypassrls=False — "
                        f"without this every check below is vacuous", str(bypass)):
            return

        async def as_org(org):
            await app.execute("SELECT set_config('app.current_org_id', $1, false)", org)
            await app.execute("SELECT set_config('app.is_super_admin', 'false', false)")

        # ── the resolver, both directions ────────────────────────────────
        await as_org(ORG)
        only_other = await resolve_reference(
            app, ORG, ref="r1", kind="ENTITY", name=NAME_OTHER_ONLY)
        R.expect("8b", only_other.status == "unresolved" and only_other.id is None,
                 "a name that exists ONLY in the other org does not resolve for "
                 "this org", str(only_other.as_dict()))

        shared = await resolve_reference(
            app, ORG, ref="r2", kind="ENTITY", name=NAME_SHARED)
        R.expect("8c", shared.status == "resolved" and shared.id == ENT_SHARED_A,
                 "a name that exists in BOTH orgs resolves to THIS org's row — "
                 "so [8b] is isolation, not a resolver that finds nothing",
                 str(shared.as_dict()))

        await as_org(OTHER_ORG)
        mirror = await resolve_reference(
            app, OTHER_ORG, ref="r3", kind="ENTITY", name=NAME_SHARED)
        R.expect("8d", mirror.status == "resolved" and mirror.id == ENT_SHARED_B,
                 "…and to the OTHER org's row when the other org asks — the "
                 "same query, two tenants, two answers",
                 str(mirror.as_dict()))

        await as_org(ORG)
        unique_acct = await resolve_reference(
            app, ORG, ref="r4", kind="ACCOUNT", name=f"***{OTHER_ACC[-3:]}")
        R.expect("8e", unique_acct.status == "unresolved",
                 "an ACCOUNT belonging to the other org does not resolve either",
                 str(unique_acct.as_dict()))

        # An org_id that is not the caller's cannot be smuggled in: the resolver
        # takes org_id as an argument, and RLS refuses the read regardless.
        smuggled = await resolve_reference(
            app, OTHER_ORG, ref="r5", kind="ENTITY", name=NAME_OTHER_ONLY)
        R.expect("8f", smuggled.status == "unresolved",
                 "passing another org's id while the session GUC says ORG still "
                 "returns nothing — RLS backs the WHERE clause rather than "
                 "being trusted instead of it", str(smuggled.as_dict()))

        # ── the correction row's own isolation ───────────────────────────
        visible_own = await app.fetchval(
            "SELECT count(*) FROM public.document_field_corrections "
            "WHERE target_type = $1 AND target_id = $2::uuid",
            FSC.TARGET_TYPE, CONV_ID)
        await as_org(OTHER_ORG)
        visible_other = await app.fetchval(
            "SELECT count(*) FROM public.document_field_corrections "
            "WHERE target_type = $1 AND target_id = $2::uuid",
            FSC.TARGET_TYPE, CONV_ID)
        R.expect("8g", visible_own > 0 and visible_other == 0,
                 "a FEE_SCHEDULE_SPEC correction is readable by its own org and "
                 "INVISIBLE to another — org_isolation is the only policy that "
                 "matches it", f"own={visible_own} other={visible_other}")

        # Reproduce the leak the migration closed, without touching the live
        # policy: evaluate the PRE-migration predicate against this row.
        would_have_leaked = await admin.fetchval(
            "SELECT $1::text <> 'document'", FSC.TARGET_TYPE)
        current_predicate = await admin.fetchval(
            "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
            "WHERE polrelid = 'public.document_field_corrections'::regclass "
            "AND polname = 'document_field_corrections_global_read'")
        R.expect("8h", would_have_leaked is True
                 and "<> 'document'" not in (current_predicate or ""),
                 "the pre-migration global_read predicate (target_type <> "
                 "'document') WOULD have matched this row and exposed it to "
                 "every org; the deployed predicate is an explicit allow-list "
                 "that does not", f"deployed: {current_predicate}")
        R.expect("8i", FSC.TARGET_TYPE not in (current_predicate or ""),
                 "FEE_SCHEDULE_SPEC is absent from the global allow-list, so it "
                 "falls under org_isolation alone")
    finally:
        await app.close()


# ═══════════════════════════════════════════════════════════════════════════
# [supporting] the resolver's ambiguity behaviour and the diff
# ═══════════════════════════════════════════════════════════════════════════


async def check_resolver_and_diff(conn):
    spec = valid_spec()
    report = await resolve_spec_references(conn, ORG, spec)
    R.expect("R1", report.is_complete and report.resolved_ids.get("r1") == ENT_MAIN,
             "an exact, unique name resolves to its real id",
             str(report.as_dict()))

    # A partial name matching two rows must produce candidates, not a pick.
    partial = await resolve_reference(
        conn, ORG, ref="p1", kind="ENTITY", name=TAG)
    R.expect("R2", partial.status == "ambiguous" and partial.id is None
             and len(partial.candidates) >= 2,
             "a name matching several records returns a disambiguation list and "
             "picks NOTHING", str(partial.as_dict())[:300])

    # LIKE metacharacters in a name are escaped rather than widening the search.
    wild = await resolve_reference(
        conn, ORG, ref="p2", kind="ENTITY", name="%")
    R.expect("R3", wild.status == "unresolved",
             "a bare '%' is escaped and matches nothing, rather than matching "
             "every entity in the org", str(wild.as_dict())[:200])

    diff = await build_diff(conn, ORG, spec)
    fields = {f["field"]: f for f in diff["fields"]}
    R.expect("R4", diff["baseline"] in ("column_default", "org_default"),
             f"a new schedule is diffed against a named baseline "
             f"({diff['baseline']}), not an unlabelled 'current'")
    R.expect("R5", fields["valuation_method"]["status"] == "new"
             and fields["currency"]["status"] == "unchanged",
             "a field with no baseline reads 'new' and one matching the default "
             "reads 'unchanged'",
             f"{fields['valuation_method']['status']} / {fields['currency']['status']}")

    incomplete = FS.normalise_fee_spec(json.loads(GUESSING_RESPONSE), AMBIGUOUS_DESCRIPTION)
    idiff = await build_diff(conn, ORG, incomplete)
    ifields = {f["field"]: f for f in idiff["fields"]}
    R.expect("R6", ifields["valuation_method"]["status"] == "unresolved"
             and ifields["valuation_method"]["reason"],
             "an unresolved field is its OWN diff status carrying the reason — "
             "visually distinct from a blank or an unchanged value",
             str(ifields["valuation_method"]))
    R.expect("R7", ifields["maximum_fee"]["status"] == "not_specified",
             "'the model deliberately left this alone' is a different status "
             "from 'nobody knows' — a screen must not show them the same",
             str(ifields["maximum_fee"]))

    tier_diff = await build_diff(conn, ORG, spec, fee_schedule_id=SCH_SAVED)
    statuses = [t["status"] for t in tier_diff["tiers"]]
    R.expect("R8", tier_diff["baseline"] == "current_schedule"
             and statuses == ["unchanged", "unchanged"],
             "diffed against the saved twin, every tier reads unchanged — the "
             "comparison detects sameness, not only difference", str(statuses))


# ═══════════════════════════════════════════════════════════════════════════
# [N] the migration did not break the OTHER users of this shared table
# ═══════════════════════════════════════════════════════════════════════════
#
# document_field_corrections is shared. This sprint rewrote two of its CHECK
# constraints and all four of its global RLS policies, so "fee40's own writes
# work" is not enough — the note-terms path (29 live rows) has to still work
# too, on both axes it depends on: an org-NULL insert must still be ACCEPTED,
# and a note_terms row must still be readable org-blind.


async def check_note_terms_unbroken(conn, app_dsn):
    from services.note_terms_corrections import (
        TARGET_TYPE as NT_TYPE,
        log_note_terms_correction,
    )

    probe_id = "99000000-0000-0000-0000-0000fee40099"
    correction_id = None
    try:
        correction_id = await log_note_terms_correction(
            conn, note_terms_id=probe_id, field_name=f"{TAG}_probe",
            original_value="a", corrected_value="b")
        row = await conn.fetchrow(
            "SELECT org_id, target_type FROM public.document_field_corrections "
            "WHERE id = $1::uuid", correction_id)
        R.expect("N1", row is not None and row["org_id"] is None
                 and row["target_type"] == NT_TYPE,
                 "the note-terms path still writes an org-NULL row — the "
                 "rewritten pairing constraint did not break the subsystem that "
                 "was already using this table", str(dict(row)) if row else "")
    except Exception as exc:  # noqa: BLE001
        R.bad("N1", "the note-terms correction path broke", str(exc)[:200])

    if app_dsn and correction_id:
        app = await connect(app_dsn)
        try:
            # No org GUC at all: note_terms is global and must stay readable
            # without one, which is what services/correction_retrieval and the
            # note-terms review path rely on.
            await app.execute("SELECT set_config('app.current_org_id', '', false)")
            await app.execute("SELECT set_config('app.is_super_admin', 'false', false)")
            seen = await app.fetchval(
                "SELECT count(*) FROM public.document_field_corrections "
                "WHERE id = $1::uuid", correction_id)
            R.expect("N2", seen == 1,
                     "a note_terms correction is STILL readable org-blind under "
                     "app_service — narrowing the global policies to an "
                     "allow-list kept the genuinely global types global",
                     f"count={seen}")
        finally:
            await app.close()

    if correction_id:
        await conn.execute(
            "DELETE FROM public.document_field_corrections WHERE id = $1::uuid",
            correction_id)


# ═══════════════════════════════════════════════════════════════════════════
# [C] the CLIENT half of the permission proof
# ═══════════════════════════════════════════════════════════════════════════
#
# The server half is [8] and the router's own gates. This half feeds real
# envelopes into apps/web/lib/feeChatGates.mjs — the module
# FeeChatWorkbench.jsx actually calls for every write control it renders. A
# hidden button behind an unprotected endpoint and a protected endpoint behind
# a visible button are both real bugs, and proving one proves nothing about
# the other.


def check_client_gates():
    harness = HERE / "feechat_harness.mjs"
    try:
        proc = subprocess.run(
            ["node", str(harness)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        R.blocked("C", f"could not run the client harness: {exc}")
        return
    if proc.returncode != 0:
        R.blocked("C", f"client harness exited {proc.returncode}: "
                       f"{proc.stderr.strip()[:200]}")
        return
    try:
        d = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        R.blocked("C", f"unparseable harness output: {exc}")
        return

    R.expect("C1",
             d["view_only_can_write"] is False
             and d["view_only_editable"] == []
             and d["view_only_may_edit_valuation"] is False
             and d["view_only_save_control"] is False
             and d["view_only_save_enabled"] is False,
             "a view-only caller's real envelope renders NO editable field and "
             "NO save control — checked through the component's own gate "
             "module, not inferred from the server", json.dumps(d)[:300])

    R.expect("C2",
             d["writer_can_write"] is True
             and d["writer_may_edit_valuation"] is True
             and d["writer_save_control"] is True,
             "a caller WITH manage_billing does get the controls — so [C1] is a "
             "gate doing work, not a component that renders nothing for anyone")

    R.expect("C3", d["writer_may_edit_unlisted"] is False,
             "a field absent from the server's `editable` list stays read-only "
             "even for a writer — the list is honoured, not just can_write")

    R.expect("C4", d["writer_save_enabled_clean"] is True
             and d["writer_save_enabled_with_errors"] is False,
             "the save is disabled while fee34 still reports errors, and "
             "enabled when it does not")

    closed = [c for c in d["lost_envelope"]
              if c["can_write"] or c["save_control"] or c["save_enabled"]
              or c["editable"] or c["may_edit"]]
    R.expect("C5", not closed,
             f"all {len(d['lost_envelope'])} lost-envelope shapes fail CLOSED "
             f"(null, undefined, {{}}, missing key, \"false\", \"true\", 1) — "
             f"no truthy fallback restores write access",
             str(closed))

    # The string "true" is the one that matters most: it is truthy, and a
    # `permissions.can_write ? …` test would grant write access on it.
    truthy_string = next(
        c for c in d["lost_envelope"] if c["label"] == "string 'true'")
    R.expect("C6", truthy_string["can_write"] is False,
             "can_write === true is an identity check: the truthy STRING "
             "\"true\" does not grant write access")

    R.expect("C7",
             d["writer_no_vocabularies"] == []
             and d["writer_bad_vocabularies"] == []
             and d["choices_no_envelope"] is None
             and d["choices_absent"] is None,
             "a missing or malformed vocabularies half also fails closed, and "
             "field choices are never defaulted client-side",
             f"{d['writer_no_vocabularies']} {d['choices_no_envelope']}")


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    if not admin_url:
        R.blocked("0", f"no working admin DSN: {admin_prov}")
        R.summary()
        return 1
    app_url, app_prov = await app_service_dsn()
    print(f"admin: {admin_prov}\napp_service: {app_prov}\n")

    admin = await connect(admin_url)
    before = None
    made_live_call = False
    try:
        await teardown(admin)           # leftovers from a crashed earlier run
        before = await counts(admin)
        await build_fixtures(admin)

        _, raw = await check_1(admin)
        made_live_call = raw is not None
        await check_2(admin)
        await check_3(admin)
        await check_4(admin)
        await check_5(admin)
        await check_6(admin)
        await check_7(admin, made_live_call)
        await check_resolver_and_diff(admin)
        await check_note_terms_unbroken(admin, app_url)
        check_client_gates()
        if app_url:
            await check_8(app_url, admin)
        else:
            R.blocked("8", f"no working app_service DSN, so isolation is "
                           f"unproven: {app_prov}")

        R.find("F40-A", (
            "The prompt's anticipated blocker did not exist: "
            "document_field_corrections.document_id was ALREADY nullable. Two "
            "different constraints blocked reuse instead. (i) "
            "document_field_corrections_target_type_chk was a closed allow-list "
            "of ('document','note_terms','template_proposal'), so "
            "'FEE_SCHEDULE_SPEC' was rejected outright. (ii) "
            "document_field_corrections_document_pairing_chk forced org_id IS "
            "NULL for EVERY non-document target — right for note_terms (a "
            "424B2's terms belong to no tenant) and wrong for a firm's fee "
            "arrangements. Writing them org-NULL would have made them invisible "
            "to correction_retrieval.py, which filters org_id = $1, defeating "
            "the whole purpose of logging them."))
        R.find("F40-B", (
            "The RLS carve-out was the real tenant risk, and it was not in the "
            "prompt. Three policies (document_field_corrections_global_read / "
            "_super_admin_insert / _update / _delete) were written as "
            "target_type <> 'document' — an OPEN-ENDED predicate that "
            "automatically globalises every target type added afterwards. "
            "Adding FEE_SCHEDULE_SPEC under it would have made one firm's fee "
            "negotiations readable by every org, with no code change to blame. "
            "The migration narrows all four to an explicit allow-list of the "
            "genuinely global types. Reproduced in [8h]."))
        R.find("F40-C", (
            "There is no TaskRouter class. The standing model-resolution path "
            "is services/extraction.py: resolve_model(org_id, "
            "key='ai.model.default') -> _execute_chain -> call_claude_* , which "
            "already writes exactly one ai_decision_log row per call carrying "
            "the real model_used. This sprint logs nothing separately — a "
            "second writer would be a second place for the model name to drift "
            "from the one that answered."))
        R.find("F40-D", (
            "LiteLLM (the intended Phase-B transport) still cannot serve any "
            "model: a real call returns 400 'Invalid model name passed in "
            "model=claude-haiku-4-5-20251001' because Phase A has ZERO models "
            "registered. The identical call succeeds under the documented "
            "rollback LITELLM_ROUTING_DISABLED=1, which THIS SCRIPT sets for "
            "its own process. Sprint code never sets it. Until Phase A "
            "registers models, every fee-chat model call in a real deployment "
            "fails through the chain and returns FeeSpecUnavailableError."))
        R.find("F40-E", (
            "The dev database had ZERO fee_schedules, accounts, households and "
            "account_balances_daily rows at sprint start, so 'a REAL household's "
            "REAL current balances' did not exist to bill. The worked example is "
            "proved against a seeded fixture, and "
            "pick_example_account raises WorkedExampleUnavailable "
            "('no_billable_account_with_balances') rather than returning a "
            "placeholder figure when nothing real is there."))
        R.find("F40-F", (
            "portfolio.securities_global has NO org_id — it is global by design "
            "(a CUSIP is a public fact). 'The resolver must never resolve a "
            "security belonging to a different org' is therefore not a property "
            "that table can have. Security resolution tries the org-scoped "
            "portfolio.assets FIRST and only falls through to the global table, "
            "and each Resolution reports its scope ('org' or 'global') so the "
            "two are never merged into one undifferentiated candidate list."))
        R.find("F40-G", (
            "The grounding check needed a human exemption. Re-normalising a "
            "spec on the way back in would have discarded the advisor's OWN "
            "edits as ungrounded — the guard would have been enforcing its rule "
            "against the one party it was never aimed at, and every edit would "
            "have snapped back to unresolved. normalise_fee_spec takes "
            "trusted_fields; propose_fee_spec passes none, so a model cannot "
            "reach it."))
        R.find("F40-I", (
            "docs/schema_snapshot.sql records columns, primary keys and unique "
            "indexes — but NOT CHECK constraint bodies and NOT RLS policies. "
            "Both things this sprint's migration changed are therefore "
            "invisible to the snapshot, so an environment rebuilt from it would "
            "silently lack them and the first correction write would fail on a "
            "constraint whose definition is nowhere in the repo. The migration "
            "is recorded as idempotent, self-verifying SQL in "
            "scripts/_apply_fee40_part1.py instead. This gap is GENERAL, not "
            "specific to fee40: every CHECK and every policy any prior sprint "
            "added is equally absent."))
        R.find("F40-H", (
            "load_account_calc_request gained an optional schedule_override so "
            "an UNSAVED proposal can be priced against real balances. This is "
            "an additive keyword on fee35/fee36's loader, not a new one: a "
            "second fee40-specific assembler of the same eight tables would "
            "drift, and the copy that stopped filtering valid_to IS NULL would "
            "price against superseded positions while still looking reasonable."))
    except Exception:
        R.bad("main", "the run raised", traceback.format_exc().strip().splitlines()[-1])
        traceback.print_exc()
    finally:
        try:
            await teardown(admin)
        except Exception:
            R.bad("teardown", "teardown raised",
                  traceback.format_exc().strip().splitlines()[-1])
        if before is not None:
            after = await counts(admin)
            drift = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
            R.expect("9", not drift,
                     "every counted table is back to its pre-test row count",
                     str(drift))
        await admin.close()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
