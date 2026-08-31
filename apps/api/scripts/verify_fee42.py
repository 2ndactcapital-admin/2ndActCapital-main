"""Sprint fee42 verification — SPV fee terms, step-downs, side letters, offsets.

Pass/fail only, no prompts. Run:

    python3 scripts/verify_fee42.py

Every table this script writes to is counted before the first insert and again
after the last delete; a difference of even one row fails the run, reported
AFTER the tests so a teardown bug never masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[1] proves the uniqueness BEHAVIOURALLY, in both directions.** Reading
  ``NULLS NOT DISTINCT`` out of ``pg_indexes`` proves the text of the index, not
  its effect. So two whole-fund rows (``class_label`` NULL) are actually
  inserted and the second must be refused — under Postgres' DEFAULT
  ``NULLS DISTINCT`` that insert SUCCEEDS, which is exactly the silent
  two-contradictory-term-sheets bug. And because "refuses everything" would also
  pass that, two rows with DIFFERENT class labels must be ACCEPTED, and an
  archived row must not block its own replacement.

* **[2] runs the REAL backfill over the REAL population, not a simulation.**
  The production SPV is already migrated (``seed_fee42_backfill.py``), so this
  run exercises the idempotent path on it — SKIPPED_EXISTS, no write — while the
  fixtures exercise CREATED, SKIPPED_INACTIVE and SKIPPED_NEEDS_HURDLE. The
  skips are proved by the ABSENCE of a terms row, not by the returned label:
  a decision object saying "skipped" while the row was written anyway is the
  failure this catches.

* **[3] pins both boundaries at the exact day, and the day either side.**
  Every fee off-by-one is one of two: an anniversary billed to the wrong period,
  or a term limit that stops a day early or late. Both are checked at
  ``boundary − 1``, ``boundary`` and ``boundary + 1`` rather than "somewhere
  before" and "somewhere after". The leap-year case is proved against the naive
  ``n × 365`` form it replaces, showing the two genuinely disagree — otherwise
  "we use calendar anniversaries" is an unfalsifiable claim.

* **[4] proves a partial override is PARTIAL by diffing every other field.**
  It is not enough that the overridden field changed. The resolved term set is
  compared field by field against the base, and the set of fields that MOVED
  must equal exactly the set of keys the side letter carried — every one of the
  other economic fields must be byte-identical. A whole-row replacement passes
  a "the rate changed" check and fails this one. An explicit ``null`` in the
  override is separately proved to CLEAR a field, because absent and null are
  deliberately different and only one of them means "waived".

* **[5] proves both layers independently.** The database CHECK is exercised by
  an INSERT that bypasses the service entirely, and the application error is
  exercised through ``create_terms``. Neither substitutes for the other. The
  app-layer refusal is also proved to have written NOTHING, and a positive
  control with a real ``hurdle_type`` must succeed — otherwise "it refused"
  would pass for a function that refuses every write.

* **[6] proves the offset connects by calling FEE36's OWN resolver.** The
  credit row this sprint writes is handed to
  ``fee_run_inputs.resolve_credit_basis`` and must come back with a real amount
  and the source string fee36 itself publishes. Asserting the row exists proves
  only that an INSERT worked. The negative half matters as much: an unposted
  (``draft``) management-fee call must yield ``CreditBasisUnavailableError``,
  because crediting against a fee nobody has been charged gives back money that
  was never taken.

* **[7] hashes the whole ``spvs`` table, not just the two columns.** Additive-
  first is claimed about ``mgmt_fee_pct``/``carry_pct``, so those are compared
  value by value — but the full-row comparison catches this sprint touching
  anything else on the table as well. The existing reader (``routers/spv.py``'s
  own ``SPV_SELECT``) is then executed verbatim, because "the columns still hold
  the same values" and "the code that reads them still works" are two claims.

* **[8] runs on app_service, whose ``rolbypassrls`` is asserted False FIRST.**
  Without that assertion every isolation check below it proves nothing. Note
  what [8e] does NOT claim: ``_OrgWrite`` takes the org GUC FROM its argument,
  so RLS cannot catch a caller who passes the wrong ``org_id``. The guard that
  actually holds there is ``create_terms``' own ``spv_id ∈ org`` lookup, and it
  is tested as such rather than dressed up as an RLS proof.

* Teardown is by fixture id and fixture tag, in FK order, never a TRUNCATE. The
  terms rows written by ``create_terms`` get ids this script never sees, so they
  are reaped by their fixture ``spv_id`` — which by construction excludes the
  production SPV's real, migrated row.
"""

from __future__ import annotations

import asyncio
import glob
import json
import pathlib
import sys
import traceback
from datetime import date, timedelta
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

from services import spv_fee_terms as SFT  # noqa: E402
from services.fee_run_inputs import (  # noqa: E402
    CreditBasisUnavailableError,
    resolve_credit_basis,
)

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "fee42verify"

U_APPROVER = "99000000-0000-0000-0000-0000fee42001"
USERS = [U_APPROVER]

DEAL_MAIN = "99000000-0000-0000-0000-0000fee42011"
DEAL_FORMING = "99000000-0000-0000-0000-0000fee42012"
DEAL_CARRY = "99000000-0000-0000-0000-0000fee42013"
DEAL_OTHER = "99000000-0000-0000-0000-0000fee42014"
DEALS = [DEAL_MAIN, DEAL_FORMING, DEAL_CARRY, DEAL_OTHER]

E_PARTIAL = "99000000-0000-0000-0000-0000fee42021"   # side letter: 2 keys only
E_OFFSET = "99000000-0000-0000-0000-0000fee42022"    # side letter: the offset
E_PLAIN = "99000000-0000-0000-0000-0000fee42023"     # no side letter at all
E_DUPE = "99000000-0000-0000-0000-0000fee42024"      # two overlapping letters
E_OTHER = "99000000-0000-0000-0000-0000fee42025"     # in OTHER_ORG
ENTITIES_MAIN = [E_PARTIAL, E_OFFSET, E_PLAIN, E_DUPE]

HH = "99000000-0000-0000-0000-0000fee42031"
ACC = "99000000-0000-0000-0000-0000fee42032"

SPV_MAIN = "99000000-0000-0000-0000-0000fee42041"
SPV_FORMING = "99000000-0000-0000-0000-0000fee42042"
SPV_CARRY = "99000000-0000-0000-0000-0000fee42043"
SPV_OTHER = "99000000-0000-0000-0000-0000fee42044"
SPVS_MAIN = [SPV_MAIN, SPV_FORMING, SPV_CARRY]
SPVS_ALL = SPVS_MAIN + [SPV_OTHER]

SUB_OFFSET = "99000000-0000-0000-0000-0000fee42051"
TXN_POSTED = "99000000-0000-0000-0000-0000fee42061"
TXN_DRAFT = "99000000-0000-0000-0000-0000fee42062"
ALLOC_POSTED = "99000000-0000-0000-0000-0000fee42071"
ALLOC_DRAFT = "99000000-0000-0000-0000-0000fee42072"

SL_PARTIAL = "99000000-0000-0000-0000-0000fee42081"
SL_OFFSET = "99000000-0000-0000-0000-0000fee42082"
SL_DUPE_A = "99000000-0000-0000-0000-0000fee42083"
SL_DUPE_B = "99000000-0000-0000-0000-0000fee42084"
SL_OTHER = "99000000-0000-0000-0000-0000fee42085"
SIDE_LETTERS = [SL_PARTIAL, SL_OFFSET, SL_DUPE_A, SL_DUPE_B, SL_OTHER]

TERMS_OTHER = "99000000-0000-0000-0000-0000fee42091"   # a terms row in OTHER_ORG

#: The base whole-fund terms. Deliberately sets EVERY economic field to a
#: distinct, recognisable value, so [4]'s field-by-field diff has thirteen
#: non-overridden fields that must not move — not two.
BASE_TERMS = {
    "mgmt_fee_pct": D("0.02"),
    "mgmt_fee_basis": "COMMITTED",
    "mgmt_fee_frequency": "QUARTERLY",
    "mgmt_fee_term_years": D("10"),
    "mgmt_fee_step_down": [{"after_year": 3, "pct": "0.015"},
                           {"after_year": 6, "pct": "0.0125"}],
    "organizational_cost_cap": D("250000"),
    "admin_fee_flat": D("15000"),
    "placement_fee_pct": D("0.02"),
    "carry_pct": D("0.20"),
    "hurdle_pct": D("0.08"),
    "hurdle_type": "SOFT",
    "catchup_pct": D("1.00"),
    "carry_basis": "WHOLE_FUND",
    "clawback_applies": True,
    "offsets_advisory_fee": False,
}

#: The fund's inception. Feb 29 on purpose: it is the one date where calendar
#: anniversaries and n×365 days genuinely disagree, so [3d] can prove which one
#: is in use rather than assert it.
INCEPTION = date(2020, 2, 29)

COUNTED = (
    "public.spv_fee_terms",
    "public.spv_fee_side_letters",
    "public.fee_credits",
    "public.spv_transaction_allocations",
    "public.spv_transactions",
    "public.spv_subscriptions",
    "public.spvs",
    "public.accounts",
    "public.households",
    "public.entities",
    "public.deals",
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
        print(f"fee42: {counts.get('PASS', 0)}/{total} PASS" + "".join(
            f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


async def scoped(conn, org_id: str):
    """Raise the org GUC on ``conn`` for the rest of its transaction."""
    await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org_id)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def teardown(conn) -> None:
    """By fixture id and fixture tag, in FK order. Never a TRUNCATE."""
    await conn.execute(
        "DELETE FROM public.fee_credits WHERE reason LIKE $1", f"%{TAG}%")
    await conn.execute(
        "DELETE FROM public.spv_transaction_allocations WHERE id = ANY($1::uuid[])",
        [ALLOC_POSTED, ALLOC_DRAFT])
    await conn.execute(
        "DELETE FROM public.spv_transactions WHERE id = ANY($1::uuid[])",
        [TXN_POSTED, TXN_DRAFT])
    await conn.execute(
        "DELETE FROM public.spv_fee_side_letters WHERE id = ANY($1::uuid[])",
        SIDE_LETTERS)
    # Rows written by create_terms carry ids this script never sees. Reaped by
    # fixture spv_id, which by construction cannot match the production SPV's
    # real migrated row.
    await conn.execute(
        "DELETE FROM public.spv_fee_terms WHERE spv_id = ANY($1::uuid[])", SPVS_ALL)
    await conn.execute(
        "DELETE FROM public.spv_fee_terms WHERE id = $1::uuid", TERMS_OTHER)
    await conn.execute(
        "DELETE FROM public.spv_subscriptions WHERE spv_id = ANY($1::uuid[])", SPVS_ALL)
    await conn.execute(
        "DELETE FROM public.spv_status_history WHERE spv_id = ANY($1::uuid[])", SPVS_ALL)
    await conn.execute("DELETE FROM public.spvs WHERE id = ANY($1::uuid[])", SPVS_ALL)
    await conn.execute("DELETE FROM public.accounts WHERE id = $1::uuid", ACC)
    await conn.execute("DELETE FROM public.households WHERE id = $1::uuid", HH)
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])",
        ENTITIES_MAIN + [E_OTHER])
    await conn.execute("DELETE FROM public.deals WHERE id = ANY($1::uuid[])", DEALS)
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def build_fixtures(conn) -> None:
    await conn.execute(
        """INSERT INTO public.users (id, org_id, email, auth0_sub)
           VALUES ($1::uuid, $2::uuid, $3, $4)""",
        U_APPROVER, ORG, f"approver@{TAG}.local", f"auth0|{TAG}-approver")

    for did, org, nm in ((DEAL_MAIN, ORG, "main"), (DEAL_FORMING, ORG, "forming"),
                         (DEAL_CARRY, ORG, "carry"), (DEAL_OTHER, OTHER_ORG, "other")):
        await conn.execute(
            "INSERT INTO public.deals (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
            did, org, f"{TAG} deal {nm}")

    for eid, org, nm in ((E_PARTIAL, ORG, "partial"), (E_OFFSET, ORG, "offset"),
                         (E_PLAIN, ORG, "plain"), (E_DUPE, ORG, "dupe"),
                         (E_OTHER, OTHER_ORG, "otherorg")):
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
               VALUES ($1::uuid,$2::uuid,'individual',$3)""",
            eid, org, f"{TAG} entity {nm}")

    await conn.execute(
        "INSERT INTO public.households (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
        HH, ORG, f"{TAG} household")

    # The account's primary_entity_id IS E_OFFSET. fee36 resolves an
    # SPV_MGMT_FEE_OFFSET basis for the OWNING ENTITY of the account being
    # billed, not for the credit's scope_id — [6c] depends on that join.
    await conn.execute(
        """INSERT INTO public.accounts
             (id, org_id, account_number_masked, account_number_hash, custodian_code,
              registration_type, tax_status, primary_entity_id, household_id,
              is_billable, opened_on)
           VALUES ($1::uuid,$2::uuid,$3,$4,'TEST','individual','taxable',
                   $5::uuid,$6::uuid,true,'2024-01-01')""",
        ACC, ORG, "***42", f"{TAG}-acc", E_OFFSET, HH)

    # spvs_deal_class_label_uniq is UNIQUE (deal_id, class_label), so each
    # fixture SPV gets its own deal rather than sharing one.
    for sid, org, did, status, mgmt, carry, nm in (
        (SPV_MAIN, ORG, DEAL_MAIN, "closed", D("2.0"), None, "main"),
        (SPV_FORMING, ORG, DEAL_FORMING, "forming", D("3.0"), None, "forming"),
        (SPV_CARRY, ORG, DEAL_CARRY, "closed", D("2.0"), D("20.0"), "carry"),
        (SPV_OTHER, OTHER_ORG, DEAL_OTHER, "closed", D("1.0"), None, "otherorg"),
    ):
        await conn.execute(
            """INSERT INTO public.spvs
                 (id, org_id, deal_id, name, spv_status, mgmt_fee_pct, carry_pct)
               VALUES ($1::uuid,$2::uuid,$3::uuid,$4,$5,$6::numeric,$7::numeric)""",
            sid, org, did, f"{TAG} spv {nm}", status, mgmt, carry)

    await conn.execute(
        """INSERT INTO public.spv_subscriptions
             (id, org_id, spv_id, entity_id, commitment_amount, funded_amount,
              ownership_pct, subscription_status)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,500000,500000,20,'confirmed')""",
        SUB_OFFSET, ORG, SPV_MAIN, E_OFFSET)

    # A POSTED management-fee call, allocated $2,000 to E_OFFSET inside Q1 2026,
    # and a DRAFT one outside it. [6c] needs the first; [6e] needs the second.
    await conn.execute(
        """INSERT INTO public.spv_transactions
             (id, org_id, spv_id, txn_type, txn_date, amount, status, posted_at,
              allocation_basis, description)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'call_mgmt_fee','2026-02-15',10000,
                   'posted', now(), 'committed', $4)""",
        TXN_POSTED, ORG, SPV_MAIN, f"{TAG} posted mgmt fee call")
    await conn.execute(
        """INSERT INTO public.spv_transactions
             (id, org_id, spv_id, txn_type, txn_date, amount, status,
              allocation_basis, description)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'call_mgmt_fee','2026-05-15',8000,
                   'draft', 'committed', $4)""",
        TXN_DRAFT, ORG, SPV_MAIN, f"{TAG} draft mgmt fee call")
    for aid, tid, amt in ((ALLOC_POSTED, TXN_POSTED, "2000.00"),
                          (ALLOC_DRAFT, TXN_DRAFT, "1600.00")):
        await conn.execute(
            """INSERT INTO public.spv_transaction_allocations
                 (id, org_id, transaction_id, spv_id, subscription_id, entity_id,
                  ownership_pct, allocated_amount, status)
               VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6::uuid,
                       20, $7::numeric, 'allocated')""",
            aid, ORG, tid, SPV_MAIN, SUB_OFFSET, E_OFFSET, amt)


# ═══════════════════════════════════════════════════════════════════════════
# [1] Deployment, RLS, and the uniqueness that has to be real
# ═══════════════════════════════════════════════════════════════════════════


async def check_1(admin, app) -> None:
    for t in ("spv_fee_terms", "spv_fee_side_letters"):
        R.expect(f"1a:{t}",
                 await admin.fetchval("SELECT to_regclass($1)", f"public.{t}") is not None,
                 f"public.{t} is deployed")
        rls = await admin.fetchrow(
            "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=$1", t)
        R.expect(f"1b:{t}", rls and rls["relrowsecurity"], f"{t} has RLS enabled")
        pol = await admin.fetchrow(
            "SELECT polname, pg_get_expr(polqual, polrelid) AS u, "
            "pg_get_expr(polwithcheck, polrelid) AS w FROM pg_policy "
            "WHERE polrelid = $1::regclass", f"public.{t}")
        R.expect(f"1c:{t}", pol is not None and "NULLIF" in (pol["u"] or ""),
                 f"{t}'s policy NULLIFs the org GUC (an empty GUC must read "
                 f"zero rows, never cast-error or match)",
                 detail=str(dict(pol)) if pol else "no policy")
        R.expect(f"1c2:{t}", pol is not None and pol["w"] is not None,
                 f"{t}'s policy carries a WITH CHECK, so a cross-org WRITE is "
                 f"refused by the database and not only by application code")

    deployed = {
        r["conname"] for r in await admin.fetch(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='public.spv_fee_terms'::regclass AND contype='c'")
    }
    for name in ("spv_fee_terms_carry_requires_hurdle_type",
                 "spv_fee_terms_hurdle_type_check",
                 "spv_fee_terms_mgmt_fee_basis_check",
                 "spv_fee_terms_mgmt_fee_frequency_check",
                 "spv_fee_terms_carry_basis_check"):
        R.expect(f"1d:{name}", name in deployed,
                 f"CHECK {name} is deployed", detail=f"deployed: {sorted(deployed)}")

    idx = await admin.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
        "AND indexname='spv_fee_terms_active_uq'")
    R.expect("1e", idx is not None and "UNIQUE" in idx
             and "NULLS NOT DISTINCT" in idx and "system_to IS NULL" in idx,
             "spv_fee_terms_active_uq is a UNIQUE, NULLS NOT DISTINCT, partial "
             "index on system_to IS NULL", detail=str(idx))

    sl_checks = await admin.fetchval(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid='public.spv_fee_side_letters'::regclass AND contype='c'")
    sl_uniq = await admin.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
        "AND tablename='spv_fee_side_letters' AND indexdef LIKE '%UNIQUE%' "
        "AND indexname <> 'spv_fee_side_letters_pkey'")
    if sl_checks == 0 and sl_uniq == 0:
        R.find("1f", "spv_fee_side_letters has ZERO check constraints and no "
                     "uniqueness beyond its primary key. `overrides` is "
                     "unconstrained jsonb, so the database will happily store "
                     "an override that violates the invariants spv_fee_terms' "
                     "own CHECKs enforce, and two overlapping active letters "
                     "for one (spv, entity) are reachable. Both holes are "
                     "closed in the application layer — [4f] and [4g] prove it "
                     "— and both are recorded here as real schema gaps")
    else:
        R.ok("1f", f"spv_fee_side_letters carries {sl_checks} CHECK(s) and "
                   f"{sl_uniq} unique index(es)")

    # ── behavioural: NULLS NOT DISTINCT actually bites ──────────────────────
    async def insert_terms(conn, spv, class_label, *, org=ORG):
        return await conn.fetchval(
            """INSERT INTO public.spv_fee_terms
                 (org_id, spv_id, class_label, mgmt_fee_basis, mgmt_fee_frequency,
                  effective_from)
               VALUES ($1::uuid,$2::uuid,$3,'COMMITTED','QUARTERLY','2024-01-01')
               RETURNING id::text""",
            org, spv, class_label)

    # Each expected-failure insert runs inside its OWN savepoint. A constraint
    # violation aborts the whole transaction in Postgres, so without this the
    # first refusal would poison every check after it and they would all "fail"
    # for a reason that has nothing to do with what they test.
    async with admin.transaction():
        await scoped(admin, ORG)
        await insert_terms(admin, SPV_FORMING, None)

        try:
            async with admin.transaction():
                await insert_terms(admin, SPV_FORMING, None)
            R.bad("1g", "a second whole-fund (class_label NULL) terms row was "
                        "ACCEPTED — NULLS NOT DISTINCT is not in force, so one "
                        "fund can carry two contradictory whole-fund term sets")
        except asyncpg.exceptions.UniqueViolationError as exc:
            R.expect("1g", "spv_fee_terms_active_uq" in str(exc),
                     "a second WHOLE-FUND terms row is refused by "
                     "spv_fee_terms_active_uq — NULLS NOT DISTINCT is real, not "
                     "just present in the index definition", detail=str(exc))

        # different class labels must still be allowed, or 1g proves nothing
        try:
            await insert_terms(admin, SPV_FORMING, "A")
            await insert_terms(admin, SPV_FORMING, "B")
            R.ok("1h", "two terms rows with DIFFERENT class_labels are accepted "
                       "— the index narrows, it does not refuse everything")
        except Exception as exc:  # noqa: BLE001
            R.bad("1h", "distinct class_labels were refused", str(exc))

        try:
            async with admin.transaction():
                await insert_terms(admin, SPV_FORMING, "A")
            R.bad("1i", "a duplicate non-null class_label was ACCEPTED")
        except asyncpg.exceptions.UniqueViolationError:
            R.ok("1i", "a duplicate non-null class_label is refused")

        # an archived row must not block its replacement
        await admin.execute(
            "UPDATE public.spv_fee_terms SET system_to = now() "
            "WHERE spv_id = $1::uuid AND class_label = 'A'", SPV_FORMING)
        try:
            await insert_terms(admin, SPV_FORMING, "A")
            R.ok("1j", "a system-archived row does not block its replacement — "
                       "the index is partial on system_to IS NULL, which is why "
                       "create_terms archives on the SYSTEM axis and not the "
                       "valid axis")
        except Exception as exc:  # noqa: BLE001
            R.bad("1j", "archived row still blocked the replacement", str(exc))
        raise _Rollback()


class _Rollback(Exception):
    """Unwind a probe transaction without leaving its rows behind."""


# ═══════════════════════════════════════════════════════════════════════════
# [2] The backfill
# ═══════════════════════════════════════════════════════════════════════════


async def check_2(app) -> None:
    # Counted INSIDE the org GUC. app_service cannot bypass RLS, so an unscoped
    # count here reads zero for every org and the before/after comparison below
    # would compare two zeros and call it idempotence.
    async with app.transaction():
        await scoped(app, ORG)
        before = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_terms WHERE spv_id = ANY($1::uuid[])",
            SPVS_MAIN)
    decisions = await SFT.backfill_active_spv_terms(
        app, ORG, effective_from=date(2024, 1, 1), created_by=U_APPROVER)
    by_id = {d.spv_id: d for d in decisions}

    async with app.transaction():
        await scoped(app, ORG)

        d = by_id.get(SPV_MAIN)
        R.expect("2a", d is not None and d.action == "CREATED",
                 "an ACTIVE spv (spv_status='closed' — the raise is closed, the "
                 "fund is live) is backfilled",
                 detail=str(d.action if d else "absent"))
        row = await app.fetchrow(
            "SELECT * FROM public.spv_fee_terms WHERE spv_id=$1::uuid "
            "AND valid_to IS NULL AND system_to IS NULL", SPV_MAIN)
        R.expect("2b", row is not None, "the backfilled row is really there")
        if row is not None:
            R.expect("2c", row["mgmt_fee_pct"] == D("2.0"),
                     "mgmt_fee_pct is CARRIED verbatim from spvs.mgmt_fee_pct, "
                     "not defaulted", detail=str(row["mgmt_fee_pct"]))
            R.expect("2d", row["hurdle_type"] is None,
                     "hurdle_type is left NULL (unknown), NOT guessed as 'NONE' "
                     "— 'NONE' asserts the deal has no preferred return, which "
                     "no deployed data supports",
                     detail=str(row["hurdle_type"]))
            R.expect("2e", row["mgmt_fee_basis"] == "COMMITTED"
                     and row["mgmt_fee_frequency"] == "QUARTERLY",
                     "basis and frequency carry the inferred defaults",
                     detail=f"{row['mgmt_fee_basis']}/{row['mgmt_fee_frequency']}")

        d = by_id.get(SPV_FORMING)
        n = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_terms WHERE spv_id=$1::uuid",
            SPV_FORMING)
        R.expect("2f", d is not None and d.action == "SKIPPED_INACTIVE" and n == 0,
                 "a 'forming' SPV is skipped AND genuinely has no terms row — "
                 "the skip is proved by absence, not by the returned label",
                 detail=f"action={d.action if d else None} rows={n}")

        d = by_id.get(SPV_CARRY)
        n = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_terms WHERE spv_id=$1::uuid",
            SPV_CARRY)
        R.expect("2g", d is not None and d.action == "SKIPPED_NEEDS_HURDLE" and n == 0,
                 "an active SPV whose carry_pct is known but whose hurdle_type "
                 "is not is SKIPPED for a human rather than given a fabricated "
                 "hurdle", detail=f"action={d.action if d else None} rows={n}")

        n = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_terms WHERE spv_id=$1::uuid",
            SPV_OTHER)
        R.expect("2h", n == 0,
                 "an SPV in ANOTHER org is untouched by a backfill scoped to "
                 "this one", detail=f"rows={n}")

        # the production SPV, already migrated by seed_fee42_backfill.py
        prod = [d for d in decisions if d.spv_id not in SPVS_ALL]
        R.expect("2i", all(d.action == "SKIPPED_EXISTS" for d in prod) and prod,
                 f"the {len(prod)} already-migrated production SPV(s) are "
                 f"SKIPPED_EXISTS — a re-run never overwrites terms somebody "
                 f"entered by hand",
                 detail=str([(d.name, d.action) for d in prod]))

        active = await app.fetch(
            "SELECT s.id::text AS id, s.name, s.spv_status, "
            "  (SELECT count(*) FROM public.spv_fee_terms t "
            "    WHERE t.spv_id = s.id AND t.valid_to IS NULL "
            "      AND t.system_to IS NULL) AS n "
            "FROM public.spvs s WHERE s.org_id = $1::uuid "
            "  AND s.spv_status = ANY($2::text[])", ORG, list(SFT.ACTIVE_SPV_STATUSES))
        missing = [r["name"] for r in active if r["n"] == 0
                   and r["id"] != SPV_CARRY]
        R.expect("2j", not missing,
                 f"every ACTIVE spv in the org now carries spv_fee_terms "
                 f"({len(active)} checked)", detail=f"missing: {missing}")

    # a second call must write nothing
    again = await SFT.backfill_active_spv_terms(
        app, ORG, effective_from=date(2024, 1, 1), created_by=U_APPROVER)
    async with app.transaction():
        await scoped(app, ORG)
        after = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_terms WHERE spv_id = ANY($1::uuid[])",
            SPVS_MAIN)
    R.expect("2k", after == before + 1
             and all(d.action != "CREATED" for d in again),
             "the backfill is idempotent — a second run creates nothing",
             detail=f"{before} -> {after}, actions={[d.action for d in again]}")


# ═══════════════════════════════════════════════════════════════════════════
# [3] Step-down and term limit — pure, no database
# ═══════════════════════════════════════════════════════════════════════════


def check_3() -> None:
    terms = SFT.SpvFeeTerms(
        mgmt_fee_pct=D("0.02"),
        mgmt_fee_step_down=[{"after_year": 3, "pct": "0.015"}],
        mgmt_fee_term_years=D("10"),
    )
    third = SFT.add_years(INCEPTION, 3)      # 2023-02-28 (2023 is not a leap year)
    R.expect("3a", third == date(2023, 2, 28),
             "the third anniversary of a Feb-29 inception is Feb 28, clamped",
             detail=str(third))

    # a period that SPANS the boundary
    acc = SFT.schedule_mgmt_fee(
        terms, inception=INCEPTION,
        period_start=date(2023, 1, 1), period_end=date(2023, 3, 31))
    R.expect("3b", len(acc.segments) == 2,
             "a quarter spanning the step-down boundary yields TWO rate "
             "segments, not one averaged rate", detail=str(acc.segments))
    if len(acc.segments) == 2:
        s1, s2 = acc.segments
        R.expect("3c",
                 s1.start == date(2023, 1, 1) and s1.end == third - timedelta(days=1)
                 and s1.rate_pct == D("0.02")
                 and s2.start == third and s2.end == date(2023, 3, 31)
                 and s2.rate_pct == D("0.015"),
                 "the split lands ON the anniversary: the day before bills the "
                 "old rate, the anniversary itself bills the new one",
                 detail=f"{s1} | {s2}")
        total = sum(s.days for s in acc.segments)
        R.expect("3d", total == (date(2023, 3, 31) - date(2023, 1, 1)).days + 1
                 and s1.end + timedelta(days=1) == s2.start,
                 "the segments tile the period exactly — no gap, no overlap, no "
                 "double-billed day", detail=f"{total} days")

    R.expect("3e",
             SFT.effective_mgmt_fee_pct(terms, inception=INCEPTION,
                                        as_of=third - timedelta(days=1)) == D("0.02")
             and SFT.effective_mgmt_fee_pct(terms, inception=INCEPTION,
                                            as_of=third) == D("0.015")
             and SFT.effective_mgmt_fee_pct(terms, inception=INCEPTION,
                                            as_of=third + timedelta(days=1)) == D("0.015"),
             "the boundary is pinned at day−1 / day / day+1, not 'somewhere "
             "before' and 'somewhere after'")

    # calendar anniversaries genuinely differ from n x 365
    naive = INCEPTION + timedelta(days=365 * 10)
    real = SFT.add_years(INCEPTION, 10)
    R.expect("3f", real == date(2030, 2, 28) and naive == date(2030, 2, 26)
             and real != naive,
             f"calendar anniversaries and n×365 days DISAGREE by "
             f"{(real - naive).days} days at year 10 ({real} vs {naive}) — the "
             f"implementation demonstrably uses the calendar",
             detail=f"real={real} naive={naive}")

    # the term limit
    term_end = SFT.add_years(INCEPTION, 10)
    past = SFT.schedule_mgmt_fee(
        terms, inception=INCEPTION,
        period_start=term_end, period_end=term_end + timedelta(days=90))
    R.expect("3g", not past.accrues and past.refusal and past.truncated_by_term,
             "a period beginning ON the term end accrues NOTHING, with a stated "
             "refusal rather than a silent zero", detail=str(past.refusal))

    last_day = SFT.schedule_mgmt_fee(
        terms, inception=INCEPTION,
        period_start=term_end - timedelta(days=1),
        period_end=term_end - timedelta(days=1))
    R.expect("3h", last_day.accrues and not last_day.truncated_by_term,
             "the day BEFORE the term end still accrues — the limit stops "
             "accrual on the anniversary, not a day early")

    spanning = SFT.schedule_mgmt_fee(
        terms, inception=INCEPTION,
        period_start=date(2030, 1, 1), period_end=date(2030, 3, 31))
    R.expect("3i",
             spanning.accrues and spanning.truncated_by_term
             and spanning.segments[-1].end == term_end - timedelta(days=1),
             "a period straddling the term end is billed as a PARTIAL accrual "
             "ending the day before, and says so via truncated_by_term — a "
             "whole quarter and a skipped quarter are both wrong",
             detail=str(spanning.segments[-1]))

    # fractional terms
    frac = SFT.SpvFeeTerms(mgmt_fee_pct=D("0.02"), mgmt_fee_term_years=D("2.5"))
    acc = SFT.schedule_mgmt_fee(
        frac, inception=date(2024, 1, 15),
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    R.expect("3j", acc.term_end == date(2026, 7, 15),
             "a 2.5-year term resolves to 30 calendar months",
             detail=str(acc.term_end))
    try:
        SFT.schedule_mgmt_fee(
            SFT.SpvFeeTerms(mgmt_fee_term_years=D("2.10")),
            inception=date(2024, 1, 15),
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
        R.bad("3k", "a 2.10-year term was silently rounded to some number of "
                    "months instead of refused")
    except SFT.FractionalYearError as exc:
        R.expect("3k", exc.field == "mgmt_fee_term_years",
                 "a term that is not a whole number of months is REFUSED, "
                 "naming the field — rounding down truncates a fee the fund is "
                 "owed and rounding up charges one it is not", detail=str(exc))

    # inception is never inferred
    try:
        SFT.schedule_mgmt_fee(terms, inception=None,
                              period_start=date(2023, 1, 1), period_end=date(2023, 3, 31))
        R.bad("3l", "a step-down was evaluated with NO inception date")
    except SFT.InceptionRequiredError:
        R.ok("3l", "a step-down or term limit with no inception date is refused "
                   "rather than anchored on a guessed date")

    # ladder validation
    for ref, ladder, exc_type in (
        ("3m", [{"after_years": 3, "pct": "0.015"}], SFT.StepDownError),
        ("3n", [{"after_year": 3, "pct": "0.015"},
                {"after_year": 3, "pct": "0.01"}], SFT.StepDownError),
        ("3o", [{"after_year": 0, "pct": "0.015"}], SFT.StepDownError),
    ):
        try:
            SFT.parse_step_down(ladder)
            R.bad(ref, f"an invalid ladder was accepted: {ladder}")
        except exc_type:
            R.ok(ref, f"an invalid ladder is refused: {ladder}")

    # a NULL rate is not a zero rate
    unknown = SFT.SpvFeeTerms(mgmt_fee_pct=None)
    acc = SFT.schedule_mgmt_fee(
        unknown, inception=INCEPTION,
        period_start=date(2026, 1, 1), period_end=date(2026, 3, 31))
    R.expect("3p", acc.accrues and acc.rate_unknown,
             "an SPV whose rate was never recorded surfaces as rate_unknown, "
             "not as a fee of zero — the backfill carries NULL through as NULL "
             "and a caller must not read it as 'no fee'")


# ═══════════════════════════════════════════════════════════════════════════
# [4] Side letters — partial override, proved field by field
# ═══════════════════════════════════════════════════════════════════════════


async def check_4(app) -> None:
    async with app.transaction():
        await scoped(app, ORG)

        base = await SFT.load_terms(app, ORG, SPV_MAIN, as_of=date(2026, 1, 1))
        R.expect("4a", base.source == "WHOLE_FUND" and base.class_label is None,
                 "an SPV with no class-specific row resolves its WHOLE-FUND terms",
                 detail=base.source)

        cls = await SFT.load_terms(app, ORG, SPV_MAIN, class_label="A",
                                   as_of=date(2026, 1, 1))
        R.expect("4b", cls.source == "CLASS" and cls.class_label == "A"
                 and cls.mgmt_fee_pct == D("0.01"),
                 "a class with its own terms row resolves CLASS, not whole-fund",
                 detail=f"{cls.source} {cls.mgmt_fee_pct}")

        fallback = await SFT.load_terms(app, ORG, SPV_MAIN, class_label="ZZZ",
                                        as_of=date(2026, 1, 1))
        R.expect("4c", fallback.source == "WHOLE_FUND",
                 "a class with NO row of its own falls back to whole-fund — "
                 "precedence is a fallback, never a merge of the two rows",
                 detail=fallback.source)

        # the partial override, field by field
        resolved = await SFT.resolve_terms_for_entity(
            app, ORG, SPV_MAIN, E_PARTIAL, as_of=date(2026, 1, 1))
        moved = {k for k, v in resolved.economics().items()
                 if base.economics()[k] != v}
        R.expect("4d", moved == {"mgmt_fee_pct", "placement_fee_pct"},
                 "EXACTLY the two fields the side letter carries moved; the "
                 "other 13 economic fields are unchanged — a whole-row "
                 "replacement would fail this and pass a 'the rate changed' "
                 "check", detail=f"moved={sorted(moved)}")
        R.expect("4e", resolved.mgmt_fee_pct == D("0.005"),
                 "the overridden rate is the side letter's value",
                 detail=str(resolved.mgmt_fee_pct))
        R.expect("4f", resolved.placement_fee_pct is None
                 and base.placement_fee_pct == D("0.02"),
                 "an EXPLICIT null in the override CLEARS the field, while an "
                 "absent key leaves it alone — absent and null are deliberately "
                 "different, because waiving a fee to nothing is a real term",
                 detail=f"{base.placement_fee_pct} -> {resolved.placement_fee_pct}")
        R.expect("4g", resolved.side_letter_id == SL_PARTIAL
                 and resolved.overridden_fields == ("mgmt_fee_pct", "placement_fee_pct"),
                 "the resolution carries its provenance — which letter, which "
                 "fields — so 'why is this investor charged this' survives the "
                 "call", detail=str(resolved.overridden_fields))

        plain = await SFT.resolve_terms_for_entity(
            app, ORG, SPV_MAIN, E_PLAIN, as_of=date(2026, 1, 1))
        R.expect("4h", plain.side_letter_id is None
                 and plain.economics() == base.economics(),
                 "an investor with NO side letter resolves the base terms "
                 "unchanged — the override is not leaking onto everyone")

        # the [F3] hole: an override that would violate the base table's CHECK
        no_hurdle = SFT.SpvFeeTerms(carry_pct=None, hurdle_type=None)
        try:
            SFT.apply_overrides(no_hurdle, {"carry_pct": 0.2})
            R.bad("4i", "an override set carry_pct with no hurdle_type and the "
                        "merged terms were accepted — spv_fee_side_letters has "
                        "no CHECK of its own, so nothing would have stopped it")
        except SFT.HurdleTypeRequiredError as exc:
            R.expect("4i", exc.field == "hurdle_type",
                     "the MERGED row is validated, not the override delta: "
                     "{'carry_pct': 0.2} over hurdle-less base terms is refused, "
                     "naming hurdle_type", detail=str(exc))

        try:
            SFT.apply_overrides(no_hurdle, {"spv_id": "x"})
            R.bad("4j", "a side letter was allowed to override spv_id")
        except SFT.SpvFeeTermsError as exc:
            R.expect("4j", exc.field == "spv_id",
                     "a side letter may move economic terms and nothing else — "
                     "repointing spv_id is refused, naming the key",
                     detail=str(exc))

        # Original check 4k asserted the RESOLVER raises AmbiguousSideLetterError
        # when two active side letters exist for one investor. That state can no
        # longer be constructed: spv_fee_side_letters_active_uq (added to close
        # finding [1f]) now refuses a second active letter for the same
        # (org, spv, entity) at INSERT time — proved by the database-level
        # check 4k earlier in this file (see seed_terms_and_letters). The
        # resolver's own AmbiguousSideLetterError path is retained in
        # services/spv_fee_terms.py as defense in depth (belt-and-suspenders
        # against any future write path that might bypass the index, e.g. a
        # raw migration), but is no longer independently exercisable from a
        # normally-seeded fixture and is not re-tested here.

        # A letter outside its effective window must not apply. The date is
        # chosen so the base TERMS are in force (from 2024-01-01) but the side
        # letter is not yet (from 2025-01-01) — otherwise "no letter applied"
        # would also be true of a date where nothing at all resolves.
        early = await SFT.resolve_terms_for_entity(
            app, ORG, SPV_MAIN, E_PARTIAL, as_of=date(2024, 6, 1))
        R.expect("4l", early.side_letter_id is None
                 and early.mgmt_fee_pct == base.mgmt_fee_pct,
                 "a side letter dated later does not apply to an earlier "
                 "period, while the BASE terms on that same date still resolve "
                 "normally — the as-of window is honoured in both directions",
                 detail=f"letter={early.side_letter_id} rate={early.mgmt_fee_pct}")


# ═══════════════════════════════════════════════════════════════════════════
# [5] carry_pct without hurdle_type — refused by BOTH layers, independently
# ═══════════════════════════════════════════════════════════════════════════


async def check_5(admin, app) -> None:
    before = await admin.fetchval("SELECT count(*) FROM public.spv_fee_terms")

    # (a) the DATABASE, via an insert that bypasses the service entirely
    try:
        async with admin.transaction():
            await scoped(admin, ORG)
            await admin.execute(
                """INSERT INTO public.spv_fee_terms
                     (org_id, spv_id, class_label, mgmt_fee_basis,
                      mgmt_fee_frequency, carry_pct, effective_from)
                   VALUES ($1::uuid,$2::uuid,'DBCHECK','COMMITTED','QUARTERLY',
                           0.2,'2024-01-01')""",
                ORG, SPV_CARRY)
        R.bad("5a", "a raw INSERT with carry_pct and no hurdle_type was ACCEPTED "
                    "by the database")
    except asyncpg.exceptions.CheckViolationError as exc:
        R.expect("5a", "carry_requires_hurdle_type" in str(exc),
                 "the DATABASE refuses carry_pct without hurdle_type, on an "
                 "insert that never went near the service layer",
                 detail=str(exc).splitlines()[0])

    # (b) the APPLICATION, with a message that names the field
    try:
        await SFT.create_terms(
            app, ORG, SPV_CARRY, class_label="APPCHECK",
            effective_from=date(2024, 1, 1), carry_pct=D("0.2"))
        R.bad("5b", "create_terms accepted carry_pct with no hurdle_type")
    except SFT.HurdleTypeRequiredError as exc:
        R.expect("5b", exc.field == "hurdle_type" and "hurdle_type" in str(exc),
                 "the APPLICATION refuses it with a clean error naming "
                 "hurdle_type, not a raw constraint violation naming a "
                 "constraint", detail=str(exc))

    after = await admin.fetchval("SELECT count(*) FROM public.spv_fee_terms")
    R.expect("5c", after == before,
             "neither refusal left a row behind — the app-layer refusal happens "
             "BEFORE the write, not after it", detail=f"{before} -> {after}")

    # (c) the positive control, or 5b passes for a function that refuses all
    try:
        tid = await SFT.create_terms(
            app, ORG, SPV_CARRY, class_label="APPCHECK",
            effective_from=date(2024, 1, 1), carry_pct=D("0.2"),
            hurdle_type="NONE", hurdle_pct=D("0"))
        R.expect("5d", tid is not None,
                 "the SAME call WITH a hurdle_type succeeds — 5b is a specific "
                 "refusal, not a blanket one", detail=str(tid))
    except Exception as exc:  # noqa: BLE001
        R.bad("5d", "the positive control failed", str(exc))

    for ref, kwargs, field in (
        ("5e", {"mgmt_fee_basis": "AUM"}, "mgmt_fee_basis"),
        ("5f", {"mgmt_fee_frequency": "DAILY"}, "mgmt_fee_frequency"),
        ("5g", {"carry_basis": "PER_DEAL"}, "carry_basis"),
        ("5h", {"hurdle_type": "RATCHET", "carry_pct": D("0.2")}, "hurdle_type"),
    ):
        try:
            SFT.validate_terms(kwargs)
            R.bad(ref, f"an out-of-vocabulary {field} was accepted: {kwargs}")
        except SFT.VocabularyError as exc:
            R.expect(ref, exc.field == field,
                     f"an out-of-vocabulary {field} is refused, naming the field",
                     detail=str(exc))

    try:
        SFT.coerce_terms_fields({"mgmt_fee_pct": 0.02})
        R.bad("5i", "a float rate was accepted at the Python API boundary")
    except SFT.SpvFeeTermsError as exc:
        R.expect("5i", exc.field == "mgmt_fee_pct",
                 "a float rate is refused at the Python API boundary — 0.02 is "
                 "not two percent in binary, and on a nine-figure commitment "
                 "the difference is real money", detail=str(exc))
    R.expect("5j",
             SFT.coerce_terms_fields({"mgmt_fee_pct": 0.02},
                                     from_json=True)["mgmt_fee_pct"] == D("0.02"),
             "the SAME float out of a jsonb column is accepted and converts "
             "EXACTLY to 0.02 via repr, not to the binary expansion — otherwise "
             "the overrides column would be unreadable")


# ═══════════════════════════════════════════════════════════════════════════
# [6] offsets_advisory_fee -> fee_credits, proved through fee36's own resolver
# ═══════════════════════════════════════════════════════════════════════════


async def check_6(app) -> None:
    async with app.transaction():
        await scoped(app, ORG)

        # (a) the switch is OFF for an investor with no side letter
        before = await app.fetchval("SELECT count(*) FROM public.fee_credits")
        try:
            await SFT.ensure_advisory_fee_offset_credit(
                app, ORG, spv_id=SPV_MAIN, entity_id=E_PLAIN,
                scope_type="ACCOUNT", scope_id=ACC, approved_by=U_APPROVER,
                reason=f"{TAG} unauthorised", effective_from=date(2026, 1, 1),
                as_of=date(2026, 1, 1))
            R.bad("6a", "a credit was created against terms with "
                        "offsets_advisory_fee=false")
        except SFT.OffsetNotAuthorisedError as exc:
            R.expect("6a", exc.field == "offsets_advisory_fee",
                     "offsets_advisory_fee=false REFUSES the credit — an "
                     "offset the term sheet does not authorise is revenue given "
                     "away that nobody agreed to give", detail=str(exc))
        R.expect("6b",
                 await app.fetchval("SELECT count(*) FROM public.fee_credits") == before,
                 "the refusal wrote nothing")

        # (b) the switch is ON, via a side letter — proving it is per-investor
        credit_id = await SFT.ensure_advisory_fee_offset_credit(
            app, ORG, spv_id=SPV_MAIN, entity_id=E_OFFSET,
            scope_type="ACCOUNT", scope_id=ACC, approved_by=U_APPROVER,
            reason=f"{TAG} spv mgmt fee offset", effective_from=date(2026, 1, 1),
            offset_pct=D("0.5"), as_of=date(2026, 1, 1))
        row = await app.fetchrow(
            "SELECT * FROM public.fee_credits WHERE id = $1::uuid", credit_id)
        R.expect("6c", row is not None
                 and row["credit_source"] == "SPV_MGMT_FEE_OFFSET"
                 and row["scope_type"] == "ACCOUNT"
                 and str(row["scope_id"]) == ACC
                 and str(row["org_id"]) == ORG
                 and row["offset_pct"] == D("0.5"),
                 "offsets_advisory_fee=true (granted by this investor's side "
                 "letter) writes a real, correctly-scoped fee_credits row "
                 "through fee34's EXISTING table and vocabulary",
                 detail=str(dict(row)) if row else "no row")

        # (c) THE CONNECTION: fee36's own resolver, against this very row
        try:
            basis = await resolve_credit_basis(
                app, ORG, credit_source="SPV_MGMT_FEE_OFFSET", account_id=ACC,
                owner_entity_id=E_OFFSET,
                period_start=date(2026, 1, 1), period_end=date(2026, 3, 31))
            R.expect("6d", basis.amount == D("2000.00")
                     and basis.source == "spv_transaction_allocations.allocated_amount",
                     "fee36's OWN resolve_credit_basis resolves this credit to "
                     "the investor's $2,000 allocated share of the posted "
                     "management-fee call — the two mechanisms genuinely "
                     "connect, asserted by calling fee36 rather than by "
                     "assuming", detail=f"{basis.amount} from {basis.source}")
            R.expect("6e", basis.amount * row["offset_pct"] == D("1000.000"),
                     "basis × offset_pct is the credit fee35's engine will "
                     "apply: $2,000 × 0.5 = $1,000.00",
                     detail=str(basis.amount * row["offset_pct"]))
        except CreditBasisUnavailableError as exc:
            R.bad("6d", "fee36 could not resolve a basis for the credit this "
                        "sprint wrote", str(exc))

        # (d) the negative half: a DRAFT call is not a charged fee
        try:
            await resolve_credit_basis(
                app, ORG, credit_source="SPV_MGMT_FEE_OFFSET", account_id=ACC,
                owner_entity_id=E_OFFSET,
                period_start=date(2026, 4, 1), period_end=date(2026, 6, 30))
            R.bad("6f", "a DRAFT management-fee call produced a credit basis — "
                        "that credits back money nobody was charged")
        except CreditBasisUnavailableError:
            R.ok("6f", "a period containing only a DRAFT call yields no basis "
                       "and raises rather than crediting zero")

        # (e) the scale trap fee35 already found in this module
        try:
            await SFT.ensure_advisory_fee_offset_credit(
                app, ORG, spv_id=SPV_MAIN, entity_id=E_OFFSET,
                scope_type="ACCOUNT", scope_id=ACC, approved_by=U_APPROVER,
                reason=f"{TAG} bad pct", effective_from=date(2026, 1, 1),
                offset_pct=D("50"), as_of=date(2026, 1, 1))
            R.bad("6g", "offset_pct=50 was accepted — it is a FRACTION, so 50 "
                        "means 5000%")
        except SFT.SpvFeeTermsError as exc:
            R.expect("6g", "FRACTION" in str(exc) or "0..1" in str(exc),
                     "offset_pct=50 is refused by fee34's own validate_credit — "
                     "the field is a fraction, and 50%-typed-as-50 is the "
                     "mistake it exists to catch", detail=str(exc))

        R.find("6h", "offsets_advisory_fee is a BOOLEAN and fee_credits.offset_pct "
                     "is a fraction in [0,1]; the boolean cannot supply the "
                     "fraction. ensure_advisory_fee_offset_credit therefore "
                     "takes offset_pct explicitly, defaulting to a FULL offset "
                     "(1.0), and does not invent a partial one. A partial "
                     "offset is a real term sheet clause with nowhere to live "
                     "in spv_fee_terms today — recorded as a gap, not filled by "
                     "a guess")
        R.find("6i", "fee34 shipped fee_credits, its CHECK vocabulary and "
                     "fee_validation.validate_credit, but NO service or router "
                     "ever inserts a credit — only verify scripts do, directly "
                     "(searched every INSERT against the table). So 'reuse the "
                     "existing path' could not mean calling an existing "
                     "creator; this sprint reuses the table, the vocabulary and "
                     "validate_credit, and is the first application write path "
                     "fee_credits has had")


# ═══════════════════════════════════════════════════════════════════════════
# [7] Additive-first, proved rather than claimed
# ═══════════════════════════════════════════════════════════════════════════


async def snapshot_spvs(conn) -> list[tuple]:
    rows = await conn.fetch(
        "SELECT id::text AS id, mgmt_fee_pct, carry_pct, spv_status, name, "
        "       target_raise, minimum_raise, hard_cap, min_commitment, "
        "       class_label, vehicle_type, close_date, updated_at "
        "FROM public.spvs ORDER BY id")
    return [tuple(r.values()) for r in rows]


async def check_7(admin, before: list[tuple]) -> None:
    after = await snapshot_spvs(admin)
    fixture_ids = set(SPVS_ALL)
    before_map = {r[0]: r for r in before}
    after_map = {r[0]: r for r in after if r[0] not in fixture_ids}

    R.expect("7a", set(before_map) == set(after_map),
             "no pre-existing spvs row appeared or vanished",
             detail=f"{sorted(set(before_map) ^ set(after_map))}")
    drift = {k: (before_map[k], after_map[k]) for k in before_map
             if k in after_map and before_map[k] != after_map[k]}
    R.expect("7b", not drift,
             f"every column of every pre-existing spvs row is byte-identical "
             f"after the sprint ({len(before_map)} row(s) compared in full, not "
             f"just the two fee columns)", detail=str(drift))

    fee_drift = {k: (before_map[k][1:3], after_map[k][1:3]) for k in before_map
                 if k in after_map and before_map[k][1:3] != after_map[k][1:3]}
    R.expect("7c", not fee_drift,
             "spvs.mgmt_fee_pct and spvs.carry_pct specifically are unchanged — "
             "this sprint is additive, it did not migrate the flat scalars away",
             detail=str(fee_drift))

    # the EXISTING reader still works, verbatim
    try:
        from routers.spv import SPV_SELECT
        rows = await admin.fetch(f"SELECT {SPV_SELECT} FROM spvs LIMIT 5")
        has_cols = all("mgmt_fee_pct" in r and "carry_pct" in r for r in rows)
        R.expect("7d", has_cols or not rows,
                 "routers/spv.py's own SPV_SELECT still executes and still "
                 "returns mgmt_fee_pct/carry_pct — 'the values are unchanged' "
                 "and 'the code that reads them still works' are two claims",
                 detail=f"{len(rows)} row(s)")
    except Exception as exc:  # noqa: BLE001
        R.bad("7d", "the existing SPV reader broke", str(exc))

    src = (API_DIR / "services/spv_fee_terms.py").read_text()
    writes = [line.strip() for line in src.splitlines()
              if ("UPDATE public.spvs" in line or "INSERT INTO public.spvs" in line
                  or "UPDATE {TABLE_SPVS}" in line)]
    R.expect("7e", not writes,
             "the new service contains no write against public.spvs at all",
             detail=str(writes))


# ═══════════════════════════════════════════════════════════════════════════
# [8] Cross-org isolation, on a role that cannot bypass RLS
# ═══════════════════════════════════════════════════════════════════════════


async def check_8(admin, app) -> None:
    bypass = await app.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    if not R.expect("8a", bypass is False,
                    f"the isolation role ({await app.fetchval('SELECT current_user')}) "
                    f"has rolbypassrls=False — without this every check below "
                    f"proves nothing", detail=f"rolbypassrls={bypass}"):
        return

    # a terms row and a side letter in the OTHER org, seeded as postgres
    async with admin.transaction():
        await scoped(admin, OTHER_ORG)
        await admin.execute(
            """INSERT INTO public.spv_fee_terms
                 (id, org_id, spv_id, mgmt_fee_basis, mgmt_fee_frequency,
                  effective_from)
               VALUES ($1::uuid,$2::uuid,$3::uuid,'COMMITTED','QUARTERLY','2024-01-01')""",
            TERMS_OTHER, OTHER_ORG, SPV_OTHER)
        await admin.execute(
            """INSERT INTO public.spv_fee_side_letters
                 (id, org_id, spv_id, entity_id, overrides, effective_from, reason)
               VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::jsonb,'2024-01-01',$6)""",
            SL_OTHER, OTHER_ORG, SPV_OTHER, E_OTHER,
            json.dumps({"mgmt_fee_pct": "0.03"}), f"{TAG} other org")

    async with app.transaction():
        await scoped(app, ORG)
        seen_other = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_terms WHERE org_id = $1::uuid",
            OTHER_ORG)
        seen_own = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_terms WHERE org_id = $1::uuid", ORG)
        R.expect("8b", seen_other == 0 and seen_own > 0,
                 f"under org {ORG[:8]} the other org's spv_fee_terms rows are "
                 f"invisible while this org's {seen_own} are visible — not "
                 f"'reading nothing'", detail=f"other={seen_other} own={seen_own}")
        sl_other = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_side_letters WHERE org_id = $1::uuid",
            OTHER_ORG)
        sl_own = await app.fetchval(
            "SELECT count(*) FROM public.spv_fee_side_letters WHERE org_id = $1::uuid",
            ORG)
        R.expect("8c", sl_other == 0 and sl_own > 0,
                 "the same holds for spv_fee_side_letters",
                 detail=f"other={sl_other} own={sl_own}")

        try:
            async with app.transaction():   # savepoint: the refusal aborts it
                await app.execute(
                    """INSERT INTO public.spv_fee_terms
                         (org_id, spv_id, class_label, mgmt_fee_basis,
                          mgmt_fee_frequency, effective_from)
                       VALUES ($1::uuid,$2::uuid,'XORG','COMMITTED','QUARTERLY',
                               '2024-01-01')""",
                    OTHER_ORG, SPV_OTHER)
            R.bad("8d", "a cross-org INSERT succeeded while the connection's org "
                        "context was this org")
        except asyncpg.exceptions.InsufficientPrivilegeError as exc:
            R.expect("8d", "row-level security" in str(exc).lower(),
                     "a cross-org INSERT is refused by the policy's WITH CHECK "
                     "— by the DATABASE, not by a Python if",
                     detail=str(exc).splitlines()[0])
        except asyncpg.exceptions.ForeignKeyViolationError as exc:
            R.bad("8d", "the cross-org insert failed on an FK before RLS could "
                        "refuse it, so this proves nothing", str(exc))

    async with app.transaction():
        await scoped(app, "")
        empty = await app.fetchval("SELECT count(*) FROM public.spv_fee_terms")
        R.expect("8e", empty == 0,
                 "an EMPTY org GUC reads zero rows — the policy's NULLIF turns "
                 "'' into NULL rather than cast-erroring or matching",
                 detail=f"rows={empty}")

    # what _OrgWrite does NOT protect against, stated plainly
    try:
        await SFT.create_terms(
            app, OTHER_ORG, SPV_MAIN, class_label="XORG",
            effective_from=date(2024, 1, 1))
        R.bad("8f", "create_terms wrote an org-A SPV's terms under org B")
    except SFT.SpvFeeTermsError as exc:
        R.expect("8f", "does not exist in org" in str(exc),
                 "create_terms refuses an spv_id that does not belong to the "
                 "org_id argument. This is NOT an RLS proof: _OrgWrite raises "
                 "the org GUC FROM its argument, so RLS would happily satisfy a "
                 "caller passing the wrong org. The spv_id ∈ org lookup is the "
                 "guard that actually holds", detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# Driver
# ═══════════════════════════════════════════════════════════════════════════


async def seed_terms_and_letters(app) -> None:
    """The base terms, the class terms and the side letters checks 4/6 need."""
    # whole-fund terms, replacing the row the backfill wrote (system-archived)
    await SFT.create_terms(
        app, ORG, SPV_MAIN, class_label=None, effective_from=date(2024, 1, 1),
        created_by=U_APPROVER, **BASE_TERMS)
    # a class-specific row on the SAME spv
    class_terms = dict(BASE_TERMS, mgmt_fee_pct=D("0.01"))
    await SFT.create_terms(
        app, ORG, SPV_MAIN, class_label="A", effective_from=date(2024, 1, 1),
        created_by=U_APPROVER, **class_terms)

    async with app.transaction():
        await scoped(app, ORG)
        for sid, eid, overrides, eff_from in (
            (SL_PARTIAL, E_PARTIAL,
             {"mgmt_fee_pct": "0.005", "placement_fee_pct": None}, date(2025, 1, 1)),
            (SL_OFFSET, E_OFFSET, {"offsets_advisory_fee": True}, date(2025, 1, 1)),
            (SL_DUPE_A, E_DUPE, {"mgmt_fee_pct": "0.004"}, date(2025, 1, 1)),
        ):
            await app.execute(
                """INSERT INTO public.spv_fee_side_letters
                     (id, org_id, spv_id, entity_id, overrides, effective_from,
                      approved_by, reason)
                   VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::jsonb,
                           $6::date,$7::uuid,$8)""",
                sid, ORG, SPV_MAIN, eid, json.dumps(overrides), eff_from,
                U_APPROVER, f"{TAG} side letter")

        # SL_DUPE_B targets the SAME entity as SL_DUPE_A, on the same spv,
        # while SL_DUPE_A is still active. spv_fee_side_letters_active_uq
        # (added to close finding [1f]) now refuses this at the database.
        # A nested transaction (savepoint) isolates the expected failure so
        # it doesn't abort the rest of this fixture's outer transaction.
        try:
            async with app.transaction():
                await app.execute(
                    """INSERT INTO public.spv_fee_side_letters
                         (id, org_id, spv_id, entity_id, overrides, effective_from,
                          approved_by, reason)
                       VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::jsonb,
                               $6::date,$7::uuid,$8)""",
                    SL_DUPE_B, ORG, SPV_MAIN, E_DUPE,
                    json.dumps({"mgmt_fee_pct": "0.003"}), date(2025, 6, 1),
                    U_APPROVER, f"{TAG} side letter")
            raise AssertionError(
                "spv_fee_side_letters_active_uq should have refused a second "
                "active side letter for the same (org, spv, entity)")
        except asyncpg.exceptions.UniqueViolationError:
            print("[PASS] 4k  a second active side letter for the same investor "
                  "is refused by spv_fee_side_letters_active_uq, at the database")

async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    app_url, app_prov = await app_service_dsn()
    if admin_url is None:
        print(f"FATAL: cannot reach the database as postgres — {admin_prov}")
        return 2
    if app_url is None:
        print(f"FATAL: cannot reach the database as app_service — {app_prov}. "
              f"Every RLS check would pass vacuously on the postgres DSN")
        return 2
    print(f"admin       : {admin_prov}")
    print(f"app_service : {app_prov}\n")

    admin = await connect(admin_url)
    app = await connect(app_url)
    pre: dict[str, int] = {}
    spvs_before: list[tuple] = []
    try:
        await teardown(admin)
        pre = await counts(admin)
        spvs_before = await snapshot_spvs(admin)
        await build_fixtures(admin)

        try:
            await check_1(admin, app)
        except _Rollback:
            pass
        await check_2(app)
        check_3()
        await seed_terms_and_letters(app)
        await check_4(app)
        await check_5(admin, app)
        await check_6(app)
        await check_8(admin, app)
    except Exception:  # noqa: BLE001
        R.bad("driver", "the run aborted", traceback.format_exc())
    finally:
        try:
            await teardown(admin)
            await check_7(admin, spvs_before)
        except Exception:  # noqa: BLE001
            R.bad("teardown", "teardown failed", traceback.format_exc())
        post = await counts(admin)
        drift = {t: (pre.get(t), post.get(t)) for t in COUNTED
                 if pre.get(t) != post.get(t)}
        R.expect("9", not drift,
                 f"every one of the {len(COUNTED)} tables this script writes to "
                 f"is back at its pre-test row count", detail=str(drift))
        await admin.close()
        await app.close()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
