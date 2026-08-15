"""`GET /categories` — the one place in the system that knows how to return a category.

The entry grid on the add screen is served by this with `sort=entry_order`, deliberately
rather than by a bespoke endpoint, so a category has exactly one shape and one ordering rule.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.auth import DbSession, UserId
from app.repositories import category as category_repo
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.schemas.category import CategoryListOut, CategorySort
from app.schemas.onboarding import Kind
from app.services import category as category_service
from app.services import me as me_service
from app.services.errors import OnboardingRequiredError

router = APIRouter(tags=["categories"])

# The universal collection defaults from the API conventions.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@router.get("/categories", response_model=CategoryListOut)
def list_categories(
    user_id: UserId,
    db: DbSession,
    kind: Kind,
    sort: CategorySort = "name",
    with_stats: bool = False,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> CategoryListOut:
    """Categories of one kind, ordered for either a settings screen or the entry grid.

    `kind` is required. The spec says the filter "drives the tabs" and every documented
    caller sends it, but never states what omitting it means — and it cannot be answered
    sensibly, because pins are capped per kind and `sort_order` restarts per kind, so a mixed
    list has no defined order. Rejecting the ambiguous request beats inventing an ordering.

    `sort` defaults to `name` because that is what a settings screen wants; the entry grid
    asks for `entry_order` explicitly. Neither default is stated in the spec.

    `with_stats` governs what is *returned*, not what is computed. `entry_order` is defined in
    terms of `usage_rank_30d`, and the entry grid requests that ordering with stats off, so
    the rank is always computed and only sometimes reported.
    """
    profile = profile_repo.get_profile(db, user_id)
    if profile is None:
        # No profile means no timezone, so there is no local "today" to measure thirty days
        # back from, and no categories to list either.
        raise OnboardingRequiredError()

    local_today = me_service.local_today_for(profile, datetime.now(UTC))
    period = profile_repo.get_period_containing(db, user_id, local_today)

    rows = category_repo.list_categories(
        db,
        user_id,
        kind=kind,
        include_archived=include_archived,
        usage_since=category_service.usage_window_start(local_today),
        # Stats are period-scoped. With no period materialised yet there is nothing to scope
        # them to, and every spend figure is correctly zero.
        budget_period_id=None if period is None else me_service.as_uuid(period["id"], "id"),
        limit=limit,
    )

    currency_code = me_service.currency_code_for(profile)
    return category_service.list_out(
        rows,
        currency_code=currency_code,
        minor_unit=me_service.require_minor_unit(
            currency_code, onboarding_repo.currency_minor_unit(db, currency_code)
        ),
        budget_period_id=None if period is None else period["id"],
        sort=sort,
        with_stats=with_stats,
    )
