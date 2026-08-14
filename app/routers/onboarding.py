from datetime import UTC, datetime

from fastapi import APIRouter, Response

from app.auth import DbSession, UserId
from app.lib.dates import days_in_period
from app.models.core import Account, BudgetLimit, BudgetPeriod, Category
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.routers.me import FALLBACK_CURRENCY_CODE, profile_out
from app.schemas.onboarding import (
    AccountOut,
    BucketAllocationOut,
    BudgetLimitOut,
    BudgetPeriodOut,
    CategoryOut,
    CategoryTemplateOut,
    CategoryTemplatesOut,
    OnboardingCompleteIn,
    OnboardingCompleteOut,
    PeriodOut,
    PreviewBudgetIn,
    PreviewBudgetOut,
    SuggestedLimitOut,
)
from app.services import onboarding as onboarding_service
from app.services.errors import OnboardingAlreadyCompleteError

router = APIRouter(tags=["onboarding"])


@router.get("/onboarding/category-templates", response_model=CategoryTemplatesOut)
def read_category_templates(user_id: UserId, db: DbSession) -> CategoryTemplatesOut:
    """The seed set offered at step 2.

    Served from the database rather than hardcoded in the client so the default set can be
    corrected without an app release — which is the whole reason `category_template` is a
    table. `user_id` goes unused in the body and is required in the signature: it is what
    makes the endpoint refuse an unauthenticated caller.
    """
    templates = [
        onboarding_service.template_from_row(row)
        for row in onboarding_repo.list_offered_templates(db)
    ]
    return CategoryTemplatesOut(
        template_version=onboarding_repo.offered_template_version(db),
        expense=[template_out(t) for t in templates if t.kind == "expense"],
        income=[template_out(t) for t in templates if t.kind == "income"],
    )


@router.post("/onboarding/preview-budget", response_model=PreviewBudgetOut)
def preview_budget(payload: PreviewBudgetIn, user_id: UserId, db: DbSession) -> PreviewBudgetOut:
    """A complete suggested allocation. Writes nothing.

    Because it is a pure function of its inputs the client may call it on every edit. It
    exists so a first-time user is never shown an empty budget, nor asked to fill in twelve
    limits by hand.
    """
    offered = {
        str(row["template_key"]): onboarding_service.template_from_row(row)
        for row in onboarding_repo.list_offered_templates(db)
    }
    selected = onboarding_service.select_expense_templates(
        payload.selected_expense_template_keys, offered
    )
    preview = onboarding_service.build_preview(
        payload.monthly_income_minor,
        payload.month_start_day,
        selected,
        onboarding_service.local_today(payload.timezone, datetime.now(UTC)),
    )

    # No profile exists yet to state a preference, and Phase 1 enables only INR.
    code = FALLBACK_CURRENCY_CODE
    return PreviewBudgetOut(
        currency_code=code,
        minor_unit=onboarding_service.require_minor_unit(
            code, onboarding_repo.currency_minor_unit(db, code)
        ),
        period=PeriodOut(
            starts_on=preview.starts_on,
            ends_on=preview.ends_on,
            days_in_period=preview.days_in_period,
        ),
        bucket_allocation=BucketAllocationOut(
            pct_needs=preview.allocation.percentages["NEEDS"],
            pct_wants=preview.allocation.percentages["WANTS"],
            pct_future=preview.allocation.percentages["FUTURE"],
            pct_debt=preview.allocation.percentages["DEBT"],
            needs_target_minor=preview.allocation.targets["NEEDS"],
            wants_target_minor=preview.allocation.targets["WANTS"],
            future_target_minor=preview.allocation.targets["FUTURE"],
            debt_target_minor=preview.allocation.targets["DEBT"],
        ),
        suggested_limits=[
            SuggestedLimitOut(
                template_key=limit.template_key,
                default_bucket=limit.default_bucket,
                limit_minor=limit.limit_minor,
            )
            for limit in preview.suggested_limits
        ],
        allocated_total_minor=preview.allocated_total_minor,
        unallocated_minor=preview.unallocated_minor,
        unallocated_note=onboarding_service.unallocated_note(preview),
    )


@router.post("/onboarding/complete", response_model=OnboardingCompleteOut, status_code=201)
def complete_onboarding(
    payload: OnboardingCompleteIn,
    user_id: UserId,
    db: DbSession,
    response: Response,
) -> OnboardingCompleteOut:
    """Create the profile, categories, accounts, first period and its limits — or nothing.

    One transaction, committed once by the session dependency. A four-call onboarding that
    failed on call three would leave a user with categories and no budget, and no screen in
    the app knows how to render that.

    The replay decision is made by **reads, before any insert is issued**. That ordering is
    load-bearing: `ON CONFLICT (id) DO NOTHING` guards only the primary key, so a second call
    carrying a different `budget_period_id` would otherwise reach `period_no_overlap` — a
    gist exclusion constraint no `ON CONFLICT` clause can name as an arbiter — and return a
    500 where an answer belongs.

    `budget.budget_period_id` is the idempotency key for the whole call: it is present in
    every request, and it is the one whose duplicate is unrecoverable.
    """
    profile = profile_repo.get_profile(db, user_id)
    period = onboarding_repo.get_budget_period(db, user_id, payload.budget.budget_period_id)

    if profile is None:
        templates = [
            onboarding_service.template_from_row(row)
            for row in onboarding_repo.list_offered_templates(db)
        ]
        rows = onboarding_service.build_rows(
            payload,
            user_id,
            templates,
            onboarding_repo.enabled_currency_codes(db),
            datetime.now(UTC),
        )
        onboarding_repo.insert_onboarding(
            db,
            user_id,
            profile=rows.profile,
            categories=rows.categories,
            accounts=rows.accounts,
            budget_period=rows.budget_period,
            budget_limits=rows.budget_limits,
        )
        # Read back rather than echo the request: `created_at`, `version`, `theme` and
        # `locale` are database defaults, and the client should be told what was stored.
        profile = profile_repo.get_profile(db, user_id)
        period = onboarding_repo.get_budget_period(db, user_id, payload.budget.budget_period_id)
        if profile is None or period is None:
            raise AssertionError("the bootstrap just written is not readable")
    elif period is None:
        # Same user, a genuinely different bootstrap. Completing it would duplicate every
        # category and orphan the first period.
        raise OnboardingAlreadyCompleteError()
    else:
        # A replayed offline write. `api.md` requires 200 with the existing row rather than a
        # conflict, which is what makes a queued POST safe to retry over a flaky connection.
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"

    code = period.currency_code.strip()
    return OnboardingCompleteOut(
        currency_code=code,
        minor_unit=onboarding_service.require_minor_unit(
            code, onboarding_repo.currency_minor_unit(db, code)
        ),
        profile=profile_out(profile),
        categories=[
            category_out(category) for category in onboarding_repo.list_categories(db, user_id)
        ],
        accounts=[account_out(account) for account in onboarding_repo.list_accounts(db, user_id)],
        budget_period=budget_period_out(period),
        budget_limits=[
            budget_limit_out(limit)
            for limit in onboarding_repo.list_budget_limits(db, user_id, period.id)
        ],
    )


def template_out(template: onboarding_service.Template) -> CategoryTemplateOut:
    return CategoryTemplateOut(
        template_key=template.template_key,
        name=template.name,
        short_label=template.short_label,
        icon=template.icon,
        colour=template.colour,
        kind=template.kind,
        default_bucket=template.default_bucket,
        flexibility=template.flexibility,
        suggested_share_pct=template.suggested_share_pct,
        sort_order=template.sort_order,
    )


def category_out(category: Category) -> CategoryOut:
    return CategoryOut(
        id=category.id,
        name=category.name,
        short_label=category.short_label,
        icon=category.icon,
        colour=category.colour,
        kind=category.kind,
        default_bucket=category.default_bucket,
        flexibility=category.flexibility,
        template_key=category.template_key,
        is_pinned=category.is_pinned,
        is_system=category.is_system,
        is_archived=category.is_archived,
        parent_id=category.parent_id,
        created_at=category.created_at,
        updated_at=category.updated_at,
        version=category.version,
    )


def account_out(account: Account) -> AccountOut:
    return AccountOut(
        id=account.id,
        name=account.name,
        type=account.type,
        icon=account.icon,
        is_default=account.is_default,
        is_archived=account.is_archived,
        created_at=account.created_at,
        updated_at=account.updated_at,
        version=account.version,
    )


def budget_period_out(period: BudgetPeriod) -> BudgetPeriodOut:
    return BudgetPeriodOut(
        id=period.id,
        starts_on=period.starts_on,
        ends_on=period.ends_on,
        # Derived. There is no `days_in_period` column and there should not be one.
        days_in_period=days_in_period(period.starts_on, period.ends_on),
        expected_income_minor=period.expected_income_minor,
        pct_needs=period.pct_needs,
        pct_wants=period.pct_wants,
        pct_future=period.pct_future,
        pct_debt=period.pct_debt,
        carried_from_budget_period_id=period.carried_from_budget_period_id,
        review_dismissed_at=period.review_dismissed_at,
        created_at=period.created_at,
        updated_at=period.updated_at,
        version=period.version,
    )


def budget_limit_out(limit: BudgetLimit) -> BudgetLimitOut:
    return BudgetLimitOut(
        id=limit.id,
        category_id=limit.category_id,
        limit_minor=limit.limit_minor,
        carried_in_minor=limit.carried_in_minor,
        effective_limit_minor=onboarding_service.effective_limit_minor(
            limit.limit_minor, limit.carried_in_minor
        ),
        rollover=limit.rollover,
        rollover_cap_minor=limit.rollover_cap_minor,
        version=limit.version,
    )
