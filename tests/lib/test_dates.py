from datetime import UTC, date, datetime

import pytest

from app.lib.dates import (
    days_in_period,
    next_occurrence_on_or_after,
    period_bounds,
    to_local_date,
)


def test_late_night_ist_spend_belongs_to_the_ist_day_not_the_utc_one():
    instant = datetime(2026, 8, 13, 20, 10, tzinfo=UTC)  # 01:40 IST on the 14th
    assert to_local_date(instant) == date(2026, 8, 14)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        to_local_date(datetime(2026, 8, 13, 20, 10))


def test_period_starting_on_the_5th_contains_a_day_after_the_start():
    assert period_bounds(date(2026, 8, 20), 5) == (date(2026, 8, 5), date(2026, 9, 4))


def test_a_day_before_the_start_belongs_to_the_previous_period():
    assert period_bounds(date(2026, 8, 3), 5) == (date(2026, 7, 5), date(2026, 8, 4))


def test_period_spanning_a_year_boundary():
    assert period_bounds(date(2027, 1, 2), 5) == (date(2026, 12, 5), date(2027, 1, 4))


def test_days_in_period_is_inclusive():
    assert days_in_period(date(2026, 8, 5), date(2026, 9, 4)) == 31


def test_period_starting_on_the_28th_of_february_is_a_short_period():
    """The 1..28 bound exists so this case needs no clamping. February is the whole reason."""
    assert period_bounds(date(2027, 3, 1), 28) == (date(2027, 2, 28), date(2027, 3, 27))


def test_period_starting_on_the_28th_in_a_leap_february():
    assert period_bounds(date(2028, 2, 29), 28) == (date(2028, 2, 28), date(2028, 3, 27))


def test_month_start_day_of_one_gives_calendar_months():
    assert period_bounds(date(2026, 8, 11), 1) == (date(2026, 8, 1), date(2026, 8, 31))


def test_next_occurrence_includes_the_day_itself():
    """On-or-after, not strictly after: the caller adds a day when it wants exclusivity."""
    assert next_occurrence_on_or_after(date(2026, 9, 1), 1) == date(2026, 9, 1)


def test_next_occurrence_rolls_into_the_following_month():
    assert next_occurrence_on_or_after(date(2026, 8, 28), 1) == date(2026, 9, 1)


def test_next_occurrence_crosses_a_year_boundary():
    assert next_occurrence_on_or_after(date(2026, 12, 15), 5) == date(2027, 1, 5)


def test_next_occurrence_refuses_a_day_that_some_month_lacks():
    with pytest.raises(ValueError):
        next_occurrence_on_or_after(date(2026, 8, 1), 31)
