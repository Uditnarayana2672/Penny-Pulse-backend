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
    """Missing, malformed or forged token. Raised by `app/auth.py`, not by a service.

    It lives here so the API has one error vocabulary rather than two.
    """

    code = "unauthenticated"
    status_code = 401


class TokenExpiredError(UnauthenticatedError):
    """Valid signature, expired token.

    Split from `unauthenticated` because the two need opposite responses from the client:
    this one is fixed by refreshing the Supabase session, and the client can do that
    without showing anybody a login screen. Collapsing them sends a user with a merely
    stale token back to sign-in, which is the same outcome as a forged token for a
    condition that happens to every session on a timer.
    """

    code = "token_expired"


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


class VersionConflictError(DomainError):
    """An `If-Match` version that no longer matches the stored row.

    The header is optional (spec §2.1): omitting it is last-write-wins, which is what a
    Phase 1 client does. Sending it opts into detection, and this is the answer.

    The spec asks for "the current resource in `details`", which `.claude/rules/api.md`
    does not sanction — its envelope is exactly `code`, `message`, `field`, "no
    exceptions". The rules file wins, so the current state is not echoed here; a client
    that sees this re-reads `GET /me`, which it needs to do anyway to show the user what
    the other device wrote.
    """

    code = "version_conflict"
    status_code = 409

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Profile has version {actual}, but If-Match asked for {expected}.",
            field="If-Match",
        )


class RuleViolationError(DomainError):
    """A business rule that needed another row, the clock, or a decision.

    Shape, type and range are Pydantic's job at the edge and never reach here.
    """

    code = "rule_violation"
    status_code = 422


class InvalidMonthStartDayError(DomainError):
    """`month_start_day` outside 1..28.

    Named rather than folded into `validation_failed` because the client shows a specific
    correction for it, and because the bound is not arbitrary: 29, 30 and 31 do not occur
    in February, so a period starting on one of them would have no start in some months.

    Raised at the edge — it is a range check, which is Pydantic's layer — but with its own
    code, because "must be 1..28 so every month has one" is a rule a user can act on and
    "validation failed" is not.
    """

    code = "invalid_month_start_day"
    status_code = 422

    def __init__(self, value: int, *, field: str = "month_start_day") -> None:
        super().__init__(
            f"month_start_day must be between 1 and 28, got {value}.", field=field
        )


class BucketPercentagesInvalidError(DomainError):
    """The four bucket percentages do not sum to 100.

    Cross-field shape, so it is checked at the edge; `period_pct_sums_to_100` stays the
    database backstop. Distinct from `validation_failed` because the client can name the
    four fields involved and show the running total.
    """

    code = "bucket_percentages_invalid"
    status_code = 422

    def __init__(self, total: int) -> None:
        super().__init__(
            f"Bucket percentages must sum to 100, got {total}.", field="budget"
        )


class CurrencyNotSupportedError(DomainError):
    """A currency that is not enabled in the `currency` reference table.

    Needs another row to decide, so it is a service rule rather than a `Literal["INR"]` at
    the edge — enabling a second currency stays a data change.
    """

    code = "currency_not_supported"
    status_code = 422

    def __init__(self, code_requested: str) -> None:
        super().__init__(
            f"{code_requested} is not a supported currency.",
            field="preferred_currency_code",
        )


class IncomeCategoryNotBudgetableError(DomainError):
    """A budget limit aimed at an income category.

    409 rather than 422: the request is well-formed and the conflict is with the state of
    the category it points at. Mirrors the `budget_limit_budgetable` trigger, which spans
    two tables and so cannot be a CHECK.
    """

    code = "income_category_not_budgetable"
    status_code = 409


class ExcludedCategoryNotBudgetableError(DomainError):
    """A budget limit aimed at an EXCLUDED-bucket category.

    EXCLUDED is a categorisation, never an allocation. Same trigger, same reason for 409.
    """

    code = "excluded_category_not_budgetable"
    status_code = 409
