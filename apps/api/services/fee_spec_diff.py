"""What the model proposed, against what is true today. fee40 Task 2.3.

An advisor approving a schedule is not reading a form, they are answering "what
changes?". A diff answers that; a filled-in form does not, because a field the
model set to the value it already had looks identical to a field it changed.

THE BASELINE IS MEASURED, NOT ASSUMED
──────────────────────────────────────────────────────────────────────────────
Three different baselines are possible and they are NOT interchangeable, so
each row says which one it was compared against:

  * ``current_schedule`` — this spec edits an existing schedule. The baseline is
    that schedule's own current values.
  * ``org_default``      — a new schedule, and the org has an ORG_DEFAULT fee
    assignment. The baseline is the schedule that assignment points at: what
    this client would be billed under today if nothing were created.
  * ``column_default``   — a new schedule and no org default exists. The
    baseline is ``fee_schedules``' own deployed column DEFAULTs.

Collapsing these into one "current" column would tell an advisor that a
proposed 100bps schedule "changes" a rate that was never set, which is the kind
of wrong that reads as authoritative.

UNRESOLVED IS ITS OWN STATUS, NOT A BLANK
──────────────────────────────────────────────────────────────────────────────
``status='unresolved'`` is visually distinct from ``status='unchanged'``
downstream because they mean opposite things. Unchanged is "we know, and it
stays". Unresolved is "nobody knows yet". A blank cell reads as the first while
meaning the second.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from services.fee_spec import (
    REQUIRED_SCHEDULE_FIELDS,
    SCHEDULE_COLUMN_DEFAULTS,
    SPEC_SCHEDULE_FIELDS,
    NormalisedSpec,
)

#: The deployed column DEFAULTs, re-exported under this module's older name.
#: ONE copy, defined in ``fee_spec``: the grounding check and this diff must
#: agree on what "the default" is, and two transcriptions of the same six
#: values would eventually disagree about ordering_policy — at which point a
#: field would be admitted without evidence and still shown as "changed".
COLUMN_DEFAULTS: Mapping[str, Any] = SCHEDULE_COLUMN_DEFAULTS

BASELINE_CURRENT = "current_schedule"
BASELINE_ORG_DEFAULT = "org_default"
BASELINE_COLUMN_DEFAULT = "column_default"


async def load_org_default_schedule(conn, org_id: str) -> dict[str, Any] | None:
    """The schedule behind the org's ORG_DEFAULT assignment, if it has one.

    ``effective_to`` is checked against today rather than ignored: an assignment
    that ended last month is not what a new client would be billed under, and
    showing it as the baseline would describe a past arrangement as the status
    quo.
    """
    row = await conn.fetchrow(
        """
        SELECT s.*
        FROM fee_assignments a
        JOIN fee_schedules s
          ON s.id = a.fee_schedule_id AND s.org_id = a.org_id
         AND s.valid_to IS NULL AND s.system_to IS NULL
        WHERE a.org_id = $1::uuid
          AND a.scope_type = 'ORG_DEFAULT'
          AND a.valid_to IS NULL AND a.system_to IS NULL
          AND a.effective_from <= CURRENT_DATE
          AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
        ORDER BY a.effective_from DESC
        LIMIT 1
        """,
        org_id,
    )
    return dict(row) if row else None


def _comparable(value: Any) -> Any:
    """Normalise for EQUALITY only — never for display.

    ``Decimal('100')`` and ``Decimal('100.00')`` are the same rate written two
    ways, and reporting that as a change would train an advisor to click past
    the diff. ``normalize()`` collapses them. The value SHOWN is always the
    original.
    """
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value)


def _display(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return value


def build_schedule_diff(
    spec: NormalisedSpec,
    *,
    baseline: Mapping[str, Any] | None,
    baseline_kind: str,
) -> list[dict[str, Any]]:
    """One row per schedule field. Every field, always — including unchanged.

    Filtering to changes only would hide the required-but-unresolved fields,
    which are exactly the ones that block the save.
    """
    unresolved = spec.unresolved_fields
    reasons = {u["field"]: u["reason"] for u in spec.unresolved}
    baseline = baseline or {}
    rows: list[dict[str, Any]] = []

    for field in SPEC_SCHEDULE_FIELDS:
        proposed = spec.schedule.get(field)
        if baseline_kind == BASELINE_COLUMN_DEFAULT:
            current = COLUMN_DEFAULTS.get(field)
            has_current = field in COLUMN_DEFAULTS
        else:
            current = baseline.get(field)
            has_current = field in baseline

        if field in unresolved:
            status = "unresolved"
        elif field not in spec.schedule:
            # Not proposed and not unresolved: the model left an OPTIONAL field
            # alone. That is a real answer ("no maximum fee"), distinct from
            # "unknown", and must not be shown as a change to null.
            status = "not_specified"
        elif not has_current or current is None:
            status = "new"
        elif _comparable(proposed) == _comparable(current):
            status = "unchanged"
        else:
            status = "changed"

        rows.append({
            "field": field,
            "proposed": _display(proposed),
            "current": _display(current) if has_current else None,
            "status": status,
            "baseline": baseline_kind,
            "required": field in REQUIRED_SCHEDULE_FIELDS,
            "reason": reasons.get(field),
        })
    return rows


def build_tier_diff(
    spec: NormalisedSpec, *, current_tiers: list[Mapping[str, Any]] | None
) -> list[dict[str, Any]]:
    """Tier ladders side by side, matched on ``tier_seq``.

    Matched by sequence and not by position: a proposal that INSERTS a tier at
    the bottom shifts every later tier's index, and a positional diff would
    report every rung as changed when only one was added.
    """
    current_by_seq = {
        t.get("tier_seq"): t for t in (current_tiers or []) if t.get("tier_seq") is not None
    }
    proposed_by_seq = {
        t.get("tier_seq"): t for t in spec.tiers if t.get("tier_seq") is not None
    }
    rows: list[dict[str, Any]] = []
    for seq in sorted(set(current_by_seq) | set(proposed_by_seq), key=lambda s: (s is None, s)):
        proposed = proposed_by_seq.get(seq)
        current = current_by_seq.get(seq)
        if proposed is None:
            status = "removed"
        elif current is None:
            status = "added"
        elif all(
            _comparable(proposed.get(f)) == _comparable(current.get(f))
            for f in ("lower_bound", "upper_bound", "rate_bps", "flat_amount")
        ):
            status = "unchanged"
        else:
            status = "changed"
        rows.append({
            "tier_seq": seq,
            "proposed": {k: _display(v) for k, v in (proposed or {}).items()},
            "current": {
                k: _display(v) for k, v in (current or {}).items()
                if k in ("tier_seq", "lower_bound", "upper_bound", "rate_bps", "flat_amount")
            } if current is not None else None,
            "status": status,
        })
    return rows


async def build_diff(
    conn, org_id: str, spec: NormalisedSpec, *, fee_schedule_id: str | None = None
) -> dict[str, Any]:
    """The whole comparison, with its baseline named.

    ``fee_schedule_id`` present means this spec EDITS that schedule; absent
    means it proposes a new one.
    """
    baseline: Mapping[str, Any] | None = None
    current_tiers: list[Mapping[str, Any]] | None = None
    baseline_kind = BASELINE_COLUMN_DEFAULT
    baseline_label = (
        "fee_schedules' own column defaults — this organisation has no "
        "ORG_DEFAULT fee assignment to compare against"
    )

    if fee_schedule_id:
        from services.fee_schedules import load_schedule, load_tiers

        baseline = await load_schedule(conn, org_id, fee_schedule_id)
        current_tiers = await load_tiers(conn, org_id, fee_schedule_id)
        baseline_kind = BASELINE_CURRENT
        baseline_label = (
            f"the current values of schedule {baseline.get('code')} "
            f"v{baseline.get('version')}"
        )
    else:
        org_default = await load_org_default_schedule(conn, org_id)
        if org_default:
            from services.fee_schedules import load_tiers

            baseline = org_default
            current_tiers = await load_tiers(conn, org_id, org_default["id"])
            baseline_kind = BASELINE_ORG_DEFAULT
            baseline_label = (
                f"the organisation's default schedule "
                f"{org_default.get('code')} v{org_default.get('version')}, which "
                f"is what would apply if nothing new were created"
            )

    return {
        "baseline": baseline_kind,
        "baseline_label": baseline_label,
        "fields": build_schedule_diff(spec, baseline=baseline, baseline_kind=baseline_kind),
        "tiers": build_tier_diff(spec, current_tiers=current_tiers),
    }


__all__ = [
    "BASELINE_COLUMN_DEFAULT",
    "BASELINE_CURRENT",
    "BASELINE_ORG_DEFAULT",
    "COLUMN_DEFAULTS",
    "build_diff",
    "build_schedule_diff",
    "build_tier_diff",
    "load_org_default_schedule",
]
