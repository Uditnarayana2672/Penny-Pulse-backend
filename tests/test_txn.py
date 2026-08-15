"""`POST /transactions` rules, with no database and no HTTP.

`app/services/txn.py` imports neither `fastapi` nor `sqlalchemy`, so bucket ownership, kind
matching, period derivation, the safe-to-spend chain and the habit tick are all testable from
plain dicts. That is the whole reason the layering rule exists.

Router-level tests against a migrated Postgres are still owed — see the note at the bottom.
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.txn import TxnCreate
from app.services.errors import (
    CategoryArchivedError,
    CategoryNotFoundError,
    KindMismatchError,
)
from app.services.txn import (
    PeriodWindow,
    build_period_row,
    build_txn_row,
    category_impact,
    check_category_is_usable,
    habit_out,
    is_committed,
    local_dates,
    resolve_bucket,
    resolve_period_bounds,
    row_style,
    safe_to_spend_minor,
)

# The spec's worked example: period 28 Jul – 27 Aug, and "today" is 11 August.
PERIOD_ID = UUID("018f9c31-0000-7000-8000-000000000001")
STARTS_ON = date(2026, 7, 28)
ENDS_ON = date(2026, 8, 27)
TODAY = date(2026, 8, 11)
NOW = datetime(2026, 8, 11, 13, 45, tzinfo=UTC)

WINDOW = PeriodWindow(
    id=PERIOD_ID, starts_on=STARTS_ON, ends_on=ENDS_ON, local_today=TODAY
)

FOOD_ID = UUID("018f9c30-0001-7000-8000-000000000001")


def category_row(**overrides):
    """Exactly the columns `repositories/txn.get_category_for_write` selects."""
    row = {
        "id": FOOD_ID,
        "name": "Food",
        "icon": "utensils",
        "colour": "#E5484D",
        "kind": "expense",
        "default_bucket": "WANTS",
        "flexibility": 4,
        "is_archived": False,
    }
    return {**row, **overrides}


def limit_row(**overrides):
    row = {"limit_minor": 315_000, "carried_in_minor": 0}
    return {**row, **overrides}


def flexible_row(**overrides):
    """A row from `list_period_limits_with_spend`."""
    row = {
        "category_id": uuid4(),
        "limit_minor": 315_000,
        "carried_in_minor": 0,
        "flexibility": 4,
        "default_bucket": "WANTS",
        "spent_minor": 0,
    }
    return {**row, **overrides}


def payload(**overrides) -> TxnCreate:
    body = {
        "id": UUID("018f9d10-0000-7000-8000-00000000002a"),
        "direction": "out",
        "amount_minor": 25_000,
        "category_id": FOOD_ID,
    }
    return TxnCreate.model_validate({**body, **overrides})


# ---------- shape, at the edge ----------


def test_only_four_fields_are_required():
    """Everything else defaults or hides behind '+ details'. The target is under five
    seconds from tapping + to a saved transaction."""
    result = payload()

    assert result.occurred_at is None
    assert result.bucket is None
    assert result.source == "manual"
    assert result.edited_before_save is False


def test_a_negative_or_zero_amount_is_refused():
    """The sign lives in `direction`. A negative amount is a bug, not a refund."""
    with pytest.raises(ValidationError):
        payload(amount_minor=0)
    with pytest.raises(ValidationError):
        payload(amount_minor=-25_000)


def test_income_may_not_carry_a_bucket():
    """Buckets classify spending. Mirrors `txn_bucket_matches_direction` so the client gets
    a named field rather than a 23514."""
    with pytest.raises(ValidationError):
        payload(direction="in", bucket="WANTS")


def test_why_belongs_only_to_spending():
    """Feature 3.5 says the API accepts `why` anywhere, but the applied migration constrains
    it to `direction = 'out'` and the migrations win."""
    with pytest.raises(ValidationError):
        payload(direction="in", why="impulse")


def test_a_field_this_endpoint_does_not_accept_is_refused():
    """`user_id` is never an input anywhere in this API, and a silently dropped field is
    worse than a 422."""
    with pytest.raises(ValidationError):
        payload(user_id=str(uuid4()))
    with pytest.raises(ValidationError):
        payload(is_refund=True)


def test_transfer_is_not_reachable_in_phase_one():
    """It needs two rows, two accounts and a `transfer_role`."""
    with pytest.raises(ValidationError):
        payload(direction="transfer")


def test_a_naive_occurred_at_is_refused():
    with pytest.raises(ValidationError):
        payload(occurred_at="2026-08-11T13:45:00")


# ---------- category rules ----------


def test_a_missing_category_is_a_404_with_its_own_code():
    with pytest.raises(CategoryNotFoundError) as caught:
        check_category_is_usable(None, "out")

    assert caught.value.code == "category_not_found"
    assert caught.value.status_code == 404


def test_another_users_category_is_indistinguishable_from_a_missing_one():
    """The repository returns None for both, and a 403 would confirm the row exists."""
    with pytest.raises(CategoryNotFoundError):
        check_category_is_usable(None, "out")


def test_an_archived_category_is_a_409_not_a_404():
    """It exists and it is the caller's; the conflict is with its state."""
    with pytest.raises(CategoryArchivedError) as caught:
        check_category_is_usable(category_row(is_archived=True), "out")

    assert caught.value.code == "category_archived"
    assert caught.value.status_code == 409


def test_spending_against_an_income_category_is_a_kind_mismatch():
    with pytest.raises(KindMismatchError) as caught:
        check_category_is_usable(category_row(kind="income", default_bucket=None), "out")

    assert caught.value.code == "kind_mismatch"
    assert caught.value.status_code == 409


def test_income_against_an_expense_category_is_a_kind_mismatch():
    with pytest.raises(KindMismatchError):
        check_category_is_usable(category_row(kind="expense"), "in")


def test_a_matching_kind_passes():
    assert check_category_is_usable(category_row(), "out")["id"] == FOOD_ID


# ---------- the transaction owns its bucket ----------


def test_an_absent_bucket_is_filled_from_the_category_default():
    assert resolve_bucket(payload(), category_row(default_bucket="NEEDS")) == "NEEDS"


def test_an_explicit_bucket_overrides_the_category_default():
    """Without the override the data is fiction by month two."""
    assert resolve_bucket(payload(bucket="NEEDS"), category_row(default_bucket="WANTS")) == "NEEDS"


def test_income_has_no_bucket_at_all():
    assert resolve_bucket(payload(direction="in"), category_row(kind="income")) is None


def test_the_stored_row_never_carries_an_exclusion_or_a_currency():
    """In Phase 1 nothing sets `is_excluded`; `currency_code` is a database default and the
    application restating it would be two places to change."""
    row = build_txn_row(payload(), uuid4(), category_row(), PERIOD_ID, NOW, NOW, "Asia/Kolkata")

    assert row["is_excluded"] is False
    assert "currency_code" not in row
    assert "version" not in row


def test_a_merchant_is_stored_trimmed_and_normalised_for_grouping():
    row = build_txn_row(
        payload(merchant="  Third Wave Coffee  "),
        uuid4(),
        category_row(),
        PERIOD_ID,
        NOW,
        NOW,
        "Asia/Kolkata",
    )

    assert row["merchant"] == "Third Wave Coffee"
    assert row["merchant_normalized"] == "third wave coffee"


def test_no_merchant_normalises_to_nothing_rather_than_an_empty_string():
    row = build_txn_row(payload(), uuid4(), category_row(), PERIOD_ID, NOW, NOW, "Asia/Kolkata")

    assert row["merchant"] is None
    assert row["merchant_normalized"] is None


# ---------- local dates, and what a backfill does not earn ----------


def test_a_backfilled_spend_keeps_its_own_day_but_earns_todays_habit():
    """Period membership keys off `occurred_on_local`; the habit day keys off
    `created_on_local`. Backfilling last Tuesday does not retroactively earn last Tuesday."""
    occurred = datetime(2026, 8, 4, 13, 45, tzinfo=UTC)

    occurred_on_local, created_on_local = local_dates(occurred, NOW, "Asia/Kolkata")

    assert occurred_on_local == date(2026, 8, 4)
    assert created_on_local == date(2026, 8, 11)


def test_a_late_night_ist_spend_belongs_to_the_ist_day():
    """23:40 IST is 18:10 UTC the same day, but 01:40 IST is 20:10 UTC the day before."""
    occurred = datetime(2026, 8, 13, 20, 10, tzinfo=UTC)

    occurred_on_local, _ = local_dates(occurred, occurred, "Asia/Kolkata")

    assert occurred_on_local == date(2026, 8, 14)


def test_a_period_is_derivable_for_a_date_before_the_first_one():
    """`period_bounds` is a pure function of the date and the salary day, so a backfill into
    the past materialises its period the same way a future date does."""
    assert resolve_period_bounds(date(2026, 5, 3), 28) == (date(2026, 4, 28), date(2026, 5, 27))


def test_a_materialised_period_seeds_from_the_profile_baseline():
    row = build_period_row(
        uuid4(), STARTS_ON, ENDS_ON, {"monthly_income_minor": 2_625_000}
    )

    assert row["expected_income_minor"] == 2_625_000
    assert row["pct_needs"] + row["pct_wants"] + row["pct_future"] + row["pct_debt"] == 100
    # The repository mints it — this row is derived, not client-supplied.
    assert "id" not in row


def test_a_materialised_period_survives_a_profile_with_no_income_yet():
    row = build_period_row(uuid4(), STARTS_ON, ENDS_ON, {"monthly_income_minor": None})

    assert row["expected_income_minor"] == 0


# ---------- the period window ----------


def test_the_window_counts_days_the_way_the_spec_does():
    assert WINDOW.days_in_period == 31
    # 28 Jul to 11 Aug is 14 whole days gone; today is in progress and does not count.
    assert WINDOW.days_completed == 14
    assert WINDOW.days_remaining == 17


def test_days_remaining_is_never_zero_because_it_is_a_divisor():
    """A period whose last day offered a zero denominator would fail on the one day it
    matters."""
    last_day = PeriodWindow(id=PERIOD_ID, starts_on=STARTS_ON, ends_on=ENDS_ON, local_today=ENDS_ON)

    assert last_day.days_remaining == 1


def test_the_first_day_of_a_period_has_no_completed_days():
    first = PeriodWindow(id=PERIOD_ID, starts_on=STARTS_ON, ends_on=ENDS_ON, local_today=STARTS_ON)

    assert first.days_completed == 0
    assert first.days_remaining == 31


# ---------- committed versus flexible ----------


def test_a_missing_flexibility_defaults_to_three_and_is_flexible():
    assert is_committed(None, "NEEDS") is False


def test_flexibility_one_is_committed_money():
    """Rent and EMI seed at 1 precisely so safe-to-spend treats them as committed."""
    assert is_committed(1, "NEEDS") is True


def test_debt_and_excluded_buckets_are_committed_whatever_their_flexibility():
    assert is_committed(5, "DEBT") is True
    assert is_committed(5, "EXCLUDED") is True


def test_an_ordinary_wants_category_is_flexible():
    assert is_committed(4, "WANTS") is False


# ---------- safe to spend ----------


def test_safe_to_spend_divides_the_flexible_remainder_by_the_days_left():
    """One flexible category, nothing spent: the cap binds, because an untouched budget
    early in the period would otherwise show a figure that reads as permission."""
    rows = [flexible_row(limit_minor=31_000, spent_minor=0)]
    window = PeriodWindow(id=PERIOD_ID, starts_on=STARTS_ON, ends_on=ENDS_ON, local_today=STARTS_ON)

    # baseline_daily = 31000 // 31 = 1000, cap = 1500. uncapped = 31000 // 31 = 1000.
    assert safe_to_spend_minor(rows, window) == 1_000


def test_the_cap_binds_once_only_a_few_days_remain():
    """31,000 left with 2 days to go is 15,500 a day, which the 1.5x baseline caps at 1,500."""
    rows = [flexible_row(limit_minor=31_000, spent_minor=0)]
    window = PeriodWindow(
        id=PERIOD_ID, starts_on=STARTS_ON, ends_on=ENDS_ON, local_today=date(2026, 8, 26)
    )

    assert safe_to_spend_minor(rows, window) == 1_500


def test_committed_categories_are_excluded_from_safe_to_spend():
    """Rent is not day-to-day money, so its unspent limit must not inflate the figure."""
    rows = [
        flexible_row(limit_minor=31_000, spent_minor=0, flexibility=4),
        flexible_row(limit_minor=900_000, spent_minor=0, flexibility=1, default_bucket="NEEDS"),
    ]
    window = PeriodWindow(id=PERIOD_ID, starts_on=STARTS_ON, ends_on=ENDS_ON, local_today=STARTS_ON)

    assert safe_to_spend_minor(rows, window) == 1_000


def test_an_overspent_category_does_not_lend_its_overspend_to_the_others():
    """Without MAX(0, ...) per category the figure quietly grows after a bad day."""
    rows = [
        flexible_row(limit_minor=31_000, spent_minor=0),
        flexible_row(limit_minor=1_000, spent_minor=50_000),
    ]
    window = PeriodWindow(id=PERIOD_ID, starts_on=STARTS_ON, ends_on=ENDS_ON, local_today=STARTS_ON)

    # The overspend is clamped, so only the first category's 31,000 counts toward remaining.
    # baseline uses both limits: 32000 // 31 = 1032, cap = 1548. uncapped = 31000 // 31 = 1000.
    assert safe_to_spend_minor(rows, window) == 1_000


def test_no_flexible_categories_means_nothing_is_safe_to_spend():
    """Every limit committed, so there is no day-to-day money and no division by zero."""
    rows = [flexible_row(flexibility=1)]

    assert safe_to_spend_minor(rows, WINDOW) == 0


def test_no_limits_at_all_means_nothing_is_safe_to_spend():
    assert safe_to_spend_minor([], WINDOW) == 0


# ---------- budget impact ----------


def test_the_specs_worked_budget_impact_numbers():
    """Straight from the 3.2 response example: a 315,000 limit, 264,600 spent, on 11 August
    of a 31-day period."""
    impact = category_impact(limit_row(), 264_600, WINDOW)

    assert impact is not None
    assert impact.effective_limit_minor == 315_000
    assert impact.spent_minor == 264_600
    assert impact.remaining_minor == 50_400
    assert impact.spent_pct == 84.0
    assert impact.pace_pct == 45.2
    assert impact.pace_status == "over_pace"
    assert impact.breached is False


def test_a_category_with_no_limit_reports_no_impact():
    """An income row has none by definition, and neither does an unbudgeted expense
    category. Zeroes would have the client render a remaining figure from a limit that does
    not exist."""
    assert category_impact(None, 0, WINDOW) is None


def test_a_carried_in_amount_raises_the_effective_limit():
    impact = category_impact(limit_row(carried_in_minor=10_000), 0, WINDOW)

    assert impact is not None
    assert impact.effective_limit_minor == 325_000


def test_remaining_may_be_negative_and_is_not_clamped():
    """An over-budget category the UI shows as zero-remaining is a lie."""
    impact = category_impact(limit_row(), 400_000, WINDOW)

    assert impact is not None
    assert impact.remaining_minor == -85_000
    assert impact.breached is True
    assert impact.pace_status == "breached"


def test_a_breach_outranks_being_off_pace():
    """An over-budget category is not merely ahead of schedule."""
    impact = category_impact(limit_row(), 400_000, WINDOW)

    assert impact is not None
    assert impact.pace_status == "breached"


def test_there_is_no_percentage_of_a_zero_limit():
    impact = category_impact(limit_row(limit_minor=0), 0, WINDOW)

    assert impact is not None
    assert impact.spent_pct is None
    assert impact.pace_status == "on_pace"


def test_spending_well_behind_schedule_is_under_pace():
    impact = category_impact(limit_row(), 10_000, WINDOW)

    assert impact is not None
    assert impact.pace_status == "under_pace"


def test_spending_in_step_with_the_period_is_on_pace():
    # 45.2% of 315,000 is 142,380, which lands inside the five-point tolerance.
    impact = category_impact(limit_row(), 142_380, WINDOW)

    assert impact is not None
    assert impact.pace_status == "on_pace"


def test_a_percentage_is_rounded_half_up_not_truncated():
    """14 of 31 days is 45.161%, which the spec reports as 45.2. Truncating gives 45.1."""
    impact = category_impact(limit_row(), 0, WINDOW)

    assert impact is not None
    assert impact.pace_pct == 45.2


# ---------- the habit tick ----------


def test_a_first_ever_transaction_starts_the_streak_at_one():
    habit = habit_out(streak=None, local_today=TODAY, newly_logged=True)

    assert habit.current == 1
    assert habit.longest == 1
    assert habit.logged_today is True
    assert habit.incremented is True


def test_the_streak_advances_for_the_days_first_transaction():
    streak = {"current": 12, "longest": 18, "last_active_date": date(2026, 8, 10)}

    habit = habit_out(streak=streak, local_today=TODAY, newly_logged=True)

    assert habit.current == 13
    assert habit.longest == 18
    assert habit.incremented is True


def test_a_second_transaction_the_same_day_does_not_advance_it_again():
    """`incremented` is what the client animates, and a day is earned once."""
    streak = {"current": 13, "longest": 18, "last_active_date": TODAY}

    habit = habit_out(streak=streak, local_today=TODAY, newly_logged=False)

    assert habit.current == 13
    assert habit.incremented is False
    assert habit.logged_today is True


def test_a_new_longest_streak_is_reported_immediately():
    streak = {"current": 18, "longest": 18, "last_active_date": date(2026, 8, 10)}

    habit = habit_out(streak=streak, local_today=TODAY, newly_logged=True)

    assert habit.current == 19
    assert habit.longest == 19


def test_a_stored_row_already_counting_today_is_not_double_counted():
    """The nightly rollover job owns the stored counters. If it has already credited today,
    the response must not add a second day on top."""
    streak = {"current": 13, "longest": 18, "last_active_date": TODAY}

    habit = habit_out(streak=streak, local_today=TODAY, newly_logged=True)

    assert habit.current == 13


# ---------- row style ----------


def test_every_phase_one_row_is_an_ordinary_one():
    """Transfers, adjustments and recurring rows are all Phase 2, so the only reachable
    value is `normal` — derived rather than hardcoded so they need no contract change."""
    row = build_txn_row(payload(), uuid4(), category_row(), PERIOD_ID, NOW, NOW, "Asia/Kolkata")

    assert row_style(row) == "normal"


def test_a_recurring_row_would_be_styled_as_one():
    assert row_style({"source": "recurring"}) == "recurring"


def test_a_transfer_leg_would_be_styled_as_one():
    assert row_style({"source": "manual", "transfer_peer_txn_id": uuid4()}) == "transfer"


# NOTE — still owed, and blocked rather than forgotten:
# router-level tests against a migrated Postgres. `.claude/rules/tests.md` mandates three per
# endpoint (happy path, a second user seeing nothing, idempotency), and this endpoint also
# needs: the habit day incrementing exactly once, `zero_spend_confirmed` being reset by a
# later real transaction, a period materialised lazily for a date outside every existing one,
# and a replay returning 200 with `Idempotent-Replay` and writing nothing. All of them need
# TEST_DATABASE_URL, which is unset — `tests/conftest.py::database_url` skips without it.
