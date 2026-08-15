from fastapi import APIRouter

from app.auth import DbSession, UserId
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.schemas.me import MeOut
from app.services import me as me_service

router = APIRouter(tags=["me"])


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
    currency_code = me_service.currency_code_for(profile)

    return me_service.me_out(
        user_id=user_id,
        email=email,
        auth_providers=providers,
        profile=profile,
        minor_unit=me_service.require_minor_unit(
            currency_code, onboarding_repo.currency_minor_unit(db, currency_code)
        ),
    )
