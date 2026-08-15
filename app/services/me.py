"""Identity and profile rules, plus the wire shapes built from them.

Imports neither `fastapi` nor `sqlalchemy`, so everything here is testable with no database
and no HTTP. Rows arrive as plain dicts from the repository; this module decides what they
mean and returns the response schema.

`services/onboarding.py` imports from this module, never the other way round. That single
direction is what keeps `profile_out` in one place: the onboarding response carries a
profile too, and before this module existed the onboarding *router* imported it from the
`/me` router.
"""

from datetime import date, datetime
from uuid import UUID

from app.schemas.me import MeOut, ProfileOut
from app.services.errors import DomainError

# Third occurrence of this alias; the other two are in `services/onboarding.py` and
# `repositories/onboarding.py`, each explaining why it is restated rather than shared. By
# the three-before-extracting rule it has now earned a module of its own — but that is a
# new file outside the five, which `CLAUDE.md` says to ask about. Flagged, not done.
RowValue = str | int | bool | UUID | date | datetime | None
RowDict = dict[str, RowValue]

# What to report before a profile exists to state a preference. Phase 1 seeds only INR, and
# `enabled_currency_codes` is what actually gates writes.
FALLBACK_CURRENCY_CODE = "INR"


def currency_code_for(profile: RowDict | None) -> str:
    """The currency this response should report.

    `preferred_currency_code` is `CHAR(3)` and therefore blank-padded by Postgres. The pad
    must not reach the wire: a client comparing `"INR "` to `"INR"` fails a check nobody
    thinks to look at.
    """
    if profile is None:
        return FALLBACK_CURRENCY_CODE
    return str(profile["preferred_currency_code"]).strip()


def require_minor_unit(code: str, minor_unit: int | None) -> int:
    """Assert the currency reference row was actually found.

    Migration 0009 seeds INR and every write path checks the code against
    `enabled_currency_codes` first, so a missing row here means the reference data is gone —
    a broken database rather than a bad request, hence a 500 and not a 422.
    """
    if minor_unit is None:
        raise DomainError(f"Currency {code} is missing from the reference table.")
    return minor_unit


def profile_out(profile: RowDict) -> ProfileOut:
    """One `profile` row as the wire shape.

    The repository selects exactly the columns this schema declares; Pydantic ignores any
    extra, so adding a column to the select cannot break the response.
    """
    return ProfileOut(**{**profile, "preferred_currency_code": currency_code_for(profile)})


def me_out(
    user_id: UUID,
    email: str | None,
    auth_providers: list[str],
    profile: RowDict | None,
    minor_unit: int,
) -> MeOut:
    """Identity plus onboarding state.

    `profile is None` is the whole answer to "has onboarding run". There is nothing finer to
    report: `POST /onboarding/complete` is the only path that creates a profile and it is one
    transaction, so a half-finished setup is not a state the database can hold.
    """
    return MeOut(
        user_id=user_id,
        email=email,
        auth_providers=auth_providers,
        onboarding_required=profile is None,
        currency_code=currency_code_for(profile),
        minor_unit=minor_unit,
        profile=None if profile is None else profile_out(profile),
    )
