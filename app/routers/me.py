from fastapi import APIRouter

from app.auth import DbSession, UserId
from app.models.profile import Profile
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.schemas.me import MeOut, ProfileOut
from app.services import onboarding as onboarding_service

router = APIRouter(tags=["me"])

# The currency to report before a profile exists to state a preference. Phase 1 seeds only
# INR, and `enabled_currency_codes` is what actually gates writes.
FALLBACK_CURRENCY_CODE = "INR"


@router.get("/me", response_model=MeOut)
def read_me(user_id: UserId, db: DbSession) -> MeOut:
    """Identity and onboarding state in one call.

    Deliberately depends on `UserId` rather than `CurrentProfile`. `CurrentProfile` raises
    403 `onboarding_required` when there is no profile row, which is correct for every other
    endpoint and exactly wrong for this one — a client cannot ask "am I onboarded?" through
    an endpoint that refuses to answer until you are.

    `profile: null` is the complete answer to that question. `POST /onboarding/complete` is
    the only path that creates a profile and it is one atomic transaction, so there is no
    half-finished setup for this endpoint to describe.
    """
    profile = profile_repo.get_profile(db, user_id)
    email, providers = onboarding_repo.get_auth_identity(db, user_id)

    code = (
        FALLBACK_CURRENCY_CODE
        if profile is None
        else profile.preferred_currency_code.strip()
    )
    minor_unit = onboarding_service.require_minor_unit(
        code, onboarding_repo.currency_minor_unit(db, code)
    )

    return MeOut(
        user_id=user_id,
        email=email,
        auth_providers=providers,
        onboarding_required=profile is None,
        currency_code=code,
        minor_unit=minor_unit,
        profile=None if profile is None else profile_out(profile),
    )


def profile_out(profile: Profile) -> ProfileOut:
    """ORM row to wire shape. Imported by the onboarding router, which returns one too."""
    return ProfileOut(
        display_name=profile.display_name,
        timezone=profile.timezone,
        preferred_currency_code=profile.preferred_currency_code.strip(),
        monthly_income_minor=profile.monthly_income_minor,
        month_start_day=profile.month_start_day,
        notify_time_local=profile.notify_time_local,
        implementation_intention=profile.implementation_intention,
        theme=profile.theme,
        role=profile.role,
        onboarding_completed_at=profile.onboarding_completed_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        version=profile.version,
    )
