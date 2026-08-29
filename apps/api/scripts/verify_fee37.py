"""Sprint fee37 verification — cost model, Altruist rate card, pass-through.

Pass/fail only, no prompts. Run:

    python3 scripts/verify_fee37.py

This sprint WRITES REAL ROWS into four brand-new tables, so teardown is the
discipline that matters most. Every table touched is counted before the first
insert and again after the last delete, and a difference of even one row fails
the run — reported AFTER the tests, so a teardown bug never masquerades as a
test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[1] compares the module's vocabularies to the DEPLOYED CHECKs, both ways.**
  ``services.cost_model`` hard-codes ten cost types, four policies, five bases.
  A check that only asserted "every constant is allowed by the CHECK" would
  pass if the database admitted five more; one that only asserted the reverse
  would pass if the module had dropped one. The comparison is set equality, so
  drift in either direction fails.

* **[2] proves the seeder is IDEMPOTENT by running it twice** and asserting the
  second run inserts nothing and returns the same ids. That is not decoration:
  this same script's teardown depends on it. If the real Altruist profile is
  already seeded in this org, run two adopts it, ``created`` is False, and
  teardown deletes nothing — so the script is safe to run before OR after the
  production seed, and requirement [7] holds either way.

* **[2f] proves the ambiguity guard rail in BOTH directions.** Seeding both
  readings of the subscription line is only safe if something stops a consumer
  summing them. A guard that refused everything, and one that refused nothing,
  both pass a single-direction test.

* **[3] checks each policy against a HAND-DERIVED literal**, not against the
  function's own arithmetic, and re-reads every ``cost_event`` from an
  INDEPENDENT connection. A write that only ever proved itself through the
  object it returned proves nothing about what landed.

* **[3e] proves ABSORB still WRITES.** The whole point of ``cost_events`` is
  that the ledger records what the firm paid regardless of who pays for it. A
  policy engine that skipped the event when nobody was billed would pass every
  revenue assertion above and silently report absorbed costs as free.

* **[4] REPRODUCES the gap before proving the gate.** The deployed CHECK is
  ``(policy <> 'MARKUP') OR (disclosure_required = true)`` — a flag, not an
  acknowledgement. [4a] inserts an undisclosed MARKUP by RAW SQL and shows the
  database accepts it. Only then does [4b] show the service refuses it. A gate
  proven in isolation, never shown to address a real hole, is not proven.

* **[4c] asserts nothing landed** after the refusal, by row count. A service
  that raised AFTER inserting would pass a naive "it raised" check.

* **[5] proves precedence by MOVING it.** The same account, same schedule,
  same as-of date resolves ORG_DEFAULT before the ACCOUNT policy exists and
  ACCOUNT after — and the ORG_DEFAULT then appears in ``losers``. A resolver
  hard-wired to ACCOUNT passes the second half alone; one hard-wired to
  ORG_DEFAULT passes the first half alone.

* **[6] runs on app_service, whose ``rolbypassrls`` is asserted False FIRST.**
  Cross-org isolation measured on the postgres DSN passes vacuously — postgres
  is a superuser and every policy here is inert for it.
"""

from __future__ import annotations

import asyncio
import glob
import json
import pathlib
import sys
import traceback
from datetime import date, datetime, timezone
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

from services.cost_model import (  # noqa: E402
    ALLOCATION_METHODS,
    ALTRUIST_PROVIDER_CODE,
    ALTRUIST_SCHEDULES,
    AMBIGUITY_GROUPS,
    COST_TYPES,
    POLICIES,
    POLICY_SCOPE_TYPES,
    PROVIDER_TYPES,
    SCHEDULE_APPLIES_SCOPES,
    SCHEDULE_BASES,
    SCHEDULE_FREQUENCIES,
    SCOPE_PRECEDENCE,
    AmbiguousRateCardError,
    DisclosureRequiredError,
    PassThroughRateError,
    assert_no_ambiguous_overlap,
    compute_pass_through,
    create_pass_through_policy,
    record_cost_event,
    resolve_pass_through_policy,
    seed_altruist_profile,
)

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "fee37verify"

COUNTED = [
    "public.cost_providers",
    "public.cost_schedules",
    "public.cost_pass_through_policies",
    "public.cost_events",
    "public.accounts",
    "public.households",
    "public.entities",
    "public.billing_groups",
    "public.billing_group_members",
]

E_A = "99000000-0000-0000-0000-0000fee37011"
HH = "99000000-0000-0000-0000-0000fee37021"
ACC_A = "99000000-0000-0000-0000-0000fee37031"
BG = "99000000-0000-0000-0000-0000fee37041"
# approved_by / disclosure_acknowledged_by carry NO foreign key to users
# (measured — see [FIND] F3), so these are deterministic literals rather than
# seeded user rows. Using real users would add 92 FKs' worth of teardown risk
# to prove something the schema does not actually check.
U_APPROVER = "99000000-0000-0000-0000-0000fee37051"
U_DISCLOSER = "99000000-0000-0000-0000-0000fee37052"

TODAY = date(2026, 8, 29)
EVENT_DAY = date(2026, 7, 31)
P_START, P_END = date(2026, 7, 1), date(2026, 7, 31)
ACK_AT = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)

#: The fixture cost the four policies are applied to. Chosen with four decimal
#: places on purpose: it makes the cents/4dp seam in compute_pass_through
#: observable instead of theoretical.
FIXTURE_COST = D("1234.5678")


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
        passed = counts.get("PASS", 0)
        print("\n" + "=" * 78)
        print(
            f"fee37: {passed}/{total} PASS"
            + "".join(f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS")
        )
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


#: Populated by check_2. Teardown removes ONLY what this run inserted, so a
#: pre-existing production Altruist profile survives untouched.
SEEDED: dict[str, object] = {"provider_id": None, "provider_was_new": False,
                             "new_schedule_ids": []}


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def teardown(conn) -> None:
    """By fixture id, in FK order. Never a TRUNCATE.

    ``cost_events`` and ``cost_pass_through_policies`` go before
    ``cost_schedules`` (real FKs), and the Altruist rows go last and
    conditionally — see :data:`SEEDED`.
    """
    await conn.execute(
        f"DELETE FROM public.cost_events WHERE org_id=$1::uuid "
        f"AND (product_type = $2 OR allocation_driver = $2)",
        ORG, TAG,
    )
    await conn.execute(
        "DELETE FROM public.cost_pass_through_policies "
        "WHERE org_id=$1::uuid AND reason LIKE $2",
        ORG, f"{TAG}%",
    )
    # Rows [4a] plants by raw SQL to reproduce the CHECK's gap carry the same
    # reason prefix, so they are covered by the delete above.

    new_ids = list(SEEDED.get("new_schedule_ids") or [])
    if new_ids:
        await conn.execute(
            "DELETE FROM public.cost_schedules WHERE id = ANY($1::uuid[])", new_ids
        )
    if SEEDED.get("provider_was_new") and SEEDED.get("provider_id"):
        # Only if THIS run created it. An Altruist provider that predates the
        # run is production data.
        still = await conn.fetchval(
            "SELECT count(*) FROM public.cost_schedules WHERE cost_provider_id=$1::uuid",
            SEEDED["provider_id"],
        )
        if still == 0:
            await conn.execute(
                "DELETE FROM public.cost_providers WHERE id=$1::uuid",
                SEEDED["provider_id"],
            )

    await conn.execute(
        "DELETE FROM public.billing_group_members WHERE billing_group_id=$1::uuid", BG
    )
    await conn.execute("DELETE FROM public.billing_groups WHERE id=$1::uuid", BG)
    await conn.execute("DELETE FROM public.accounts WHERE id=$1::uuid", ACC_A)
    await conn.execute("DELETE FROM public.households WHERE id=$1::uuid", HH)
    await conn.execute("DELETE FROM public.entities WHERE id=$1::uuid", E_A)


async def build_fixtures(conn) -> None:
    await conn.execute(
        """INSERT INTO public.entities (id, org_id, entity_type, display_name)
           VALUES ($1::uuid, $2::uuid, 'individual', $3)""",
        E_A, ORG, f"{TAG} entity",
    )
    await conn.execute(
        "INSERT INTO public.households (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
        HH, ORG, f"{TAG} household",
    )
    await conn.execute(
        """INSERT INTO public.accounts
             (id, org_id, account_number_masked, account_number_hash, custodian_code,
              registration_type, tax_status, primary_entity_id, household_id,
              is_billable, opened_on)
           VALUES ($1::uuid,$2::uuid,$3,$4,'TEST','individual','taxable',
                   $5::uuid,$6::uuid,true,'2024-01-01')""",
        ACC_A, ORG, "***A", f"{TAG}-A", E_A, HH,
    )
    await conn.execute(
        """INSERT INTO public.billing_groups (id, org_id, name, group_type, household_id)
           VALUES ($1::uuid,$2::uuid,$3,'BREAKPOINT',$4::uuid)""",
        BG, ORG, f"{TAG} group", HH,
    )
    await conn.execute(
        """INSERT INTO public.billing_group_members
             (org_id, billing_group_id, account_id, valid_from)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'2024-01-01')""",
        ORG, BG, ACC_A,
    )


# ═══════════════════════════════════════════════════════════════════════════
# [1] Deployment, RLS, constraint shape
# ═══════════════════════════════════════════════════════════════════════════

TABLES = (
    "cost_providers",
    "cost_schedules",
    "cost_pass_through_policies",
    "cost_events",
)


async def check_1(conn) -> None:
    present = {
        r["relname"]: r
        for r in await conn.fetch(
            """SELECT t.relname, t.relrowsecurity,
                      (SELECT count(*) FROM pg_policy p WHERE p.polrelid=t.oid) n
               FROM pg_class t JOIN pg_namespace ns ON ns.oid=t.relnamespace
               WHERE ns.nspname='public' AND t.relname = ANY($1::text[])""",
            list(TABLES),
        )
    }
    R.expect(
        "1a",
        set(present) == set(TABLES),
        "all four cost_* tables are deployed",
        f"missing={sorted(set(TABLES) - set(present))}",
    )
    if set(present) != set(TABLES):
        return

    no_rls = [t for t in TABLES if not present[t]["relrowsecurity"]]
    R.expect("1b", not no_rls, "RLS is ENABLED on all four tables", f"without={no_rls}")

    no_policy = [t for t in TABLES if present[t]["n"] == 0]
    R.expect(
        "1c",
        not no_policy,
        "each table carries an org-isolation policy — RLS enabled with zero "
        "policies denies everything and would look like isolation while being "
        "an outage",
        f"without={no_policy}",
    )

    quals = {
        r["relname"]: r["q"]
        for r in await conn.fetch(
            """SELECT c.relname, pg_get_expr(p.polqual,p.polrelid) q
               FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
               JOIN pg_namespace ns ON ns.oid=c.relnamespace
               WHERE ns.nspname='public' AND c.relname = ANY($1::text[])""",
            list(TABLES),
        )
    }
    missing_nullif = [t for t, q in quals.items() if "NULLIF" not in (q or "")]
    R.expect(
        "1d",
        not missing_nullif,
        "every policy NULLIFs the org GUC — without it an unset GUC casts '' "
        "to uuid and errors, or worse, matches",
        f"without={missing_nullif}",
    )

    # ── The deployed CHECK vocabularies, compared to the module's constants ──
    def parse_check(defn: str) -> set[str]:
        import re

        return set(re.findall(r"'([A-Z_]+)'::text", defn or ""))

    checks = {
        r["conname"]: r["d"]
        for r in await conn.fetch(
            """SELECT con.conname, pg_get_constraintdef(con.oid) d
               FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
               JOIN pg_namespace ns ON ns.oid=c.relnamespace
               WHERE ns.nspname='public' AND c.relname = ANY($1::text[])
                 AND con.contype='c'""",
            list(TABLES),
        )
    }

    expectations = [
        ("1e", "cost_providers_type_check", PROVIDER_TYPES, "provider_type"),
        ("1f", "cost_schedules_basis_check", SCHEDULE_BASES, "basis"),
        ("1g", "cost_schedules_frequency_check", SCHEDULE_FREQUENCIES, "frequency"),
        ("1h", "cost_schedules_applies_scope_check", SCHEDULE_APPLIES_SCOPES,
         "applies_scope"),
        ("1i", "cost_pass_through_policy_check", POLICIES, "policy"),
        ("1j", "cost_pass_through_scope_type_check", POLICY_SCOPE_TYPES, "scope_type"),
        ("1k", "cost_events_cost_type_check", COST_TYPES, "cost_type"),
        ("1l", "cost_events_allocation_method_check", ALLOCATION_METHODS,
         "allocation_method"),
    ]
    for ref, conname, constants, label in expectations:
        deployed = parse_check(checks.get(conname, ""))
        R.expect(
            ref,
            deployed == set(constants),
            f"{label}: the module's constant tuple and the deployed CHECK are "
            f"the SAME set ({len(constants)} values) — set equality, so drift "
            "in either direction fails",
            f"deployed-only={sorted(deployed - set(constants))} "
            f"module-only={sorted(set(constants) - deployed)}",
        )

    for ref, conname in (
        ("1m", "cost_pass_through_rate_required"),
        ("1n", "cost_pass_through_scope_id_required"),
        ("1o", "cost_pass_through_markup_requires_disclosure"),
    ):
        R.expect(ref, conname in checks, f"{conname} is deployed")

    R.expect(
        "1p",
        set(SCOPE_PRECEDENCE) == set(POLICY_SCOPE_TYPES),
        "SCOPE_PRECEDENCE covers exactly the deployed scope vocabulary and no "
        "more — an unranked scope would sort to 999 and silently lose to "
        "everything",
    )

    # ── linked_revenue_event_id: deliberately unconstrained ─────────────────
    fks = {
        r["conname"]: r["d"]
        for r in await conn.fetch(
            """SELECT con.conname, pg_get_constraintdef(con.oid) d
               FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
               JOIN pg_namespace ns ON ns.oid=c.relnamespace
               WHERE ns.nspname='public' AND c.relname='cost_events'
                 AND con.contype='f'""",
        )
    }
    R.expect(
        "1q",
        not any("linked_revenue_event_id" in d for d in fks.values()),
        "cost_events.linked_revenue_event_id carries NO foreign key — "
        "deliberate, since fee39 owns revenue_events and it does not exist yet",
        f"fks={list(fks)}",
    )
    R.expect(
        "1r",
        await conn.fetchval("SELECT to_regclass('public.revenue_events') IS NULL"),
        "public.revenue_events genuinely does not exist — which is what makes "
        "[1q] a deliberate omission rather than a missing constraint",
    )

    # ── Findings the schema itself reports ──────────────────────────────────
    prov_cols = {
        r["column_name"]
        for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='cost_providers'"
        )
    }
    if not {"source_url", "source_verified_on"} & prov_cols:
        R.find(
            "F1",
            "cost_providers has NO source_url/source_verified_on and no name "
            "column at all — provenance is expressible only per-SCHEDULE. A "
            "provider-level citation has nowhere to live.",
        )
    if not await conn.fetchval("SELECT to_regclass('public.cost_schedule_tiers') IS NOT NULL"):
        R.find(
            "F2",
            "there is no cost_schedule_tiers table (fee_schedule_tiers belongs "
            "to the revenue side and FKs to fee_schedules), so a tiered vendor "
            "rate — the Altruist One margin ladder — can only be seeded as "
            "endpoint rows.",
        )
    if not any(
        "approved_by" in d
        for d in (
            r["d"]
            for r in await conn.fetch(
                """SELECT pg_get_constraintdef(con.oid) d FROM pg_constraint con
                   JOIN pg_class c ON c.oid=con.conrelid
                   JOIN pg_namespace ns ON ns.oid=c.relnamespace
                   WHERE ns.nspname='public'
                     AND c.relname='cost_pass_through_policies'
                     AND con.contype='f'"""
            )
        )
    ):
        R.find(
            "F3",
            "cost_pass_through_policies.approved_by and "
            "disclosure_acknowledged_by have NO foreign key to users — an "
            "approval or a disclosure can name a user id that does not exist, "
            "which is exactly the field an audit would lean on.",
        )
    idx = [
        r["indexdef"]
        for r in await conn.fetch(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='cost_events'"
        )
    ]
    if not any("UNIQUE" in d and "pkey" not in d for d in idx):
        R.find(
            "F4",
            "cost_events has no unique index other than the pkey — nothing "
            "stops the same vendor charge being recorded twice for the same "
            "period. fee39's profitability rollup would double-count it.",
        )
    pol_cols = {
        r["column_name"]
        for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE "
            "table_schema='public' AND table_name='cost_pass_through_policies'"
        )
    }
    if "status" not in pol_cols:
        R.find(
            "F5",
            "cost_pass_through_policies has no status column, so 'active' has "
            "no direct representation: a policy is active by being a current "
            "row inside its effective window. Creation IS activation, which is "
            "where the MARKUP disclosure gate therefore has to live.",
        )


# ═══════════════════════════════════════════════════════════════════════════
# [2] The Altruist seed
# ═══════════════════════════════════════════════════════════════════════════


async def check_2(conn) -> dict[str, str]:
    pre_provider = await conn.fetchval(
        "SELECT id::text FROM public.cost_providers WHERE org_id=$1::uuid "
        "AND provider_code=$2 AND valid_to IS NULL AND system_to IS NULL",
        ORG, ALTRUIST_PROVIDER_CODE,
    )
    pre_schedule_ids = {
        r["id"]
        for r in await conn.fetch(
            "SELECT s.id::text AS id FROM public.cost_schedules s "
            "JOIN public.cost_providers p ON p.id = s.cost_provider_id "
            "WHERE s.org_id=$1::uuid AND p.provider_code=$2",
            ORG, ALTRUIST_PROVIDER_CODE,
        )
    }

    seeded = await seed_altruist_profile(conn, ORG, source_verified_on=TODAY)
    SEEDED["provider_id"] = seeded.provider_id
    SEEDED["provider_was_new"] = pre_provider is None
    SEEDED["new_schedule_ids"] = [
        sid for sid in seeded.schedule_ids.values() if sid not in pre_schedule_ids
    ]

    R.expect(
        "2a",
        seeded.provider_code == ALTRUIST_PROVIDER_CODE
        and seeded.provider_id is not None,
        "the ALTRUIST provider row exists after seeding",
        f"provider_id={seeded.provider_id}",
    )
    ptype = await conn.fetchval(
        "SELECT provider_type FROM public.cost_providers WHERE id=$1::uuid",
        seeded.provider_id,
    )
    R.expect("2b", ptype == "CUSTODIAN", "ALTRUIST is typed CUSTODIAN", f"got={ptype}")

    expected_codes = {r.cost_code for r in ALTRUIST_SCHEDULES}
    R.expect(
        "2c",
        set(seeded.schedule_ids) == expected_codes,
        f"all {len(expected_codes)} rate-card schedules are seeded",
        f"missing={sorted(expected_codes - set(seeded.schedule_ids))}",
    )

    rows = await conn.fetch(
        """SELECT s.cost_code, s.basis, s.rate, s.flat_amount, s.minimum_amount,
                  s.frequency, s.applies_scope, s.source_url, s.source_verified_on
           FROM public.cost_schedules s
           WHERE s.id = ANY($1::uuid[])""",
        list(seeded.schedule_ids.values()),
    )
    by_code = {r["cost_code"]: r for r in rows}

    unsourced = [
        c
        for c, r in by_code.items()
        if not r["source_url"] or r["source_verified_on"] is None
    ]
    R.expect(
        "2d",
        not unsourced,
        f"every one of the {len(by_code)} seeded schedules has a non-null "
        "source_url AND source_verified_on",
        f"unsourced={unsourced}",
    )

    # Each seeded row matches the constant it came from — catches a seeder that
    # writes rows successfully but writes the wrong numbers into them.
    drift = []
    for spec in ALTRUIST_SCHEDULES:
        got = by_code.get(spec.cost_code)
        if got is None:
            drift.append(f"{spec.cost_code}: absent")
            continue
        # Text columns compared as text, numeric ones as Decimal. numeric(14,8)
        # comes back as 0.00010000, so a string comparison of the numerics
        # would fail on scale alone and prove nothing about the value.
        for fname, want, numeric in (
            ("basis", spec.basis, False),
            ("frequency", spec.frequency, False),
            ("applies_scope", spec.applies_scope, False),
            ("rate", spec.rate, True),
            ("flat_amount", spec.flat_amount, True),
            ("minimum_amount", spec.minimum_amount, True),
        ):
            have = got[fname]
            if want is None:
                if have is not None:
                    drift.append(f"{spec.cost_code}.{fname}: {have!r} != None")
            elif have is None:
                drift.append(f"{spec.cost_code}.{fname}: None != {want!r}")
            elif numeric:
                if D(str(have)) != D(str(want)):
                    drift.append(f"{spec.cost_code}.{fname}: {have!r} != {want!r}")
            elif str(have) != str(want):
                drift.append(f"{spec.cost_code}.{fname}: {have!r} != {want!r}")
    R.expect(
        "2e",
        not drift,
        "every seeded row's basis/frequency/scope/rate/flat/minimum matches "
        "the rate-card constant it came from",
        "; ".join(drift[:6]),
    )

    # ── the ambiguity guard rail, BOTH directions ───────────────────────────
    group, readings = next(iter(AMBIGUITY_GROUPS.items()))
    both = list(readings[0]) + list(readings[1])
    try:
        assert_no_ambiguous_overlap(both)
        R.bad(
            "2f",
            "a selection drawing from BOTH readings of the subscription line "
            "was accepted — that is a double-count",
        )
    except AmbiguousRateCardError as exc:
        R.expect(
            "2f",
            exc.group == group,
            "drawing from both readings of one card line is REFUSED, naming "
            "the group",
            f"group={exc.group}",
        )
    try:
        assert_no_ambiguous_overlap(list(readings[1]) + ["ALTRUIST_DIRECT_INDEXING"])
        R.ok(
            "2g",
            "a selection using exactly ONE reading plus unrelated codes is "
            "ACCEPTED — the guard narrows, it does not refuse everything",
        )
    except AmbiguousRateCardError as exc:
        R.bad("2g", "a single valid reading was refused", str(exc))

    # ── idempotency: run two adopts, inserts nothing ────────────────────────
    n_before = await conn.fetchval("SELECT count(*) FROM public.cost_schedules")
    again = await seed_altruist_profile(conn, ORG, source_verified_on=TODAY)
    n_after = await conn.fetchval("SELECT count(*) FROM public.cost_schedules")
    R.expect(
        "2h",
        n_after == n_before and again.schedule_ids == seeded.schedule_ids
        and again.created is False,
        "re-seeding is idempotent: no new cost_schedules rows and the same "
        "ids come back — which is also what makes this script safe to run "
        "against an already-seeded production org",
        f"before={n_before} after={n_after} created={again.created}",
    )

    R.find(
        "F6",
        "the seeded rates are UNVERIFIED. They come from the original "
        "design-doc research, which had no live web access; a re-check against "
        "altruist.com was attempted during this sprint and could not be "
        f"performed either. source_verified_on={TODAY} means ENTERED ON, not "
        "confirmed-against-the-source on. Do not bill from these numbers until "
        "a human re-reads the source and re-stamps the date.",
    )
    return seeded.schedule_ids


# ═══════════════════════════════════════════════════════════════════════════
# [3] The four policies
# ═══════════════════════════════════════════════════════════════════════════

#: Hand-derived, not computed by the code under test.
#:   cost                      = 1234.5678
#:   ABSORB                    -> 0.00
#:   PASS_FULL      x 1        -> 1234.5678 -> 1234.57 (cents, HALF_UP)
#:   PASS_PARTIAL   x 0.50     ->  617.2839 ->  617.28
#:   MARKUP         x 1.25     -> 1543.20975 -> 1543.21
EXPECTED = {
    "ABSORB": (None, D("0.00"), False),
    "PASS_FULL": (D("1"), D("1234.57"), True),
    "PASS_PARTIAL": (D("0.50"), D("617.28"), True),
    "MARKUP": (D("1.25"), D("1543.21"), True),
}


async def check_3(conn, indep_conn, schedule_ids) -> None:
    sched = schedule_ids["ALTRUIST_DIRECT_INDEXING"]

    for i, (policy, (rate, want_rev, want_passed)) in enumerate(EXPECTED.items()):
        ref = f"3{'abcd'[i]}"
        # Fresh ACCOUNT-scoped policy per case; removed before the next.
        await conn.execute(
            "DELETE FROM public.cost_pass_through_policies "
            "WHERE org_id=$1::uuid AND reason LIKE $2",
            ORG, f"{TAG}%",
        )
        kwargs = {}
        if policy == "MARKUP":
            kwargs = {
                "disclosure_acknowledged_by": U_DISCLOSER,
                "disclosure_acknowledged_at": ACK_AT,
            }
        await create_pass_through_policy(
            conn, ORG,
            cost_schedule_id=sched,
            scope_type="ACCOUNT", scope_id=ACC_A,
            policy=policy, pass_through_rate=rate,
            approved_by=U_APPROVER,
            reason=f"{TAG} {policy} case",
            effective_from=date(2026, 1, 1),
            **kwargs,
        )
        rec = await record_cost_event(
            conn, ORG,
            amount=FIXTURE_COST,
            cost_type="DIRECT_INDEXING",
            event_date=EVENT_DAY,
            period_start=P_START, period_end=P_END,
            allocation_method="DIRECT",
            cost_schedule_id=sched,
            account_id=ACC_A,
            allocation_driver=TAG,
            product_type=TAG,
        )

        # Re-read from an INDEPENDENT connection: the returned object proves
        # nothing about what landed.
        row = await indep_conn.fetchrow(
            "SELECT amount, is_passed_through, cost_type, linked_revenue_event_id, "
            "       period_start, period_end "
            "FROM public.cost_events WHERE id=$1::uuid",
            rec.cost_event_id,
        )
        checks = [
            (row is not None, "the cost_event persisted"),
            (row is not None and D(str(row["amount"])) == FIXTURE_COST,
             f"the RECORDED cost is the real cost {FIXTURE_COST}, unchanged by "
             "the policy"),
            (rec.outcome.implied_revenue == want_rev,
             f"implied revenue is {want_rev} (hand-derived, not re-computed)"),
            (row is not None and row["is_passed_through"] is want_passed,
             f"is_passed_through={want_passed}"),
            (rec.revenue_event_id is None
             and row is not None and row["linked_revenue_event_id"] is None,
             "no revenue_event is written and linked_revenue_event_id stays "
             "NULL — fee39 owns that table and it does not exist"),
            (rec.resolved_policy is not None
             and rec.resolved_policy.policy == policy,
             f"the resolved policy is {policy}"),
        ]
        bad = [m for cond, m in checks if not cond]
        R.expect(
            ref,
            not bad,
            f"{policy}: cost {FIXTURE_COST} -> revenue {want_rev}, event "
            "persisted and re-read independently",
            f"failed: {bad} | got_rev={rec.outcome.implied_revenue} "
            f"row={dict(row) if row else None}",
        )

    # ── [3e] ABSORB still WRITES — the invariant the table exists for ───────
    await conn.execute(
        "DELETE FROM public.cost_pass_through_policies "
        "WHERE org_id=$1::uuid AND reason LIKE $2",
        ORG, f"{TAG}%",
    )
    await conn.execute(
        "DELETE FROM public.cost_events WHERE org_id=$1::uuid AND product_type=$2",
        ORG, TAG,
    )
    await create_pass_through_policy(
        conn, ORG, cost_schedule_id=sched,
        scope_type="ACCOUNT", scope_id=ACC_A,
        policy="ABSORB", pass_through_rate=None,
        approved_by=U_APPROVER, reason=f"{TAG} absorb writes",
        effective_from=date(2026, 1, 1),
    )
    rec = await record_cost_event(
        conn, ORG, amount=FIXTURE_COST, cost_type="DIRECT_INDEXING",
        event_date=EVENT_DAY, allocation_method="DIRECT",
        cost_schedule_id=sched, account_id=ACC_A, product_type=TAG,
    )
    n = await indep_conn.fetchval(
        "SELECT count(*) FROM public.cost_events WHERE org_id=$1::uuid "
        "AND product_type=$2",
        ORG, TAG,
    )
    R.expect(
        "3e",
        n == 1 and rec.outcome.implied_revenue == D("0.00")
        and rec.outcome.is_passed_through is False,
        "ABSORB bills nobody and STILL writes the cost_event — a rollup that "
        "only saw passed-through costs would report absorbed ones as free",
        f"events={n} rev={rec.outcome.implied_revenue}",
    )

    # ── [3f] the rate bands are real, in both directions ────────────────────
    band_cases = [
        ("PASS_FULL", D("0.5"), True),
        ("PASS_PARTIAL", D("1"), True),
        ("MARKUP", D("0.9"), True),
        ("ABSORB", D("0"), True),
        ("PASS_PARTIAL", D("0.5"), False),
        ("MARKUP", D("1.25"), False),
        ("PASS_FULL", D("1"), False),
    ]
    wrong = []
    for policy, rate, should_raise in band_cases:
        try:
            compute_pass_through(FIXTURE_COST, policy, rate)
            if should_raise:
                wrong.append(f"{policy}@{rate} accepted")
        except PassThroughRateError:
            if not should_raise:
                wrong.append(f"{policy}@{rate} refused")
    R.expect(
        "3f",
        not wrong,
        "the policy label constrains the rate band both ways: PASS_FULL@0.5, "
        "PASS_PARTIAL@1, MARKUP@0.9 and ABSORB@0 are refused while the "
        "in-band values are accepted — the DB's own CHECK enforces presence "
        "only, so a PASS_FULL passing half a cost would otherwise insert",
        str(wrong),
    )

    # ── [3g] the sub-cent residual is surfaced, not silently eaten ──────────
    out = compute_pass_through(D("0.12345"), "PASS_FULL", D("1"))
    R.expect(
        "3g",
        out.cost_amount == D("0.1235")
        and out.implied_revenue == D("0.12")
        and out.residual_absorbed == D("0.00350000"),
        "a $0.12345 cost passed through at 100% records 0.1235 (the column's "
        "own 4dp scale), bills $0.12, and REPORTS the 0.0035 residual rather "
        "than losing it",
        f"cost={out.cost_amount} rev={out.implied_revenue} "
        f"residual={out.residual_absorbed}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# [4] The MARKUP disclosure gate
# ═══════════════════════════════════════════════════════════════════════════


async def check_4(conn, schedule_ids) -> None:
    sched = schedule_ids["ALTRUIST_DIRECT_INDEXING"]
    await conn.execute(
        "DELETE FROM public.cost_pass_through_policies "
        "WHERE org_id=$1::uuid AND reason LIKE $2",
        ORG, f"{TAG}%",
    )

    # ── [4a] REPRODUCE the gap: the database accepts the bad state ──────────
    raw_id = None
    try:
        raw_id = await conn.fetchval(
            """INSERT INTO public.cost_pass_through_policies
                 (org_id, cost_schedule_id, scope_type, scope_id, policy,
                  pass_through_rate, disclosure_required, approved_by, reason,
                  effective_from)
               VALUES ($1::uuid,$2::uuid,'ACCOUNT',$3::uuid,'MARKUP',
                       1.25, true, $4::uuid, $5, '2026-01-01')
               RETURNING id::text""",
            ORG, sched, ACC_A, U_APPROVER, f"{TAG} raw undisclosed markup",
        )
        R.expect(
            "4a",
            raw_id is not None,
            "REPRODUCED: raw SQL inserts a MARKUP policy with "
            "disclosure_required=true and BOTH acknowledgement columns NULL. "
            "cost_pass_through_markup_requires_disclosure checks the FLAG, not "
            "the acknowledgement — this is the hole the service gate exists to "
            "close",
        )
    except Exception as exc:  # noqa: BLE001
        R.bad("4a", "the raw undisclosed MARKUP insert was refused by the DB "
                    "— then the app-layer gate is redundant, not load-bearing",
              str(exc))

    # ── [4b] the belt: a resolved undisclosed MARKUP must not price ─────────
    if raw_id:
        resolved = await resolve_pass_through_policy(
            conn, ORG, sched, account_id=ACC_A, as_of=EVENT_DAY
        )
        try:
            await record_cost_event(
                conn, ORG, amount=FIXTURE_COST, cost_type="DIRECT_INDEXING",
                event_date=EVENT_DAY, allocation_method="DIRECT",
                cost_schedule_id=sched, account_id=ACC_A, product_type=TAG,
                resolved_policy=resolved,
            )
            R.bad(
                "4b",
                "an undisclosed MARKUP policy that already exists in the table "
                "was allowed to price a client charge",
            )
        except DisclosureRequiredError as exc:
            R.expect(
                "4b",
                set(exc.missing)
                == {"disclosure_acknowledged_by", "disclosure_acknowledged_at"},
                "a pre-existing undisclosed MARKUP policy is refused at "
                "PRICING time too, naming both missing fields — the gate is "
                "not only at insert, so a row planted by raw SQL cannot bill",
                f"missing={exc.missing}",
            )
        await conn.execute(
            "DELETE FROM public.cost_pass_through_policies WHERE id=$1::uuid", raw_id
        )

    # ── [4c] the service refuses to create it, and nothing lands ────────────
    n_before = await conn.fetchval(
        "SELECT count(*) FROM public.cost_pass_through_policies"
    )
    try:
        await create_pass_through_policy(
            conn, ORG, cost_schedule_id=sched,
            scope_type="ACCOUNT", scope_id=ACC_A,
            policy="MARKUP", pass_through_rate=D("1.25"),
            approved_by=U_APPROVER, reason=f"{TAG} undisclosed",
            effective_from=date(2026, 1, 1),
        )
        R.bad("4c", "a MARKUP policy with no disclosure acknowledgement was "
                    "made active")
    except DisclosureRequiredError as exc:
        n_after = await conn.fetchval(
            "SELECT count(*) FROM public.cost_pass_through_policies"
        )
        R.expect(
            "4c",
            n_after == n_before
            and set(exc.missing)
            == {"disclosure_acknowledged_by", "disclosure_acknowledged_at"},
            "creating an undisclosed MARKUP policy is REFUSED and leaves the "
            "table unchanged — a service that raised after inserting would "
            "pass a bare 'it raised' check",
            f"before={n_before} after={n_after} missing={exc.missing}",
        )

    # ── [4d] half-acknowledged is still refused ─────────────────────────────
    for label, kw in (
        ("by-only", {"disclosure_acknowledged_by": U_DISCLOSER}),
        ("at-only", {"disclosure_acknowledged_at": ACK_AT}),
    ):
        try:
            await create_pass_through_policy(
                conn, ORG, cost_schedule_id=sched,
                scope_type="ACCOUNT", scope_id=ACC_A,
                policy="MARKUP", pass_through_rate=D("1.25"),
                approved_by=U_APPROVER, reason=f"{TAG} half {label}",
                effective_from=date(2026, 1, 1), **kw,
            )
            R.bad("4d", f"a half-acknowledged MARKUP ({label}) was accepted")
            break
        except DisclosureRequiredError:
            pass
    else:
        R.ok(
            "4d",
            "a MARKUP acknowledged by somebody at no time, or at a time by "
            "nobody, is refused as well — both columns are required together",
        )

    # ── [4e] the positive direction: fully acknowledged succeeds ────────────
    created = await create_pass_through_policy(
        conn, ORG, cost_schedule_id=sched,
        scope_type="ACCOUNT", scope_id=ACC_A,
        policy="MARKUP", pass_through_rate=D("1.25"),
        approved_by=U_APPROVER, reason=f"{TAG} disclosed markup",
        effective_from=date(2026, 1, 1),
        disclosure_acknowledged_by=U_DISCLOSER,
        disclosure_acknowledged_at=ACK_AT,
    )
    R.expect(
        "4e",
        created["policy"] == "MARKUP",
        "a MARKUP policy WITH both acknowledgement fields is accepted — the "
        "gate narrows, it does not block markups outright",
        f"got={created}",
    )
    await conn.execute(
        "DELETE FROM public.cost_pass_through_policies "
        "WHERE org_id=$1::uuid AND reason LIKE $2",
        ORG, f"{TAG}%",
    )


# ═══════════════════════════════════════════════════════════════════════════
# [5] Precedence
# ═══════════════════════════════════════════════════════════════════════════


async def check_5(conn, schedule_ids) -> None:
    sched = schedule_ids["ALTRUIST_MODEL_MARKETPLACE_PAID_LOW"]
    await conn.execute(
        "DELETE FROM public.cost_pass_through_policies "
        "WHERE org_id=$1::uuid AND reason LIKE $2",
        ORG, f"{TAG}%",
    )

    async def mk(scope_type, scope_id, policy, rate, label):
        return await create_pass_through_policy(
            conn, ORG, cost_schedule_id=sched,
            scope_type=scope_type, scope_id=scope_id,
            policy=policy, pass_through_rate=rate,
            approved_by=U_APPROVER, reason=f"{TAG} prec {label}",
            effective_from=date(2026, 1, 1),
        )

    # ── ORG_DEFAULT alone ───────────────────────────────────────────────────
    await mk("ORG_DEFAULT", None, "ABSORB", None, "org")
    r = await resolve_pass_through_policy(
        conn, ORG, sched, account_id=ACC_A, as_of=EVENT_DAY
    )
    R.expect(
        "5a",
        r is not None and r.scope_type == "ORG_DEFAULT" and r.policy == "ABSORB"
        and r.precedence == SCOPE_PRECEDENCE["ORG_DEFAULT"],
        "with only an ORG_DEFAULT policy, an account falls back to it",
        f"got={r}",
    )

    # ── HOUSEHOLD beats ORG_DEFAULT ─────────────────────────────────────────
    await mk("HOUSEHOLD", HH, "PASS_PARTIAL", D("0.25"), "hh")
    r = await resolve_pass_through_policy(
        conn, ORG, sched, account_id=ACC_A, as_of=EVENT_DAY
    )
    R.expect(
        "5b",
        r is not None and r.scope_type == "HOUSEHOLD"
        and {l["scope_type"] for l in r.losers} == {"ORG_DEFAULT"},
        "HOUSEHOLD outranks ORG_DEFAULT, and the ORG_DEFAULT is reported as a "
        "loser rather than discarded",
        f"got={r.scope_type if r else None} "
        f"losers={[l['scope_type'] for l in (r.losers if r else ())]}",
    )

    # ── BILLING_GROUP beats HOUSEHOLD ───────────────────────────────────────
    await mk("BILLING_GROUP", BG, "PASS_FULL", D("1"), "bg")
    r = await resolve_pass_through_policy(
        conn, ORG, sched, account_id=ACC_A, as_of=EVENT_DAY
    )
    R.expect(
        "5c",
        r is not None and r.scope_type == "BILLING_GROUP" and len(r.losers) == 2,
        "BILLING_GROUP outranks HOUSEHOLD, which outranks ORG_DEFAULT — the "
        "group membership is gathered from the account, not passed in",
        f"got={r.scope_type if r else None} losers={len(r.losers) if r else 0}",
    )

    # ── ACCOUNT beats everything: THE requirement ───────────────────────────
    await mk("ACCOUNT", ACC_A, "PASS_PARTIAL", D("0.75"), "acct")
    r = await resolve_pass_through_policy(
        conn, ORG, sched, account_id=ACC_A, as_of=EVENT_DAY
    )
    R.expect(
        "5d",
        r is not None
        and r.scope_type == "ACCOUNT"
        and r.policy == "PASS_PARTIAL"
        and r.pass_through_rate is not None
        and D(str(r.pass_through_rate)) == D("0.75")
        and r.precedence == SCOPE_PRECEDENCE["ACCOUNT"]
        and {l["scope_type"] for l in r.losers}
        == {"BILLING_GROUP", "HOUSEHOLD", "ORG_DEFAULT"},
        "an ACCOUNT-level policy overrides the ORG_DEFAULT (and the group and "
        "household) for the SAME cost_schedule and the SAME as-of date — the "
        "resolution MOVED from ORG_DEFAULT in [5a] to ACCOUNT here on the same "
        "account, so neither answer is hard-wired",
        f"got={r}",
    )

    # ── the winner actually drives the money ────────────────────────────────
    rec = await record_cost_event(
        conn, ORG, amount=D("1000.0000"), cost_type="MODEL_FEE",
        event_date=EVENT_DAY, allocation_method="DIRECT",
        cost_schedule_id=sched, account_id=ACC_A, product_type=TAG,
    )
    R.expect(
        "5e",
        rec.outcome.implied_revenue == D("750.00")
        and rec.resolved_policy is not None
        and rec.resolved_policy.scope_type == "ACCOUNT",
        "the resolved winner is the rate that actually prices the cost: "
        "$1000 at the ACCOUNT policy's 0.75 gives $750.00, not the "
        "ORG_DEFAULT's ABSORB $0 nor the group's PASS_FULL $1000",
        f"rev={rec.outcome.implied_revenue}",
    )

    # ── effective-window is real ────────────────────────────────────────────
    r_before = await resolve_pass_through_policy(
        conn, ORG, sched, account_id=ACC_A, as_of=date(2025, 6, 1)
    )
    R.expect(
        "5f",
        r_before is None,
        "as-of a date BEFORE every policy's effective_from, nothing resolves — "
        "the effective window narrows rather than being decorative, and the "
        "resolver returns None rather than silently absorbing",
        f"got={r_before}",
    )

    # ── no policy at all -> recorded, flagged, not silently absorbed ────────
    await conn.execute(
        "DELETE FROM public.cost_pass_through_policies "
        "WHERE org_id=$1::uuid AND reason LIKE $2",
        ORG, f"{TAG}%",
    )
    rec = await record_cost_event(
        conn, ORG, amount=D("500.0000"), cost_type="MODEL_FEE",
        event_date=EVENT_DAY, allocation_method="DIRECT",
        cost_schedule_id=sched, account_id=ACC_A, product_type=TAG,
    )
    R.expect(
        "5g",
        rec.resolved_policy is None
        and rec.outcome.implied_revenue == D("0.00")
        and rec.warnings
        and "unruled" in rec.warnings[0],
        "a cost with NO policy at all is still recorded, is not passed "
        "through, and carries an explicit 'unruled' warning — an unruled cost "
        "is not the same as a decision to absorb, and the difference matters "
        "to whoever reviews it",
        f"warnings={rec.warnings}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# [6] Cross-org isolation, on app_service
# ═══════════════════════════════════════════════════════════════════════════


async def check_6(app_dsn, admin_conn, schedule_ids) -> None:
    if app_dsn is None:
        R.blocked("6", "no working app_service DSN — RLS is unprovable on postgres")
        return

    # check_5 ends by clearing every policy row, so a live one has to be put
    # back here. An isolation probe that reads zero rows on BOTH sides passes
    # a naive "the other org saw nothing" check while proving nothing at all —
    # which is why [6b-e] assert own >= 1 as well as other == 0.
    await create_pass_through_policy(
        admin_conn, ORG,
        cost_schedule_id=schedule_ids["ALTRUIST_DIRECT_INDEXING"],
        scope_type="ORG_DEFAULT", scope_id=None,
        policy="ABSORB", pass_through_rate=None,
        approved_by=U_APPROVER, reason=f"{TAG} isolation probe",
        effective_from=date(2026, 1, 1),
    )

    conn = await connect(app_dsn)
    try:
        bypass = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if not R.expect(
            "6a",
            bypass is False,
            "the test role does NOT bypass RLS — without this every check "
            "below is vacuous",
            f"rolbypassrls={bypass}",
        ):
            return

        async def as_org(org, sql, *args):
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute(
                    "SELECT set_config('app.current_org_id', $1, true)", org
                )
                await conn.execute(
                    "SELECT set_config('app.is_super_admin', 'false', true)"
                )
                return await conn.fetch(sql, *args)
            finally:
                await tx.rollback()

        sched = schedule_ids["ALTRUIST_DIRECT_INDEXING"]
        provider_id = SEEDED["provider_id"]

        probes = [
            ("6b", "cost_providers",
             "SELECT id FROM public.cost_providers WHERE id=$1::uuid", provider_id),
            ("6c", "cost_schedules",
             "SELECT id FROM public.cost_schedules WHERE id=$1::uuid", sched),
            ("6d", "cost_pass_through_policies",
             "SELECT id FROM public.cost_pass_through_policies "
             "WHERE org_id=$1::uuid AND reason LIKE 'fee37verify%'", ORG),
            ("6e", "cost_events",
             "SELECT id FROM public.cost_events WHERE org_id=$1::uuid "
             "AND product_type='fee37verify'", ORG),
        ]
        for ref, table, sql, arg in probes:
            mine = await as_org(ORG, sql, arg)
            theirs = await as_org(OTHER_ORG, sql, arg)
            empty = await as_org("", sql, arg)
            R.expect(
                ref,
                len(mine) >= 1 and len(theirs) == 0 and len(empty) == 0,
                f"{table}: the owning org sees the rows, the other org sees "
                "none, and an EMPTY org GUC sees none — inclusion, exclusion, "
                "and the policy's NULLIF, on the same rows",
                f"own={len(mine)} other={len(theirs)} empty={len(empty)}",
            )

        # WITH CHECK: writing INTO another org is refused, not merely hidden.
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true)", OTHER_ORG
            )
            await conn.execute(
                "SELECT set_config('app.is_super_admin', 'false', true)"
            )
            try:
                await conn.execute(
                    "INSERT INTO public.cost_providers (org_id, provider_code, "
                    "provider_type) VALUES ($1::uuid, $2, 'CUSTODIAN')",
                    ORG, f"{TAG}-XORG",
                )
                R.bad(
                    "6f",
                    "an org was able to INSERT a cost_provider into ANOTHER "
                    "org — the policy's WITH CHECK is not doing anything",
                )
            except Exception as exc:  # noqa: BLE001
                R.expect(
                    "6f",
                    "policy" in str(exc).lower(),
                    "inserting a cost_provider whose org_id is not the "
                    "connection's org is refused by the policy's WITH CHECK, "
                    "not merely hidden from reads",
                    str(exc)[:160],
                )
        finally:
            await tx.rollback()
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    app_url, app_prov = await app_service_dsn()
    print(f"admin dsn:       {admin_prov}")
    print(f"app_service dsn: {app_prov}\n")
    if admin_url is None:
        print("BLOCKED: no working admin DSN")
        return 2

    conn = await connect(admin_url)
    indep = await connect(admin_url)
    before = None
    try:
        await teardown(conn)
        before = await counts(conn)
        print("pre-test row counts captured\n")

        await build_fixtures(conn)

        await check_1(conn)
        schedule_ids = await check_2(conn)
        await check_3(conn, indep, schedule_ids)
        await check_4(conn, schedule_ids)
        await check_5(conn, schedule_ids)
        await check_6(app_url, conn, schedule_ids)

    except Exception:
        R.bad("RUN", "the script raised", traceback.format_exc())
    finally:
        try:
            await teardown(conn)
        except Exception:
            R.bad("TEARDOWN", "teardown raised", traceback.format_exc())

        R.summary()

        if before is not None:
            after = await counts(conn)
            drift = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
            if drift:
                R.bad(
                    "7", "row counts differ after teardown",
                    json.dumps({k: list(v) for k, v in drift.items()}),
                )
                print(
                    "\n[FAIL] 7  ROW COUNT DRIFT: "
                    + json.dumps({k: list(v) for k, v in drift.items()})
                )
            else:
                print(
                    f"\n[PASS] 7  every one of {len(COUNTED)} touched tables is "
                    "back to its pre-test row count"
                )
                R.rows.append(("PASS", "7", "no row-count drift"))
        await indep.close()
        await conn.close()

    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
