"""Local-date and budget-period maths. Pure functions, no imports from `app/`.

Postgres stores instants; it does not know a user's timezone. Every local date in this
app — `occurred_on_local`, `created_on_local`, the day a habit belongs to — is computed
here, in `profile.timezone`.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Kolkata"


def to_local_date(instant: datetime, timezone: str = DEFAULT_TIMEZONE) -> date:
    """The calendar day `instant` fell on, for this user.

    A 23:40 IST spend is 18:10 UTC the same day, but an 05:10 IST spend is 23:40 UTC
    the day before — which is the wrong day for both the streak and the budget period.
    """
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware; naive datetimes are a bug here")
    return instant.astimezone(ZoneInfo(timezone)).date()


def period_bounds(local_day: date, month_start_day: int) -> tuple[date, date]:
    """Start and inclusive end of the budget period containing `local_day`.

    `month_start_day` is 1..28 by CHECK on `profile`, so no month is short enough to
    need clamping.
    """
    if not 1 <= month_start_day <= 28:
        raise ValueError("month_start_day must be between 1 and 28")

    if local_day.day >= month_start_day:
        start = local_day.replace(day=month_start_day)
    else:
        start = _previous_month(local_day.replace(day=month_start_day))

    return start, _next_month(start) - timedelta(days=1)


def days_in_period(start: date, end_inclusive: date) -> int:
    if end_inclusive < start:
        raise ValueError("end must not precede start")
    return (end_inclusive - start).days + 1


def _next_month(day: date) -> date:
    if day.month == 12:
        return day.replace(year=day.year + 1, month=1)
    return day.replace(month=day.month + 1)


def _previous_month(day: date) -> date:
    if day.month == 1:
        return day.replace(year=day.year - 1, month=12)
    return day.replace(month=day.month - 1)
