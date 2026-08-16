"""The Categories page — features 6.1 to 6.6.

One router because they are one resource and share a shape. Each function parses, calls the
service, and returns; every rule lives in `app/services/category.py`, and the SQL in
`app/repositories/category.py`.

The whole page is a single transaction per request, committed once by the session
dependency. Merge depends on that outright: transactions move, a limit is summed and a
source is archived, and a partial application would leave a user's money in two places.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from app.auth import DbSession, UserId
from app.repositories import category as category_repo
from app.repositories import icon_catalog as icon_repo
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.schemas.category import (
    CategoryArchiveOut,
    CategoryCreate,
    CategoryItemOut,
    CategoryListOut,
    CategoryPatch,
    CategoryRestoreOut,
    CategorySort,
    CategorySuggestionListOut,
    CategoryUpdateOut,
    MergeIn,
    MergeOut,
)
from app.schemas.onboarding import Kind
from app.services import category as category_service
from app.services import icon_catalog as icon_service
from app.services import me as me_service
from app.services.errors import CategoryNotFoundError, OnboardingRequiredError

router = APIRouter(tags=["categories"])

# The universal collection defaults from the API conventions.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _require_profile(db: DbSession, user_id: UUID):
    profile = profile_repo.get_profile(db, user_id)
    if profile is None:
        raise OnboardingRequiredError()
    return profile


def _icons(db: DbSession) -> dict[str, dict]:
    """The token index both the validator and the renderer read.

    One query per request rather than one per category: the catalog is ~26 rows and the
    alternative is an N+1 on a list endpoint.
    """
    return icon_service.index_by_token(icon_repo.list_icons_for_validation(db))


def _require_category(db: DbSession, user_id: UUID, category_id: UUID):
    category = category_repo.get_category(db, user_id, category_id)
    if category is None:
        # 404 for another user's row too — a 403 would confirm it exists (api.md).
        raise CategoryNotFoundError()
    return category


@router.get("/categories", response_model=CategoryListOut)
def list_categories(
    user_id: UserId,
    db: DbSession,
    kind: Kind,
    sort: CategorySort = "name",
    with_stats: bool = False,
    include_archived: bool = False,
    budget_period_id: UUID | None = None,
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

    `budget_period_id` scopes `stats.spent_minor`. Omitted, it is the current period. A period
    that is not this user's simply matches nothing, which reads as a zero spend rather than
    leaking that someone else's period exists.
    """
    profile = _require_profile(db, user_id)

    local_today = me_service.local_today_for(profile, datetime.now(UTC))
    period = profile_repo.get_period_containing(db, user_id, local_today)
    # Stats are period-scoped. With no period materialised yet there is nothing to scope
    # them to, and every spend figure is correctly zero.
    scope_id = budget_period_id or (
        None if period is None else me_service.as_uuid(period["id"], "id")
    )

    rows = category_repo.list_categories(
        db,
        user_id,
        kind=kind,
        include_archived=include_archived,
        usage_since=category_service.usage_window_start(local_today),
        budget_period_id=scope_id,
        limit=limit,
    )

    currency_code = me_service.currency_code_for(profile)
    return category_service.list_out(
        rows,
        currency_code=currency_code,
        minor_unit=me_service.require_minor_unit(
            currency_code, onboarding_repo.currency_minor_unit(db, currency_code)
        ),
        budget_period_id=scope_id,
        sort=sort,
        with_stats=with_stats,
        icons_by_token=_icons(db),
    )


@router.get("/categories/suggestions", response_model=CategorySuggestionListOut)
def list_category_suggestions(
    user_id: UserId,
    db: DbSession,
    kind: Kind,
) -> CategorySuggestionListOut:
    """Presets of one kind this user has no category for.

    Onboarding offers only the starter templates, and it offers them once. This is the way
    back: a preset unticked on day one, or one of the wider set 0012 added, is a tap rather
    than retyping a name, icon, colour and bucket the app already knows.

    Declared ahead of the `{category_id}` routes so `suggestions` is never parsed as a UUID.
    `kind` is required for the same reason it is on the list endpoint — the screen is tabbed
    and a mixed list has no defined order.
    """
    _require_profile(db, user_id)

    return category_service.suggestions_out(
        category_repo.list_unused_templates(db, user_id, kind=kind),
        _icons(db),
    )


@router.post("/categories", response_model=CategoryItemOut, status_code=201)
def create_category(
    payload: CategoryCreate,
    user_id: UserId,
    db: DbSession,
    response: Response,
) -> CategoryItemOut:
    """Create, with the two duplicate tests in front of it.

    Order matters: exact first, then near. An exact clash must not be reported as an
    overridable near-duplicate just because it would also score 1.0.
    """
    _require_profile(db, user_id)
    icons = _icons(db)
    now = datetime.now(UTC)

    existing = category_repo.get_category(db, user_id, payload.id)
    if existing is not None:
        # Replay of a queued POST. Returning the stored row is what makes an offline retry
        # safe (spec 2.1) — a fresh 409 would strand the client.
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
        return category_service.item_out(existing, None, icons)

    resolved_icon = category_service.require_known_icon(payload.icon, icons)
    category_service.require_known_template(
        payload.template_key,
        # Only looked up when the body claims a preset, which the New button does not.
        payload.template_key is not None
        and category_repo.template_exists(db, payload.template_key),
    )

    siblings = category_repo.list_duplicate_candidates(db, user_id, payload.kind)
    category_service.check_exact_duplicate(payload.name, payload.kind, siblings)
    category_service.check_near_duplicate(
        payload.name,
        siblings,
        category_repo.list_synonym_terms(db),
        force=payload.force,
    )

    row = category_service.build_create_row(payload, resolved_icon, now=now)
    outcome = category_repo.insert_category(db, user_id, row)
    category_service.raise_for_outcome(outcome, name=payload.name, kind=payload.kind)

    return category_service.item_out(
        _require_category(db, user_id, payload.id), None, icons
    )


@router.patch("/categories/{category_id}", response_model=CategoryUpdateOut)
def update_category(
    category_id: UUID,
    payload: CategoryPatch,
    user_id: UserId,
    db: DbSession,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> CategoryUpdateOut:
    """Partial update, plus the retroactive bucket question.

    `apply_to_past` is honoured only alongside a real bucket change, and writes exactly one
    `audit_log` row recording the before, the after and the count.
    """
    _require_profile(db, user_id)
    icons = _icons(db)
    now = datetime.now(UTC)

    category = _require_category(db, user_id, category_id)
    category_service.require_mutable(category)
    category_service.require_version(if_match, category)

    resolved_icon = (
        None
        if payload.icon is None
        else category_service.require_known_icon(payload.icon, icons)
    )

    if payload.name is not None:
        siblings = category_repo.list_duplicate_candidates(
            db, user_id, str(category["kind"]), exclude_id=category_id
        )
        category_service.check_exact_duplicate(
            payload.name, str(category["kind"]), siblings
        )

    changed = category_service.bucket_actually_changed(payload, category)
    patch = category_service.build_patch_row(payload, category, resolved_icon, now=now)

    outcome = category_repo.update_category(db, user_id, category_id, patch)
    category_service.raise_for_outcome(
        outcome, name=payload.name or str(category["name"]), kind=str(category["kind"])
    )

    rows_updated = 0
    applied = changed and payload.apply_to_past
    if applied:
        bucket = str(payload.default_bucket)
        rows_updated = category_repo.rewrite_buckets(db, user_id, category_id, bucket)
        category_repo.insert_audit_log(
            db,
            user_id,
            entity_id=category_id,
            action="bucket_backfill",
            before={"default_bucket": category["default_bucket"]},
            after={"default_bucket": bucket},
            row_count=rows_updated,
        )

    return category_service.update_out(
        _require_category(db, user_id, category_id),
        icons,
        category_service.bucket_change_out(applied=applied, rows_updated=rows_updated),
    )


@router.post("/categories/{category_id}/archive", response_model=CategoryArchiveOut)
def archive_category(
    category_id: UUID, user_id: UserId, db: DbSession
) -> CategoryArchiveOut:
    """Hide from the pickers, keep every row.

    Deliberately does not touch the current period's budget limit: removing a limit
    mid-month moves the unallocated figure under the user (spec 1386).
    """
    _require_profile(db, user_id)
    category = _require_category(db, user_id, category_id)
    category_service.require_mutable(category)

    outcome = category_repo.set_archived(
        db, user_id, category_id, archived=True, now=datetime.now(UTC)
    )
    category_service.raise_for_outcome(
        outcome, name=str(category["name"]), kind=str(category["kind"])
    )

    return category_service.archive_out(_require_category(db, user_id, category_id))


@router.post("/categories/{category_id}/restore", response_model=CategoryRestoreOut)
def restore_category(
    category_id: UUID, user_id: UserId, db: DbSession
) -> CategoryRestoreOut:
    """Return an archived category to the pickers.

    Can fail on the name: `category_unique_name` excludes archived rows, so the name may
    have been reused while this one was away. The database is the arbiter — checking first
    would be a race — and `duplicate_name` comes back through the same classifier.
    """
    _require_profile(db, user_id)
    category = _require_category(db, user_id, category_id)
    category_service.require_mutable(category)

    outcome = category_repo.set_archived(
        db, user_id, category_id, archived=False, now=datetime.now(UTC)
    )
    category_service.raise_for_outcome(
        outcome, name=str(category["name"]), kind=str(category["kind"])
    )

    return category_service.restore_out(_require_category(db, user_id, category_id))


@router.post("/categories/{category_id}/merge", response_model=MergeOut)
def merge_category(
    category_id: UUID, payload: MergeIn, user_id: UserId, db: DbSession
) -> MergeOut:
    """`{category_id}` is the SOURCE. One transaction, one audit row.

    Past periods are never rewritten: only the current period's limits are combined, and
    `txn.bucket` is not touched at all.
    """
    _require_profile(db, user_id)
    now = datetime.now(UTC)

    source = _require_category(db, user_id, category_id)
    target = _require_category(db, user_id, payload.target_category_id)
    category_service.validate_merge(source, target)

    moved = category_repo.move_transactions(
        db, user_id, category_id, payload.target_category_id
    )

    profile = profile_repo.get_profile(db, user_id)
    period = profile_repo.get_period_containing(
        db, user_id, me_service.local_today_for(profile, now)
    )
    plan: dict = {"action": "none", "block": None}
    if period is not None:
        period_id = me_service.as_uuid(period["id"], "id")
        plan = category_service.merged_limit(
            category_repo.get_limit(db, user_id, category_id, period_id),
            category_repo.get_limit(db, user_id, payload.target_category_id, period_id),
        )
        if plan["action"] == "reassign":
            category_repo.reassign_limit(
                db, user_id, plan["source_limit_id"], payload.target_category_id
            )
        elif plan["action"] == "sum":
            category_repo.sum_limit_into_target(
                db,
                user_id,
                source_limit_id=plan["source_limit_id"],
                target_limit_id=plan["target_limit_id"],
                total=plan["target_limit_after"],
            )

    category_repo.set_merged_into(db, user_id, category_id, payload.target_category_id)
    if payload.archive_source:
        category_repo.set_archived(db, user_id, category_id, archived=True, now=now)

    audit_log_id = category_repo.insert_audit_log(
        db,
        user_id,
        entity_id=category_id,
        action="merge",
        before={"category_id": str(category_id), "name": str(source["name"])},
        after={"category_id": str(payload.target_category_id), "name": str(target["name"])},
        row_count=moved,
    )

    return category_service.merge_out(
        source_id=category_id,
        target_id=payload.target_category_id,
        transactions_moved=moved,
        limits=plan["block"],
        source_archived=payload.archive_source,
        audit_log_id=audit_log_id,
    )


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: UUID, user_id: UserId, db: DbSession) -> Response:
    """The only hard delete in the system, and only for a category with no history.

    Two guards, not one. Transactions are counted *including soft-deleted rows*, because a
    soft-deleted row still references the category and would raise 23503. Budget limits
    outside the current period block it too — a category allocated money in a closed month
    has history, even with nothing spent against it.
    """
    profile = _require_profile(db, user_id)
    category = _require_category(db, user_id, category_id)

    period = profile_repo.get_period_containing(
        db, user_id, me_service.local_today_for(profile, datetime.now(UTC))
    )
    period_id = None if period is None else me_service.as_uuid(period["id"], "id")

    category_service.require_deletable(
        category,
        historical_txn_count=category_repo.count_transactions_ever(
            db, user_id, category_id
        ),
        past_limit_count=category_repo.count_limits_outside_period(
            db, user_id, category_id, period_id
        ),
    )

    # RESTRICT, not CASCADE — the current period's limits have to go explicitly, and the
    # guard above has already established there are no others.
    category_repo.delete_current_period_limits(db, user_id, category_id, period_id)
    category_repo.delete_category(db, user_id, category_id)

    return Response(status_code=204)
