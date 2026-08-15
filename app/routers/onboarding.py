"""The three onboarding endpoints.

Each one reads through the repositories, hands plain data to the service, and returns what
the service built. No ORM type appears here, and no arithmetic: `days_in_period` and
`effective_limit_minor` are computed in `app/services/onboarding.py`, because `api.md`
allows a router no money or date maths.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Response

from app.auth import DbSession, UserId
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.schemas.onboarding import (
    CategoryTemplatesOut,
    OnboardingCompleteIn,
    OnboardingCompleteOut,
    PreviewBudgetIn,
    PreviewBudgetOut,
)
from app.services import me as me_service
from app.services import onboarding as onboarding_service

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
    return onboarding_service.templates_out(
        templates, onboarding_repo.offered_template_version(db)
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
    code = me_service.FALLBACK_CURRENCY_CODE
    return onboarding_service.preview_out(
        preview,
        currency_code=code,
        minor_unit=me_service.require_minor_unit(
            code, onboarding_repo.currency_minor_unit(db, code)
        ),
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

    Which of the four things this call is gets decided by **reads, before any insert**, in
    `decide_bootstrap`. That ordering is load-bearing: `ON CONFLICT (id) DO NOTHING` guards
    only the primary key, so any other outcome would otherwise reach `period_no_overlap` — a
    gist exclusion constraint no `ON CONFLICT` clause can name as an arbiter.
    """
    period_id = payload.budget.budget_period_id
    period = onboarding_repo.get_budget_period(db, user_id, period_id)

    action = onboarding_service.decide_bootstrap(
        period_is_mine=period is not None,
        profile_exists=profile_repo.profile_exists(db, user_id),
        # Only asked when it can change the answer: a period id that is not ours and not
        # free belongs to somebody else, and is the case that used to be a 500.
        period_id_is_taken=(
            period is None and onboarding_repo.period_id_exists_for_anyone(db, period_id)
        ),
    )
    onboarding_service.raise_for_bootstrap(action)

    if action == "create":
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
        period = onboarding_repo.get_budget_period(db, user_id, period_id)
    else:
        # A replayed offline write. `api.md` requires 200 with the existing row rather than a
        # conflict, which is what makes a queued POST safe to retry over a flaky connection.
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"

    profile = profile_repo.get_profile(db, user_id)
    if profile is None or period is None:
        raise AssertionError("the bootstrap just written is not readable")

    currency_code = me_service.currency_code_for(profile)
    return onboarding_service.complete_out(
        minor_unit=me_service.require_minor_unit(
            currency_code, onboarding_repo.currency_minor_unit(db, currency_code)
        ),
        profile=profile,
        categories=onboarding_repo.list_categories(db, user_id),
        accounts=onboarding_repo.list_accounts(db, user_id),
        budget_period=period,
        budget_limits=onboarding_repo.list_budget_limits(db, user_id, period_id),
    )
