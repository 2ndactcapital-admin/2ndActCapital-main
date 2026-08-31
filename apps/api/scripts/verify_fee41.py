"""Sprint fee41 verification — fee narrative generation.

Pass/fail only, no prompts. Run:

    python3 scripts/verify_fee41.py

Every table this script writes to is counted before the first insert and again
after the last delete; a difference of even one row fails the run, reported
AFTER the tests so a teardown bug never masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[2] proves the loud failure is SPECIFIC, not a blanket refusal.** The tier
  template is rendered against the FLAT schedule and must raise — but the FLAT
  schedule is then rendered successfully against its own template. Without the
  second half, "FLAT schedules raise" would also pass for a renderer that had
  simply broken on FLAT schedules entirely, which proves nothing about tokens.

* **[3] proves the two households differ for the RIGHT reason.** It is not
  enough that two texts differ — a copy-paste with one word swapped differs
  too. So: each household's ``SourceOrder`` is resolved INDEPENDENTLY through
  fee32's own function and the two are asserted to disagree at the source
  (different ``origin``, different ``order[0]``); each label is read straight
  out of ``public.config`` rather than through the module under test; each text
  must contain its OWN label and NOT the other's; and the two renders are
  diffed line by line, with every schedule-derived and tier-derived line
  required to be BYTE-IDENTICAL. Only the precedence and household lines may
  move.

* **[4] exercises fee34's two real edit paths, not a simulation of them.**
  ``update_schedule`` is called for both. A DRAFT edit updates in place (same
  id) so the narrative pointing at it must stale. An APPROVED edit forks
  version N+1 at a NEW id and leaves the approved row untouched, so the
  narrative pointing at the approved row must NOT stale — and that is checked
  after three separate pieces of unrelated activity, not in a quiet moment.

* **[5] proves independence in BOTH directions.** One household's override is
  changed while the schedule is not touched at all. That household's narrative
  must stale; the OTHER household's narrative, on the SAME schedule, must not.
  A staleness check that stale-d everything on the schedule would pass a
  one-household test.

* **[6] drives the REJECTING branch deliberately.** A real model cannot be
  asked to alter a number on demand, so the gate is driven through
  ``polish_narrative``'s transport seam with a hand-written response that
  rounds ``12.345678 bps`` to ``12.35 bps`` — the exact contract defect the gate
  exists to stop. The ACCEPTING case is guarded too: the accepted candidate
  must genuinely DIFFER from the deterministic text and the comparison must
  have found a non-empty set of numbers, or "accepted" would be vacuous.

* **[8] runs on app_service, whose ``rolbypassrls`` is asserted False FIRST.**
  Without that assertion every isolation check below it proves nothing.
  Isolation is proved in three ways: the other org's rows are invisible, this
  org's rows ARE visible (so it is not just reading nothing), and an empty GUC
  reads zero rows. A cross-org WRITE is attempted and must be refused by the
  policy's WITH CHECK, not by a Python ``if``.

* Teardown is by fixture id and fixture tag, in FK order, never a TRUNCATE.
  Schedules forked by ``update_schedule`` get ids this script never sees, so
  they are reaped by their inherited ``code``.


THE HONEST GAP
──────────────────────────────────────────────────────────────────────────────
``adv_check_status`` is verified as WIRED — the column, its CHECK, its default,
the app-layer refusal of an invalid value — and is verified to be UNCHECKED on
every narrative this script renders. No ADV comparison is performed, because
there is no Form ADV Part 2A source in this database to compare against. That
is reported as a named gap, not as a passing test.
"""

from __future__ import annotations

import asyncio
import glob
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

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from services import fee_narratives as FN  # noqa: E402
from services import portfolio_precedence as PP  # noqa: E402
from services.fee_schedules import (  # noqa: E402
    create_schedule,
    load_schedule,
    submit_for_approval,
    update_schedule,
)

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "fee41verify"
CODE_PREFIX = "FEE41VERIFY"

U_APPROVER = "99000000-0000-0000-0000-0000fee41001"
USERS = [U_APPROVER]

HH_ADDEPAR = "99000000-0000-0000-0000-0000fee41011"   # has a household override
HH_DEFAULT = "99000000-0000-0000-0000-0000fee41012"   # falls through to the default
HH_UNRELATED = "99000000-0000-0000-0000-0000fee41013"  # only ever unrelated activity
OTHER_HH = "99000000-0000-0000-0000-0000fee41014"
HOUSEHOLDS = [HH_ADDEPAR, HH_DEFAULT, HH_UNRELATED, OTHER_HH]

ENT_A = "99000000-0000-0000-0000-0000fee41021"
ENT_B = "99000000-0000-0000-0000-0000fee41022"
ENT_OTHER = "99000000-0000-0000-0000-0000fee41023"
ENTITIES = [ENT_A, ENT_B, ENT_OTHER]

ACC_A = "99000000-0000-0000-0000-0000fee41031"
ACC_B = "99000000-0000-0000-0000-0000fee41032"
ACC_OTHER = "99000000-0000-0000-0000-0000fee41033"
ACCOUNTS = [ACC_A, ACC_B, ACC_OTHER]

OTHER_SCH = "99000000-0000-0000-0000-0000fee41041"
OTHER_TPL = "99000000-0000-0000-0000-0000fee41051"
OTHER_NAR = "99000000-0000-0000-0000-0000fee41061"

#: The top band's rate. numeric(12,6) carries six decimals and this value uses
#: all six, so [7] can prove none of them is silently rounded away — and [6] has
#: a number a "helpful" polish would obviously want to tidy to 12.35.
TOP_RATE_BPS = D("12.345678")
FIRST_RATE_BPS = D("100")
BAND_BREAK = D("1000000")

#: The tier template. Deliberately mixes schedule tokens, ladder tokens, indexed
#: tier tokens, count tokens and precedence tokens — so [2]'s failure against a
#: FLAT schedule is a failure of the TIER tokens specifically, and [3]'s diff
#: has plenty of schedule-derived text that must NOT move between households.
TPL_TIERED_BODY = """\
Fee Schedule {{schedule.code}} (version {{schedule.version}}) for the \
{{household.name}} household.

The adviser charges {{schedule.rate_type_label}}, billed \
{{schedule.billing_frequency_label}} {{schedule.billing_timing_label}}. The fee \
is calculated on {{schedule.valuation_method_label}}, and is \
{{schedule.proration_method_label}} for any partial period.

This schedule is tiered, and {{schedule.tier_method_label}}. The bands are:
{{tiers.ladder}}

The first band carries a rate of {{tiers.1.rate}} ({{tiers.1.rate_pct}}) across \
{{tiers.1.band}}. The second band carries a rate of {{tiers.2.rate}} \
({{tiers.2.rate_pct}}) across {{tiers.2.band}}.

This arrangement reflects {{exclusions.count}} exclusion, \
{{discounts.count}} discount and {{credits.count}} credit.

For this household, portfolio values are taken from \
{{precedence.primary_source_label}}, reflecting {{precedence.origin_label}}.
"""

#: Flat-compatible only. Rendering this successfully against the FLAT schedule
#: is the half of [2] that stops "it raised" from being a vacuous pass.
TPL_FLAT_BODY = """\
Fee Schedule {{schedule.code}} (version {{schedule.version}}) for the \
{{household.name}} household.

The adviser charges {{schedule.rate_type_label}}, billed \
{{schedule.billing_frequency_label}} {{schedule.billing_timing_label}}, \
calculated on {{schedule.valuation_method_label}}.
"""

#: Nothing but tier tokens, so the error [2b] reports is unambiguously the tier
#: error and not an incidental NULL column that happened to be resolved first.
TPL_TIERONLY_BODY = "The first band is {{tiers.1.band}} at {{tiers.1.rate}}.\n"

TEMPLATE_BODIES = {
    f"{CODE_PREFIX}-TIERED": TPL_TIERED_BODY,
    f"{CODE_PREFIX}-FLAT": TPL_FLAT_BODY,
    f"{CODE_PREFIX}-TIERONLY": TPL_TIERONLY_BODY,
}

COUNTED = (
    "public.fee_narratives",
    "public.fee_narrative_templates",
    "public.fee_credits",
    "public.fee_discounts",
    "public.fee_exclusions",
    "public.fee_schedule_tiers",
    "public.fee_schedules",
    "public.portfolio_precedence_household_overrides",
    "public.accounts",
    "public.households",
    "public.entities",
    "public.users",
    "public.config",
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
        print(f"fee41: {counts.get('PASS', 0)}/{total} PASS" + "".join(
            f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


async def raises(fn, *exc_types):
    """Await ``fn()`` and return the exception it raised, or None."""
    try:
        await fn()
    except exc_types as e:
        return e
    except Exception as e:  # noqa: BLE001 - a WRONG exception is a real failure
        return e
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def teardown(conn) -> None:
    """By fixture id and fixture tag, in FK order. Never a TRUNCATE.

    Schedules forked by ``update_schedule`` carry ids this script never learns,
    so schedules — and everything pointing at them — are reaped through the
    inherited ``code`` prefix rather than through a list of known ids.
    """
    await conn.execute(
        "DELETE FROM public.fee_narratives WHERE template_id IN "
        "(SELECT id FROM public.fee_narrative_templates WHERE template_code LIKE $1)",
        f"{CODE_PREFIX}%")
    await conn.execute(
        "DELETE FROM public.fee_narratives WHERE fee_schedule_id IN "
        "(SELECT id FROM public.fee_schedules WHERE code LIKE $1)",
        f"{CODE_PREFIX}%")
    await conn.execute(
        "DELETE FROM public.fee_narratives WHERE id = ANY($1::uuid[])", [OTHER_NAR])
    await conn.execute(
        "DELETE FROM public.fee_narrative_templates WHERE template_code LIKE $1",
        f"{CODE_PREFIX}%")
    await conn.execute(
        "DELETE FROM public.fee_narrative_templates WHERE id = ANY($1::uuid[])",
        [OTHER_TPL])
    for table in ("fee_credits", "fee_discounts", "fee_exclusions"):
        await conn.execute(
            f"DELETE FROM public.{table} WHERE reason LIKE $1", f"{TAG}%")
    await conn.execute(
        "DELETE FROM public.fee_schedule_tiers WHERE fee_schedule_id IN "
        "(SELECT id FROM public.fee_schedules WHERE code LIKE $1)",
        f"{CODE_PREFIX}%")
    await conn.execute(
        "DELETE FROM public.fee_schedule_tiers WHERE fee_schedule_id = ANY($1::uuid[])",
        [OTHER_SCH])
    await conn.execute(
        "DELETE FROM public.fee_schedules WHERE code LIKE $1", f"{CODE_PREFIX}%")
    await conn.execute(
        "DELETE FROM public.fee_schedules WHERE id = ANY($1::uuid[])", [OTHER_SCH])
    await conn.execute(
        "DELETE FROM public.portfolio_precedence_household_overrides "
        "WHERE household_id = ANY($1::uuid[])", HOUSEHOLDS)
    await conn.execute(
        "DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", ACCOUNTS)
    await conn.execute(
        "DELETE FROM public.households WHERE id = ANY($1::uuid[])", HOUSEHOLDS)
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", ENTITIES)
    await conn.execute(
        "DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def build_fixtures(conn) -> dict[str, str]:
    """Everything the tests read. Returns the ids assigned by the services."""
    await conn.execute(
        """INSERT INTO public.users (id, org_id, email, auth0_sub)
           VALUES ($1::uuid,$2::uuid,$3,$4)""",
        U_APPROVER, ORG, f"approver@{TAG}.local", f"auth0|{TAG}-approver")

    for eid, org in ((ENT_A, ORG), (ENT_B, ORG), (ENT_OTHER, OTHER_ORG)):
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
               VALUES ($1::uuid,$2::uuid,'individual',$3)""",
            eid, org, f"{TAG} {eid[-3:]}")

    for hid, org, name in (
        (HH_ADDEPAR, ORG, f"{TAG} Hollis"),
        (HH_DEFAULT, ORG, f"{TAG} Marchetti"),
        (HH_UNRELATED, ORG, f"{TAG} Unrelated"),
        (OTHER_HH, OTHER_ORG, f"{TAG} Other Org"),
    ):
        await conn.execute(
            "INSERT INTO public.households (id, org_id, name) "
            "VALUES ($1::uuid,$2::uuid,$3)", hid, org, name)

    for aid, org, ent, hh in ((ACC_A, ORG, ENT_A, HH_ADDEPAR),
                              (ACC_B, ORG, ENT_B, HH_DEFAULT),
                              (ACC_OTHER, OTHER_ORG, ENT_OTHER, OTHER_HH)):
        await conn.execute(
            """INSERT INTO public.accounts
                 (id, org_id, account_number_masked, account_number_hash,
                  custodian_code, registration_type, tax_status,
                  primary_entity_id, household_id, is_billable, opened_on)
               VALUES ($1::uuid,$2::uuid,$3,$4,'TEST','individual','taxable',
                       $5::uuid,$6::uuid,true,'2024-01-01')""",
            aid, org, f"***{aid[-3:]}", f"{TAG}-{aid[-3:]}", ent, hh)

    # One ORG-scoped exclusion plus a symmetric discount and credit per
    # household. Symmetric on purpose: [3] must be able to attribute the whole
    # textual difference between the two households to PRECEDENCE, and an
    # asymmetric discount would give it a second, confounding explanation.
    await conn.execute(
        """INSERT INTO public.fee_exclusions
             (org_id, scope_type, scope_id, basis_type, treatment, reason,
              effective_from)
           VALUES ($1::uuid,'ORG',NULL,'HELD_AWAY','EXCLUDE',$2,'2024-01-01')""",
        ORG, f"{TAG} held-away assets are not billed")
    for hh in (HH_ADDEPAR, HH_DEFAULT):
        await conn.execute(
            """INSERT INTO public.fee_discounts
                 (org_id, scope_type, scope_id, discount_type, value, applies_to,
                  effective_from, approved_by, reason)
               VALUES ($1::uuid,'HOUSEHOLD',$2::uuid,'PCT_OFF',10,'GROSS',
                       '2024-01-01',$3::uuid,$4)""",
            ORG, hh, U_APPROVER, f"{TAG} founding member discount")
        await conn.execute(
            """INSERT INTO public.fee_credits
                 (org_id, scope_type, scope_id, credit_source, offset_pct,
                  effective_from, reason, approved_by)
               VALUES ($1::uuid,'HOUSEHOLD',$2::uuid,'12B1',1.0,'2024-01-01',
                       $3,$4::uuid)""",
            ORG, hh, f"{TAG} 12b-1 offset", U_APPROVER)

    # fee32's household override — the ONE thing that differs between the two
    # households, and therefore the whole of [3]'s explanation.
    await PP.set_household_source_order(
        conn, ORG, household_id=HH_ADDEPAR,
        source_order=["reporting_tool_addepar"] + [
            s for s in PP.DEFAULT_SOURCE_ORDER if s != "reporting_tool_addepar"],
        reason=f"{TAG} custodian feed lags; the aggregated record is reported",
        approved_by=U_APPROVER,
    )

    ids: dict[str, str] = {}

    tiers = [
        {"tier_seq": 1, "lower_bound": D(0), "upper_bound": BAND_BREAK,
         "rate_bps": FIRST_RATE_BPS},
        {"tier_seq": 2, "lower_bound": BAND_BREAK, "upper_bound": None,
         "rate_bps": TOP_RATE_BPS},
    ]
    common = dict(
        product_type="ASSET_MANAGEMENT", billing_frequency="QUARTERLY",
        billing_timing="ARREARS", valuation_method="PERIOD_END",
        proration_method="CALENDAR_DAYS", currency="USD",
    )
    sched = await create_schedule(
        conn, ORG, code=f"{CODE_PREFIX}-TIERED", name=f"{TAG} tiered",
        rate_type="BPS", tier_method="GRADUATED",
        tiers=[dict(t) for t in tiers], created_by=U_APPROVER, **common)
    ids["tiered"] = sched["schedule"]["id"]

    flat = await create_schedule(
        conn, ORG, code=f"{CODE_PREFIX}-FLAT", name=f"{TAG} flat",
        rate_type="FLAT", created_by=U_APPROVER, **common)
    ids["flat"] = flat["schedule"]["id"]

    appr = await create_schedule(
        conn, ORG, code=f"{CODE_PREFIX}-APPR", name=f"{TAG} approved",
        rate_type="BPS", tier_method="GRADUATED",
        tiers=[dict(t) for t in tiers], created_by=U_APPROVER, **common)
    ids["approved"] = appr["schedule"]["id"]
    await submit_for_approval(conn, ORG, ids["approved"], approved_by=U_APPROVER)

    for code, body in TEMPLATE_BODIES.items():
        tid = await conn.fetchval(
            """INSERT INTO public.fee_narrative_templates
                 (org_id, template_code, body_template, version, approved_by)
               VALUES ($1::uuid,$2,$3,1,$4::uuid) RETURNING id::text""",
            ORG, code, body, U_APPROVER)
        ids[code] = tid

    # The other org's rows, for [8]. Written directly: the point is that they
    # exist and that app_service cannot see them, not how they were made.
    await conn.execute(
        """INSERT INTO public.fee_schedules
             (id, org_id, code, name, product_type, rate_type, billing_frequency,
              billing_timing, valuation_method, status)
           VALUES ($1::uuid,$2::uuid,$3,$4,'ASSET_MANAGEMENT','FLAT','QUARTERLY',
                   'ARREARS','PERIOD_END','APPROVED')""",
        OTHER_SCH, OTHER_ORG, f"{CODE_PREFIX}-OTHER", f"{TAG} other org")
    await conn.execute(
        """INSERT INTO public.fee_narrative_templates
             (id, org_id, template_code, body_template, version)
           VALUES ($1::uuid,$2::uuid,$3,$4,1)""",
        OTHER_TPL, OTHER_ORG, f"{CODE_PREFIX}-OTHER", "other org body")
    await conn.execute(
        """INSERT INTO public.fee_narratives
             (id, org_id, fee_schedule_id, household_id, template_id,
              rendered_text, input_hash)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6,$7)""",
        OTHER_NAR, OTHER_ORG, OTHER_SCH, OTHER_HH, OTHER_TPL,
        "other org narrative", "0" * 64)

    return ids


# ═══════════════════════════════════════════════════════════════════════════
# [1] Deployment shape
# ═══════════════════════════════════════════════════════════════════════════


async def test_1_catalog(conn) -> None:
    for table in ("fee_narrative_templates", "fee_narratives"):
        reg = await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
        if not R.expect(f"1a:{table}", reg is not None, "table is deployed"):
            continue
        rls = await conn.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass",
            f"public.{table}")
        R.expect(f"1b:{table}", rls is True, "RLS is enabled")

        pols = await conn.fetch(
            "SELECT polname, pg_get_expr(polqual, polrelid) AS q, "
            "       pg_get_expr(polwithcheck, polrelid) AS w "
            "FROM pg_policy WHERE polrelid = $1::regclass", f"public.{table}")
        R.expect(f"1c:{table}", len(pols) == 1,
                 "exactly one org-isolation policy", f"found {len(pols)}")
        for p in pols:
            for label, expr in (("USING", p["q"]), ("WITH CHECK", p["w"])):
                R.expect(
                    f"1d:{table}:{label}",
                    expr is not None
                    and "NULLIF(current_setting('app.current_org_id'" in expr
                    and "org_id =" in expr,
                    f"{label} scopes org_id through the NULLIF'd GUC",
                    repr(expr))

    default = await conn.fetchval(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='fee_narratives' "
        "AND column_name='adv_check_status'")
    R.expect("1e", default is not None and "UNCHECKED" in default,
             "adv_check_status defaults to UNCHECKED", repr(default))

    chk = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='public.fee_narratives'::regclass "
        "AND conname='fee_narratives_adv_check_status_check'")
    R.expect("1f", chk is not None and all(s in chk for s in FN.ADV_STATUSES),
             f"adv_check_status CHECK admits exactly {FN.ADV_STATUSES}", repr(chk))

    # The CHECK is proved by trying to violate it, not by reading its text.
    err = await raises(lambda: conn.execute(
        """INSERT INTO public.fee_narratives
             (org_id, fee_schedule_id, template_id, rendered_text, input_hash,
              adv_check_status)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'x','y','BOGUS')""",
        OTHER_ORG, OTHER_SCH, OTHER_TPL), Exception)
    R.expect("1g", err is not None and "adv_check_status" in str(err),
             "a BOGUS adv_check_status is refused by the database", repr(err))

    idx = await conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
        "AND tablename='fee_narrative_templates' "
        "AND indexname='fee_narrative_templates_code_version_uq'")
    R.expect("1h", idx is not None and "UNIQUE" in idx
             and "template_code" in idx and "version" in idx,
             "templates are unique per (org_id, template_code, version)", repr(idx))

    # The app layer refuses the same value the database would, so a caller never
    # discovers the constraint as a 500 from a failed transaction.
    err = await raises(
        lambda: FN.set_adv_check_status(conn, ORG, OTHER_NAR, "BOGUS"),
        FN.NarrativeError)
    R.expect("1i", isinstance(err, FN.NarrativeError),
             "set_adv_check_status refuses a value outside the CHECK", repr(err))


# ═══════════════════════════════════════════════════════════════════════════
# [2] Tokens resolve, or fail loudly
# ═══════════════════════════════════════════════════════════════════════════


async def test_2_tokens(conn, ids) -> str:
    rendered = await FN.render_narrative(
        conn, ORG, fee_schedule_id=ids["tiered"], household_id=HH_DEFAULT,
        template_id=ids[f"{CODE_PREFIX}-TIERED"])
    text = rendered.rendered_text

    R.expect("2a", "{{" not in text and "}}" not in text,
             "the tiered render leaves no template artifact")
    R.expect("2b", "  " not in text.replace("\n  ", "\n"),
             "the tiered render leaves no blank where a token stood")
    R.expect("2c", "100 bps" in text and "12.345678 bps" in text
             and "$1,000,000.00" in text,
             "tier tokens resolved to the ladder's real values")
    R.expect("2d", len(rendered.input_hash) == 64,
             "input_hash is a full sha256", rendered.input_hash)

    # The same template against a FLAT schedule with no ladder.
    err = await raises(lambda: FN.render_narrative(
        conn, ORG, fee_schedule_id=ids["flat"], household_id=HH_DEFAULT,
        template_id=ids[f"{CODE_PREFIX}-TIERED"]), FN.NarrativeTokenError)
    R.expect("2e", isinstance(err, FN.NarrativeTokenError),
             "the tier template against a FLAT schedule raises "
             "NarrativeTokenError", repr(err))

    # ...and the error names the token, so an operator can fix the template.
    err2 = await raises(lambda: FN.render_narrative(
        conn, ORG, fee_schedule_id=ids["flat"], household_id=HH_DEFAULT,
        template_id=ids[f"{CODE_PREFIX}-TIERONLY"]), FN.NarrativeTokenError)
    R.expect("2f", isinstance(err2, FN.NarrativeTokenError)
             and "tiers.1.band" in str(err2) and "no tier ladder" in str(err2),
             "the tier-only template names the token and the missing ladder",
             repr(err2))

    # The half that stops [2e] from being vacuous: the FLAT schedule renders
    # perfectly well against a template that fits it.
    flat_rendered = await FN.render_narrative(
        conn, ORG, fee_schedule_id=ids["flat"], household_id=HH_DEFAULT,
        template_id=ids[f"{CODE_PREFIX}-FLAT"])
    R.expect("2g", "{{" not in flat_rendered.rendered_text
             and f"{CODE_PREFIX}-FLAT" in flat_rendered.rendered_text,
             "the FLAT schedule renders its OWN template cleanly — the refusal "
             "in [2e] is token-specific, not a blanket failure on FLAT")

    # A schedule column that is genuinely NULL must also refuse, rather than
    # inventing a zero. minimum_fee is NULL on every fixture schedule.
    inputs = await FN.collect_inputs(
        conn, ORG, fee_schedule_id=ids["tiered"], household_id=HH_DEFAULT,
        template={"id": ids[f"{CODE_PREFIX}-TIERED"],
                  "template_code": f"{CODE_PREFIX}-TIERED", "version": 1,
                  "body_template": "x"})
    resolver = FN.TokenResolver(inputs)
    try:
        resolver.resolve("schedule.minimum_fee")
        R.bad("2h", "a NULL minimum_fee must not resolve")
    except FN.NarrativeTokenError as e:
        R.expect("2h", "minimum_fee" in str(e) and "NULL" in str(e),
                 "a NULL schedule column refuses rather than rendering $0.00",
                 repr(e))

    return text


# ═══════════════════════════════════════════════════════════════════════════
# [3] Two households, one schedule, genuinely different language
# ═══════════════════════════════════════════════════════════════════════════


async def test_3_precedence_language(conn, ids) -> None:
    # Resolve each household's order INDEPENDENTLY, through fee32's own
    # function, before rendering anything. If these two agree there is nothing
    # for the narrative to differ about and the rest of this test is theatre.
    so_a = await PP.resolve_source_order_for_household(conn, ORG, HH_ADDEPAR)
    so_d = await PP.resolve_source_order_for_household(conn, ORG, HH_DEFAULT)
    R.expect("3a", so_a.origin == PP.ORIGIN_HOUSEHOLD
             and so_d.origin != PP.ORIGIN_HOUSEHOLD,
             "the two households resolve through different precedence origins",
             f"{so_a.origin} vs {so_d.origin}")
    R.expect("3b", so_a.order[0] != so_d.order[0],
             "the two households' most-trusted source genuinely differs",
             f"{so_a.order[0]} vs {so_d.order[0]}")

    # Labels read straight from config — NOT through the module under test — so
    # a bug in load_vocabulary cannot make this test agree with itself.
    async def label(domain, value):
        return await conn.fetchval(
            "SELECT config_value FROM public.config "
            "WHERE org_id = $1::uuid AND config_key = $2",
            ORG, FN.vocab_config_key(domain, value))

    lbl_a = await label("source_system", so_a.order[0])
    lbl_d = await label("source_system", so_d.order[0])
    org_a = await label("precedence_origin", so_a.origin)
    org_d = await label("precedence_origin", so_d.origin)
    R.expect("3c", lbl_a and lbl_d and lbl_a != lbl_d,
             "config carries a distinct human label for each source",
             f"{lbl_a!r} vs {lbl_d!r}")

    ren_a = await FN.render_narrative(
        conn, ORG, fee_schedule_id=ids["tiered"], household_id=HH_ADDEPAR,
        template_id=ids[f"{CODE_PREFIX}-TIERED"])
    ren_d = await FN.render_narrative(
        conn, ORG, fee_schedule_id=ids["tiered"], household_id=HH_DEFAULT,
        template_id=ids[f"{CODE_PREFIX}-TIERED"])

    R.expect("3d", lbl_a in ren_a.rendered_text and lbl_a not in ren_d.rendered_text,
             "the override household's text names ITS source and only its own")
    R.expect("3e", lbl_d in ren_d.rendered_text and lbl_d not in ren_a.rendered_text,
             "the default household's text names ITS source and only its own")
    R.expect("3f", org_a in ren_a.rendered_text and org_a != org_d
             and org_d in ren_d.rendered_text,
             "each text states a different provenance for that valuation policy",
             f"{org_a!r} vs {org_d!r}")

    # The load-bearing half: the difference must be CONFINED to the precedence
    # and household lines. A copy-paste with one word swapped would pass every
    # check above; it would not survive a line-by-line diff of the rest.
    lines_a = ren_a.rendered_text.splitlines()
    lines_d = ren_d.rendered_text.splitlines()
    R.expect("3g", len(lines_a) == len(lines_d),
             "both renders have the same shape")
    moved = [i for i, (a, d) in enumerate(zip(lines_a, lines_d)) if a != d]
    allowed = [i for i, a in enumerate(lines_a)
               if lbl_a in a or org_a in a or "household." in a
               or f"{TAG} Hollis" in a]
    R.expect("3h", moved and set(moved) <= set(allowed),
             "every line that differs is a precedence or household line; every "
             "schedule and tier line is byte-identical",
             f"moved={moved} allowed={allowed}")

    tier_lines_a = [ln for ln in lines_a if "bps" in ln or "$" in ln]
    R.expect("3i", tier_lines_a and all(ln in lines_d for ln in tier_lines_a),
             "every line carrying a rate or an amount is identical across the "
             "two households — the difference is language, not arithmetic")

    R.expect("3j", ren_a.input_hash != ren_d.input_hash,
             "the two narratives hash differently")
    pay_a = FN.hash_payload(ren_a.inputs)["precedence"]
    pay_d = FN.hash_payload(ren_d.inputs)["precedence"]
    R.expect("3k", pay_a != pay_d and pay_a["origin"] != pay_d["origin"],
             "the precedence set is inside the hashed payload and differs",
             f"{pay_a} vs {pay_d}")

    # Saved, so [8] has this org's rows to be visible and the ADV default has a
    # real row to be observed on.
    nid = await FN.save_narrative(conn, ORG, ren_a)
    status = await conn.fetchval(
        "SELECT adv_check_status FROM public.fee_narratives WHERE id = $1::uuid",
        nid)
    R.expect("3l", status == FN.ADV_UNCHECKED,
             "a freshly saved narrative is adv_check_status=UNCHECKED", status)


# ═══════════════════════════════════════════════════════════════════════════
# [4] Staleness from the schedule's own state
# ═══════════════════════════════════════════════════════════════════════════


async def test_4_schedule_staleness(conn, ids) -> dict[str, str]:
    saved: dict[str, str] = {}

    # A narrative against the DRAFT schedule.
    ren_draft = await FN.render_narrative(
        conn, ORG, fee_schedule_id=ids["tiered"], household_id=HH_DEFAULT,
        template_id=ids[f"{CODE_PREFIX}-TIERED"])
    saved["draft"] = await FN.save_narrative(conn, ORG, ren_draft)

    # Narratives against the APPROVED schedule, one per household. [5] reuses
    # these; here they only have to survive unrelated activity.
    for key, hh in (("appr_default", HH_DEFAULT), ("appr_addepar", HH_ADDEPAR)):
        ren = await FN.render_narrative(
            conn, ORG, fee_schedule_id=ids["approved"], household_id=hh,
            template_id=ids[f"{CODE_PREFIX}-TIERED"])
        saved[key] = await FN.save_narrative(conn, ORG, ren)

    # Nothing has changed yet, so nothing may be stale. Without this the test
    # below cannot tell "went stale" from "was born stale".
    before = await FN.recompute_staleness(conn, ORG, narrative_ids=list(saved.values()))
    R.expect("4a", all(not r.is_stale for r in before),
             "no narrative is stale before anything changes",
             str([(r.narrative_id[-4:], r.is_stale) for r in before]))

    # DRAFT edit — fee34 updates in place, so the id does not move.
    outcome = await update_schedule(
        conn, ORG, ids["tiered"], billing_frequency="MONTHLY",
        created_by=U_APPROVER)
    after_edit = await load_schedule(conn, ORG, ids["tiered"])
    R.expect("4b", after_edit["id"] == ids["tiered"]
             and after_edit["billing_frequency"] == "MONTHLY",
             "a DRAFT edit updated the schedule IN PLACE, same id",
             f"{outcome}")

    res = await FN.recompute_staleness(conn, ORG, fee_schedule_id=ids["tiered"])
    R.expect("4c", res and all(r.is_stale for r in res),
             "every narrative against the edited DRAFT is now stale")
    persisted = await conn.fetchval(
        "SELECT is_stale FROM public.fee_narratives WHERE id = $1::uuid",
        saved["draft"])
    R.expect("4d", persisted is True,
             "is_stale=true actually persisted, read back independently",
             repr(persisted))

    # Unrelated activity, three kinds, none of which touches the APPROVED
    # schedule or either household's precedence.
    await PP.set_household_source_order(
        conn, ORG, household_id=HH_UNRELATED,
        source_order=["altruist"] + [s for s in PP.DEFAULT_SOURCE_ORDER
                                     if s != "altruist"],
        reason=f"{TAG} unrelated household", approved_by=U_APPROVER)
    await update_schedule(conn, ORG, ids["flat"], name=f"{TAG} flat renamed",
                          created_by=U_APPROVER)
    fork = await update_schedule(
        conn, ORG, ids["approved"], billing_timing="ADVANCE",
        created_by=U_APPROVER)
    forked_id = fork.schedule_id
    R.expect("4e", forked_id != ids["approved"] and fork.versioned is True,
             "editing the APPROVED schedule forked a NEW version at a new id, "
             "leaving the approved row untouched",
             f"{forked_id} vs {ids['approved']} versioned={fork.versioned}")
    still = await load_schedule(conn, ORG, ids["approved"])
    R.expect("4f", still["status"] == "APPROVED" and still["billing_timing"] == "ARREARS",
             "the APPROVED row itself is unchanged after the fork")

    res = await FN.recompute_staleness(conn, ORG, fee_schedule_id=ids["approved"])
    R.expect("4g", res and not any(r.is_stale for r in res),
             "narratives against the untouched APPROVED version did NOT go "
             "stale on unrelated activity, including the version fork",
             str([(r.narrative_id[-4:], r.is_stale, r.error) for r in res]))
    persisted = await conn.fetchval(
        "SELECT bool_or(is_stale) FROM public.fee_narratives WHERE id = ANY($1::uuid[])",
        [saved["appr_default"], saved["appr_addepar"]])
    R.expect("4h", persisted is False,
             "is_stale=false persisted for both approved-version narratives",
             repr(persisted))

    return saved


# ═══════════════════════════════════════════════════════════════════════════
# [5] Staleness from the household's precedence, independently
# ═══════════════════════════════════════════════════════════════════════════


async def test_5_precedence_staleness(conn, ids, saved) -> None:
    before = await load_schedule(conn, ORG, ids["approved"])

    await PP.set_household_source_order(
        conn, ORG, household_id=HH_DEFAULT,
        source_order=["chancery"] + [s for s in PP.DEFAULT_SOURCE_ORDER
                                     if s != "chancery"],
        reason=f"{TAG} household moved to document-of-record valuation",
        approved_by=U_APPROVER)

    after = await load_schedule(conn, ORG, ids["approved"])
    R.expect("5a", {k: after[k] for k in FN._HASHED_SCHEDULE_COLUMNS}
             == {k: before[k] for k in FN._HASHED_SCHEDULE_COLUMNS},
             "the schedule itself did not change — only the household override "
             "moved")

    res = await FN.recompute_staleness(conn, ORG, fee_schedule_id=ids["approved"])
    by_id = {r.narrative_id: r for r in res}
    R.expect("5b", by_id[saved["appr_default"]].is_stale is True,
             "the household whose precedence changed has a stale narrative")
    R.expect("5c", by_id[saved["appr_addepar"]].is_stale is False,
             "the OTHER household, on the SAME schedule, did not go stale — "
             "staleness is per-household, not per-schedule")

    rows = await conn.fetch(
        "SELECT id::text AS id, is_stale FROM public.fee_narratives "
        "WHERE id = ANY($1::uuid[])",
        [saved["appr_default"], saved["appr_addepar"]])
    got = {r["id"]: r["is_stale"] for r in rows}
    R.expect("5d", got.get(saved["appr_default"]) is True
             and got.get(saved["appr_addepar"]) is False,
             "both outcomes persisted, read back independently", str(got))

    # Restoring the override un-stales it. That follows from re-hashing rather
    # than from a dirty flag, and proves the mechanism is an equality check on
    # real inputs, not a one-way marker.
    cleared = await PP.clear_household_source_order(
        conn, ORG, household_id=HH_DEFAULT)
    R.expect("5e", cleared is True,
             "the override this test added was really retired")
    res = await FN.recompute_staleness(
        conn, ORG, narrative_ids=[saved["appr_default"]])
    R.expect("5f", res and res[0].is_stale is False,
             "restoring the precedence set un-stales the narrative — the check "
             "is an equality on real inputs, not a one-way flag",
             str([(r.is_stale, r.current_hash == r.stored_hash) for r in res]))


# ═══════════════════════════════════════════════════════════════════════════
# [6] The numeric-invariance gate
# ═══════════════════════════════════════════════════════════════════════════


def make_transport(payload):
    """A stand-in for ``call_claude_text`` returning exactly ``payload``.

    It ignores the system prompt entirely — which is the point. The gate has to
    hold against a model that never read the instruction, and this transport is
    exactly that model.
    """
    async def transport(system, messages):
        return payload
    return transport


async def test_6_polish_gate(det_text: str) -> None:
    nums, terms = FN.extract_invariants(det_text)
    R.expect("6a", len(nums) >= 4 and len(terms) >= 1,
             "the deterministic text carries a non-empty set of numbers and "
             "defined terms for the gate to compare",
             f"{len(nums)} numbers, {len(terms)} terms")

    # ── the accepting case ──────────────────────────────────────────────
    good = det_text.replace("carries a rate of", "is charged at the rate of")
    good = good.replace("The adviser charges", "The adviser instead charges")
    R.expect("6b", good != det_text,
             "the prose-only candidate genuinely differs from the deterministic "
             "text — an identity 'polish' would make acceptance vacuous")

    outcome = await FN.polish_narrative(det_text, transport=make_transport(good))
    # ``polish_narrative`` strips the model's surrounding whitespace before
    # gating. Compared against the stripped candidate deliberately: trailing
    # newlines are not a term of the agreement, and asserting on the raw string
    # would be asserting on the transport, not on the gate.
    R.expect("6c", outcome.accepted and outcome.text == good.strip(),
             "a polish that preserves every number and defined term is ACCEPTED",
             f"accepted={outcome.accepted} reason={outcome.reason}")

    # ── the rejecting case: a rounded rate ──────────────────────────────
    bad = good.replace("12.345678 bps", "12.35 bps")
    R.expect("6d", bad != good and "12.35 bps" in bad,
             "the mismatching candidate really does round the rate")

    outcome = await FN.polish_narrative(det_text, transport=make_transport(bad))
    R.expect("6e", outcome.accepted is False,
             "a polish that rounds a rate is REJECTED", repr(outcome.reason))
    R.expect("6f", outcome.text == det_text,
             "the REJECTED polish returns the deterministic text, not the "
             "altered one")
    R.expect("6g", "12.35" not in outcome.text and "12.345678" in outcome.text,
             "the altered figure never reaches the caller")
    R.expect("6h", "12.345678bps" in outcome.number_diff
             and "12.35bps" in outcome.number_diff,
             "the divergence is reported field by field, not as a bare refusal",
             str(outcome.number_diff))

    # ── a DROPPED number ────────────────────────────────────────────────
    dropped = good.replace("$1,000,000.00", "the first band's ceiling")
    outcome = await FN.polish_narrative(det_text, transport=make_transport(dropped))
    R.expect("6i", outcome.accepted is False and outcome.text == det_text,
             "a polish that DROPS a dollar amount is rejected", repr(outcome.reason))

    # ── an ADDED number ─────────────────────────────────────────────────
    added = good.replace("The bands are:", "The bands are (3 of them):")
    outcome = await FN.polish_narrative(det_text, transport=make_transport(added))
    R.expect("6j", outcome.accepted is False and outcome.text == det_text,
             "a polish that ADDS a number is rejected", repr(outcome.reason))

    # ── an altered defined term ─────────────────────────────────────────
    swapped = good.replace("GRADUATED", "graduated") if "GRADUATED" in good else None
    if swapped is None:
        # The rendered text uses the config label, not the enum, so synthesise
        # the case on a text that does carry one rather than skipping it.
        base = det_text + '\nThe method is GRADUATED.\n'
        swapped_txt = base.replace("GRADUATED", "CLIFF")
        outcome = FN.check_invariance(base, swapped_txt)
        R.expect("6k", outcome.accepted is False and outcome.text == base,
                 "a polish that alters a capitalised defined term is rejected",
                 repr(outcome.reason))
    else:
        outcome = FN.check_invariance(good, swapped)
        R.expect("6k", outcome.accepted is False,
                 "a polish that alters a capitalised defined term is rejected",
                 repr(outcome.reason))

    # ── no model at all ─────────────────────────────────────────────────
    outcome = await FN.polish_narrative(det_text, transport=make_transport(""))
    R.expect("6l", outcome.accepted is False and outcome.text == det_text,
             "a model that returns nothing yields the deterministic text, "
             "unpolished — the gate holds when the proxy is down")

    R.find("6m", "the gate is enforced arithmetically in polish_narrative, not "
                 "by POLISH_SYSTEM's instruction: every case above is driven "
                 "through a transport that ignores the system prompt entirely")


# ═══════════════════════════════════════════════════════════════════════════
# [7] Decimal precision survives into the text
# ═══════════════════════════════════════════════════════════════════════════


async def test_7_decimal(conn, ids, det_text: str) -> None:
    stored = await conn.fetchval(
        "SELECT t.rate_bps FROM public.fee_schedule_tiers t "
        "WHERE t.fee_schedule_id = $1::uuid AND t.tier_seq = 2", ids["tiered"])
    R.expect("7a", isinstance(stored, Decimal) and stored == TOP_RATE_BPS,
             "the rate comes back from numeric(12,6) as an exact Decimal",
             f"{type(stored).__name__} {stored!r}")

    R.expect("7b", FN.format_bps(stored) == "12.345678 bps"
             and FN.format_bps(stored) in det_text,
             "all six decimal places reach the rendered text",
             FN.format_bps(stored))
    R.expect("7c", FN.format_pct(stored) == "0.12345678%"
             and FN.format_pct(stored) in det_text,
             "the percentage conversion is Decimal-exact, no float artifact",
             FN.format_pct(stored))
    R.expect("7d", not any(bad in det_text for bad in
                           ("12.35 ", "12.3457", "0.1235%", "0.12%", "12.345678000")),
             "no rounded or float-padded spelling of the rate appears anywhere")

    first = await conn.fetchval(
        "SELECT t.rate_bps FROM public.fee_schedule_tiers t "
        "WHERE t.fee_schedule_id = $1::uuid AND t.tier_seq = 1", ids["tiered"])
    R.expect("7e", FN.format_bps(first) == "100 bps"
             and FN.format_pct(first) == "1.00%",
             "a whole rate renders as '100 bps' / '1.00%', never '1E+2' or '1%'",
             f"{FN.format_bps(first)} / {FN.format_pct(first)}")
    R.expect("7f", "1.00%" in det_text and "$1,000,000.00" in det_text,
             "the rate and the band boundary are both in the final text at "
             "full precision")

    # The refusal is what keeps the above true. A float that reached a formatter
    # would already have lost digits, so it is rejected rather than coerced.
    for bad_value in (0.12345678, 1000000.0):
        try:
            FN.format_bps(bad_value)
            R.bad("7g", f"a float {bad_value} must not be formatted")
            break
        except FN.NarrativeError:
            pass
    else:
        R.ok("7g", "a float is REFUSED by the formatters, not silently coerced")


# ═══════════════════════════════════════════════════════════════════════════
# [8] Cross-org isolation under app_service
# ═══════════════════════════════════════════════════════════════════════════


async def test_8_isolation() -> None:
    dsn, provenance = await app_service_dsn()
    if not dsn:
        R.blocked("8", f"no working app_service DSN: {provenance}")
        return
    conn = await connect(dsn)
    try:
        who = await conn.fetchval("SELECT current_user")
        bypass = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        if not R.expect("8a", bypass is False,
                        f"the test role {who!r} does NOT bypass RLS — without "
                        f"this every check below is vacuous", repr(bypass)):
            return
        super_admin = await conn.fetchval(
            "SELECT current_setting('app.is_super_admin', true)")
        R.expect("8b", super_admin in (None, "", "false"),
                 "the test connection is not super-admin elevated",
                 repr(super_admin))

        for table, other_id in (("fee_narrative_templates", OTHER_TPL),
                                ("fee_narratives", OTHER_NAR)):
            # Empty GUC reads nothing at all — the NULLIF guard doing its job.
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_org_id','',true)")
                n = await conn.fetchval(f"SELECT count(*) FROM public.{table}")
                R.expect(f"8c:{table}", n == 0,
                         "an empty org GUC reads zero rows", f"saw {n}")

            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true)", ORG)
                leaked = await conn.fetchval(
                    f"SELECT count(*) FROM public.{table} WHERE id = $1::uuid",
                    other_id)
                R.expect(f"8d:{table}", leaked == 0,
                         "the other org's row is invisible under this org's GUC",
                         f"saw {leaked}")
                mine = await conn.fetchval(
                    f"SELECT count(*) FROM public.{table} WHERE org_id = $1::uuid",
                    ORG)
                R.expect(f"8e:{table}", mine > 0,
                         "this org's own rows ARE visible — the check above is "
                         "not passing merely because nothing is readable",
                         f"saw {mine}")
                cross = await conn.fetchval(
                    f"SELECT count(*) FROM public.{table} WHERE org_id = $1::uuid",
                    OTHER_ORG)
                R.expect(f"8f:{table}", cross == 0,
                         "no row of the other org is readable by any predicate",
                         f"saw {cross}")

        # A cross-org WRITE must be refused by the policy's WITH CHECK.
        err = None
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true)", ORG)
                await conn.execute(
                    """INSERT INTO public.fee_narrative_templates
                         (org_id, template_code, body_template, version)
                       VALUES ($1::uuid,$2,'x',99)""",
                    OTHER_ORG, f"{CODE_PREFIX}-LEAK")
        except Exception as e:  # noqa: BLE001
            err = e
        R.expect("8g", err is not None and "policy" in str(err).lower(),
                 "writing a row for another org is refused by the RLS policy, "
                 "not by application code", repr(err))
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    dsn, provenance = await admin_dsn()
    if not dsn:
        print(f"FAIL: no working admin DSN: {provenance}")
        return 1
    print(f"admin: {provenance}\n")

    conn = await connect(dsn)
    before = after = None
    try:
        await teardown(conn)
        before = await counts(conn)
        ids = await build_fixtures(conn)

        await test_1_catalog(conn)
        det_text = await test_2_tokens(conn, ids)
        await test_3_precedence_language(conn, ids)
        saved = await test_4_schedule_staleness(conn, ids)
        await test_5_precedence_staleness(conn, ids, saved)
        await test_6_polish_gate(det_text)
        await test_7_decimal(conn, ids, det_text)
        await test_8_isolation()

        R.find("ADV", "adv_check_status is wired (column, CHECK, UNCHECKED "
                      "default, app-layer validation, a setter) and stays "
                      "UNCHECKED. NO Form ADV Part 2A source exists in this "
                      "database — no table, no column, no ingest — so no "
                      "comparison is performed and none is faked.")
        R.find("CHANCERY", "attaching a rendered narrative to a signed Chancery "
                           "document is out of scope for fee41 and is left as a "
                           "TODO in services/fee_narratives.py, not stubbed.")
    except Exception:
        R.bad("harness", "the run raised", traceback.format_exc())
    finally:
        try:
            await teardown(conn)
            after = await counts(conn)
        except Exception:
            R.bad("teardown", "teardown raised", traceback.format_exc())
        finally:
            await conn.close()

    R.summary()

    if before and after:
        drift = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
        if drift:
            print(f"\n[FAIL] teardown  row counts drifted: {drift}")
            return 1
        print(f"\n[PASS] teardown  all {len(COUNTED)} counted tables returned to "
              f"their pre-test row counts")
    else:
        print("\n[FAIL] teardown  counts unavailable")
        return 1

    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
