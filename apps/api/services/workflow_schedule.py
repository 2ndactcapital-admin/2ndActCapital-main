"""Recurrence computation for scheduled workflow triggers.

This module is PURE: no database, no I/O, no ``datetime.now()`` unless one is
handed in. Everything the firing loop decides about *whether a trigger is due*
is decided here, so it can be proven against injected instants instead of
against whatever the clock happens to say when the test runs.

────────────────────────────────────────────────────────────────────────────
WHY A CRON STRING AND AN RRULE LIBRARY
────────────────────────────────────────────────────────────────────────────
``workflow_triggers.schedule_cron`` already exists, already holds real data
(``'0 9 * * *'``), and is already rendered by the trigger list UI. It stays the
stored form. The *evaluation* is done by ``dateutil.rrule`` — a real,
maintained recurrence library — by translating the cron expression into an
equivalent rrule. We do not hand-roll "does this minute match" arithmetic.

The translation has one trap that a naive mapping gets wrong, and it is the
reason this is a function with tests rather than a dict comprehension:

    **cron ORs day-of-month against day-of-week; rrule ANDs them.**

In cron, ``0 0 13 * 5`` means "the 13th, OR any Friday" — not "Friday the
13th". ``rrule(bymonthday=13, byweekday=FR)`` means the latter. When BOTH
fields are restricted we therefore build two rrules and union them in an
``rruleset``. When only one is restricted the AND/OR distinction is vacuous and
a single rrule is exact.

────────────────────────────────────────────────────────────────────────────
WHY PER-TRIGGER TIMEZONE IS COMPUTED HERE AND NOT BY THE CRON SERVICE
────────────────────────────────────────────────────────────────────────────
Render's ``type: cron`` schedules are **UTC-only** and cannot be made
timezone-aware (Render docs, confirmed 2026-08-26 by schedulerdiscovery). The
platform cron therefore ticks frequently in UTC and this module answers, per
trigger, "is *your* local schedule due at this UTC instant?". Occurrences are
generated in the trigger's own naive local time and only then attached to its
``ZoneInfo``, which is what makes "09:00 every weekday" stay at 09:00 across a
DST change instead of drifting by an hour.

────────────────────────────────────────────────────────────────────────────
THE LOOKBACK WINDOW
────────────────────────────────────────────────────────────────────────────
A tick asks for the most recent occurrence in ``(now - LOOKBACK, now]``, not
"is now exactly an occurrence". Two reasons:

  * the platform cron fires every few minutes, so an occurrence almost never
    lands exactly on a tick — without a window nearly everything would be
    missed;
  * Render *delays* a cron run when the previous one is still active, so ticks
    can be late. A bounded catch-up turns a late tick into a fired occurrence.

The window is bounded (default 60 minutes) on purpose. An occurrence older than
that is **stale** — firing a 09:00 report at 14:00 because the service was down
all morning is worse than skipping it — and the bound also caps the rrule
iteration to ~60 candidates per trigger, so the scan cost does not depend on
how long ago ``start_date`` was.

────────────────────────────────────────────────────────────────────────────
DST EDGES, STATED RATHER THAN HIDDEN
────────────────────────────────────────────────────────────────────────────
  * Ambiguous local times (the repeated hour when clocks go back) resolve to
    ``fold=0``, i.e. the FIRST of the two real instants. The occurrence fires
    once, not twice, because ``last_fired_at`` then covers it.
  * Non-existent local times (the skipped hour when clocks go forward) are
    mapped by ``ZoneInfo`` onto a real instant offset by the gap. A 02:30 daily
    schedule in America/New_York therefore fires on spring-forward day rather
    than silently vanishing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import MINUTELY, rrule, rruleset

# How far back a tick will reach for an unfired occurrence. See module docstring.
DEFAULT_LOOKBACK_MINUTES = 60

# Cron field bounds, in field order: minute hour day-of-month month day-of-week.
_FIELD_RANGES = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day_of_month", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 7),   # both 0 and 7 mean Sunday, as in POSIX cron
)

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

# A few cron nicknames that appear in real configuration.
_NICKNAMES = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


class ScheduleError(ValueError):
    """A schedule that cannot be evaluated: bad cron, or an unknown IANA zone.

    Raised rather than defaulted. A trigger whose timezone is a typo must fail
    loudly and visibly in the tick log — silently falling back to UTC would fire
    somebody's 09:00 report at the wrong hour and look like it worked.
    """


# ── cron parsing ────────────────────────────────────────────────────────────
def _parse_field(raw: str, name: str, low: int, high: int) -> set[int] | None:
    """Return the set of matching values, or ``None`` for an unrestricted ``*``.

    Supports the standard vocabulary: ``*``, ``a``, ``a-b``, ``a-b/s``, ``*/s``
    and comma-separated lists of those, plus three-letter month / weekday names.
    """
    raw = raw.strip().lower()
    if not raw:
        raise ScheduleError(f"cron field '{name}' is empty")
    if raw == "*":
        return None

    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise ScheduleError(f"cron field '{name}' has an empty list element")

        step = 1
        if "/" in part:
            part, _, step_raw = part.partition("/")
            if not step_raw.isdigit() or int(step_raw) < 1:
                raise ScheduleError(f"cron field '{name}' has a bad step '{step_raw}'")
            step = int(step_raw)
            part = part.strip() or "*"

        if part == "*":
            start, end = low, high
        elif "-" in part.lstrip("-"):
            start_raw, _, end_raw = part.partition("-")
            start = _atom(start_raw, name, low, high)
            end = _atom(end_raw, name, low, high)
            if start > end:
                raise ScheduleError(
                    f"cron field '{name}' has an inverted range '{part}'"
                )
        else:
            start = end = _atom(part, name, low, high)

        values.update(range(start, end + 1, step))

    if not values:
        raise ScheduleError(f"cron field '{name}' matches nothing")
    return values


def _atom(raw: str, name: str, low: int, high: int) -> int:
    raw = raw.strip()
    if name == "month" and raw in _MONTH_NAMES:
        return _MONTH_NAMES[raw]
    if name == "day_of_week" and raw in _DOW_NAMES:
        return _DOW_NAMES[raw]
    try:
        value = int(raw)
    except ValueError:
        raise ScheduleError(f"cron field '{name}' has a non-numeric value '{raw}'") from None
    if not low <= value <= high:
        raise ScheduleError(
            f"cron field '{name}' value {value} is outside {low}-{high}"
        )
    return value


def parse_cron(expression: str) -> dict[str, set[int] | None]:
    """Parse a 5-field cron expression into per-field value sets.

    ``None`` for a field means unrestricted (``*``). Raises ``ScheduleError``
    on anything it cannot evaluate — never a partial or guessed parse.
    """
    if not expression or not expression.strip():
        raise ScheduleError("schedule_cron is empty")
    text = expression.strip().lower()
    text = _NICKNAMES.get(text, text)

    fields = text.split()
    if len(fields) != 5:
        raise ScheduleError(
            f"schedule_cron must have 5 fields (minute hour day-of-month month "
            f"day-of-week); got {len(fields)}: {expression!r}"
        )
    return {
        name: _parse_field(fields[i], name, low, high)
        for i, (name, low, high) in enumerate(_FIELD_RANGES)
    }


def _to_rrule_weekdays(dow: set[int]) -> list[int]:
    """cron day-of-week (0/7=Sun … 6=Sat) → dateutil weekday (0=Mon … 6=Sun).

    Both 0 and 7 are Sunday in cron, so 7 is normalized to 0 before the shift.
    """
    return sorted({((0 if d == 7 else d) + 6) % 7 for d in dow})


def build_recurrence(expression: str, dtstart: datetime):
    """Build the dateutil recurrence for ``expression``, starting at ``dtstart``.

    ``dtstart`` is a NAIVE local datetime — occurrences are generated in the
    trigger's own wall-clock time and localized afterwards, which is what makes
    a 09:00 schedule survive a DST change.
    """
    if dtstart.tzinfo is not None:
        raise ScheduleError("build_recurrence requires a naive local dtstart")
    fields = parse_cron(expression)

    common = dict(
        freq=MINUTELY,
        interval=1,
        dtstart=dtstart.replace(second=0, microsecond=0),
        byminute=sorted(fields["minute"]) if fields["minute"] else list(range(60)),
        byhour=sorted(fields["hour"]) if fields["hour"] else list(range(24)),
        bymonth=sorted(fields["month"]) if fields["month"] else list(range(1, 13)),
        bysecond=[0],
    )

    dom, dow = fields["day_of_month"], fields["day_of_week"]

    if dom is None and dow is None:
        return rrule(**common)
    if dow is None:
        return rrule(bymonthday=sorted(dom), **common)
    if dom is None:
        return rrule(byweekday=_to_rrule_weekdays(dow), **common)

    # BOTH restricted: cron means OR, rrule means AND. Union two rrules so the
    # semantics stay cron's. See the module docstring.
    combined = rruleset()
    combined.rrule(rrule(bymonthday=sorted(dom), **common))
    combined.rrule(rrule(byweekday=_to_rrule_weekdays(dow), **common))
    return combined


# ── the human-readable summary ──────────────────────────────────────────────
# Built on parse_cron, NOT on a second pass over the raw string. A summary
# produced by its own regex would be free to describe a schedule that the
# evaluator reads differently — and the summary is the ONLY form most operators
# will ever read, so a disagreement there means somebody signs off on a
# recurrence that is not the one that runs. Anything the describer cannot phrase
# confidently falls back to the raw cron expression rather than guessing.
_ORDINAL_WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _clock(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    return f"{display}:{minute:02d} {suffix}"


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _even_step(values: set[int], low: int, high: int) -> int | None:
    """The step of an evenly-spaced field starting at ``low``, or ``None``.

    ``*/15`` parses to {0,15,30,45}; recovering the 15 is what lets the summary
    say "every 15 minutes" instead of listing four numbers.
    """
    ordered = sorted(values)
    if len(ordered) < 2 or ordered[0] != low:
        return None
    step = ordered[1] - ordered[0]
    if step < 1 or set(range(low, high + 1, step)) != values:
        return None
    return step


def describe_schedule(expression: str, timezone_name: str | None = None) -> str:
    """A one-line English rendering of ``expression``, e.g.
    ``"Daily at 9:00 AM (America/New_York)"``.

    Never raises: an expression this cannot parse or cannot phrase comes back as
    the raw cron string, which is honest and still renderable.
    """
    zone = (timezone_name or "UTC").strip() or "UTC"
    raw = (expression or "").strip()
    suffix = f" ({zone})" if zone else ""
    try:
        fields = parse_cron(raw)
    except ScheduleError:
        return raw or "—"

    # Bound by NAME, not by position: the cron field order (minute hour
    # day-of-month month day-of-week) puts month between the two day fields, and
    # unpacking positionally is how a describer ends up rendering months as
    # weekdays.
    minute = fields["minute"]
    hour = fields["hour"]
    dom = fields["day_of_month"]
    month = fields["month"]
    dow = fields["day_of_week"]

    # ── the time-of-day phrase ──
    if minute is None and hour is None:
        when = "every minute"
    elif minute is None:
        when = f"every minute of {', '.join(f'{h:02d}:00' for h in sorted(hour))}"
    elif hour is None:
        step = _even_step(minute, 0, 59)
        if step is not None:
            when = f"every {step} minutes"
        elif len(minute) == 1:
            when = f"hourly at :{next(iter(minute)):02d}"
        else:
            return raw + suffix
    elif len(minute) == 1 and len(hour) == 1:
        when = f"at {_clock(next(iter(hour)), next(iter(minute)))}"
    elif len(minute) == 1:
        step = _even_step(hour, 0, 23)
        m = next(iter(minute))
        if step is not None:
            when = f"every {step} hours at :{m:02d}"
        else:
            when = ("at " + ", ".join(_clock(h, m) for h in sorted(hour)))
    else:
        return raw + suffix

    # ── the day phrase ──
    if dom is None and dow is None:
        day = "Daily"
    elif dow is not None and dom is None:
        normalized = sorted({0 if d == 7 else d for d in dow})
        if normalized == [1, 2, 3, 4, 5]:
            day = "Weekdays"
        elif normalized == [0, 6]:
            day = "Weekends"
        elif len(normalized) == 7:
            day = "Daily"
        else:
            day = "Every " + ", ".join(_ORDINAL_WEEKDAYS[d] for d in normalized)
    elif dom is not None and dow is None:
        day = "Monthly on the " + ", ".join(_ordinal(d) for d in sorted(dom))
    else:
        # cron ORs the two restricted day fields — say so rather than implying
        # the intersection the reader would otherwise assume.
        normalized = sorted({0 if d == 7 else d for d in dow})
        day = ("Monthly on the " + ", ".join(_ordinal(d) for d in sorted(dom))
               + " or every " + ", ".join(_ORDINAL_WEEKDAYS[d] for d in normalized))

    # "Daily every 15 minutes" and "Daily hourly at :05" are both worse than
    # dropping the redundant day word — a frequency phrase already implies every
    # day. A RESTRICTED day still carries information ("Weekdays every 15
    # minutes") and is kept.
    if day == "Daily" and when.startswith(("every", "hourly")):
        text = when
    else:
        text = f"{day} {when}"
    if month is not None and len(month) < 12:
        text += " in " + ", ".join(_MONTH_LABELS[m - 1] for m in sorted(month))
    return text[0].upper() + text[1:] + suffix


# ── the due decision ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ScheduleDecision:
    """The verdict for one trigger at one instant.

    ``occurrence_utc`` is the instant of the occurrence being claimed — the
    value written to ``last_fired_at`` — NOT the wall-clock time of the tick.
    Writing the tick time instead would make idempotency drift: two ticks two
    minutes apart would both compare "greater than the last one" and fire twice.
    """

    due: bool
    occurrence_utc: datetime | None
    reason: str

    def __str__(self) -> str:  # for the tick log
        when = self.occurrence_utc.isoformat() if self.occurrence_utc else "-"
        return f"{'DUE' if self.due else 'skip'} occurrence={when} — {self.reason}"


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Resolve an IANA zone name, raising ``ScheduleError`` on anything unknown."""
    candidate = (name or "").strip() or "UTC"
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ScheduleError(f"unknown IANA timezone {candidate!r}") from None


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize to an aware UTC datetime. A naive input is read as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_trigger(
    *,
    schedule_cron: str,
    timezone_name: str | None,
    now_utc: datetime,
    last_fired_at: datetime | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    max_occurrences: int | None = None,
    occurrence_count: int = 0,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
) -> ScheduleDecision:
    """Decide whether this trigger is due at ``now_utc``, and for which occurrence.

    Every ``skip`` carries a reason string, which the tick logs verbatim. A
    scheduler that says only "nothing fired" is unauditable.
    """
    tz = resolve_timezone(timezone_name)
    now_utc = _as_utc(now_utc)
    last_fired_at = _as_utc(last_fired_at)
    start_date = _as_utc(start_date)
    end_date = _as_utc(end_date)

    # Caps first: a spent trigger should say so, not report "no occurrence".
    if max_occurrences is not None and (occurrence_count or 0) >= max_occurrences:
        return ScheduleDecision(
            False, None,
            f"max_occurrences reached ({occurrence_count}/{max_occurrences})",
        )
    if end_date is not None and now_utc > end_date:
        return ScheduleDecision(False, None, f"past end_date {end_date.isoformat()}")

    # The search window: (floor, now]. Raised by start_date and by whatever the
    # last fire already covered, so an occurrence is never re-examined.
    floor_utc = now_utc - timedelta(minutes=lookback_minutes)
    if start_date is not None and start_date > floor_utc:
        floor_utc = start_date
    if last_fired_at is not None and last_fired_at >= floor_utc:
        floor_utc = last_fired_at + timedelta(minutes=1)
    if floor_utc > now_utc:
        if start_date is not None and start_date > now_utc:
            return ScheduleDecision(
                False, None, f"before start_date {start_date.isoformat()}"
            )
        return ScheduleDecision(
            False, None, "no unfired occurrence in the lookback window"
        )

    floor_local = floor_utc.astimezone(tz).replace(tzinfo=None)
    now_local = now_utc.astimezone(tz).replace(tzinfo=None)

    recurrence = build_recurrence(schedule_cron, floor_local)
    candidates = recurrence.between(floor_local, now_local, inc=True)
    if not candidates:
        return ScheduleDecision(
            False, None,
            f"no occurrence between {floor_local.isoformat()} and "
            f"{now_local.isoformat()} local ({tz.key})",
        )

    occurrence_utc = candidates[-1].replace(tzinfo=tz).astimezone(timezone.utc)

    # Re-check the bounds against the OCCURRENCE, not against `now`. The window
    # reaches backwards, so an occurrence can legitimately predate start_date or
    # postdate end_date even when `now` is inside the window.
    if start_date is not None and occurrence_utc < start_date:
        return ScheduleDecision(
            False, None, f"occurrence precedes start_date {start_date.isoformat()}"
        )
    if end_date is not None and occurrence_utc > end_date:
        return ScheduleDecision(
            False, None, f"occurrence follows end_date {end_date.isoformat()}"
        )
    if last_fired_at is not None and occurrence_utc <= last_fired_at:
        return ScheduleDecision(
            False, None,
            f"occurrence already covered by last_fired_at {last_fired_at.isoformat()}",
        )

    return ScheduleDecision(
        True, occurrence_utc,
        f"occurrence {candidates[-1].isoformat()} local ({tz.key}) is due",
    )


# ── the dry-run preview ─────────────────────────────────────────────────────
# How far forward the preview will search before giving up. A cron expression
# CAN match nothing for years — ``0 0 30 2 *`` (30 February) matches never — and
# ``rrule`` iterates forever rather than raising. Without this bound a preview
# request for such an expression would hang a worker instead of returning a
# short list.
_PREVIEW_HORIZON_DAYS = 366 * 5


def next_occurrences(
    *,
    schedule_cron: str,
    timezone_name: str | None,
    after_utc: datetime,
    count: int = 5,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    max_occurrences: int | None = None,
    occurrence_count: int = 0,
) -> list[datetime]:
    """The next ``count`` occurrences strictly after ``after_utc``, in UTC.

    THIS IS THE PREVIEW, AND IT SHARES THE RECURRENCE WITH THE FIRING LOOP.
    It calls the same :func:`build_recurrence`, the same :func:`resolve_timezone`
    and the same generate-naive-then-localize order that :func:`evaluate_trigger`
    uses, and it applies the same ``start_date`` / ``end_date`` /
    ``max_occurrences`` bounds. It is a DIFFERENT QUESTION asked of ONE engine —
    "which occurrences are coming" rather than "is one due right now" — not a
    second implementation of the recurrence.

    That distinction is the whole point. A preview computed by a parallel
    implementation would agree with the scheduler right up until the day one of
    them was changed, and the disagreement would surface as a workflow that ran
    at a time nobody was shown. ``verify_schedulerux`` proves the equivalence
    directly: for every occurrence returned here it drives ``evaluate_trigger``
    at that instant and asserts it reports DUE for exactly that occurrence.

    ``after_utc`` is normalized to the start of its minute — cron granularity is
    one minute, so a preview anchored mid-minute would otherwise skip an
    occurrence falling in the same minute as the anchor.
    """
    if count <= 0:
        return []
    tz = resolve_timezone(timezone_name)
    after_utc = _as_utc(after_utc).replace(second=0, microsecond=0)
    start_date = _as_utc(start_date)
    end_date = _as_utc(end_date)

    # A trigger that has already spent its cap has no next occurrence, and
    # saying so is more useful than listing five that will never run.
    remaining = count
    if max_occurrences is not None:
        remaining = min(remaining, max(0, max_occurrences - (occurrence_count or 0)))
    if remaining <= 0:
        return []
    if end_date is not None and end_date <= after_utc:
        return []

    floor_utc = after_utc
    if start_date is not None and start_date > floor_utc:
        floor_utc = start_date
    floor_local = floor_utc.astimezone(tz).replace(tzinfo=None)
    horizon_local = floor_local + timedelta(days=_PREVIEW_HORIZON_DAYS)

    recurrence = build_recurrence(schedule_cron, floor_local)

    found: list[datetime] = []
    for local in recurrence:
        if local > horizon_local:
            break
        occurrence_utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
        if occurrence_utc <= after_utc:
            continue
        if start_date is not None and occurrence_utc < start_date:
            continue
        if end_date is not None and occurrence_utc > end_date:
            break
        found.append(occurrence_utc)
        if len(found) >= remaining:
            break
    return found
