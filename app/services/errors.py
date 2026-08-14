"""Domain exceptions. Services raise these; only routers turn them into HTTP.

Each carries a stable snake_case `code` — that is what the client branches on. The
`message` is for logs, never for UI copy. `status_code` is the one HTTP detail allowed
here, so the mapping lives next to the meaning instead of being repeated per router.
"""


class DomainError(Exception):
    code = "internal_error"
    status_code = 500

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class UnauthenticatedError(DomainError):
    """Missing, malformed or expired token. Raised by `app/auth.py`, not by a service.

    It lives here so the API has one error vocabulary rather than two.
    """

    code = "unauthenticated"
    status_code = 401


class AuthUnavailableError(DomainError):
    """The JWKS endpoint could not be reached, so no token can be verified.

    Deliberately not a 401. The token may well be perfectly valid — Supabase is simply
    unreachable — and answering "unauthenticated" tells four users they have been logged
    out, sending them to a login screen that cannot work either. A 503 says retry.
    """

    code = "auth_unavailable"
    status_code = 503


class NotFoundError(DomainError):
    """Row does not exist, or belongs to another user.

    Both cases are 404 on purpose. A 403 for someone else's row confirms the row
    exists, which leaks one brother's data to another.
    """

    code = "not_found"
    status_code = 404


class ForbiddenError(DomainError):
    """Authenticated, the row is theirs, and the action is still not allowed."""

    code = "forbidden"
    status_code = 403


class OnboardingRequiredError(ForbiddenError):
    """Valid token, no `profile` row yet."""

    code = "onboarding_required"

    def __init__(self, message: str = "Profile has not been created yet.") -> None:
        super().__init__(message)


class OnboardingAlreadyCompleteError(DomainError):
    """A second onboarding bootstrap for a profile that already finished one.

    Replaying the call with the *same* client-generated ids is not an error — it returns
    the existing state, which is what makes a queued offline POST safe to retry. This is
    the other case: a genuinely different bootstrap arriving against a completed profile,
    which would silently duplicate every category and orphan the first budget period.
    """

    code = "onboarding_already_complete"
    status_code = 409

    def __init__(self, message: str = "Onboarding has already been completed.") -> None:
        super().__init__(message)


class RuleViolationError(DomainError):
    """A business rule that needed another row, the clock, or a decision.

    Shape, type and range are Pydantic's job at the edge and never reach here.
    """

    code = "rule_violation"
    status_code = 422
