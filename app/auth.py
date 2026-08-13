import logging
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models.profile import Profile
from app.repositories import profile as profile_repo
from app.services.errors import OnboardingRequiredError, UnauthenticatedError

logger = logging.getLogger(__name__)

# auto_error=False so a missing header produces our envelope, not FastAPI's `detail`.
bearer_scheme = HTTPBearer(auto_error=False)


def current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UUID:
    """The `sub` claim of a locally verified Supabase access token.

    This is the only source of identity in the application. A `user_id` in a body,
    query param or path segment is one brother reading another's spending.

    The signature is checked here against the project JWT secret — no network call to
    Supabase per request.
    """
    if credentials is None:
        raise UnauthenticatedError("Authorization header missing.")

    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=settings.supabase_jwt_audience,
        )
    except jwt.ExpiredSignatureError:
        raise UnauthenticatedError("Token has expired.") from None
    except jwt.InvalidTokenError:
        # The token itself is never logged.
        raise UnauthenticatedError("Token is invalid.") from None

    subject = claims.get("sub")
    if not subject:
        raise UnauthenticatedError("Token carries no subject.")

    try:
        return UUID(subject)
    except ValueError:
        raise UnauthenticatedError("Token subject is not a UUID.") from None


UserId = Annotated[UUID, Depends(current_user_id)]


def current_profile(
    user_id: UserId,
    db: Annotated[Session, Depends(get_db)],
) -> Profile:
    """A valid token with no `profile` row means onboarding has not run yet."""
    profile = profile_repo.get_profile(db, user_id)
    if profile is None:
        raise OnboardingRequiredError()
    return profile


CurrentProfile = Annotated[Profile, Depends(current_profile)]
DbSession = Annotated[Session, Depends(get_db)]
