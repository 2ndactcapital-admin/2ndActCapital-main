"""fee41 Part 1 (beyond) — seed the narrative label vocabulary.

Idempotent, self-verifying. Run:

    python3 scripts/_apply_fee41_part1.py


WHY THIS FILE EXISTS AT ALL
──────────────────────────────────────────────────────────────────────────────
Joe applied ``fee_narrative_templates`` and ``fee_narratives`` directly. Task 1
confirmed both live and found them correct — RLS on, the right org_isolation
policy with the NULLIF guard, the ``adv_check_status`` CHECK already spelling
``MATCHED | DIVERGENT | UNCHECKED``. Nothing about those two tables needs
changing.

What is missing is DATA, and it is missing for a Rule 1 reason. The renderer has
to put a human word where the database holds ``reporting_tool_addepar``,
``AVG_MONTH_END`` or ``GRADUATED``. Rule 1 says that word is config, not a
Python dict — so ``services/fee_narratives.py`` refuses to render a label it
cannot find (``VocabularyMissing``) rather than falling back to the raw enum
token, because the fallback's failure mode is a client signing an agreement
that says their assets are valued per ``AVG_MONTH_END``.

That refusal is only safe if the rows exist. This script is what makes them
exist, and — per fee40's F40-I — it is Python rather than a ``.sql`` file
because ``docs/schema_snapshot.sql`` records neither CHECK bodies nor RLS
policies nor seed data, so a bare migration file is one nobody re-checks.

SEEDED FOR EVERY ORG, NOT JUST THE DEFAULT ONE
──────────────────────────────────────────────────────────────────────────────
``config`` is per-org and ``config_key`` is unique per org. A seed that covered
only ``00000000-…-0001`` would leave the Hollisworks platform org — and every
org onboarded later — unable to render a narrative, with a
``VocabularyMissing`` naming a config_key nobody had ever seen. So the script
enumerates ``organizations`` and seeds each. Re-running after a new org is
onboarded is the supported way to provision it.

WHAT IT WILL NOT DO
──────────────────────────────────────────────────────────────────────────────
It does not UPDATE a label that already exists. The labels below are a starting
vocabulary; the words in a firm's own agreements are the firm's to choose, and
an applier that overwrote them on every run would silently revert an operator's
edit. ``ON CONFLICT DO NOTHING`` is deliberate, and the verification reports
divergence rather than correcting it.
"""

from __future__ import annotations

import asyncio
import glob
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, connect  # noqa: E402

from services.fee_narratives import (  # noqa: E402
    LABELLED_DOMAINS,
    VOCAB_CATEGORY,
    vocab_config_key,
)

#: The starting vocabulary. Voice per CLAUDE.md's brand system: quiet and
#: precise, the words a private-client agreement actually uses. Never a product
#: name where a description will do — "the aggregated reporting record" reads as
#: a valuation policy; "Addepar" reads as an endorsement, and it also breaks the
#: day the firm changes vendors while the signed agreements do not.
LABELS: dict[str, dict[str, str]] = {
    "source_system": {
        "reporting_tool_bd": "the broker-dealer reporting record",
        "reporting_tool_addepar": "the aggregated reporting record",
        "reporting_tool_orion": "the portfolio accounting record",
        "reporting_tool_apx": "the portfolio accounting record",
        "reporting_tool_import": "the reporting record supplied by the adviser",
        "altruist": "the custodial record",
        "spv_subscriptions": "the subscription register of the vehicle",
        "chancery": "the executed offering and subscription documents",
        "manual": "a valuation recorded by the adviser",
    },
    "valuation_method": {
        "PERIOD_END": "the market value on the last day of the billing period",
        "PERIOD_START": "the market value on the first day of the billing period",
        "AVG_DAILY": "the average daily market value over the billing period",
        "AVG_MONTH_END": "the average of the month-end market values in the "
                         "billing period",
    },
    "rate_type": {
        "BPS": "an asset-based fee expressed in basis points",
        "FLAT": "a flat fee",
        "HYBRID": "a combination of an asset-based fee and a flat fee",
        "HOURLY": "an hourly fee",
        "PER_ACCOUNT": "a fee charged for each account",
    },
    "tier_method": {
        "GRADUATED": "each portion of the portfolio is charged at the rate for "
                     "its own band",
        "CLIFF": "the entire portfolio is charged at the rate for the band it "
                 "falls in",
        "BLENDED_PUBLISHED": "a single blended rate published for the portfolio "
                             "as a whole",
    },
    "billing_frequency": {
        "MONTHLY": "monthly",
        "QUARTERLY": "quarterly",
        "SEMIANNUAL": "semi-annually",
        "ANNUAL": "annually",
    },
    "billing_timing": {
        "ADVANCE": "in advance",
        "ARREARS": "in arrears",
    },
    "proration_method": {
        "CALENDAR_DAYS": "prorated by calendar days",
        "BUSINESS_DAYS": "prorated by business days",
        "NONE": "not prorated",
    },
    "precedence_origin": {
        "household_override": "an arrangement agreed for this household",
        "org_setting": "the adviser's standing valuation policy",
        "platform_default": "the adviser's standing valuation policy",
    },
}

# The seed must cover every value the CHECK constraints admit. A domain value
# with no label is a render that dies with VocabularyMissing the first time a
# schedule uses it — which is correct behaviour and a terrible way to find out.
# Asserted at import so the file cannot be edited into that state.
for _domain, _values in LABELLED_DOMAINS.items():
    _missing = sorted(set(_values) - set(LABELS.get(_domain, {})))
    assert not _missing, f"LABELS is missing {_domain} value(s): {_missing}"
    _extra = sorted(set(LABELS.get(_domain, {})) - set(_values))
    assert not _extra, (
        f"LABELS declares {_domain} value(s) no CHECK constraint admits: "
        f"{_extra} — a label for a value no row can carry is dead data"
    )


async def main() -> int:
    dsn, provenance = await admin_dsn()
    if not dsn:
        print(f"FAIL: no working admin DSN: {provenance}")
        return 1
    print(f"admin: {provenance}")

    conn = await connect(dsn)
    try:
        orgs = [r["id"] for r in await conn.fetch(
            "SELECT id::text AS id FROM public.organizations ORDER BY id")]
        if not orgs:
            print("FAIL: no organizations — nothing to seed")
            return 1
        print(f"orgs: {len(orgs)}")

        expected: dict[str, str] = {}
        for domain, values in LABELLED_DOMAINS.items():
            for order, value in enumerate(values, start=1):
                expected[vocab_config_key(domain, value)] = LABELS[domain][value]

        inserted = 0
        for org_id in orgs:
            for order, (key, label) in enumerate(sorted(expected.items()), start=1):
                status = await conn.execute(
                    """
                    INSERT INTO public.config
                        (org_id, config_key, config_value, value_type, category,
                         display_order, is_active)
                    VALUES ($1::uuid, $2, $3, 'string', $4, $5, true)
                    ON CONFLICT (org_id, config_key) DO NOTHING
                    """,
                    org_id, key, label, VOCAB_CATEGORY, order,
                )
                inserted += int(status.rsplit(" ", 1)[-1])
        print(f"inserted {inserted} new config row(s) "
              f"({len(expected)} keys x {len(orgs)} org(s) expected present)")

        # VERIFY against the live catalog. An "INSERT 0 0" is not evidence of
        # anything except that the statement ran.
        failures = 0
        for org_id in orgs:
            rows = await conn.fetch(
                "SELECT config_key, config_value, is_active FROM public.config "
                "WHERE org_id = $1::uuid AND category = $2",
                org_id, VOCAB_CATEGORY,
            )
            present = {r["config_key"]: r for r in rows}
            missing = sorted(set(expected) - set(present))
            inactive = sorted(k for k, r in present.items() if not r["is_active"])
            if missing:
                print(f"FAIL org {org_id}: {len(missing)} label(s) missing, "
                      f"first: {missing[:3]}")
                failures += 1
            if inactive:
                print(f"FAIL org {org_id}: {len(inactive)} label(s) inactive: "
                      f"{inactive[:3]}")
                failures += 1
            edited = sorted(
                k for k, r in present.items()
                if k in expected and r["config_value"] != expected[k]
            )
            if edited:
                # Reported, NOT corrected. See the module docstring.
                print(f"  note org {org_id}: {len(edited)} label(s) differ from "
                      f"the seed — left as the operator wrote them: {edited[:3]}")

        if failures:
            return 1
        print(f"OK: {len(expected)} narrative labels present and active in "
              f"all {len(orgs)} org(s), verified against the live catalog")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
