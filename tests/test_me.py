"""`GET /me`'s rules, with no database and no HTTP.

`app/services/me.py` imports neither `fastapi` nor `sqlalchemy`, so the shape of the
response and every decision behind it is testable from plain dicts. The router's own three
cases live in `test_onboarding.py`, which needs Postgres.
"""

from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.me import MeUpdate
from app.services.errors import (
    CurrencyNotSupportedError,
    DomainError,
    InvalidMonthStartDayError,
    VersionConflictError,
)
from app.services.me import (
    FALLBACK_CURRENCY_CODE,
    currency_code_for,
    local_today_for,
    me_out,
    month_start_day_change_out,
    plan_month_start_day_change,
    profile_out,
    profile_patch,
    require_minor_unit,
    require_supported_currency,
    require_version_match,
)

NOW = datetime(2026, 8, 11, 9, 2, tzinfo=UTC)

# The period from the spec's worked example: month_start_day 28, so 28 Jul to 27 Aug.
PERIOD = {
    "id": UUID("018f9c31-0000-7000-8000-000000000001"),
    "starts_on": date(2026, 7, 28),
    "ends_on": date(2026, 8, 27),
}


def profile_row(**overrides):
    """Exactly the columns `repositories/profile.get_profile` selects."""
    row = {
        "display_name": "Udit",
        "timezone": "Asia/Kolkata",
        "preferred_currency_code": "INR",
        "monthly_income_minor": 2_625_000,
        "month_start_day": 28,
        "notify_time_local": None,
        "implementation_intention": None,
        "theme": "system",
        "role": "user",
        "onboarding_completed_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
        "version": 1,
    }
    return {**row, **overrides}


def test_no_profile_reports_onboarding_required_and_the_fallback_currency():
    result = me_out(uuid4(), "a@b.com", ["email"], None, 2)

    assert result.onboarding_required is True
    assert result.profile is None
    assert result.currency_code == FALLBACK_CURRENCY_CODE


def test_a_profile_reports_onboarding_done_and_its_own_currency():
    result = me_out(uuid4(), "a@b.com", ["email", "google"], profile_row(), 2)

    assert result.onboarding_required is False
    assert result.profile is not None
    assert result.profile.display_name == "Udit"
    assert result.auth_providers == ["email", "google"]


def test_the_blank_padding_of_a_char3_currency_never_reaches_the_wire():
    """`preferred_currency_code` is CHAR(3), so Postgres pads it.

    A client comparing "INR " to "INR" fails a check nobody thinks to look at.
    """
    padded = profile_row(preferred_currency_code="INR ")

    assert currency_code_for(padded) == "INR"
    assert profile_out(padded).preferred_currency_code == "INR"
    assert me_out(uuid4(), None, [], padded, 2).currency_code == "INR"


def test_onboarding_required_is_exactly_whether_the_profile_is_absent():
    assert me_out(uuid4(), None, [], None, 2).onboarding_required is True
    assert me_out(uuid4(), None, [], profile_row(), 2).onboarding_required is False


def test_a_null_email_is_carried_rather_than_invented():
    """`auth.users` is Supabase's table; a row we cannot read is reported as absent."""
    result = me_out(uuid4(), None, [], None, 2)

    assert result.email is None
    assert result.auth_providers == []


def test_role_is_readable_but_the_schema_never_accepts_it():
    """Settings shows the role. No request schema has the field — that is the only defence
    that applies, because the guarding trigger is gated on the client roles and this backend
    connects as `postgres`."""
    assert profile_out(profile_row(role="admin")).role == "admin"


def test_a_missing_currency_reference_row_is_a_server_error_not_a_bad_request():
    """Migration 0009 seeds INR, so `None` here means the reference data is gone."""
    with pytest.raises(DomainError) as caught:
        require_minor_unit("INR", None)

    assert caught.value.status_code == 500


def test_a_present_minor_unit_passes_through():
    assert require_minor_unit("INR", 2) == 2
    # Zero is a real minor unit (JPY), so it must not be treated as missing.
    assert require_minor_unit("JPY", 0) == 0


# ---------- PATCH /me ----------


def test_an_absent_key_is_untouched_and_an_explicit_null_clears():
    """The distinction the whole partial-update contract rests on (spec §2.1).

    `exclude_none` instead of `exclude_unset` would collapse these two into one and make
    `notify_time_local` impossible to turn off once set.
    """
    absent = MeUpdate().model_dump(exclude_unset=True)
    assert "notify_time_local" not in absent

    cleared = MeUpdate(notify_time_local=None).model_dump(exclude_unset=True)
    assert cleared == {"notify_time_local": None}


def test_a_patch_carrying_only_the_day_two_fields_touches_nothing_else():
    """Feature 1.6: notification time and the written intention arrive on the second
    session, through this endpoint, and must not disturb the budget the user just set up."""
    patch = MeUpdate(
        notify_time_local=time(21, 30),
        implementation_intention="After I lock my phone for the night, I log my spending.",
    ).model_dump(exclude_unset=True)

    assert set(patch) == {"notify_time_local", "implementation_intention"}


def test_an_explicit_null_on_a_not_null_column_is_refused():
    """`theme` and `timezone` have no unset state in the database, so clearing them is not
    a thing a client may ask for."""
    with pytest.raises(ValidationError):
        MeUpdate(theme=None)
    with pytest.raises(ValidationError):
        MeUpdate(timezone=None)


def test_an_unknown_key_is_refused_rather_than_ignored():
    """A PATCH that reports success and changed nothing is the worst available outcome."""
    with pytest.raises(ValidationError):
        MeUpdate.model_validate({"montly_income_minor": 100})


def test_role_cannot_be_patched():
    with pytest.raises(ValidationError):
        MeUpdate.model_validate({"role": "admin"})


def test_month_start_day_outside_the_range_keeps_its_own_error_code():
    with pytest.raises(InvalidMonthStartDayError) as caught:
        MeUpdate(month_start_day=31)

    assert caught.value.code == "invalid_month_start_day"


def test_if_match_is_optional_and_absence_means_last_write_wins():
    require_version_match(None, profile_row(version=7))


def test_a_stale_if_match_is_a_version_conflict():
    with pytest.raises(VersionConflictError) as caught:
        require_version_match(3, profile_row(version=7))

    assert caught.value.code == "version_conflict"
    assert caught.value.status_code == 409


def test_a_matching_if_match_passes():
    require_version_match(7, profile_row(version=7))


def test_an_unsupported_currency_is_named_rather_than_generically_invalid():
    with pytest.raises(CurrencyNotSupportedError) as caught:
        require_supported_currency("USD", {"INR"})

    assert caught.value.code == "currency_not_supported"


def test_a_currency_is_normalised_the_same_way_onboarding_normalises_it():
    """A code accepted at signup must not be rejected on Settings."""
    assert require_supported_currency("inr", {"INR"}) == "INR"


def test_the_worked_example_from_the_spec_extends_august_to_the_31st():
    """"moving 28 to 1, with a period ending 27 August, extends it to 31 August and the
    next period begins 1 September"."""
    change = plan_month_start_day_change(
        profile_row(month_start_day=28),
        {"month_start_day": 1},
        PERIOD,
        date(2026, 8, 11),
    )

    assert change is not None
    assert change.previous_value == 28
    assert change.new_value == 1
    assert change.current_period_extended_to == date(2026, 8, 31)
    assert change.effective_from_period_starts_on == date(2026, 9, 1)
    assert change.period_to_extend == PERIOD["id"]


def test_a_salary_date_that_did_not_move_is_not_a_change():
    assert (
        plan_month_start_day_change(
            profile_row(month_start_day=28),
            {"month_start_day": 28},
            PERIOD,
            date(2026, 8, 11),
        )
        is None
    )


def test_a_patch_that_never_mentions_the_salary_date_is_not_a_change():
    assert (
        plan_month_start_day_change(
            profile_row(month_start_day=28),
            {"theme": "dark"},
            PERIOD,
            date(2026, 8, 11),
        )
        is None
    )


def test_a_later_salary_date_extends_the_period_and_never_shrinks_it():
    """Anchoring on `ends_on` rather than on today is what guarantees this.

    Moving 1 to 20 on the 11th would, if anchored on today, end the current period on the
    19th — re-dating a period the user has already been spending against. The spec only
    ever describes moving `ends_on` forward.
    """
    period = {"id": uuid4(), "starts_on": date(2026, 8, 1), "ends_on": date(2026, 8, 31)}

    change = plan_month_start_day_change(
        profile_row(month_start_day=1), {"month_start_day": 20}, period, date(2026, 8, 11)
    )

    assert change is not None
    assert change.current_period_extended_to == date(2026, 9, 19)
    assert change.current_period_extended_to > period["ends_on"]
    assert change.effective_from_period_starts_on == date(2026, 9, 20)


def test_no_day_is_left_outside_every_period():
    """The reason the extension exists at all: the old end and the new start must abut."""
    change = plan_month_start_day_change(
        profile_row(month_start_day=28), {"month_start_day": 1}, PERIOD, date(2026, 8, 11)
    )

    assert change is not None
    assert change.effective_from_period_starts_on == change.current_period_extended_to + (
        date(2026, 1, 2) - date(2026, 1, 1)
    )


def test_with_no_period_covering_today_there_is_nothing_to_extend():
    """Periods are materialised lazily, so having none is an ordinary state."""
    change = plan_month_start_day_change(
        profile_row(month_start_day=28), {"month_start_day": 1}, None, date(2026, 8, 11)
    )

    assert change is not None
    assert change.current_period_extended_to is None
    assert change.period_to_extend is None
    assert change.effective_from_period_starts_on == date(2026, 9, 1)


def test_a_salary_date_change_records_when_it_takes_effect():
    change = plan_month_start_day_change(
        profile_row(month_start_day=28), {"month_start_day": 1}, PERIOD, date(2026, 8, 11)
    )
    patch = profile_patch(MeUpdate(month_start_day=1), change)

    assert patch["month_start_day"] == 1
    assert patch["month_start_day_effective_from"] == date(2026, 9, 1)


def test_a_patch_that_does_not_move_the_salary_date_writes_no_effective_date():
    patch = profile_patch(MeUpdate(theme="dark"), None)

    assert patch == {"theme": "dark"}


def test_the_patch_never_carries_version_or_updated_at():
    """`touch_row()` does `NEW.version := OLD.version + 1`, so writing either here would
    fight the trigger."""
    patch = profile_patch(MeUpdate(display_name="Udit", theme="dark"), None)

    assert "version" not in patch
    assert "updated_at" not in patch


def test_the_change_block_is_present_and_quiet_when_nothing_moved():
    """The spec shows only the `changed: true` shape. A consistent object lets the client
    read `.changed` without testing the block for null first."""
    block = month_start_day_change_out(None)

    assert block.changed is False
    assert block.previous_value is None
    assert block.current_period_extended_to is None
    assert block.effective_from_period_starts_on is None


def test_today_is_resolved_in_the_profiles_own_timezone():
    """01:40 IST on the 14th is still the 13th in UTC, and which day it is decides which
    period gets extended."""
    late = datetime(2026, 8, 13, 20, 10, tzinfo=UTC)

    assert local_today_for(profile_row(timezone="Asia/Kolkata"), late) == date(2026, 8, 14)
    assert local_today_for(profile_row(timezone="UTC"), late) == date(2026, 8, 13)
