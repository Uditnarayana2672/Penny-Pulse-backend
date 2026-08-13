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


class RuleViolationError(DomainError):
    """A business rule that needed another row, the clock, or a decision.

    Shape, type and range are Pydantic's job at the edge and never reach here.
    """

    code = "rule_violation"
    status_code = 422
