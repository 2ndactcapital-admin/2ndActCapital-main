"""The worked example: a proposed schedule, priced by fee35. fee40 Task 3.2.

Advisors verify a fee schedule by recognising a dollar figure, not by reading
field values. "0.85% graduated, quarterly, in arrears, period-end valuation" is
correct-looking to almost anyone; "$4,212.50 for the Harrison household this
quarter" is either right or obviously wrong to the person who negotiated it.

THE NUMBER IS fee35's NUMBER
──────────────────────────────────────────────────────────────────────────────
This module performs no arithmetic. It converts a FeeSpec into fee35's own
input dataclasses, hands them to ``fee_run_inputs.load_account_calc_request``
and then to ``fee_calc.calculate_account_fee``, and returns what comes back.
There is no multiplication anywhere in this file, and that is deliberate: a
second implementation that "agrees with fee35" agrees right up until one of the
two is changed, and the one on the confirmation screen is the one the client
sees.

The model contributes NOTHING to the figure beyond the schedule fields it
proposed, all of which have been through vocabulary and grounding checks and
are Decimals by the time they reach here.

WHEN IT REFUSES
──────────────────────────────────────────────────────────────────────────────
A worked example that cannot be computed is reported as such. It is never
approximated, never computed from a partial schedule with defaults filled in,
and never carried over from a previous spec. :class:`WorkedExampleUnavailable`
names the reason. A confirmation screen showing a stale or assumed figure is
worse than one showing none, because the advisor cannot tell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Mapping

from services.fee_calc import FeeCalcResult, calculate_account_fee
from services.fee_calc_inputs import FeeScheduleInput, FeeTierInput
from services.fee_run_inputs import load_account_calc_request
from services.fee_spec import REQUIRED_SCHEDULE_FIELDS, NormalisedSpec

#: The id stamped on a schedule that has no row yet. Not a uuid on purpose: it
#: must be impossible to mistake for a real ``fee_schedules.id`` if it ever
#: reaches a log or a payload.
UNSAVED_SCHEDULE_ID = "unsaved-proposal"


class WorkedExampleUnavailable(ValueError):
    """No dollar figure can honestly be produced. Carries the reason.

    Typed rather than a None return: a caller that forgot to check a None would
    render an empty currency cell, which reads as "$0.00 — nothing is owed"
    rather than "this could not be computed".
    """

    code = "worked_example_unavailable"

    def __init__(self, message: str, *, reason_code: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.context = context

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "reason_code": self.reason_code,
            "message": self.message, **({"context": self.context} if self.context else {}),
        }


@dataclass(frozen=True)
class WorkedExample:
    """One account, one period, one figure, and everything behind it."""

    account_id: str
    account_label: str
    household_id: str | None
    period_start: date
    period_end: date
    account_value: Decimal
    billable_value: Decimal
    gross_fee: Decimal
    amount: Decimal
    currency: str
    is_refund: bool
    engine_version: str
    assumptions: tuple[str, ...]
    calc_detail: dict[str, Any]
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "account_label": self.account_label,
            "household_id": self.household_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            # Money crosses the wire as a STRING. A JSON number would be parsed
            # back as a float by every client, reintroducing at the very last
            # step the drift the whole pipeline avoided.
            "account_value": str(self.account_value),
            "billable_value": str(self.billable_value),
            "gross_fee": str(self.gross_fee),
            "amount": str(self.amount),
            "currency": self.currency,
            "is_refund": self.is_refund,
            "engine_version": self.engine_version,
            "assumptions": list(self.assumptions),
            "calc_detail": self.calc_detail,
            "provenance": self.provenance,
            "computed_by": "services.fee_calc.calculate_account_fee",
        }


def spec_to_engine_inputs(
    spec: NormalisedSpec, *, schedule_id: str = UNSAVED_SCHEDULE_ID, org_id: str | None = None
) -> tuple[FeeScheduleInput, tuple[FeeTierInput, ...]]:
    """A FeeSpec as fee35's own input types.

    ``status`` is forced to DRAFT and never taken from the spec. fee35 does not
    gate on status (pricing an unapproved schedule is exactly the question being
    asked here), so the value is inert to the arithmetic — but a proposal
    labelled APPROVED in a stored ``calc_detail`` would misdescribe itself
    forever afterwards.

    Raises :class:`WorkedExampleUnavailable` rather than letting
    ``FeeCalcInputError`` out: a missing ``valuation_method`` here is not a
    programming fault, it is the ordinary state of a half-specified proposal and
    the screen needs to say so.
    """
    missing = [f for f in REQUIRED_SCHEDULE_FIELDS if f not in spec.schedule]
    if missing:
        raise WorkedExampleUnavailable(
            f"the proposal does not yet specify {', '.join(missing)}. A worked "
            f"example would have to assume a value for each, and an assumed "
            f"valuation method or billing frequency changes the figure without "
            f"showing that it did.",
            reason_code="spec_incomplete", missing=missing,
        )
    if not spec.tiers:
        raise WorkedExampleUnavailable(
            "the proposal has no tiers yet, so there is no rate to apply.",
            reason_code="no_tiers",
        )

    row = dict(spec.schedule)
    row["id"] = schedule_id
    row["org_id"] = org_id
    row["status"] = "DRAFT"

    from services.fee_calc_inputs import FeeCalcInputError

    try:
        schedule = FeeScheduleInput.from_row(row)
        tiers = tuple(
            FeeTierInput.from_row({**t, "fee_schedule_id": schedule_id})
            for t in sorted(spec.tiers, key=lambda t: (t.get("tier_seq") is None,
                                                       t.get("tier_seq")))
        )
    except FeeCalcInputError as exc:
        raise WorkedExampleUnavailable(
            f"the proposed schedule is not yet in a state fee35 can price: {exc}",
            reason_code="spec_rejected_by_engine", field=getattr(exc, "field", None),
        ) from exc
    return schedule, tiers


async def pick_example_account(
    conn, org_id: str, *, household_id: str | None = None, account_id: str | None = None
) -> Mapping[str, Any]:
    """Which real account to bill in the example.

    Preference order, most specific first: the account the advisor named, then
    any billable account in the household they are looking at, then the org's
    billable account with the most recent balance — the "designated demo
    household" of the sprint brief, resolved from data rather than configured,
    because a configured demo id goes stale silently the moment that account
    closes.

    An account with NO balance row in range is never chosen: fee35 would raise
    ``ValuationUnavailableError`` and the advisor would see a failure that looks
    like a bug in the proposal rather than an empty custodian feed.
    """
    base = """
        SELECT a.id::text AS id, a.account_number_masked AS label,
               a.household_id::text AS household_id, a.base_currency,
               (SELECT max(b.as_of_date) FROM account_balances_daily b
                 WHERE b.org_id = a.org_id AND b.account_id = a.id) AS last_balance_on
        FROM accounts a
        WHERE a.org_id = $1::uuid
          AND a.valid_to IS NULL AND a.system_to IS NULL
          AND a.is_billable
    """
    if account_id:
        row = await conn.fetchrow(base + " AND a.id = $2::uuid", org_id, account_id)
        if row is None:
            raise WorkedExampleUnavailable(
                f"account {account_id} is not a current billable account in this "
                f"organisation.",
                reason_code="account_not_found", account_id=account_id,
            )
        return dict(row)

    params: list[Any] = [org_id]
    sql = base + " AND EXISTS (SELECT 1 FROM account_balances_daily b " \
                 "  WHERE b.org_id = a.org_id AND b.account_id = a.id)"
    if household_id:
        params.append(household_id)
        sql += " AND a.household_id = $2::uuid"
    sql += " ORDER BY last_balance_on DESC NULLS LAST, a.account_number_masked LIMIT 1"

    row = await conn.fetchrow(sql, *params)
    if row is None:
        raise WorkedExampleUnavailable(
            (
                f"no billable account in this organisation has any balance history "
                f"to bill against"
                + (f" for household {household_id}" if household_id else "")
                + ". A worked example needs a real balance; there is nothing here "
                  "to compute from and a placeholder figure would be a fabrication."
            ),
            reason_code="no_billable_account_with_balances",
            household_id=household_id,
        )
    return dict(row)


async def compute_worked_example(
    conn,
    org_id: str,
    spec: NormalisedSpec,
    *,
    period_start: date,
    period_end: date,
    household_id: str | None = None,
    account_id: str | None = None,
) -> WorkedExample:
    """Price ``spec`` against a real account. The figure comes from fee35.

    Every exclusion, discount and credit that really applies to the chosen
    account is loaded and applied, exactly as fee36 would for a real run. A
    worked example that ignored an existing 20% legacy discount would show the
    advisor a number the client will never be billed.
    """
    account = await pick_example_account(
        conn, org_id, household_id=household_id, account_id=account_id
    )
    schedule, tiers = spec_to_engine_inputs(spec, org_id=org_id)

    from services.fee_calc import FeeCalcError
    from services.fee_run_inputs import FeeRunInputError

    try:
        request, provenance = await load_account_calc_request(
            conn, org_id,
            account_id=account["id"],
            period_start=period_start, period_end=period_end,
            schedule_override=(schedule, tiers),
        )
        result: FeeCalcResult = calculate_account_fee(request)
    except (FeeCalcError, FeeRunInputError) as exc:
        # fee35's own refusals are surfaced verbatim. Restating them ("could not
        # compute a fee") would hide ValuationUnavailableError's actual content,
        # which names the period it found no balance in.
        raise WorkedExampleUnavailable(
            f"{type(exc).__name__}: {exc}",
            reason_code="engine_refused", account_id=account["id"],
        ) from exc

    return WorkedExample(
        account_id=result.account_id,
        account_label=account["label"],
        household_id=account["household_id"],
        period_start=result.period_start,
        period_end=result.period_end,
        account_value=result.account_value,
        billable_value=result.billable_value,
        gross_fee=result.gross_fee,
        amount=result.amount,
        currency=result.currency,
        is_refund=result.is_refund,
        engine_version=result.calc_detail.get("engine_version", ""),
        assumptions=result.assumptions,
        calc_detail=result.calc_detail,
        provenance=provenance,
    )


def default_period(today: date, billing_frequency: str | None) -> tuple[date, date]:
    """A sensible period to price, derived from the schedule's own frequency.

    The CURRENT period, not the next one: the advisor is checking a figure
    against balances they can go and look at today.
    """
    months = {"MONTHLY": 1, "QUARTERLY": 3, "SEMIANNUAL": 6, "ANNUAL": 12}.get(
        billing_frequency or "QUARTERLY", 3
    )
    # Period index within the year, so a quarterly period starts in Jan/Apr/Jul/Oct
    # rather than "three months back from today", which would produce a different
    # period every day and a worked example that never reproduces.
    index = (today.month - 1) // months
    start_month = index * months + 1
    start = date(today.year, start_month, 1)
    end_month = start_month + months
    end_year = today.year + (1 if end_month > 12 else 0)
    end_month = end_month - 12 if end_month > 12 else end_month
    end = date(end_year, end_month, 1) - timedelta(days=1)
    return start, end


__all__ = [
    "UNSAVED_SCHEDULE_ID",
    "WorkedExample",
    "WorkedExampleUnavailable",
    "compute_worked_example",
    "default_period",
    "pick_example_account",
    "spec_to_engine_inputs",
]
