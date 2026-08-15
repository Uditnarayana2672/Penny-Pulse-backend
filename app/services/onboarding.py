"""Onboarding rules and the seeded-budget arithmetic.

Imports neither `fastapi` nor `sqlalchemy`, so every rule below is testable with no
database and no HTTP. It does import `app.schemas`, which is plain validated data — mapping
those into a second, identical set of classes is the domain-entity ceremony delta D7 rules
out.

The clock arrives as an argument. It is the one thing `.claude/rules/tests.md` allows
mocking, and passing it in means none of these functions need it mocked at all.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from app.lib.dates import days_in_period, period_bounds, to_local_date
from app.lib.money import split_by_shares
from app.schemas.onboarding import (
    AccountOut,
    BucketAllocationOut,
    BudgetLimitOut,
    BudgetPeriodOut,
    CategoryOut,
    CategoryTemplateOut,
    CategoryTemplatesOut,
    OnboardingAccountIn,
    OnboardingCategoryIn,
    OnboardingCompleteIn,
    OnboardingCompleteOut,
    PeriodOut,
    PreviewBudgetOut,
    SuggestedLimitOut,
)
from app.services.errors import (
    CurrencyNotSupportedError,
    ExcludedCategoryNotBudgetableError,
    IncomeCategoryNotBudgetableError,
    OnboardingAlreadyCompleteError,
    RuleViolationError,
)
from app.services.me import profile_out

# The same union appears in the repository. Two occurrences is inside the "three before
# extracting" rule, and a shared alias would need a module both layers may import — a new
# layer for one type.
RowValue = str | int | bool | UUID | date | datetime | None
RowDict = dict[str, RowValue]

# Buckets a limit can belong to, in report order. EXCLUDED is deliberately absent: it is a
# categorisation, never an allocation, and it can hold no budget limit.
BUDGETABLE_BUCKETS = ("NEEDS", "WANTS", "FUTURE", "DEBT")

BASE_BUCKET_PCT = {"NEEDS": 50, "WANTS": 30, "FUTURE": 20, "DEBT": 0}

# A user carrying an EMI is not a user with 50% spare for needs. The points come out of
# NEEDS rather than WANTS because debt service is a need that was already being paid.
DEBT_SHIFT_PCT = 10

LIABILITY_ACCOUNT_TYPES = frozenset({"credit_card", "loan"})

# Exactly the columns `template_from_row` expects, so the repository and this module cannot
# drift apart silently — a missing or extra key is a TypeError at the boundary.
TEMPLATE_COLUMNS = (
    "template_key",
    "name",
    "short_label",
    "icon",
    "colour",
    "kind",
    "default_bucket",
    "flexibility",
    "suggested_share_pct",
    "sort_order",
)


@dataclass(frozen=True)
class Template:
    """One offered `category_template` row, as this module needs it.

    A plain dataclass rather than the ORM model, so nothing here imports `sqlalchemy`.
    `default_bucket`, `flexibility` and `suggested_share_pct` are all None on income
    templates — income is not budgeted, and buckets classify spending rather than earning.
    """

    template_key: str
    name: str
    short_label: str
    icon: str
    colour: str
    kind: str
    default_bucket: str | None
    flexibility: int | None
    suggested_share_pct: int | None
    sort_order: int


@dataclass(frozen=True)
class BucketAllocation:
    percentages: dict[str, int]
    targets: dict[str, int]


@dataclass(frozen=True)
class SuggestedLimit:
    template_key: str
    default_bucket: str
    limit_minor: int


@dataclass(frozen=True)
class BudgetPreview:
    starts_on: date
    ends_on: date
    days_in_period: int
    allocation: BucketAllocation
    suggested_limits: list[SuggestedLimit]
    allocated_total_minor: int
    unallocated_minor: int
    unallocated_buckets: list[str]


@dataclass(frozen=True)
class OnboardingRows:
    """Exact column values for one atomic bootstrap, ready for the repository.

    Columns with a database default are absent rather than restated — `models.md` is
    explicit that the migrations are the source of truth, so `locale`, `theme`, `version`,
    `metadata`, `carried_in_minor` and the timestamps are left to Postgres. `role` is absent
    because it is never a client input, and that is the only defence that applies: the
    trigger guarding it is gated on the client roles and this backend connects as
    `postgres`. `is_pinned` is absent too, which is what keeps the four-pin trigger out of
    the bootstrap path entirely.
    """

    profile: RowDict
    categories: list[RowDict]
    accounts: list[RowDict]
    budget_period: RowDict
    budget_limits: list[RowDict]


def template_from_row(row: RowDict) -> Template:
    """The one place repository output is turned into a `Template`.

    `**row` rather than field-by-field so a column the repository stops selecting fails
    loudly here instead of arriving as a silent None deep inside the allocation.
    """
    return Template(**row)


def sort_canonically(templates: list[Template]) -> list[Template]:
    """One fixed order for any set of templates, everywhere.

    Not cosmetic. `split_by_shares` breaks ties toward the earliest index, so two requests
    listing the same categories in a different order would produce different limits — the
    user would be shown one number on the preview screen and a different one would be
    saved. `sort_order` alone is not enough: it restarts at 1 for income, so it collides
    across kinds.
    """
    return sorted(templates, key=lambda t: (t.kind, t.sort_order, t.template_key))


def bucket_percentages(selected: list[Template]) -> dict[str, int]:
    """50/30/20/0, shifted to 40/30/20/10 when anything selected is debt."""
    percentages = dict(BASE_BUCKET_PCT)
    if any(template.default_bucket == "DEBT" for template in selected):
        percentages["NEEDS"] -= DEBT_SHIFT_PCT
        percentages["DEBT"] += DEBT_SHIFT_PCT
    return percentages


def allocate_buckets(monthly_income_minor: int, selected: list[Template]) -> BucketAllocation:
    """Split income across the four buckets so the parts sum to it exactly.

    Largest-remainder rather than `floor(income * pct / 100)`: flooring loses up to three
    paise whenever income is not a whole number of rupees, and those paise then go missing
    from every total derived from the budget.
    """
    percentages = bucket_percentages(selected)
    shares = [percentages[bucket] for bucket in BUDGETABLE_BUCKETS]
    amounts = split_by_shares(monthly_income_minor, shares)
    targets = dict(zip(BUDGETABLE_BUCKETS, amounts, strict=True))

    if sum(targets.values()) != monthly_income_minor:
        raise AssertionError("bucket targets must sum to income")

    return BucketAllocation(percentages=percentages, targets=targets)


def build_preview(
    monthly_income_minor: int,
    month_start_day: int,
    selected: list[Template],
    today_local: date,
) -> BudgetPreview:
    """A complete suggested budget. Pure — which is what makes it safe to call repeatedly."""
    ordered = sort_canonically(selected)
    allocation = allocate_buckets(monthly_income_minor, ordered)

    suggested: list[SuggestedLimit] = []
    unallocated_buckets: list[str] = []

    for bucket in BUDGETABLE_BUCKETS:
        target = allocation.targets[bucket]
        members = [template for template in ordered if template.default_bucket == bucket]

        # A bucket nobody selected reports its whole target as unallocated, and no division
        # happens. FUTURE is always in this branch in Phase 1 — there are no seeded savings
        # categories, and showing that empty line on day one is the point of the screen.
        #
        # A bucket with a zero target is not reported: DEBT sits at 0% unless something
        # debt-shaped was selected, and naming a bucket that is owed nothing would make the
        # note read as a problem when it is just an absence.
        if not members:
            if target > 0:
                unallocated_buckets.append(bucket)
            continue

        shares = [template.suggested_share_pct or 0 for template in members]
        if sum(shares) == 0:
            # Unreachable with the seeded set: every budgetable template has a positive
            # share. Getting here means a template was seeded wrong, not that a user did
            # something, so it is a bug rather than a 422.
            raise AssertionError(f"selected {bucket} templates have no positive share")

        amounts = split_by_shares(target, shares)
        if sum(amounts) != target:
            raise AssertionError(f"{bucket} limits must sum to its target")

        for template, amount in zip(members, amounts, strict=True):
            suggested.append(
                SuggestedLimit(
                    template_key=template.template_key,
                    default_bucket=bucket,
                    limit_minor=amount,
                )
            )

    allocated = sum(limit.limit_minor for limit in suggested)

    starts_on, ends_on = period_bounds(today_local, month_start_day)

    return BudgetPreview(
        starts_on=starts_on,
        ends_on=ends_on,
        days_in_period=days_in_period(starts_on, ends_on),
        allocation=allocation,
        suggested_limits=suggested,
        allocated_total_minor=allocated,
        unallocated_minor=monthly_income_minor - allocated,
        unallocated_buckets=unallocated_buckets,
    )


def unallocated_note(preview: BudgetPreview) -> str | None:
    """Copy naming the buckets with nothing behind them, or nothing to say.

    Bucket names stay in their uppercase API spelling: this is one of the few response
    fields that is genuinely server-owned copy, and it should read the same as the bucket
    labels beside it on the screen.
    """
    if preview.unallocated_minor == 0 or not preview.unallocated_buckets:
        return None

    names = preview.unallocated_buckets
    if len(names) == 1:
        return f"Your {names[0]} bucket has nothing allocated to it yet."

    listed = ", ".join(names[:-1]) + f" and {names[-1]}"
    return f"Your {listed} buckets have nothing allocated to them yet."


def local_today(timezone: str, now: datetime) -> date:
    """The calendar day `now` falls on in `timezone`.

    Exposed so a router can obtain it without importing `app.lib` directly, and so both
    onboarding endpoints resolve the day the same way. A period boundary computed in UTC is
    wrong for every Indian user between 00:00 and 05:30 local.
    """
    return to_local_date(now, timezone)


def select_expense_templates(keys: list[str], offered: dict[str, Template]) -> list[Template]:
    """Resolve requested template keys, rejecting anything not on offer."""
    selected: list[Template] = []
    for key in keys:
        template = offered.get(key)
        if template is None:
            raise RuleViolationError(
                f"Unknown category template: {key}.", field="selected_expense_template_keys"
            )
        selected.append(template)
    return selected


def effective_limit_minor(limit_minor: int, carried_in_minor: int) -> int:
    """What is actually spendable against a limit this period.

    The rollover cap is applied when the carry is *computed* at period close, not here, so
    `carried_in_minor` is already the capped figure by the time it is stored. For a first
    period it is always zero — there is no earlier period to carry from.
    """
    return limit_minor + carried_in_minor


def normalized_name(name: str) -> str:
    """`lower(trim(name))`, matching what `category_unique_name` indexes.

    The column is NOT NULL with no default and no generated expression, so the application
    is the only thing that can produce it.
    """
    return name.strip().lower()


def resolve_categories(
    categories: list[OnboardingCategoryIn], offered: dict[str, Template]
) -> list[tuple[OnboardingCategoryIn, Template]]:
    """Pair each requested category with the template it claims to come from."""
    resolved: list[tuple[OnboardingCategoryIn, Template]] = []
    for category in categories:
        template = offered.get(category.template_key)
        if template is None:
            raise RuleViolationError(
                f"Unknown category template: {category.template_key}.", field="categories"
            )
        resolved.append((category, template))
    return resolved


def check_category_names_are_distinct(
    resolved: list[tuple[OnboardingCategoryIn, Template]],
) -> None:
    """`category_unique_name` is per `(user_id, kind, name_normalized)`.

    Checked after templates resolve because a rename can collide with another category's
    *default* name, which the request body alone cannot see. "Other" existing once as an
    expense and once as income is fine — `kind` is part of the key.
    """
    seen: set[tuple[str, str]] = set()
    for category, template in resolved:
        name = category.name or template.name
        key = (template.kind, normalized_name(name))
        if key in seen:
            raise RuleViolationError(
                f"Two categories are both named {name!r}.", field="categories"
            )
        seen.add(key)


def check_account_names_are_distinct(accounts: list[OnboardingAccountIn]) -> None:
    """`account_unique_name` would otherwise turn a second "Cash" into a 23505."""
    seen: set[str] = set()
    for account in accounts:
        key = normalized_name(account.name)
        if key in seen:
            raise RuleViolationError(
                f"Two accounts are both named {account.name!r}.", field="accounts"
            )
        seen.add(key)


def build_rows(
    payload: OnboardingCompleteIn,
    user_id: UUID,
    offered_templates: list[Template],
    enabled_currency_codes: set[str],
    now: datetime,
) -> OnboardingRows:
    """Validate the whole bootstrap and return the exact rows to write.

    Every rule needing another row lives here, so the repository writes without deciding
    anything and a database CHECK stays what it should be: a backstop nobody reaches.
    """
    currency = payload.profile.preferred_currency_code.upper()
    if currency not in enabled_currency_codes:
        raise CurrencyNotSupportedError(currency)

    offered = {template.template_key: template for template in offered_templates}
    resolved = resolve_categories(payload.categories, offered)
    check_category_names_are_distinct(resolved)
    check_account_names_are_distinct(payload.accounts)

    if not any(template.kind == "expense" for _, template in resolved):
        raise RuleViolationError(
            "At least one expense category is needed to build a budget.", field="categories"
        )

    category_rows: list[RowDict] = []
    template_by_category_id: dict[UUID, Template] = {}
    for category, template in resolved:
        name = category.name or template.name
        template_by_category_id[category.id] = template
        category_rows.append(
            {
                "id": category.id,
                "user_id": user_id,
                "name": name,
                "name_normalized": normalized_name(name),
                "short_label": template.short_label,
                "icon": template.icon,
                "colour": template.colour,
                "kind": template.kind,
                # None for income and non-None for expense, or
                # `category_bucket_matches_kind` rejects the row.
                "default_bucket": template.default_bucket,
                "flexibility": template.flexibility,
                "template_key": template.template_key,
                "sort_order": template.sort_order,
            }
        )

    limit_rows = _build_limit_rows(payload, user_id, template_by_category_id)

    account_rows: list[RowDict] = [
        {
            "id": account.id,
            "user_id": user_id,
            "name": account.name,
            "name_normalized": normalized_name(account.name),
            "type": account.type,
            "is_liability": account.type in LIABILITY_ACCOUNT_TYPES,
            "is_default": account.is_default,
            "currency_code": currency,
            "sort_order": index,
        }
        for index, account in enumerate(payload.accounts)
    ]

    starts_on, ends_on = period_bounds(
        to_local_date(now, payload.profile.timezone), payload.profile.month_start_day
    )

    return OnboardingRows(
        profile={
            "user_id": user_id,
            "display_name": payload.profile.display_name,
            "timezone": payload.profile.timezone,
            "preferred_currency_code": currency,
            "monthly_income_minor": payload.profile.monthly_income_minor,
            "month_start_day": payload.profile.month_start_day,
            "onboarding_completed_at": now,
            # Set once and never again: it is documented to survive a replay of onboarding,
            # which `onboarding_completed_at` is not.
            "onboarding_first_completed_at": now,
        },
        categories=category_rows,
        accounts=account_rows,
        budget_period={
            "id": payload.budget.budget_period_id,
            "user_id": user_id,
            "starts_on": starts_on,
            "ends_on": ends_on,
            "currency_code": currency,
            "expected_income_minor": payload.budget.expected_income_minor,
            "pct_needs": payload.budget.pct_needs,
            "pct_wants": payload.budget.pct_wants,
            "pct_future": payload.budget.pct_future,
            "pct_debt": payload.budget.pct_debt,
        },
        budget_limits=limit_rows,
    )


BootstrapAction = Literal["create", "replay", "period_id_taken", "already_complete"]


def decide_bootstrap(
    *, period_is_mine: bool, profile_exists: bool, period_id_is_taken: bool
) -> BootstrapAction:
    """Which of the four things a `POST /onboarding/complete` actually is.

    A pure decision on three booleans the router has already read, deliberately made
    *before* any insert is issued. `ON CONFLICT (id) DO NOTHING` guards only the primary
    key, so every other outcome has to be resolved here or it reaches `period_no_overlap` —
    a gist exclusion constraint no `ON CONFLICT` clause can name as an arbiter.

    The period id is tested first because it, not the profile, is the idempotency key: it is
    present in every request and it is the one whose duplicate is unrecoverable.

    `period_id_taken` is the case that used to be a 500. The id is a global primary key, so
    one already held by another user can never be inserted — the insert silently wrote
    nothing and the read-back then failed an assertion. It is answered as a 422 rather than a
    409 because the client's recovery is to generate a fresh UUIDv7 and retry, which is what
    a validation failure means, and because saying no more than "this id is unusable" avoids
    confirming whose it is.
    """
    if period_is_mine:
        return "replay"
    if profile_exists:
        return "already_complete"
    if period_id_is_taken:
        return "period_id_taken"
    return "create"


def raise_for_bootstrap(action: BootstrapAction) -> None:
    """Turn the two refusing actions into their domain errors.

    Kept beside `decide_bootstrap` so the decision and its consequence cannot drift, and out
    of the router so no business rule lives at the edge.
    """
    if action == "period_id_taken":
        raise RuleViolationError(
            "That budget_period_id is already in use. Generate a new one and retry.",
            field="budget.budget_period_id",
        )
    if action == "already_complete":
        raise OnboardingAlreadyCompleteError()


def templates_out(templates: list[Template], template_version: int) -> CategoryTemplatesOut:
    """The seed set, split by kind because the two lists are offered separately."""
    return CategoryTemplatesOut(
        template_version=template_version,
        expense=[_template_out(t) for t in templates if t.kind == "expense"],
        income=[_template_out(t) for t in templates if t.kind == "income"],
    )


def _template_out(template: Template) -> CategoryTemplateOut:
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


def preview_out(preview: BudgetPreview, currency_code: str, minor_unit: int) -> PreviewBudgetOut:
    """The preview as the wire shape. Every number here was computed by `build_preview`."""
    return PreviewBudgetOut(
        currency_code=currency_code,
        minor_unit=minor_unit,
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
        unallocated_note=unallocated_note(preview),
    )


def complete_out(
    *,
    minor_unit: int,
    profile: RowDict,
    categories: list[RowDict],
    accounts: list[RowDict],
    budget_period: RowDict,
    budget_limits: list[RowDict],
) -> OnboardingCompleteOut:
    """Everything the client needs to render Home and the entry screen without a second call.

    The two derived fields are computed here rather than at the edge: `days_in_period` is
    period arithmetic and `effective_limit_minor` is money arithmetic, and `api.md` allows a
    router neither.
    """
    currency_code = str(budget_period["currency_code"]).strip()
    starts_on = budget_period["starts_on"]
    ends_on = budget_period["ends_on"]
    if not isinstance(starts_on, date) or not isinstance(ends_on, date):
        raise AssertionError("budget period bounds must be dates")

    return OnboardingCompleteOut(
        currency_code=currency_code,
        minor_unit=minor_unit,
        profile=profile_out(profile),
        categories=[CategoryOut(**row) for row in categories],
        accounts=[AccountOut(**row) for row in accounts],
        budget_period=BudgetPeriodOut(
            **budget_period,
            days_in_period=days_in_period(starts_on, ends_on),
        ),
        budget_limits=[
            BudgetLimitOut(
                **row,
                effective_limit_minor=effective_limit_minor(
                    _as_int(row["limit_minor"]), _as_int(row["carried_in_minor"])
                ),
            )
            for row in budget_limits
        ],
    )


def _as_int(value: RowValue) -> int:
    """Narrow a money column to `int` before arithmetic.

    `BIGINT` always arrives as `int`; this exists so the addition in `effective_limit_minor`
    is never handed a `None` from a column that changed nullability under it.
    """
    if not isinstance(value, int):
        raise AssertionError("money columns must be integers")
    return value


def _build_limit_rows(
    payload: OnboardingCompleteIn,
    user_id: UUID,
    template_by_category_id: dict[UUID, Template],
) -> list[RowDict]:
    """Mirrors the `budget_limit_budgetable` trigger, and carries the tenancy check.

    That trigger looks the category up by id with **no `user_id` filter**, so it would
    happily validate a limit against another user's category. Restricting the lookup to
    categories created by this same request is the only thing standing between that and a
    cross-user write.
    """
    rows: list[RowDict] = []
    for limit in payload.budget.limits:
        template = template_by_category_id.get(limit.category_id)
        if template is None:
            raise RuleViolationError(
                "A budget limit points at a category that is not part of this request.",
                field="budget.limits",
            )
        if template.kind != "expense":
            raise IncomeCategoryNotBudgetableError(
                f"{template.name} is an income category and cannot hold a budget limit.",
                field="budget.limits",
            )
        if template.default_bucket == "EXCLUDED":
            raise ExcludedCategoryNotBudgetableError(
                f"{template.name} is excluded from budgets and cannot hold a limit.",
                field="budget.limits",
            )
        rows.append(
            {
                "id": limit.id,
                "user_id": user_id,
                "budget_period_id": payload.budget.budget_period_id,
                "category_id": limit.category_id,
                "limit_minor": limit.limit_minor,
                "rollover": limit.rollover,
                "rollover_cap_minor": limit.rollover_cap_minor,
            }
        )
    return rows
