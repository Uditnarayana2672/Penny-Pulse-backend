import logging
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.repositories import profile as profile_repo
from app.repositories.profile import RowDict
from app.services.errors import (
    AuthUnavailableError,
    OnboardingRequiredError,
    TokenExpiredError,
    UnauthenticatedError,
)

logger = logging.getLogger(__name__)

# auto_error=False so a missing header produces our envelope, not FastAPI's `detail`.
bearer_scheme = HTTPBearer(auto_error=False)

# Supabase signs access tokens with an asymmetric key (ES256, P-256) and publishes the
# public half at the JWKS endpoint. Exactly one algorithm is accepted: the endpoint
# offers one key, and a wider list is how algorithm-confusion attacks get in.
JWT_ALGORITHMS = ["ES256"]


@lru_cache
def get_jwk_client() -> PyJWKClient:
    """One client per process, holding the cached JWK set.

    Verification stays local — this is not a call to Supabase per request. The set is
    refetched when it ages out, and `get_signing_key_from_jwt` refetches immediately on
    an unrecognised `kid`, so rotating the key in the dashboard needs no redeploy and
    signs nobody out.

    `timeout` overrides PyJWT's 30-second default because this runs inside the request
    path: an unreachable endpoint should fail fast into a 503, not hang the request.
    """
    settings = get_settings()
    return PyJWKClient(
        settings.supabase_jwks_url,
        cache_jwk_set=True,
        lifespan=600,
        timeout=5,
    )


def current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UUID:
    """The `sub` claim of a locally verified Supabase access token.

    This is the only source of identity in the application. A `user_id` in a body,
    query param or path segment is one brother reading another's spending.

    The signature is checked against the project's published public key — no shared
    secret, and no network call to Supabase on the request path.
    """
    if credentials is None:
        raise UnauthenticatedError("Authorization header missing.")

    try:
        signing_key = get_jwk_client().get_signing_key_from_jwt(credentials.credentials)
    except PyJWKClientConnectionError:
        # Not the caller's fault, so not a 401. Logged without the token.
        logger.warning("jwks endpoint unreachable url=%s", settings.supabase_jwks_url)
        raise AuthUnavailableError("Could not reach the token signing keys.") from None
    except PyJWKClientError:
        # PyJWKClientError is NOT an InvalidTokenError, so it has to be caught here or
        # an unknown `kid` becomes a 500 instead of a 401.
        raise UnauthenticatedError("Token was signed by an unknown key.") from None
    except jwt.InvalidTokenError:
        raise UnauthenticatedError("Token is malformed.") from None

    try:
        claims = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=JWT_ALGORITHMS,
            audience=settings.supabase_jwt_audience,
            issuer=settings.supabase_issuer,
        )
    except jwt.ExpiredSignatureError:
        # Its own code, not `unauthenticated`: the client refreshes the Supabase session
        # and retries, rather than sending a signed-in user back to the login screen.
        raise TokenExpiredError("Token has expired.") from None
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
) -> RowDict:
    """A valid token with no `profile` row means onboarding has not run yet."""
    profile = profile_repo.get_profile(db, user_id)
    if profile is None:
        raise OnboardingRequiredError()
    return profile


CurrentProfile = Annotated[RowDict, Depends(current_profile)]

# `sqlalchemy.orm.Session` is the one ORM name this module imports, and it is here because
# this alias has nowhere else to live: `api.md` bans `sqlalchemy` from the edge and
# `repositories.md` bans `fastapi` from `app/db.py`, so an `Annotated[Session, Depends(...)]`
# is illegal in both homes the rules offer. Left here, where it already was, rather than
# adding a module outside the five to hold one line.
DbSession = Annotated[Session, Depends(get_db)]
