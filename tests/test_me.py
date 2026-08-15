"""`GET /me`'s rules, with no database and no HTTP.

`app/services/me.py` imports neither `fastapi` nor `sqlalchemy`, so the shape of the
response and every decision behind it is testable from plain dicts. The router's own three
cases live in `test_onboarding.py`, which needs Postgres.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services.errors import DomainError
from app.services.me import (
    FALLBACK_CURRENCY_CODE,
    currency_code_for,
    me_out,
    profile_out,
    require_minor_unit,
)

NOW = datetime(2026, 8, 11, 9, 2, tzinfo=UTC)


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
