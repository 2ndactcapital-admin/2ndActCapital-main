"""Natural language -> FeeSpec. The model's ONLY output. Sprint fee40.

THE CORE RULE
──────────────────────────────────────────────────────────────────────────────
The model never computes a fee. It emits a structured FeeSpec — JSON, no prose
— and everything downstream of that JSON is deterministic code: this module
normalises it, ``fee_spec_resolver`` turns names into ids, ``fee_validation``
(fee34) decides whether it may be saved, and ``fee_calc`` (fee35) is the only
thing that ever produces a dollar figure. No number an advisor sees originates
in a model response.

WHY A GROUNDING CHECK AND NOT JUST A PROMPT INSTRUCTION
──────────────────────────────────────────────────────────────────────────────
"Do not guess a valuation method" is an instruction, and an instruction is a
request. A schedule billed PERIOD_END when the agreement says AVG_DAILY is a
real, recurring overcharge on a volatile quarter, and the difference is
invisible on the confirmation screen because both are plausible values in the
same vocabulary.

So the refusal is mechanical, in :func:`normalise_fee_spec`, not rhetorical.
For every field in :data:`GROUNDED_FIELDS` the model must also return the span
of the advisor's own description that justifies its answer. This module checks
that the cited span actually occurs in the description. It if does not, the
VALUE IS DISCARDED and the field goes to ``unresolved`` — whatever the model
said, however confidently. A model that invents both a value and its evidence
still loses the value, because the evidence is checked against the input text
rather than against the model.

That makes "the model was not permitted to silently fill this in" a property of
the code, provable with a hand-written response, instead of a property of a
prompt that has to be re-proved against every model version.

DECIMAL AT THE JSON BOUNDARY
──────────────────────────────────────────────────────────────────────────────
``json.loads`` is called with ``parse_float=Decimal``. A float is never
constructed at all, so there is no window in which ``1.15`` exists as
``1.1499999999999999`` and no later coercion that could miss a field. fee34
refuses float outright (``MoneyTypeError``) and fee35's inputs coerce through
``money()``; this module makes sure neither ever sees one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence

from services.fee_calc_inputs import (
    BILLING_FREQUENCIES,
    BILLING_TIMINGS,
    CASH_TREATMENTS,
    MARGIN_TREATMENTS,
    PRODUCT_TYPES,
    PRORATION_METHODS,
    RATE_TYPES,
    TIER_METHODS,
    VALUATION_METHODS,
)
from services.fee_validation import (
    CREDIT_SCOPE_TYPES,
    DEFAULT_ORDERING_POLICY,
    DISCOUNT_SCOPE_TYPES,
    EXCLUSION_SCOPE_TYPES,
    EXCLUSION_TREATMENTS,
    MINIMUM_FEE_SCOPES,
    ORDERING_STEPS,
)

#: Bumped when the FeeSpec contract changes shape. Stored on the conversation
#: so a spec drafted under an older contract is identifiable rather than
#: silently reinterpreted.
FEE_SPEC_VERSION = "fee40.1"

#: ``ai_decision_log.task_type`` for every model call this module makes. Its own
#: value, not "extraction", so the fee module's model spend and failure rate are
#: separable from the document pipeline's.
TASK_TYPE = "fee_schedule_spec"

#: The schedule fields the model may propose. Exactly ``fee_schedules``'
#: definition columns (fee_schedules.DEFINITION_FIELDS) plus ``code``, which is
#: the versioning identity and so is not in that tuple. Anything else the model
#: returns is dropped, not merged: ``status``, ``version`` and ``approved_by``
#: are lifecycle and a spec that could assert APPROVED would be a door around
#: the fee34 gate this sprint exists to run.
SPEC_SCHEDULE_FIELDS = (
    "code",
    "name",
    "product_type",
    "rate_type",
    "tier_method",
    "billing_frequency",
    "billing_timing",
    "valuation_method",
    "day_weight_flows",
    "day_weight_threshold",
    "proration_method",
    "minimum_fee",
    "minimum_fee_scope",
    "maximum_fee",
    "minimum_billable_value",
    "cash_treatment",
    "cash_exclusion_pct",
    "margin_treatment",
    "ordering_policy",
    "currency",
)

#: Schedule fields that are NOT NULL on ``fee_schedules`` with no default worth
#: assuming. A spec missing any of these cannot be saved and cannot be priced;
#: each one absent becomes an ``unresolved`` entry rather than a filled-in guess.
REQUIRED_SCHEDULE_FIELDS = (
    "code",
    "name",
    "product_type",
    "rate_type",
    "billing_frequency",
    "billing_timing",
    "valuation_method",
)

#: The fields a model must never infer from context. Each is a POLICY choice
#: that changes the money and has a plausible-looking answer, which is exactly
#: the combination that makes a silent guess expensive. A value here survives
#: only if the model cites a span of the advisor's own words and that span is
#: really in them — see the module docstring.
#:
#: ``rate_type`` and ``product_type`` are deliberately NOT here: they are
#: derivable from the arrangement's own shape ("100 bps on the first million"
#: is unambiguously BPS), and requiring a citation for them would push every
#: well-formed description into the unresolved list for no gain.
GROUNDED_FIELDS = frozenset({
    "billing_frequency",
    "billing_timing",
    "valuation_method",
    "proration_method",
    "tier_method",
    "minimum_fee_scope",
    "cash_treatment",
    "margin_treatment",
    "ordering_policy",
})

#: ``fee_schedules``' deployed column DEFAULTs, from docs/schema_snapshot.sql.
#: Only columns that HAVE a default appear; a field absent here has none, and a
#: schedule that omits it is genuinely missing a value rather than taking one.
#:
#: This is the OTHER half of the grounding rule, and the half that stops it
#: eating well-formed proposals. Grounding exists to keep a model from moving
#: money to a policy nobody asked for. A proposed value IDENTICAL to the
#: deployed default moves no money at all: the schedule it produces is
#: byte-for-byte the one the firm would get by leaving the field out. So it
#: needs no citation.
#:
#: Measured, not reasoned about: without this, ``ordering_policy`` was
#: unsatisfiable in practice. The model returns the standard six-step order —
#: correctly — but no advisor has ever written that order out in prose, so
#: there is no span to cite and the field was discarded on EVERY call and
#: reported unresolved forever. ``proration_method``, ``cash_treatment`` and
#: ``margin_treatment`` had the same defect for the same reason.
#:
#: The fields that matter most are unaffected, because they have no default:
#: ``valuation_method``, ``billing_frequency``, ``billing_timing``,
#: ``tier_method`` and ``minimum_fee_scope`` still require real evidence every
#: time. Those are the ones the guard was built for.
SCHEDULE_COLUMN_DEFAULTS: Mapping[str, Any] = {
    "day_weight_flows": True,
    "proration_method": "CALENDAR_DAYS",
    "cash_treatment": "INCLUDE",
    "margin_treatment": "IGNORE",
    "ordering_policy": list(ORDERING_STEPS),
    "currency": "USD",
}

#: Fields fee34 requires to travel together. ``minimum_fee`` without
#: ``minimum_fee_scope`` is a floor that does not say what it is a floor PER,
#: and ``fee_validation.MinimumFeeScopeError`` refuses it in both directions.
#:
#: This module must never break such a pair BY ITS OWN REFUSAL. Discarding an
#: ungrounded ``minimum_fee_scope`` while keeping ``minimum_fee`` manufactures
#: a schedule fee34 rejects — out of a proposal that was fine — and fee34's
#: message then misdescribes what happened ("minimum_fee_scope is not set" when
#: the model did set it and this module threw it away). A pair broken by the
#: MODEL is left broken, so fee34 reports it honestly.
PAIRED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "minimum_fee": ("minimum_fee_scope",),
    "minimum_fee_scope": ("minimum_fee",),
}

#: Schedule fields carrying money or a rate. Converted to Decimal on the way in.
SPEC_MONEY_FIELDS = (
    "day_weight_threshold",
    "minimum_fee",
    "maximum_fee",
    "minimum_billable_value",
    "cash_exclusion_pct",
)

#: Per-field vocabularies, mirrored from fee34/fee35's own constants rather than
#: retyped. A value outside its vocabulary is dropped and reported, never passed
#: down to fail later as a CHECK violation naming a constraint.
SPEC_VOCABULARIES: Mapping[str, Sequence[str]] = {
    "product_type": PRODUCT_TYPES,
    "rate_type": RATE_TYPES,
    "tier_method": TIER_METHODS,
    "billing_frequency": BILLING_FREQUENCIES,
    "billing_timing": BILLING_TIMINGS,
    "valuation_method": VALUATION_METHODS,
    "proration_method": PRORATION_METHODS,
    "minimum_fee_scope": MINIMUM_FEE_SCOPES,
    "cash_treatment": CASH_TREATMENTS,
    "margin_treatment": MARGIN_TREATMENTS,
}

#: What a reference in the spec may point at. SECURITY resolves against BOTH
#: ``portfolio.assets`` (org-scoped) and ``portfolio.securities_global`` (which
#: has no org_id at all — see fee_spec_resolver).
REFERENCE_KINDS = ("ENTITY", "HOUSEHOLD", "ACCOUNT", "BILLING_GROUP", "SECURITY")

_TIER_MONEY_FIELDS = ("lower_bound", "upper_bound", "rate_bps", "flat_amount")

#: Shortest citation this module will accept as evidence. A two-character span
#: matches somewhere in almost any description, so a citation that short proves
#: nothing about whether the model read the sentence it claims to have read.
MIN_EVIDENCE_CHARS = 8


# ═══════════════════════════════════════════════════════════════════════════
# Errors — typed, so a malformed model response is a 502 and not a 500
# ═══════════════════════════════════════════════════════════════════════════


class FeeSpecError(ValueError):
    """A FeeSpec could not be obtained or understood.

    ``ValueError`` so an existing ``except ValueError`` still catches it, but
    never raised bare: every subclass carries a stable ``code`` the router maps
    to a status and the UI switches on.
    """

    code = "fee_spec_error"

    def __init__(self, message: str, *, field: str | None = None, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.context = context

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            out["field"] = self.field
        if self.context:
            out["context"] = self.context
        return out


class FeeSpecParseError(FeeSpecError):
    """The model returned something that is not JSON.

    Its own class, distinct from :class:`FeeSpecShapeError`, because the two are
    different failures with different operator actions: this one means the model
    ignored the output contract (retry, or the wrong model is configured), the
    other means it honoured the contract and got the content wrong.
    """

    code = "fee_spec_unparseable"


class FeeSpecShapeError(FeeSpecError):
    """Valid JSON, but not a FeeSpec — a list, a scalar, or a mistyped section."""

    code = "fee_spec_malformed"


class FeeSpecUnavailableError(FeeSpecError):
    """No model answered at all — no credential, or the whole chain failed.

    Distinct from a parse failure on purpose: nothing was returned to
    misinterpret, and the fix is a deployment one.
    """

    code = "fee_spec_unavailable"


# ═══════════════════════════════════════════════════════════════════════════
# The prompt
# ═══════════════════════════════════════════════════════════════════════════


def build_system_prompt() -> str:
    """The structured-output contract, built FROM the deployed vocabularies.

    The allowed values are interpolated from fee34/fee35's own constants rather
    than typed into the prose. A vocabulary that gains a value there gains it
    here on the next call, instead of the prompt quietly instructing the model
    to avoid a value the database now accepts.
    """
    vocab_lines = "\n".join(
        f"  {name}: {list(values)}" for name, values in SPEC_VOCABULARIES.items()
    )
    return f"""You convert an advisor's description of a fee arrangement into a FeeSpec.

Return ONE JSON object and NOTHING else. No prose. No explanation. No markdown
code fences. Your entire response must parse as JSON.

You do NOT calculate fees. You never return a dollar amount owed, a computed
fee, or an example bill. A separate calculation engine does that from the
schedule you describe. Return the RULE, never a result of applying it.

Shape:

{{
  "schedule": {{
    "code": "SHORT_UPPER_SNAKE_IDENTIFIER",
    "name": "human readable name",
    "product_type": ..., "rate_type": ...,
    "billing_frequency": ..., "billing_timing": ...,
    "valuation_method": ..., "proration_method": ...,
    "tier_method": ..., "minimum_fee": "2500.00", "minimum_fee_scope": ...,
    "maximum_fee": null, "minimum_billable_value": null,
    "cash_treatment": ..., "cash_exclusion_pct": null,
    "margin_treatment": ..., "day_weight_flows": true,
    "day_weight_threshold": null,
    "ordering_policy": {list(ORDERING_STEPS)},
    "currency": "USD"
  }},
  "tiers": [
    {{"tier_seq": 1, "lower_bound": "0", "upper_bound": "1000000", "rate_bps": "100"}},
    {{"tier_seq": 2, "lower_bound": "1000000", "upper_bound": null, "rate_bps": "75"}}
  ],
  "exclusions": [
    {{"scope_type": ..., "scope_ref": "r1", "basis_type": "SECURITY",
      "basis_value": "…", "treatment": ..., "reason": "…"}}
  ],
  "discounts": [
    {{"scope_type": ..., "scope_ref": "r1", "discount_type": "PCT_OFF",
      "value": "10", "applies_to": "GROSS", "reason": "…"}}
  ],
  "credits": [
    {{"scope_type": ..., "scope_ref": "r1", "credit_source": "…",
      "offset_pct": "1.0", "reason": "…"}}
  ],
  "references": [
    {{"ref": "r1", "kind": "ENTITY", "name": "exactly as the advisor wrote it"}}
  ],
  "evidence": {{"valuation_method": "the exact words that told you this"}},
  "unresolved": [{{"field": "valuation_method", "reason": "not stated"}}],
  "notes": "one short line, optional"
}}

RULES

1. Money and rates are STRINGS, never JSON numbers: "1000000.00", not 1000000.0.
   Rates are basis points in `rate_bps` ("100" means 1.00%).
2. Tiers are contiguous and half-open: each `lower_bound` equals the previous
   `upper_bound`. Exactly one tier — the last — has `upper_bound: null`.
3. Never invent a name, an account, or an entity. Anything the advisor named
   goes in `references` verbatim and is pointed at by `scope_ref`. You do not
   resolve names to ids; you cannot see the database.
4. If the description does not tell you a field, set it to null and add an
   `unresolved` entry naming that field. A plausible default is still a guess.
   This is the single most important rule here.
5. For each of these fields — {sorted(GROUNDED_FIELDS)} — if you give a value
   you MUST also give `evidence[field]`: a VERBATIM span copied from the
   advisor's description that states it, at least {MIN_EVIDENCE_CHARS}
   characters long. The span is checked against the original text. If it is not
   found there your value is DISCARDED and the field is treated as unresolved,
   so a paraphrase costs you the answer. If the description does not state the
   field, do not cite anything — use `unresolved`.
   EXCEPTION: no evidence is needed when your value is identical to the
   platform default below, because that value changes nothing.
   Defaults: {json.dumps({k: v for k, v in SCHEDULE_COLUMN_DEFAULTS.items()})}

ALLOWED VALUES (anything else is discarded):
{vocab_lines}
  ordering_policy: a permutation of {list(ORDERING_STEPS)}
  exclusion scope_type: {list(EXCLUSION_SCOPE_TYPES)}
  exclusion treatment: {list(EXCLUSION_TREATMENTS)}
  discount scope_type: {list(DISCOUNT_SCOPE_TYPES)}
  credit scope_type: {list(CREDIT_SCOPE_TYPES)}
  reference kind: {list(REFERENCE_KINDS)}
"""


# ═══════════════════════════════════════════════════════════════════════════
# Parsing
# ═══════════════════════════════════════════════════════════════════════════

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _unfence(text: str) -> tuple[str, bool]:
    """Strip one markdown fence. Returns ``(payload, was_fenced)``.

    Tolerated rather than refused — a fenced but otherwise perfect FeeSpec is a
    formatting slip, and failing the advisor's request over it would trade a
    real answer for a point of principle. It is REPORTED (``warnings``) so the
    slip stays visible instead of being normalised into invisibility.
    """
    match = _FENCE_RE.match(text or "")
    if match:
        return match.group(1), True
    return (text or "").strip(), False


def parse_fee_spec(raw: str | None) -> tuple[dict[str, Any], list[str]]:
    """Model text -> a FeeSpec dict. Raises a TYPED error, never a bare one.

    ``json.loads(..., parse_float=Decimal)`` is the whole Decimal story: no
    float is ever constructed, so there is no later coercion that could miss a
    field the model added.

    Returns ``(spec, warnings)``. Raises :class:`FeeSpecParseError` when the
    response is not JSON and :class:`FeeSpecShapeError` when it is JSON but not
    an object.
    """
    if raw is None:
        raise FeeSpecUnavailableError(
            "no model response to parse — the model returned nothing (no "
            "credential configured, or every model in the org's chain failed)"
        )

    payload, was_fenced = _unfence(raw)
    warnings: list[str] = []
    if was_fenced:
        warnings.append(
            "the model wrapped its JSON in a markdown fence; the contract asks "
            "for bare JSON. Stripped and parsed."
        )
    if not payload:
        raise FeeSpecParseError("the model returned an empty response")

    try:
        spec = json.loads(payload, parse_float=Decimal)
    except (json.JSONDecodeError, ValueError) as exc:
        # The excerpt is bounded: a model that answers with three paragraphs of
        # apology should not put three paragraphs into an error body.
        excerpt = payload[:200] + ("…" if len(payload) > 200 else "")
        raise FeeSpecParseError(
            f"the model did not return JSON ({exc}). Response began: {excerpt!r}"
        ) from exc

    if not isinstance(spec, dict):
        raise FeeSpecShapeError(
            f"a FeeSpec must be a JSON object; got {type(spec).__name__}"
        )
    return spec, warnings


# ═══════════════════════════════════════════════════════════════════════════
# Normalisation — where the model stops being trusted
# ═══════════════════════════════════════════════════════════════════════════


def _norm_text(value: str) -> str:
    """Case-folded, whitespace-collapsed, for the evidence substring test.

    Punctuation is deliberately KEPT. Stripping it would let "1.00%" match a
    description containing "100%", which is a hundredfold difference in exactly
    the field type this check exists to protect.
    """
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def _money(value: Any, field: str, problems: list[str]) -> Decimal | None:
    """A spec value as Decimal, or None with a recorded problem.

    ``float`` is refused rather than converted even though one cannot arrive
    through :func:`parse_fee_spec`: a caller building a spec by hand (the UI
    posting an edited field back, a test) can still produce one, and silently
    accepting it here would reintroduce the exact binary-repr drift the
    ``parse_float=Decimal`` boundary exists to prevent.
    """
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        problems.append(
            f"{field}: refused a {type(value).__name__} ({value!r}); money and "
            f"rates must arrive as a string or an integer so no binary float "
            f"rounding can occur before fee34 sees them"
        )
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ArithmeticError):
        problems.append(f"{field}: {value!r} is not a number")
        return None


@dataclass
class NormalisedSpec:
    """A FeeSpec after the deterministic layer has had its say.

    ``schedule`` holds only values that survived vocabulary AND grounding
    checks. ``unresolved`` names every field that did not, whatever the reason
    and whoever noticed — the model's own admission and this module's refusals
    are merged, because the screen's question is "what is still unknown", not
    "who noticed it was unknown".
    """

    schedule: dict[str, Any] = dc_field(default_factory=dict)
    tiers: list[dict[str, Any]] = dc_field(default_factory=list)
    exclusions: list[dict[str, Any]] = dc_field(default_factory=list)
    discounts: list[dict[str, Any]] = dc_field(default_factory=list)
    credits: list[dict[str, Any]] = dc_field(default_factory=list)
    references: list[dict[str, Any]] = dc_field(default_factory=list)
    unresolved: list[dict[str, Any]] = dc_field(default_factory=list)
    discarded: list[dict[str, Any]] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    notes: str | None = None
    #: Fields a HUMAN set, which are therefore exempt from the grounding check.
    #: Published so the screen can distinguish a value the advisor decided from
    #: one the model proposed — they carry very different confidence and only
    #: one of them is worth logging a correction against.
    advisor_set: list[str] = dc_field(default_factory=list)

    @property
    def unresolved_fields(self) -> set[str]:
        return {u["field"] for u in self.unresolved}

    @property
    def is_priceable(self) -> bool:
        """True when nothing REQUIRED is still unknown.

        Not the same as valid: fee34 may still refuse it. This only says the
        spec is complete enough that fee35 could be handed it at all.
        """
        return not (set(REQUIRED_SCHEDULE_FIELDS) & self.unresolved_fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schedule": self.schedule,
            "tiers": self.tiers,
            "exclusions": self.exclusions,
            "discounts": self.discounts,
            "credits": self.credits,
            "references": self.references,
            "unresolved": self.unresolved,
            "discarded": self.discarded,
            "warnings": self.warnings,
            "notes": self.notes,
            "advisor_set": self.advisor_set,
            "is_priceable": self.is_priceable,
            "spec_version": FEE_SPEC_VERSION,
        }


def _add_unresolved(spec: NormalisedSpec, field: str, reason: str, source: str) -> None:
    """Record a field as unknown, once. First reason wins.

    De-duplicated by field because the screen marks an INPUT, and two entries
    for ``valuation_method`` would mark it twice and say two different things.
    """
    if any(u["field"] == field for u in spec.unresolved):
        return
    spec.unresolved.append({"field": field, "reason": reason, "source": source})


def normalise_fee_spec(
    raw_spec: Mapping[str, Any],
    description: str,
    *,
    trusted_fields: Sequence[str] = (),
) -> NormalisedSpec:
    """Turn a parsed model response into a spec this codebase will act on.

    Four things happen, in order, and all four are deterministic:

      1. every money field becomes a Decimal (or is refused and reported);
      2. every vocabulary field is checked against fee34/fee35's own constant
         and DISCARDED if it is outside it;
      3. every field in :data:`GROUNDED_FIELDS` must cite a span that really
         occurs in ``description``, or its value is DISCARDED;
      4. anything required and still missing becomes ``unresolved``.

    Step 3 is the one that matters. It runs regardless of what the model put in
    its own ``unresolved`` list, so "the model was not permitted to silently
    fill this in" holds even for a model that never read the instruction.

    ``trusted_fields`` names fields a HUMAN set, which skip step 3. This is not
    a hole in the check: the check exists to stop a MODEL inventing a policy
    nobody stated, and an advisor typing AVG_DAILY into the field is the person
    with the authority to state it. Without it, the very first edit an advisor
    made would be discarded as ungrounded and the field would snap back to
    unresolved — the check would be enforcing its rule against the one party it
    was never aimed at.

    ``propose_fee_spec`` passes nothing here, so a model cannot reach this
    argument or mark its own answers trusted. Only a request that has already
    been through the ``manage_billing`` gate can.
    """
    spec = NormalisedSpec()
    trusted = {str(f) for f in trusted_fields}
    spec.advisor_set = sorted(trusted)
    normalised_description = _norm_text(description)

    raw_schedule = raw_spec.get("schedule")
    if raw_schedule is None:
        raw_schedule = {}
    if not isinstance(raw_schedule, Mapping):
        raise FeeSpecShapeError(
            f"'schedule' must be an object; got {type(raw_schedule).__name__}",
            field="schedule",
        )

    raw_evidence = raw_spec.get("evidence")
    evidence: Mapping[str, Any] = raw_evidence if isinstance(raw_evidence, Mapping) else {}

    # ── the model's own admissions, carried through first ────────────────
    for entry in _as_list(raw_spec.get("unresolved")):
        if isinstance(entry, Mapping) and entry.get("field"):
            _add_unresolved(
                spec, str(entry["field"]),
                str(entry.get("reason") or "the model reported this as not stated"),
                "model",
            )
        elif isinstance(entry, str):
            _add_unresolved(spec, entry, "the model reported this as not stated", "model")

    # ── schedule fields ──────────────────────────────────────────────────
    for field in SPEC_SCHEDULE_FIELDS:
        value = raw_schedule.get(field)
        if value is None or value == "":
            continue

        if field in SPEC_MONEY_FIELDS:
            problems: list[str] = []
            money_value = _money(value, field, problems)
            if problems:
                spec.discarded.append({"field": field, "value": _plain(value),
                                       "reason": problems[0]})
                _add_unresolved(spec, field, problems[0], "resolver")
                continue
            spec.schedule[field] = money_value
            continue

        if field == "day_weight_flows":
            if isinstance(value, bool):
                spec.schedule[field] = value
            else:
                spec.discarded.append({"field": field, "value": _plain(value),
                                       "reason": "not a boolean"})
            continue

        if field == "ordering_policy":
            policy = _as_list(value)
            if sorted(str(s) for s in policy) != sorted(ORDERING_STEPS):
                spec.discarded.append({
                    "field": field, "value": _plain(value),
                    "reason": (
                        f"ordering_policy must be a permutation of "
                        f"{list(ORDERING_STEPS)} — a policy missing a step makes "
                        f"the engine skip it silently"
                    ),
                })
                continue
            spec.schedule[field] = [str(s) for s in policy]
            continue

        vocabulary = SPEC_VOCABULARIES.get(field)
        if vocabulary is not None and str(value) not in vocabulary:
            spec.discarded.append({
                "field": field, "value": _plain(value),
                "reason": f"{value!r} is not one of {list(vocabulary)}",
            })
            _add_unresolved(
                spec, field,
                f"the model proposed {value!r}, which is not a legal value",
                "resolver",
            )
            continue

        spec.schedule[field] = str(value) if not isinstance(value, (int, Decimal)) else value

    # ── step 3: grounding. Values without real evidence do not survive ───
    refused: list[str] = []
    for field in sorted(GROUNDED_FIELDS):
        if field not in spec.schedule or field in trusted:
            continue
        # A value equal to the deployed column default changes nothing, so it
        # is not a guess that could move money. See SCHEDULE_COLUMN_DEFAULTS.
        if field in SCHEDULE_COLUMN_DEFAULTS and _same_as_default(
            field, spec.schedule[field]
        ):
            continue
        cited = evidence.get(field)
        reason = _grounding_failure(cited, normalised_description)
        if reason is None:
            continue
        spec.discarded.append({
            "field": field,
            "value": _plain(spec.schedule.pop(field)),
            "reason": reason,
        })
        _add_unresolved(spec, field, reason, "resolver")
        refused.append(field)

    # ── this module never leaves a pair broken by its OWN refusal ────────
    _withdraw_broken_pairs(spec, refused)

    # ── tiers ────────────────────────────────────────────────────────────
    for index, raw_tier in enumerate(_as_list(raw_spec.get("tiers"))):
        if not isinstance(raw_tier, Mapping):
            spec.discarded.append({"field": f"tiers[{index}]", "value": _plain(raw_tier),
                                   "reason": "not an object"})
            continue
        problems = []
        tier: dict[str, Any] = {}
        seq = raw_tier.get("tier_seq")
        # Left as-is when it is not an int: fee34's TierSequenceError says
        # precisely what is wrong with it, and pre-empting that here would put a
        # second, differently-worded copy of the same rule in front of the one
        # the operator will actually be judged by.
        tier["tier_seq"] = seq if isinstance(seq, int) and not isinstance(seq, bool) else seq
        for money_field in _TIER_MONEY_FIELDS:
            if raw_tier.get(money_field) is not None:
                tier[money_field] = _money(
                    raw_tier[money_field], f"tiers[{index}].{money_field}", problems
                )
        if problems:
            spec.discarded.append({"field": f"tiers[{index}]", "value": _plain(raw_tier),
                                   "reason": problems[0]})
            continue
        spec.tiers.append(tier)

    # ── references, then the scoped bundles that point at them ───────────
    seen_refs: set[str] = set()
    for index, raw_ref in enumerate(_as_list(raw_spec.get("references"))):
        if not isinstance(raw_ref, Mapping):
            continue
        ref = str(raw_ref.get("ref") or f"r{index + 1}")
        kind = str(raw_ref.get("kind") or "").upper()
        name = str(raw_ref.get("name") or "").strip()
        if kind not in REFERENCE_KINDS or not name:
            spec.discarded.append({
                "field": f"references[{index}]", "value": _plain(raw_ref),
                "reason": f"kind must be one of {list(REFERENCE_KINDS)} and name non-empty",
            })
            continue
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        spec.references.append({"ref": ref, "kind": kind, "name": name})

    spec.exclusions = _normalise_bundle(
        raw_spec.get("exclusions"), "exclusions", EXCLUSION_SCOPE_TYPES,
        ("basis_type", "basis_value", "treatment", "reason"), ("flat_amount",), spec,
    )
    spec.discounts = _normalise_bundle(
        raw_spec.get("discounts"), "discounts", DISCOUNT_SCOPE_TYPES,
        ("discount_type", "applies_to", "reason"), ("value",), spec,
    )
    spec.credits = _normalise_bundle(
        raw_spec.get("credits"), "credits", CREDIT_SCOPE_TYPES,
        ("credit_source", "reason"), ("offset_pct",), spec,
    )

    # ── reconcile: a field that HAS a surviving value is not unresolved ──
    #
    # Two ways this happens, and the answer is the same for both. The advisor
    # supplied a value the model had admitted it did not know; or the model
    # contradicted itself, listing a field as unresolved while also giving it a
    # value that then passed the grounding check. Either way the value in hand
    # is real and the admission is stale. Leaving both would make
    # ``is_priceable`` false for a spec that is demonstrably complete, and the
    # worked example — the whole point of the screen — would refuse to compute.
    spec.unresolved = [u for u in spec.unresolved if u["field"] not in spec.schedule]

    # ── step 4: what is required and still missing ───────────────────────
    for field in REQUIRED_SCHEDULE_FIELDS:
        if field not in spec.schedule:
            _add_unresolved(
                spec, field,
                f"{field} is required on fee_schedules and the description did "
                f"not state it; it must be supplied before this can be saved or "
                f"priced",
                "resolver",
            )

    # A tier_method with no tiers, and tiers with no method, are both fee34
    # errors (TiersMissingError). Not duplicated here — see the tier_seq note.
    notes = raw_spec.get("notes")
    spec.notes = str(notes).strip() if notes else None
    return spec


def _same_as_default(field: str, value: Any) -> bool:
    """Is this proposed value the deployed column DEFAULT, written any way?

    ``["EXCLUSIONS", …]`` and ``('EXCLUSIONS', …)`` are the same policy, and a
    list arriving from JSON must compare equal to the tuple this module holds.
    Compared as lists of ``str`` for that reason rather than by ``==`` on the
    containers, which would be False for every ordering_policy ever proposed.
    """
    default = SCHEDULE_COLUMN_DEFAULTS.get(field)
    if isinstance(default, (list, tuple)):
        return isinstance(value, (list, tuple)) and \
            [str(v) for v in value] == [str(v) for v in default]
    if isinstance(default, bool):
        return value is default
    return value == default


def _withdraw_broken_pairs(spec: NormalisedSpec, refused: Sequence[str]) -> None:
    """Withdraw the surviving half of any pair THIS module just broke.

    Scoped deliberately to ``refused`` — the fields this module discarded on
    this run. A pair the MODEL left half-specified is untouched, so fee34 still
    reports it and the operator still has to fix it. Only self-inflicted damage
    is undone. See :data:`PAIRED_FIELDS`.
    """
    for field in refused:
        for partner in PAIRED_FIELDS.get(field, ()):
            if partner not in spec.schedule:
                continue
            reason = (
                f"withdrawn because {field} could not be established, and the "
                f"two are only meaningful together — a {partner} with no "
                f"{field} would be refused by fee34 for a reason the advisor "
                f"did not cause"
            )
            spec.discarded.append({
                "field": partner,
                "value": _plain(spec.schedule.pop(partner)),
                "reason": reason,
            })
            _add_unresolved(spec, partner, reason, "resolver")


def _grounding_failure(cited: Any, normalised_description: str) -> str | None:
    """None when the citation holds up; otherwise why it does not."""
    if not isinstance(cited, str) or not cited.strip():
        return (
            "the model gave a value but cited no span of the description to "
            "support it. Policy fields are not inferred — state it explicitly "
            "or leave it unset"
        )
    if len(cited.strip()) < MIN_EVIDENCE_CHARS:
        return (
            f"the model's citation {cited.strip()!r} is shorter than "
            f"{MIN_EVIDENCE_CHARS} characters; a span that short matches almost "
            f"any text and evidences nothing"
        )
    if _norm_text(cited) not in normalised_description:
        return (
            f"the model cited {cited.strip()!r} as the words that state this, "
            f"but that text does not appear in the description. The value was "
            f"discarded as ungrounded"
        )
    return None


def _normalise_bundle(
    raw: Any,
    label: str,
    scope_types: Sequence[str],
    text_fields: Sequence[str],
    money_fields: Sequence[str],
    spec: NormalisedSpec,
) -> list[dict[str, Any]]:
    """Exclusions / discounts / credits — shared shape, per-table vocabulary.

    ``scope_types`` differs per table (fee34 measured three different scope
    vocabularies) so it is passed in rather than shared.
    """
    out: list[dict[str, Any]] = []
    for index, row in enumerate(_as_list(raw)):
        if not isinstance(row, Mapping):
            continue
        entry: dict[str, Any] = {}
        scope_type = row.get("scope_type")
        if scope_type is not None and str(scope_type) not in scope_types:
            spec.discarded.append({
                "field": f"{label}[{index}].scope_type", "value": _plain(scope_type),
                "reason": f"not one of {list(scope_types)}",
            })
        elif scope_type is not None:
            entry["scope_type"] = str(scope_type)
        if row.get("scope_ref"):
            entry["scope_ref"] = str(row["scope_ref"])
        for text_field in text_fields:
            if row.get(text_field) is not None:
                entry[text_field] = str(row[text_field])
        problems: list[str] = []
        for money_field in money_fields:
            if row.get(money_field) is not None:
                entry[money_field] = _money(
                    row[money_field], f"{label}[{index}].{money_field}", problems
                )
        if problems:
            spec.discarded.append({"field": f"{label}[{index}]", "value": _plain(row),
                                   "reason": problems[0]})
            continue
        out.append(entry)
    return out


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _plain(value: Any) -> Any:
    """JSON-safe echo of a rejected value, for the ``discarded`` report."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def spec_to_json(spec: Mapping[str, Any]) -> str:
    """Serialise a spec for storage. Decimals become their exact digit strings."""
    return json.dumps(spec, default=str)


# ═══════════════════════════════════════════════════════════════════════════
# The model call
# ═══════════════════════════════════════════════════════════════════════════


async def propose_fee_spec(
    description: str,
    *,
    org_id: str | None,
    transport: Callable[..., Any] | None = None,
) -> tuple[NormalisedSpec, str]:
    """Ask the model for a FeeSpec. Returns ``(normalised spec, raw response)``.

    Routes through ``services.extraction.call_claude_text`` — the standing
    model-resolution path (``resolve_model`` on ``ai.model.default``, then the
    org's fallback chain), which writes exactly one ``ai_decision_log`` row per
    call naming the model actually used. This module does not log separately:
    a second writer would be a second place for the model name to drift from
    the one that answered.

    ``call_claude_text`` rather than ``call_claude_json`` on purpose. The JSON
    helper swallows an unparseable response into ``None``, which is the same
    value it returns when no credential is configured — collapsing "the model
    misbehaved" and "the platform is not set up" into one indistinguishable
    outcome. Parsing here keeps them separate and typed.

    ``transport`` is an injection seam for tests: a callable with
    ``call_claude_text``'s signature. It is NOT a fallback — passing nothing
    calls the real path, and there is no default stub that could quietly answer
    for a model that never ran.
    """
    if not (description or "").strip():
        raise FeeSpecShapeError("a fee description is required", field="description")

    if transport is None:
        from services.extraction import call_claude_text

        transport = call_claude_text

    raw = await transport(
        build_system_prompt(),
        [{"role": "user", "content": description}],
        4000,
        org_id=org_id,
        task_type=TASK_TYPE,
    )
    if raw is None:
        raise FeeSpecUnavailableError(
            "no model responded for task "
            f"'{TASK_TYPE}' — either no AI credential is configured for this "
            "deployment or every model in the org's fallback chain failed. "
            "ai_decision_log carries the per-model outcome."
        )

    parsed, warnings = parse_fee_spec(raw)
    spec = normalise_fee_spec(parsed, description)
    spec.warnings.extend(warnings)
    return spec, raw


__all__ = [
    "DEFAULT_ORDERING_POLICY",
    "FEE_SPEC_VERSION",
    "GROUNDED_FIELDS",
    "MIN_EVIDENCE_CHARS",
    "PAIRED_FIELDS",
    "REFERENCE_KINDS",
    "REQUIRED_SCHEDULE_FIELDS",
    "SCHEDULE_COLUMN_DEFAULTS",
    "SPEC_MONEY_FIELDS",
    "SPEC_SCHEDULE_FIELDS",
    "SPEC_VOCABULARIES",
    "TASK_TYPE",
    "FeeSpecError",
    "FeeSpecParseError",
    "FeeSpecShapeError",
    "FeeSpecUnavailableError",
    "NormalisedSpec",
    "build_system_prompt",
    "normalise_fee_spec",
    "parse_fee_spec",
    "propose_fee_spec",
    "spec_to_json",
]
