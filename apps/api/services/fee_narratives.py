"""Fee narrative generation — sprint fee41.

Turns a fee schedule into the paragraph of an advisory agreement that describes
it, deterministically, and then lets a model improve the PROSE without being
able to touch a NUMBER.

Four decisions in this module are load-bearing. Each is here because the
obvious alternative fails in a way that would not be visible until a client
signed the wrong document.


1. WHY ``household_id`` IS NOT DERIVABLE FROM THE SCHEDULE
──────────────────────────────────────────────────────────────────────────────
A schedule says *when* to value an account — ``valuation_method`` is
PERIOD_END / PERIOD_START / AVG_DAILY / AVG_MONTH_END. It says nothing about
*whose number* is valued. That is fee32's precedence order, and fee32 put an
override on the HOUSEHOLD ("we trust Addepar for the Hollis household because
their custodian's feed is six weeks behind").

So two households billed under the SAME schedule genuinely have two different
true sentences about how their assets are valued, and a narrative that named
only the schedule would state one of them falsely. ``fee_narratives.household_id``
is therefore an input to the render, not a label on it.

2. WHAT "THE RESOLVED PRECEDENCE SET" IS, CONCRETELY
──────────────────────────────────────────────────────────────────────────────
Measured, not assumed — see the sprint's Task 1 report. It is NOT a SQL
function. ``resolve_precedence`` is Python, in ``services/portfolio_precedence``,
and it resolves POSITIONS; it cannot be called for a household that owns none,
which is every household at the moment its agreement is drafted.

The datum is a :class:`~services.portfolio_precedence.SourceOrder`:

    order           tuple[str, ...]   ranked source_system values, most-trusted first
    origin          str               household_override | org_setting | platform_default
    is_default      bool              whether the ORG configured its own order
    invalid_reason  str | None        a stored order that no longer validates
    household_id    str | None
    household_reason str | None       why no household override applied

fee41 lifted fee32's household → org → default fall-through into
``resolve_source_order_for_household`` so this module resolves through the
identical code path the ingestion pipeline resolves through. A copy would have
let a firm's billing prose and its portfolio disagree about which feed wins.

``origin`` is hashed alongside ``order``. An org whose household override
happens to spell the same order as the platform default has still made a
deliberate choice — ``SourceOrder``'s own docstring refuses to call that "the
default" — and the narrative's provenance sentence differs accordingly.

3. WHY EVERY UNRESOLVED TOKEN RAISES
──────────────────────────────────────────────────────────────────────────────
``{{tiers.1.rate}}`` against a FLAT schedule with no ladder has no value. The
three ways to handle that are: emit nothing, leave the token, or raise. The
first two both ship. "The first $1,000,000 is charged at  annually" is a
sentence a person signs. So: :class:`NarrativeTokenError`, naming the token and
the reason, and no ``rendered_text`` exists at all.

The same rule covers a NULL column. A schedule with no ``minimum_fee`` does not
render ``{{schedule.minimum_fee}}`` as "$0.00" or as blank — the org's template
is asserting a minimum this schedule does not have, and that is a template bug
to fix, not a value to invent.

4. WHY THE POLISH GATE IS ARITHMETIC, NOT A PROMPT
──────────────────────────────────────────────────────────────────────────────
The model is told to change only prose. That is worth saying and worth nothing:
:func:`polish_narrative` re-derives the invariant multiset from the returned
text and compares it to the deterministic render's. On any difference the
polished text is DISCARDED and the deterministic text is what the caller gets.
The gate holds on a model that ignores the instruction, on a model that has not
shipped yet, and on a day the proxy is down.

The comparison is deliberately strict — see :func:`extract_invariants`. It is
literal, not numeric: "1.00%" and "1%" are a mismatch. A prose polish has no
business restyling a rate, and the cost of being wrong is asymmetric. A
false rejection loses a stylistic improvement; a false acceptance ships a
contract that misstates a fee.

WHAT IS NOT BUILT (see also the sprint report)
──────────────────────────────────────────────────────────────────────────────
* ``adv_check_status`` is wired and constrained but never leaves ``UNCHECKED``.
  No Form ADV Part 2A source exists in this database — no table, no column, no
  ingest. Comparing against it is not deferred work with a stub standing in; it
  is work with nothing to compare to. :func:`set_adv_check_status` exists so the
  field is writable by whatever loads that source, and nothing in this module
  calls it.
* Attaching a rendered narrative to a signed Chancery document. TODO below.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence

from services.fee_schedules import (
    TABLE_ACCOUNTS,
    TABLE_BILLING_GROUPS,
    FeeScheduleNotFoundError,
    _as_uuid_text,
    _current,
    load_schedule,
    load_tiers,
)
from services.portfolio_assets import _OrgWrite, _require_org
from services.portfolio_precedence import (
    SourceOrder,
    resolve_source_order_for_household,
)

TABLE_TEMPLATES = "public.fee_narrative_templates"
TABLE_NARRATIVES = "public.fee_narratives"
TABLE_EXCLUSIONS = "public.fee_exclusions"
TABLE_DISCOUNTS = "public.fee_discounts"
TABLE_CREDITS = "public.fee_credits"
TABLE_HOUSEHOLDS = "public.households"
TABLE_CONFIG = "public.config"

#: fee_narratives_adv_check_status_check, read off the deployed catalog.
ADV_STATUSES = ("MATCHED", "DIVERGENT", "UNCHECKED")
ADV_UNCHECKED = "UNCHECKED"

#: The config category holding display labels. Rule 1: a label is DATA. The
#: enum DOMAINS below are code — they mirror CHECK constraints, which are also
#: code — but no English word for any of them is written in this file.
VOCAB_CATEGORY = "fee_narrative_vocab"

#: domain name → the values that domain admits, from the deployed CHECK
#: constraints (and, for ``source_system``, from ``positions_source_chk`` via
#: ``portfolio_assets.SOURCE_SYSTEMS``). The applier seeds one config row per
#: (domain, value); a missing row makes the token that needs it fail loudly
#: rather than fall back to the raw enum token, which would put
#: ``reporting_tool_addepar`` into an advisory agreement.
LABELLED_DOMAINS: dict[str, tuple[str, ...]] = {
    "source_system": (
        "reporting_tool_bd", "reporting_tool_addepar", "reporting_tool_orion",
        "reporting_tool_apx", "reporting_tool_import", "altruist",
        "spv_subscriptions", "chancery", "manual",
    ),
    "valuation_method": ("PERIOD_END", "PERIOD_START", "AVG_DAILY", "AVG_MONTH_END"),
    "rate_type": ("BPS", "FLAT", "HYBRID", "HOURLY", "PER_ACCOUNT"),
    "tier_method": ("GRADUATED", "CLIFF", "BLENDED_PUBLISHED"),
    "billing_frequency": ("MONTHLY", "QUARTERLY", "SEMIANNUAL", "ANNUAL"),
    "billing_timing": ("ADVANCE", "ARREARS"),
    "proration_method": ("CALENDAR_DAYS", "BUSINESS_DAYS", "NONE"),
    "precedence_origin": ("household_override", "org_setting", "platform_default"),
}


def vocab_config_key(domain: str, value: str) -> str:
    """``config.config_key`` is unique per ORG across every category, so the
    domain has to be in the key. Built by one function so the applier, the
    loader and the verify script cannot spell it three different ways."""
    return f"narrative_label.{domain}.{value}"


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class NarrativeError(ValueError):
    """A narrative operation was refused for a reason the caller can fix."""


class NarrativeTokenError(NarrativeError):
    """A template token could not be resolved to a real value.

    Its own class because this is the failure the sprint is built around: a
    caller rendering a batch of narratives wants to quarantine the template that
    does not fit a schedule and keep going, without also swallowing a missing
    template or a bad org_id.
    """


class NarrativeTemplateNotFound(NarrativeError):
    """No such template_code (at that version) is current in this org."""


class VocabularyMissing(NarrativeError):
    """A display label this render needs has no ``config`` row in this org.

    Deliberately fatal. The alternative — emitting the raw enum token — puts
    ``reporting_tool_addepar`` or ``AVG_MONTH_END`` into a document a client
    signs, and does it silently.
    """


# ═══════════════════════════════════════════════════════════════════════════
# Decimal rendering
#
# Every one of these takes a Decimal and returns a str. None of them accepts a
# float, and none of them is reachable from one: asyncpg hands back
# numeric(p,s) as Decimal, and `_dec` refuses anything that is not already
# exact. A float that reached here would print 0.8749999999999999 into an
# agreement, which is why the refusal is a raise and not a coercion.
# ═══════════════════════════════════════════════════════════════════════════


def _dec(value: Any, *, field_name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise NarrativeError(
            f"{field_name} arrived as {type(value).__name__} ({value!r}). Money "
            f"and rates are Decimal end to end in this module; a float here has "
            f"already lost the digits this function would be formatting."
        )
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise NarrativeError(f"{field_name} is not a number: {value!r}")


def _plain(value: Decimal) -> str:
    """Trailing zeros dropped, every significant digit kept, no exponent.

    ``Decimal.normalize()`` alone is wrong here: it turns ``Decimal('100.000000')``
    — which is exactly what ``numeric(12,6)`` returns for a 100bps tier — into
    ``Decimal('1E+2')``, and "1E+2 bps" is not a fee.
    """
    normalized = value.normalize()
    sign, digits, exponent = normalized.as_tuple()
    if isinstance(exponent, int) and exponent > 0:
        normalized = normalized.quantize(Decimal(1))
    return format(normalized, "f")


def format_bps(value: Any, *, field_name: str = "rate_bps") -> str:
    """``Decimal('100.000000')`` → ``'100 bps'``; ``Decimal('12.345678')`` →
    ``'12.345678 bps'``. Never rounds — ``numeric(12,6)`` carries six decimal
    places and all six are somebody's negotiated rate."""
    return f"{_plain(_dec(value, field_name=field_name))} bps"


def _at_least_two_places(value: Decimal) -> Decimal:
    """Trailing storage zeros dropped, then padded back to a two-place MINIMUM.

    The order matters and is the whole function. ``numeric(12,6)`` returns a
    100bps rate as ``100.000000``, so quantizing straight to two places would
    keep whichever scale the column happened to declare — ``1.000000%`` today
    and something else after a column-type change. Normalising first makes the
    output a property of the VALUE; padding second makes a rate read as a rate
    rather than as ``1%``. A value with genuine extra precision (a stored
    ``0.2525``) survives both steps untouched.
    """
    exact = Decimal(_plain(value))
    if -exact.as_tuple().exponent < 2:
        exact = exact.quantize(Decimal("0.01"))
    return exact


def format_pct(value: Any, *, field_name: str = "rate_bps") -> str:
    """Basis points as a percentage, at least two decimal places.

    ``100`` → ``'1.00%'``; ``12.345678`` → ``'0.12345678%'``. The division is
    Decimal-exact, so no digit is invented and none is lost.
    """
    pct = _dec(value, field_name=field_name) / Decimal(100)
    return f"{format(_at_least_two_places(pct), 'f')}%"


def format_money(value: Any, currency: str, *, field_name: str = "amount") -> str:
    """``Decimal('1000000.0000')`` → ``'$1,000,000.00'`` for USD.

    Two decimal places unless the stored ``numeric(20,4)`` actually carries
    more, in which case all four are printed. Rounding a stored 0.2525 to 0.25
    inside a contract is the silent truncation this sprint's rule 7 is about.
    """
    amount = _at_least_two_places(_dec(value, field_name=field_name))
    text = format(amount, ",f")
    prefix = "$" if currency == "USD" else ""
    suffix = "" if currency == "USD" else f" {currency}"
    if text.startswith("-"):
        return f"-{prefix}{text[1:]}{suffix}"
    return f"{prefix}{text}{suffix}"


def _canon_decimal(value: Any) -> str:
    """Hash-stable spelling of a Decimal.

    ``numeric(12,6)`` returns ``100.000000`` and ``numeric(20,4)`` returns
    ``100.0000`` for the same rate. Hashing ``str(value)`` would make an
    unrelated column-type change look like a schedule edit and stale every
    narrative in the firm.
    """
    return _plain(_dec(value, field_name="hashed value"))


# ═══════════════════════════════════════════════════════════════════════════
# Vocabulary
# ═══════════════════════════════════════════════════════════════════════════


async def load_vocabulary(conn, org_id: str) -> dict[str, dict[str, str]]:
    """``{domain: {value: label}}`` from ``public.config``, org-scoped.

    Read through ``_OrgWrite`` so RLS confirms the connection's context, with
    the org predicate ALSO in the WHERE clause — ``_OrgWrite`` raises the GUC
    from its own argument, so a caller that passed the wrong org would satisfy
    the policy against its own mistake.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"""
            SELECT config_key, config_value
            FROM {TABLE_CONFIG}
            WHERE org_id = $1::uuid AND category = $2 AND is_active
            """,
            org_id, VOCAB_CATEGORY,
        )
    out: dict[str, dict[str, str]] = {d: {} for d in LABELLED_DOMAINS}
    for row in rows:
        parts = row["config_key"].split(".", 2)
        if len(parts) != 3 or parts[0] != "narrative_label":
            continue
        out.setdefault(parts[1], {})[parts[2]] = row["config_value"]
    return out


def _label(vocab: Mapping[str, Mapping[str, str]], domain: str, value: str) -> str:
    label = (vocab.get(domain) or {}).get(value)
    if not label:
        raise VocabularyMissing(
            f"no display label for {domain}={value!r} in this org. Expected a "
            f"{TABLE_CONFIG} row with category={VOCAB_CATEGORY!r} and "
            f"config_key={vocab_config_key(domain, value)!r}. Rendering the raw "
            f"value instead would put {value!r} into a client agreement."
        )
    return label


# ═══════════════════════════════════════════════════════════════════════════
# The inputs
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class NarrativeInputs:
    """Everything the render and the hash are computed from.

    One object so :func:`compute_input_hash` and :func:`build_resolver` cannot
    be handed different states — a hash covering less than the render is a
    staleness check that misses edits, and a hash covering more is one that
    stales narratives nothing changed.
    """

    org_id: str
    schedule: Mapping[str, Any]
    tiers: Sequence[Mapping[str, Any]]
    exclusions: Sequence[Mapping[str, Any]]
    discounts: Sequence[Mapping[str, Any]]
    credits: Sequence[Mapping[str, Any]]
    precedence: SourceOrder
    household_id: str | None
    household_name: str | None
    template: Mapping[str, Any]
    vocab: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


#: The schedule columns whose value is part of "what this narrative describes".
#: Audit and temporal columns are deliberately OUT: ``created_at`` moving does
#: not change the arrangement, and a DRAFT edit that touched only ``created_by``
#: should not stale a delivered document. ``status`` is IN — a schedule going
#: from DRAFT to APPROVED changes what the narrative is describing.
_HASHED_SCHEDULE_COLUMNS = (
    "id", "code", "version", "name", "product_type", "rate_type", "tier_method",
    "billing_frequency", "billing_timing", "valuation_method",
    "day_weight_flows", "day_weight_threshold", "proration_method",
    "minimum_fee", "minimum_fee_scope", "maximum_fee", "minimum_billable_value",
    "cash_treatment", "cash_exclusion_pct", "margin_treatment",
    "ordering_policy", "currency", "status",
)

_HASHED_TIER_COLUMNS = ("tier_seq", "lower_bound", "upper_bound", "rate_bps", "flat_amount")
_HASHED_EXCLUSION_COLUMNS = (
    "scope_type", "scope_id", "basis_type", "basis_value", "treatment",
    "alt_fee_schedule_id", "flat_amount", "reason", "effective_from", "effective_to",
)
_HASHED_DISCOUNT_COLUMNS = (
    "scope_type", "scope_id", "discount_type", "value", "applies_to",
    "reason", "effective_from", "effective_to",
)
_HASHED_CREDIT_COLUMNS = (
    "scope_type", "scope_id", "credit_source", "offset_pct",
    "reason", "effective_from", "effective_to",
)


async def _household_scope_ids(
    conn, org_id: str, household_id: str | None
) -> dict[str, list[str]]:
    """Which ``scope_id`` values a household's arrangement covers, per scope_type.

    A fee_exclusion / discount / credit is scoped to an ACCOUNT, a
    BILLING_GROUP or a HOUSEHOLD (exclusions additionally to the whole ORG) —
    never to a fee_schedule. So "the exclusions this schedule references" is
    only answerable relative to a household, and answering it means walking down
    to the accounts and billing groups underneath.

    Both walks use the tables' own current-row predicate on BOTH temporal axes.
    An account closed on the valid axis is not part of the arrangement being
    described, and including it would stale every narrative in the household the
    day a client closes one account.
    """
    if not household_id:
        return {"HOUSEHOLD": [], "ACCOUNT": [], "BILLING_GROUP": []}
    async with _OrgWrite(conn, org_id) as c:
        accounts = await c.fetch(
            f"SELECT a.id::text AS id FROM {TABLE_ACCOUNTS} a "
            f"WHERE a.org_id = $1::uuid AND a.household_id = $2::uuid "
            f"AND {_current('a')} ORDER BY a.id",
            org_id, household_id,
        )
        groups = await c.fetch(
            f"SELECT g.id::text AS id FROM {TABLE_BILLING_GROUPS} g "
            f"WHERE g.org_id = $1::uuid AND g.household_id = $2::uuid "
            f"AND {_current('g')} ORDER BY g.id",
            org_id, household_id,
        )
    return {
        "HOUSEHOLD": [household_id],
        "ACCOUNT": [r["id"] for r in accounts],
        "BILLING_GROUP": [r["id"] for r in groups],
    }


async def _load_scoped(
    conn, org_id: str, table: str, columns: Sequence[str],
    scopes: Mapping[str, Sequence[str]], *, include_org_scope: bool,
) -> list[dict[str, Any]]:
    """Current rows in ``table`` whose (scope_type, scope_id) the household covers.

    ``ORDER BY`` is explicit and total — ``id`` is the final key — because this
    list is hashed. A planner-dependent order would produce a different
    ``input_hash`` for identical data and stale narratives at random.
    """
    pairs_type: list[str] = []
    pairs_id: list[str] = []
    for scope_type, ids in scopes.items():
        for scope_id in ids:
            pairs_type.append(scope_type)
            pairs_id.append(scope_id)
    select = ", ".join(
        f"x.{c}::text AS {c}" if c in ("scope_id", "alt_fee_schedule_id") else f"x.{c}"
        for c in columns
    )
    # EXISTS over a zipped unnest rather than a row-valued `= ANY`, which is not
    # Postgres syntax against a two-column subquery. It also behaves correctly on
    # an empty pair list (a household with no accounts and no billing groups):
    # the EXISTS is simply false and only the ORG arm can match.
    org_clause = " OR x.scope_type = 'ORG'" if include_org_scope else ""
    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"""
            SELECT {select}, x.id::text AS id
            FROM {table} x
            WHERE x.org_id = $1::uuid AND {_current('x')}
              AND ( EXISTS (
                      SELECT 1 FROM unnest($2::text[], $3::text[]) AS s(st, si)
                      WHERE s.st = x.scope_type AND s.si = x.scope_id::text
                    ) {org_clause} )
            ORDER BY x.scope_type, x.scope_id, x.effective_from, x.id
            """,
            org_id, pairs_type, pairs_id,
        )
    return [dict(r) for r in rows]


async def load_template(
    conn, org_id: str, template_code: str, version: int | None = None
) -> dict[str, Any]:
    """One template. ``version=None`` means the highest CURRENT version.

    Pinning a version is the whole point of the (template_code, version) unique
    index: a narrative stores ``template_id``, so an org that publishes v2 of its
    house language has changed nothing about the v1 text already delivered to a
    client. Re-rendering against v2 is a deliberate act with a different
    ``template_id``, not a side effect of editing a row.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        if version is None:
            row = await c.fetchrow(
                f"""
                SELECT t.id::text AS id, t.template_code, t.version, t.jurisdiction,
                       t.body_template, t.approved_by::text AS approved_by, t.approved_at
                FROM {TABLE_TEMPLATES} t
                WHERE t.org_id = $1::uuid AND t.template_code = $2
                  AND {_current('t')}
                ORDER BY t.version DESC LIMIT 1
                """,
                org_id, template_code,
            )
        else:
            row = await c.fetchrow(
                f"""
                SELECT t.id::text AS id, t.template_code, t.version, t.jurisdiction,
                       t.body_template, t.approved_by::text AS approved_by, t.approved_at
                FROM {TABLE_TEMPLATES} t
                WHERE t.org_id = $1::uuid AND t.template_code = $2
                  AND t.version = $3 AND {_current('t')}
                """,
                org_id, template_code, int(version),
            )
    if row is None:
        at = "any current version" if version is None else f"version {version}"
        raise NarrativeTemplateNotFound(
            f"no fee narrative template {template_code!r} at {at} in this org"
        )
    return dict(row)


async def load_template_by_id(conn, org_id: str, template_id: Any) -> dict[str, Any]:
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""
            SELECT t.id::text AS id, t.template_code, t.version, t.jurisdiction,
                   t.body_template, t.approved_by::text AS approved_by, t.approved_at
            FROM {TABLE_TEMPLATES} t
            WHERE t.id = $1::uuid AND t.org_id = $2::uuid AND {_current('t')}
            """,
            _as_uuid_text(template_id, field="template_id"), org_id,
        )
    if row is None:
        raise NarrativeTemplateNotFound(
            f"fee narrative template {template_id} is not current in this org"
        )
    return dict(row)


async def collect_inputs(
    conn,
    org_id: str,
    *,
    fee_schedule_id: Any,
    household_id: Any = None,
    template: Mapping[str, Any],
) -> NarrativeInputs:
    """Read every input the render and the hash depend on, under one org context."""
    org_id = _require_org(org_id)
    schedule_id = _as_uuid_text(fee_schedule_id, field="fee_schedule_id")
    hh = _as_uuid_text(household_id, field="household_id") if household_id else None

    async with _OrgWrite(conn, org_id) as c:
        schedule = await load_schedule(c, org_id, schedule_id)
        tiers = await load_tiers(c, org_id, schedule_id)
        household_name = None
        if hh:
            household_name = await c.fetchval(
                f"SELECT h.name FROM {TABLE_HOUSEHOLDS} h "
                f"WHERE h.id = $1::uuid AND h.org_id = $2::uuid",
                hh, org_id,
            )
            if household_name is None:
                raise NarrativeError(
                    f"household {hh} does not exist in this org — a narrative "
                    f"cannot be scoped to a household that is not there"
                )

    scopes = await _household_scope_ids(conn, org_id, hh)
    exclusions = await _load_scoped(
        conn, org_id, TABLE_EXCLUSIONS, _HASHED_EXCLUSION_COLUMNS, scopes,
        include_org_scope=True,
    )
    discounts = await _load_scoped(
        conn, org_id, TABLE_DISCOUNTS, _HASHED_DISCOUNT_COLUMNS, scopes,
        include_org_scope=False,
    )
    credits = await _load_scoped(
        conn, org_id, TABLE_CREDITS, _HASHED_CREDIT_COLUMNS, scopes,
        include_org_scope=False,
    )
    precedence = await resolve_source_order_for_household(conn, org_id, hh)
    vocab = await load_vocabulary(conn, org_id)

    return NarrativeInputs(
        org_id=org_id, schedule=schedule, tiers=tiers, exclusions=exclusions,
        discounts=discounts, credits=credits, precedence=precedence,
        household_id=hh, household_name=household_name, template=template,
        vocab=vocab,
    )


# ═══════════════════════════════════════════════════════════════════════════
# input_hash
# ═══════════════════════════════════════════════════════════════════════════

#: Bumped when the CONTENT of the hash payload changes shape. A schema version
#: inside the hashed document means an upgrade that starts hashing a new field
#: stales every existing narrative deliberately and visibly, instead of stale-ing
#: them for a reason nobody can reconstruct later.
INPUT_HASH_VERSION = 1


def _hashable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _canon_decimal(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_hashable(v) for v in value]
    if isinstance(value, dict):
        return {k: _hashable(v) for k, v in sorted(value.items())}
    return str(value)  # dates, UUIDs, timestamps


def _project(row: Mapping[str, Any], columns: Sequence[str]) -> dict[str, Any]:
    return {c: _hashable(row.get(c)) for c in columns}


def hash_payload(inputs: NarrativeInputs) -> dict[str, Any]:
    """The exact document that gets hashed. Public so a divergence is debuggable.

    A staleness bug is otherwise a 64-character hex string that differs from
    another 64-character hex string, and no way to see which field moved.
    """
    ordering = inputs.schedule.get("ordering_policy")
    schedule = _project(inputs.schedule, _HASHED_SCHEDULE_COLUMNS)
    schedule["ordering_policy"] = _hashable(
        json.loads(ordering) if isinstance(ordering, str) else ordering
    )
    return {
        "v": INPUT_HASH_VERSION,
        "schedule": schedule,
        "tiers": [_project(t, _HASHED_TIER_COLUMNS) for t in inputs.tiers],
        "exclusions": [_project(x, _HASHED_EXCLUSION_COLUMNS) for x in inputs.exclusions],
        "discounts": [_project(x, _HASHED_DISCOUNT_COLUMNS) for x in inputs.discounts],
        "credits": [_project(x, _HASHED_CREDIT_COLUMNS) for x in inputs.credits],
        # The resolved precedence SET, per this module's docstring point 2.
        # `origin` is in here on purpose: a household that switches from the org
        # default to a deliberate override of the identical order has changed
        # the provenance sentence, and the narrative must go stale.
        "precedence": {
            "order": list(inputs.precedence.order),
            "origin": inputs.precedence.origin,
            "is_default": inputs.precedence.is_default,
            "invalid_reason": inputs.precedence.invalid_reason,
        },
        "household_id": inputs.household_id,
        "template": {
            "id": inputs.template["id"],
            "code": inputs.template["template_code"],
            "version": int(inputs.template["version"]),
            # The BODY, not just the version. An org that edits a template row
            # in place instead of publishing a new version has changed the
            # delivered language, and version-only hashing would miss it.
            "body_sha256": hashlib.sha256(
                inputs.template["body_template"].encode("utf-8")
            ).hexdigest(),
        },
    }


def compute_input_hash(inputs: NarrativeInputs) -> str:
    payload = json.dumps(
        hash_payload(inputs), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# Token resolution
# ═══════════════════════════════════════════════════════════════════════════

_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
#: Any surviving brace pair after substitution. A template with `{{tiers.1.rate}`
#: (one brace short) would otherwise ship the literal text into an agreement.
_STRAY_RE = re.compile(r"\{\{|\}\}")
_TIER_RE = re.compile(r"^tiers\.(\d+)\.([A-Za-z0-9_]+)$")


class TokenResolver:
    """Resolves one token, or raises :class:`NarrativeTokenError` saying why.

    Resolution is a function call rather than a prebuilt dict because tier
    tokens are indexed — ``tiers.1.rate``, ``tiers.7.upper_bound`` — and the
    index space is unbounded. A prebuilt dict would have to choose between
    enumerating a bounded prefix (and treating ``tiers.8.rate`` as an unknown
    token when the real answer is "this ladder has seven bands") and leaving the
    caller to guess.
    """

    def __init__(self, inputs: NarrativeInputs):
        self.inputs = inputs
        self.schedule = inputs.schedule
        self.currency = str(inputs.schedule["currency"])
        self._simple = self._build_simple()

    # ── labels ──────────────────────────────────────────────────────────
    def _lbl(self, domain: str, value: str) -> str:
        return _label(self.inputs.vocab, domain, value)

    # ── the flat namespace ──────────────────────────────────────────────
    def _build_simple(self) -> dict[str, str]:
        s = self.schedule
        p = self.inputs.precedence
        out: dict[str, str] = {
            "schedule.code": str(s["code"]),
            "schedule.version": str(int(s["version"])),
            "schedule.name": str(s["name"]),
            "schedule.product_type": str(s["product_type"]),
            "schedule.status": str(s["status"]),
            "schedule.currency": self.currency,
            "schedule.rate_type": str(s["rate_type"]),
            "schedule.rate_type_label": self._lbl("rate_type", str(s["rate_type"])),
            "schedule.billing_frequency": str(s["billing_frequency"]),
            "schedule.billing_frequency_label": self._lbl(
                "billing_frequency", str(s["billing_frequency"])),
            "schedule.billing_timing": str(s["billing_timing"]),
            "schedule.billing_timing_label": self._lbl(
                "billing_timing", str(s["billing_timing"])),
            "schedule.valuation_method": str(s["valuation_method"]),
            "schedule.valuation_method_label": self._lbl(
                "valuation_method", str(s["valuation_method"])),
            "schedule.proration_method": str(s["proration_method"]),
            "schedule.proration_method_label": self._lbl(
                "proration_method", str(s["proration_method"])),
            "schedule.cash_treatment": str(s["cash_treatment"]),
            "schedule.margin_treatment": str(s["margin_treatment"]),
            "tiers.count": str(len(self.inputs.tiers)),
            "exclusions.count": str(len(self.inputs.exclusions)),
            "discounts.count": str(len(self.inputs.discounts)),
            "credits.count": str(len(self.inputs.credits)),
            "precedence.origin": str(p.origin),
            "precedence.origin_label": self._lbl("precedence_origin", str(p.origin)),
        }
        if p.order:
            out["precedence.primary_source"] = p.order[0]
            out["precedence.primary_source_label"] = self._lbl(
                "source_system", p.order[0])
            out["precedence.order"] = ", ".join(
                self._lbl("source_system", src) for src in p.order)
        if s.get("tier_method"):
            out["schedule.tier_method"] = str(s["tier_method"])
            out["schedule.tier_method_label"] = self._lbl(
                "tier_method", str(s["tier_method"]))
        for column in ("minimum_fee", "maximum_fee", "minimum_billable_value",
                       "day_weight_threshold"):
            if s.get(column) is not None:
                out[f"schedule.{column}"] = format_money(
                    s[column], self.currency, field_name=column)
        if s.get("minimum_fee_scope"):
            out["schedule.minimum_fee_scope"] = str(s["minimum_fee_scope"])
        if s.get("cash_exclusion_pct") is not None:
            # Stored as a FRACTION — fee_calc multiplies account_value by it
            # directly (services/fee_calc.py:802), so 0.10 means ten percent.
            # ×10000 puts it in the basis points format_pct expects. Getting
            # this scale wrong is a 100x error in a client's agreement, which is
            # why it is converted through one named unit rather than eyeballed.
            out["schedule.cash_exclusion_pct"] = format_pct(
                _dec(s["cash_exclusion_pct"], field_name="cash_exclusion_pct")
                * Decimal(10000),
                field_name="cash_exclusion_pct",
            )
        if self.inputs.household_id:
            out["household.id"] = self.inputs.household_id
            out["household.name"] = str(self.inputs.household_name)
        if self.inputs.tiers:
            out["tiers.ladder"] = self._ladder()
            first, last = self.inputs.tiers[0], self.inputs.tiers[-1]
            if first.get("rate_bps") is not None:
                out["tiers.first_rate"] = format_bps(first["rate_bps"])
                out["tiers.first_rate_pct"] = format_pct(first["rate_bps"])
            if last.get("rate_bps") is not None:
                out["tiers.last_rate"] = format_bps(last["rate_bps"])
                out["tiers.last_rate_pct"] = format_pct(last["rate_bps"])
        return out

    def _band(self, tier: Mapping[str, Any]) -> str:
        lower = format_money(tier["lower_bound"], self.currency, field_name="lower_bound")
        if tier.get("upper_bound") is None:
            return f"amounts above {lower}"
        upper = format_money(tier["upper_bound"], self.currency, field_name="upper_bound")
        return f"{lower} to {upper}"

    def _tier_amount(self, tier: Mapping[str, Any]) -> str:
        # fee_schedule_tiers_rate_or_flat_check guarantees exactly one is set.
        if tier.get("rate_bps") is not None:
            return f"{format_bps(tier['rate_bps'])} ({format_pct(tier['rate_bps'])})"
        return format_money(tier["flat_amount"], self.currency, field_name="flat_amount")

    def _ladder(self) -> str:
        return "\n".join(
            f"  {self._band(t)}: {self._tier_amount(t)}" for t in self.inputs.tiers
        )

    # ── the entry point ─────────────────────────────────────────────────
    def resolve(self, token: str) -> str:
        if token in self._simple:
            return self._simple[token]

        tier_match = _TIER_RE.match(token)
        if tier_match:
            return self._resolve_tier(token, int(tier_match.group(1)),
                                      tier_match.group(2))

        raise NarrativeTokenError(self._explain(token))

    def _resolve_tier(self, token: str, index: int, attribute: str) -> str:
        tiers = self.inputs.tiers
        if not tiers:
            raise NarrativeTokenError(
                f"template token {{{{{token}}}}} refers to tier {index}, but fee "
                f"schedule {self.schedule['code']} v{self.schedule['version']} is "
                f"{self.schedule['rate_type']} with no tier ladder "
                f"(tier_method={self.schedule.get('tier_method')!r}, 0 tier rows). "
                f"This template does not fit this schedule."
            )
        if index < 1 or index > len(tiers):
            raise NarrativeTokenError(
                f"template token {{{{{token}}}}} refers to tier {index}, but fee "
                f"schedule {self.schedule['code']} v{self.schedule['version']} has "
                f"{len(tiers)} tier(s). Tier tokens are 1-based."
            )
        tier = tiers[index - 1]
        if attribute == "band":
            return self._band(tier)
        if attribute == "amount":
            return self._tier_amount(tier)
        if attribute in ("lower_bound", "upper_bound", "flat_amount"):
            if tier.get(attribute) is None:
                raise NarrativeTokenError(
                    f"template token {{{{{token}}}}} has no value: tier {index} of "
                    f"{self.schedule['code']} v{self.schedule['version']} has a NULL "
                    f"{attribute}"
                    + (" (it is the open-ended top band)"
                       if attribute == "upper_bound" else "")
                    + ". Rendering it blank would leave a band with no boundary."
                )
            return format_money(tier[attribute], self.currency, field_name=attribute)
        if attribute in ("rate", "rate_bps", "rate_pct"):
            if tier.get("rate_bps") is None:
                raise NarrativeTokenError(
                    f"template token {{{{{token}}}}} asks for a rate, but tier "
                    f"{index} of {self.schedule['code']} v{self.schedule['version']} "
                    f"is a flat-amount band (rate_bps IS NULL). Use "
                    f"{{{{tiers.{index}.flat_amount}}}} or {{{{tiers.{index}.amount}}}}."
                )
            return (format_pct(tier["rate_bps"]) if attribute == "rate_pct"
                    else format_bps(tier["rate_bps"]))
        raise NarrativeTokenError(
            f"template token {{{{{token}}}}} names an unknown tier attribute "
            f"{attribute!r}. Known: band, amount, lower_bound, upper_bound, "
            f"rate, rate_bps, rate_pct, flat_amount."
        )

    def _explain(self, token: str) -> str:
        """Why this token has no value — the schedule's fault or the template's."""
        namespace = token.split(".", 1)[0]
        if namespace == "schedule":
            column = token.split(".", 1)[1].removesuffix("_label")
            if column in _HASHED_SCHEDULE_COLUMNS:
                return (
                    f"template token {{{{{token}}}}} has no value: fee schedule "
                    f"{self.schedule['code']} v{self.schedule['version']} has "
                    f"{column} IS NULL. A schedule with no {column} must not be "
                    f"described by a template that asserts one — rendering it "
                    f"blank or as zero would state a term the firm never agreed."
                )
        if namespace == "precedence" and not self.inputs.precedence.order:
            return (
                f"template token {{{{{token}}}}} has no value: the resolved "
                f"precedence order for household {self.inputs.household_id} is "
                f"empty ({self.inputs.precedence.invalid_reason or 'no order'})"
            )
        if namespace == "household" and not self.inputs.household_id:
            return (
                f"template token {{{{{token}}}}} needs a household, but this "
                f"narrative was rendered with household_id=None. Two households "
                f"on one schedule can have different precedence, so a template "
                f"naming a household must be rendered for one."
            )
        return (
            f"template token {{{{{token}}}}} is not a known narrative token. "
            f"Known namespaces: schedule.*, tiers.*, exclusions.*, discounts.*, "
            f"credits.*, precedence.*, household.*"
        )

    def known_tokens(self) -> tuple[str, ...]:
        """What resolves right now, for a template editor. Tier tokens are
        enumerated for the bands that actually exist."""
        tokens = set(self._simple)
        for i in range(1, len(self.inputs.tiers) + 1):
            tokens.update({
                f"tiers.{i}.band", f"tiers.{i}.amount",
                f"tiers.{i}.lower_bound", f"tiers.{i}.rate",
            })
        return tuple(sorted(tokens))


def render_body(body_template: str, resolver: TokenResolver) -> str:
    """Substitute every token, or raise. Never returns a partial render.

    Tokens are collected and resolved BEFORE any substitution happens, so a
    template with two bad tokens reports the first one against a text that was
    never half-built — and so a resolver failure can never leave a
    ``rendered_text`` in a caller's hands.
    """
    values: dict[str, str] = {}
    for token in _TOKEN_RE.findall(body_template):
        if token not in values:
            values[token] = resolver.resolve(token)
    rendered = _TOKEN_RE.sub(lambda m: values[m.group(1)], body_template)
    stray = _STRAY_RE.search(rendered)
    if stray:
        raise NarrativeTokenError(
            f"template still contains a brace sequence at offset {stray.start()} "
            f"after substitution: {rendered[max(0, stray.start() - 30):stray.start() + 30]!r}. "
            "A malformed token (one brace short at either end) would ship literally."
        )
    return rendered


# ═══════════════════════════════════════════════════════════════════════════
# Render
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RenderedNarrative:
    rendered_text: str
    input_hash: str
    template_id: str
    template_code: str
    template_version: int
    fee_schedule_id: str
    household_id: str | None
    precedence: SourceOrder
    inputs: NarrativeInputs

    @property
    def deterministic_text(self) -> str:
        """Alias that says what it is at the polish call site."""
        return self.rendered_text


async def render_narrative(
    conn,
    org_id: str,
    *,
    fee_schedule_id: Any,
    household_id: Any = None,
    template_code: str | None = None,
    template_version: int | None = None,
    template_id: Any = None,
) -> RenderedNarrative:
    """Render one narrative. Writes nothing.

    Either ``template_code`` (optionally with ``template_version``) or
    ``template_id``. Re-rendering an existing narrative passes the stored
    ``template_id``, so the language it was delivered under is the language it is
    checked against — resolving by code would silently upgrade it to whatever
    version the org published since.
    """
    org_id = _require_org(org_id)
    if template_id is not None:
        template = await load_template_by_id(conn, org_id, template_id)
    elif template_code:
        template = await load_template(conn, org_id, template_code, template_version)
    else:
        raise NarrativeError("one of template_code or template_id is required")

    inputs = await collect_inputs(
        conn, org_id, fee_schedule_id=fee_schedule_id,
        household_id=household_id, template=template,
    )
    resolver = TokenResolver(inputs)
    text = render_body(template["body_template"], resolver)
    return RenderedNarrative(
        rendered_text=text,
        input_hash=compute_input_hash(inputs),
        template_id=template["id"],
        template_code=template["template_code"],
        template_version=int(template["version"]),
        fee_schedule_id=str(inputs.schedule["id"]),
        household_id=inputs.household_id,
        precedence=inputs.precedence,
        inputs=inputs,
    )


async def save_narrative(
    conn,
    org_id: str,
    rendered: RenderedNarrative,
    *,
    rendered_text: str | None = None,
    fee_assignment_id: Any = None,
) -> str:
    """Persist a render. Returns the new ``fee_narratives.id``.

    ``rendered_text`` overrides only the TEXT — it is where a polished version
    goes — and never the ``input_hash``, which describes the inputs regardless of
    how the prose was finished. A polished narrative and its deterministic
    original stale for exactly the same reasons.

    ``adv_check_status`` is left to its column default. See the module docstring:
    there is nothing in this database to check it against.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_NARRATIVES}
                (org_id, fee_schedule_id, fee_assignment_id, household_id,
                 template_id, rendered_text, input_hash, is_stale)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7, false)
            RETURNING id::text
            """,
            org_id, rendered.fee_schedule_id,
            _as_uuid_text(fee_assignment_id, field="fee_assignment_id")
            if fee_assignment_id else None,
            rendered.household_id, rendered.template_id,
            rendered_text if rendered_text is not None else rendered.rendered_text,
            rendered.input_hash,
        )


# ═══════════════════════════════════════════════════════════════════════════
# The polish gate
# ═══════════════════════════════════════════════════════════════════════════

#: A number, with whatever unit is glued to it. ``$`` before, ``%`` / ``bps`` /
#: ``basis points`` after. The unit is part of the invariant: dropping the ``$``
#: from ``$1,000,000`` changes the meaning of the sentence, and a bare-value
#: comparison would call that a formatting change.
_NUMBER_RE = re.compile(
    r"(\$)?\s?(\d[\d,]*(?:\.\d+)?)\s*(%|bps|basis\s+points)?",
    re.IGNORECASE,
)
#: A defined term: anything inside straight or curly double quotes that starts
#: with a capital. This is exactly how an agreement introduces one.
_QUOTED_RE = re.compile(r'["“]\s*([A-Z][^"”]{0,80})\s*["”]')
#: A vocabulary token the render emits verbatim — PERIOD_END, ADVANCE, USD,
#: GRADUATED. Contractually meaningful and trivially "improved" into prose.
_ENUM_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,})\b")


def extract_invariants(text: str) -> tuple[Counter, Counter]:
    """(numbers, defined terms) as MULTISETS.

    Multisets, not sets. A polish that mentions ``$1,000,000`` twice where the
    deterministic text said it once has changed the document, and set equality
    would call the two identical. That failure mode is the reason this returns
    ``Counter``.

    Numbers are compared LITERALLY — commas and internal whitespace stripped,
    nothing else. ``1.00%`` and ``1%`` are DIFFERENT invariants. See the module
    docstring, point 4: the strictness is the point, and its cost is a rejected
    stylistic edit rather than a misstated fee.
    """
    numbers: Counter = Counter()
    for currency, digits, unit in _NUMBER_RE.findall(text):
        canonical = digits.replace(",", "")
        prefix = "$" if currency else ""
        suffix = re.sub(r"\s+", " ", unit).strip().lower() if unit else ""
        numbers[f"{prefix}{canonical}{suffix}"] += 1

    terms: Counter = Counter()
    for match in _QUOTED_RE.findall(text):
        terms[re.sub(r"\s+", " ", match).strip()] += 1
    for match in _ENUM_RE.findall(text):
        terms[match] += 1
    return numbers, terms


@dataclass(frozen=True)
class PolishOutcome:
    """What the gate decided, and enough to explain it in a log.

    ``accepted=False`` is not an error state — it is the designed outcome for a
    model that touched a number, and ``text`` is still a correct narrative.
    """

    text: str
    accepted: bool
    reason: str | None
    number_diff: dict[str, int] = field(default_factory=dict)
    term_diff: dict[str, int] = field(default_factory=dict)

    @property
    def polished(self) -> bool:
        return self.accepted


def check_invariance(deterministic: str, candidate: str) -> PolishOutcome:
    """The gate itself, as a pure function of two strings.

    Separated from :func:`polish_narrative` so it is testable without a model,
    a network, or a database — the sprint requires the REJECTING case to be
    provable on demand, and waiting for a model to misbehave is not on demand.
    """
    det_nums, det_terms = extract_invariants(deterministic)
    cand_nums, cand_terms = extract_invariants(candidate)

    number_diff = {
        k: cand_nums[k] - det_nums[k]
        for k in set(det_nums) | set(cand_nums)
        if cand_nums[k] != det_nums[k]
    }
    term_diff = {
        k: cand_terms[k] - det_terms[k]
        for k in set(det_terms) | set(cand_terms)
        if cand_terms[k] != det_terms[k]
    }
    if not number_diff and not term_diff:
        return PolishOutcome(text=candidate, accepted=True, reason=None)

    parts = []
    if number_diff:
        parts.append("numbers " + ", ".join(
            f"{k}({d:+d})" for k, d in sorted(number_diff.items())))
    if term_diff:
        parts.append("defined terms " + ", ".join(
            f"{k}({d:+d})" for k, d in sorted(term_diff.items())))
    return PolishOutcome(
        text=deterministic,
        accepted=False,
        reason="polish altered " + "; ".join(parts)
               + " — discarded, deterministic text returned",
        number_diff=number_diff,
        term_diff=term_diff,
    )


POLISH_SYSTEM = (
    "You are copy-editing a paragraph of an investment advisory agreement. "
    "Improve ONLY the prose: sentence flow, connective words, and removal of "
    "mechanical repetition. "
    "Do not add, remove, alter, reformat, or reorder any number, percentage, "
    "basis-point figure, dollar amount, date, or capitalised defined term. "
    "Do not add a term the input does not contain. Do not add commentary. "
    "Return the edited paragraph and nothing else."
)


async def polish_narrative(
    rendered: RenderedNarrative | str,
    *,
    org_id: str | None = None,
    transport: Callable[[str, list[dict]], Any] | None = None,
    max_tokens: int = 2000,
) -> PolishOutcome:
    """Ask a model to improve the prose; accept it only if nothing numeric moved.

    ``transport`` is an injection seam, the same shape fee40 used on
    ``propose_fee_spec``. Verification drives a deliberately number-altering
    response through it, because the rejecting branch is the branch that matters
    and a real model cannot be asked to misbehave on demand.

    A model that returns nothing — no key, exhausted chain, proxy down — is not
    a failure here. It is the deterministic text, unpolished, which is the same
    thing a rejected polish produces.
    """
    deterministic = (
        rendered if isinstance(rendered, str) else rendered.rendered_text
    )
    messages = [{"role": "user", "content": deterministic}]

    if transport is not None:
        candidate = await transport(POLISH_SYSTEM, messages)
    else:
        from services.extraction import call_claude_text

        candidate = await call_claude_text(
            POLISH_SYSTEM, messages, max_tokens=max_tokens,
            org_id=org_id, task_type="fee_narrative_polish",
        )

    if not candidate or not str(candidate).strip():
        return PolishOutcome(
            text=deterministic, accepted=False,
            reason="model returned no text — deterministic text returned unchanged",
        )
    outcome = check_invariance(deterministic, str(candidate).strip())
    if not outcome.accepted:
        # Logged, never swallowed. A polish that keeps getting rejected is a
        # signal about the model or the template, and it is invisible if the
        # only evidence is that the text came back the same.
        print(f"fee_narrative_polish REJECTED: {outcome.reason}")
    return outcome


# ═══════════════════════════════════════════════════════════════════════════
# Staleness
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StalenessResult:
    narrative_id: str
    was_stale: bool
    is_stale: bool
    stored_hash: str
    current_hash: str | None
    error: str | None = None

    @property
    def changed(self) -> bool:
        return self.was_stale != self.is_stale


async def recompute_staleness(
    conn,
    org_id: str,
    *,
    narrative_ids: Sequence[Any] | None = None,
    fee_schedule_id: Any = None,
    household_id: Any = None,
    apply: bool = True,
) -> list[StalenessResult]:
    """Re-derive each narrative's ``input_hash`` and set ``is_stale`` to whether
    it moved.

    This is the whole mechanism, and it is deliberately not a trigger or a
    documented convention. Staleness has TWO independent causes — the schedule's
    own state and the household's resolved precedence — and only one of them is
    a row in a table a trigger could watch. The org's precedence setting, the
    household's override, the accounts under the household, and the template body
    all feed the hash; a trigger per source would be four triggers that each
    know a different amount.

    Why re-hashing rather than a dirty flag:

    * A DRAFT schedule edited in place keeps its id (fee34: DRAFT → UPDATE in
      place), so its hash moves and every narrative against it stales. Correct.
    * An APPROVED schedule "edited" forks a NEW id (fee34: APPROVED → INSERT
      version N+1). The old row is untouched, so a narrative pointing at it
      re-hashes identically and does NOT stale. Also correct — that narrative
      still accurately describes the version it was written for.
    * Unrelated activity — another household's override, a different schedule,
      an org setting that is not precedence — does not enter the payload, so it
      cannot stale anything.

    A narrative whose inputs are RESTORED un-stales. That follows from the same
    equality and is the honest answer: the text again describes current state.

    ``apply=False`` computes without writing — the read a review screen wants.
    """
    org_id = _require_org(org_id)
    clauses = ["n.org_id = $1::uuid"]
    params: list[Any] = [org_id]
    if narrative_ids is not None:
        params.append([_as_uuid_text(i, field="narrative_id") for i in narrative_ids])
        clauses.append(f"n.id = ANY(${len(params)}::uuid[])")
    if fee_schedule_id is not None:
        params.append(_as_uuid_text(fee_schedule_id, field="fee_schedule_id"))
        clauses.append(f"n.fee_schedule_id = ${len(params)}::uuid")
    if household_id is not None:
        params.append(_as_uuid_text(household_id, field="household_id"))
        clauses.append(f"n.household_id = ${len(params)}::uuid")

    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"""
            SELECT n.id::text AS id, n.fee_schedule_id::text AS fee_schedule_id,
                   n.household_id::text AS household_id,
                   n.template_id::text AS template_id,
                   n.input_hash, n.is_stale
            FROM {TABLE_NARRATIVES} n
            WHERE {' AND '.join(clauses)}
            ORDER BY n.created_at, n.id
            """,
            *params,
        )

    results: list[StalenessResult] = []
    for row in rows:
        try:
            template = await load_template_by_id(conn, org_id, row["template_id"])
            inputs = await collect_inputs(
                conn, org_id, fee_schedule_id=row["fee_schedule_id"],
                household_id=row["household_id"], template=template,
            )
            current = compute_input_hash(inputs)
            error = None
        except (FeeScheduleNotFoundError, NarrativeError) as exc:
            # An input that has VANISHED is the strongest possible staleness
            # signal, not a reason to skip the row. A narrative whose schedule
            # was closed still sits in the table describing something that is no
            # longer there, and leaving is_stale=false says it is current.
            current, error = None, str(exc)
        stale = True if current is None else current != row["input_hash"]
        results.append(StalenessResult(
            narrative_id=row["id"], was_stale=bool(row["is_stale"]), is_stale=stale,
            stored_hash=row["input_hash"], current_hash=current, error=error,
        ))

    if apply:
        # Only rows whose flag actually MOVES are written. A blanket UPDATE over
        # every narrative would be simpler and would touch production rows this
        # call had nothing to say about.
        changed = [r for r in results if r.changed]
        if changed:
            async with _OrgWrite(conn, org_id) as c:
                for flag in (True, False):
                    ids = [r.narrative_id for r in changed if r.is_stale is flag]
                    if ids:
                        await c.execute(
                            f"UPDATE {TABLE_NARRATIVES} SET is_stale = $3 "
                            f"WHERE org_id = $1::uuid AND id = ANY($2::uuid[])",
                            org_id, ids, flag,
                        )
    return results


# ═══════════════════════════════════════════════════════════════════════════
# ADV — wired, never fabricated
# ═══════════════════════════════════════════════════════════════════════════


async def set_adv_check_status(
    conn, org_id: str, narrative_id: Any, status: str
) -> None:
    """Record the outcome of a Form ADV Part 2A comparison.

    NOTHING IN THIS CODEBASE CALLS THIS. There is no ADV source in the database
    — no table, no column, no ingest path (measured in Task 1, not assumed). The
    setter exists so that whatever loads that source has a correct, constrained
    place to write its verdict, and so ``adv_check_status`` is not a column with
    a default and no writer.

    Reported as a named gap rather than stubbed with a comparison that always
    returns MATCHED, which would look identical in the data and be a compliance
    claim the firm cannot support.
    """
    org_id = _require_org(org_id)
    if status not in ADV_STATUSES:
        raise NarrativeError(
            f"adv_check_status must be one of {ADV_STATUSES}, got {status!r} "
            f"(fee_narratives_adv_check_status_check would refuse it)"
        )
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"UPDATE {TABLE_NARRATIVES} SET adv_check_status = $3 "
            f"WHERE org_id = $1::uuid AND id = $2::uuid",
            org_id, _as_uuid_text(narrative_id, field="narrative_id"), status,
        )


# TODO(fee42+): attach an approved narrative to the signed Chancery document it
# appears in. ``fee_assignments.agreement_document_id`` already points at the
# document and ``fee_narratives.fee_assignment_id`` at the assignment, so the
# join exists; what does not exist is the decision about what happens to a
# SIGNED document when its narrative goes stale. Deliberately out of scope for
# fee41 — see the sprint brief.
