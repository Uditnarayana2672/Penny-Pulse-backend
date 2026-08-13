from datetime import UTC, date, datetime

import pytest

from app.lib.dates import days_in_period, period_bounds, to_local_date


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
