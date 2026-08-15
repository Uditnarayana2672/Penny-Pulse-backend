from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header

from app.auth import DbSession, UserId
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.schemas.me import MeOut, MeUpdate, MeUpdateOut
from app.services import me as me_service
from app.services.errors import OnboardingRequiredError

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


@router.patch("/me", response_model=MeUpdateOut)
def update_me(
    payload: MeUpdate,
    user_id: UserId,
    db: DbSession,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> MeUpdateOut:
    """Partial profile update, and the one write that can move budget periods.

    The consequential field is `month_start_day`. Changing it never re-dates a closed
    period — that would rewrite every past budget and savings figure the user has already
    seen — but it does extend the current period forward to meet the new start date, so no
    day belongs to no period. Both effects are stated in `month_start_day_change` rather
    than left for the client to infer.

    Unlike `GET /me` this endpoint requires a profile: there is nothing to patch before
    onboarding, and the two fields 1.6 collects here are day-two prompts by definition.

    The profile update and the period extension are two statements in one transaction,
    committed once by the session dependency. Splitting them would leave a profile whose
    `month_start_day` disagrees with the periods it is supposed to govern.
    """
    profile = profile_repo.get_profile(db, user_id)
    if profile is None:
        raise OnboardingRequiredError()

    me_service.require_version_match(if_match, profile)

    if "preferred_currency_code" in payload.model_fields_set:
        payload.preferred_currency_code = me_service.require_supported_currency(
            payload.preferred_currency_code, onboarding_repo.enabled_currency_codes(db)
        )

    local_today = me_service.local_today_for(profile, datetime.now(UTC))
    change = me_service.plan_month_start_day_change(
        profile,
        payload.model_dump(exclude_unset=True),
        profile_repo.get_period_containing(db, user_id, local_today),
        local_today,
    )

    patch = me_service.profile_patch(payload, change)
    if patch:
        profile_repo.update_profile(db, user_id, patch)

    if change is not None and change.period_to_extend is not None:
        if change.current_period_extended_to is None:
            raise AssertionError("a period to extend always carries the date to extend it to")
        profile_repo.extend_period_end(
            db, user_id, change.period_to_extend, change.current_period_extended_to
        )

    # Read back rather than echo the request: `updated_at` and `version` are set by the
    # `profile_touch` trigger, so the client is told what was stored.
    stored = profile_repo.get_profile(db, user_id)
    if stored is None:
        raise AssertionError("the profile just updated is not readable")

    currency_code = me_service.currency_code_for(stored)
    return me_service.update_out(
        profile=stored,
        minor_unit=me_service.require_minor_unit(
            currency_code, onboarding_repo.currency_minor_unit(db, currency_code)
        ),
        change=change,
    )
