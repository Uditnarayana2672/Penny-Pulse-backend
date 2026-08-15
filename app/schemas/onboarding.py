"""Wire types for the onboarding endpoints.

Validators here check shape, type and range only. Anything needing another row — is this
`template_key` real, is this currency enabled, does this category belong to the caller —
is a service rule, because a Pydantic validator that queries the database is in the wrong
layer.
"""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.lib.dates import DEFAULT_TIMEZONE
from app.schemas.me import MonthStartDay, ProfileOut
from app.services.errors import BucketPercentagesInvalidError

Bucket = Literal["NEEDS", "WANTS", "FUTURE", "DEBT", "EXCLUDED"]
Kind = Literal["income", "expense"]
Rollover = Literal["none", "carry", "carry_capped"]
AccountType = Literal["cash", "bank", "wallet", "credit_card", "loan", "investment"]


class CategoryTemplateOut(BaseModel):
    """One row of the seeded `category_template` table.

    `default_bucket`, `flexibility` and `suggested_share_pct` are all NULL on income
    templates — income is not budgeted and buckets classify spending, not earning.
    """

    template_key: str
    name: str
    short_label: str
    icon: str
    colour: str
    kind: Kind
    default_bucket: Bucket | None
    flexibility: int | None
    suggested_share_pct: int | None
    sort_order: int


class CategoryTemplatesOut(BaseModel):
    """Split by kind because the two lists are offered on different parts of the screen."""

    template_version: int
    expense: list[CategoryTemplateOut]
    income: list[CategoryTemplateOut]


class PreviewBudgetIn(BaseModel):
    """Inputs to a pure calculation. Nothing here is persisted.

    `timezone` defaults rather than being required so that preview and commit cannot
    silently disagree about which day it is for a user near midnight — there is no timezone
    picker in the flow, so both endpoints resolve to the same default.
    """

    monthly_income_minor: int = Field(ge=0)
    month_start_day: MonthStartDay
    timezone: str = DEFAULT_TIMEZONE
    selected_expense_template_keys: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def template_keys_are_distinct(self) -> Self:
        keys = self.selected_expense_template_keys
        if len(set(keys)) != len(keys):
            raise ValueError("selected_expense_template_keys contains duplicates")
        return self


class PeriodOut(BaseModel):
    """`days_in_period` is derived — there is no such column, and there should not be."""

    starts_on: date
    ends_on: date
    days_in_period: int


class BucketAllocationOut(BaseModel):
    pct_needs: int
    pct_wants: int
    pct_future: int
    pct_debt: int
    needs_target_minor: int
    wants_target_minor: int
    future_target_minor: int
    debt_target_minor: int


class SuggestedLimitOut(BaseModel):
    template_key: str
    default_bucket: Bucket
    limit_minor: int


class PreviewBudgetOut(BaseModel):
    """A complete suggested allocation the user can accept or edit.

    `unallocated_minor` is not slack to be tidied away. FUTURE has no seeded categories in
    Phase 1, so its whole target lands here — an empty savings line the user should see on
    day one rather than a number quietly rounded into something else.
    """

    currency_code: str
    minor_unit: int
    period: PeriodOut
    bucket_allocation: BucketAllocationOut
    suggested_limits: list[SuggestedLimitOut]
    allocated_total_minor: int
    unallocated_minor: int
    unallocated_note: str | None


class OnboardingProfileIn(BaseModel):
    """`role` and `theme` are absent by design.

    `role` is never a client input — see `ProfileOut`. Theme, notification time and the
    implementation intention are day-two prompts (spec 1.6) collected through `PATCH /me`,
    because every extra onboarding step costs users before they have felt any value.

    `preferred_currency_code` is a plain 3-char string, not a `Literal["INR"]`: the enabled
    set lives in the `currency` table, so adding a currency stays a data change.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str = DEFAULT_TIMEZONE
    preferred_currency_code: str = Field(default="INR", min_length=3, max_length=3)
    monthly_income_minor: int = Field(ge=0)
    month_start_day: MonthStartDay


class OnboardingCategoryIn(BaseModel):
    """A chosen template, and optionally a different name for it.

    `kind`, `default_bucket`, `flexibility`, `icon`, `colour`, `short_label` and
    `sort_order` are copied server-side from `category_template` rather than accepted here.
    Taking them from the client would let one arrive with `default_bucket = 'NEEDS'` on an
    income category, which `category_bucket_matches_kind` would then reject as a 500
    instead of a message — and it would make the seed set correctable only by app release.
    """

    id: UUID
    template_key: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=80)


class OnboardingAccountIn(BaseModel):
    """A label, not a balance (delta D3). There is no balance column and there will not be
    one in Phase 1."""

    id: UUID
    name: str = Field(min_length=1, max_length=80)
    type: AccountType = "cash"
    is_default: bool = False


class OnboardingBudgetLimitIn(BaseModel):
    id: UUID
    category_id: UUID
    limit_minor: int = Field(ge=0)
    rollover: Rollover = "none"
    rollover_cap_minor: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def a_capped_rollover_needs_its_cap(self) -> Self:
        # Mirrors the `rollover_cap_required` CHECK, so the client gets a named field
        # rather than a 23514 it cannot act on.
        if self.rollover == "carry_capped" and self.rollover_cap_minor is None:
            raise ValueError("rollover_cap_minor is required when rollover is carry_capped")
        return self


class OnboardingBudgetIn(BaseModel):
    """The budget the user accepted, which may differ from what preview suggested.

    The limits are sent back rather than recomputed because the seeded budget is
    adjustable by design (FR-130) — accepting only an income would make the preview screen
    read-only.
    """

    budget_period_id: UUID
    expected_income_minor: int = Field(ge=0)
    pct_needs: int = Field(ge=0, le=100)
    pct_wants: int = Field(ge=0, le=100)
    pct_future: int = Field(ge=0, le=100)
    pct_debt: int = Field(ge=0, le=100)
    limits: list[OnboardingBudgetLimitIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def percentages_sum_to_one_hundred(self) -> Self:
        # The same rule as the `period_pct_sums_to_100` CHECK. Cross-field shape, so it
        # belongs at the edge; the CHECK stays the backstop.
        total = self.pct_needs + self.pct_wants + self.pct_future + self.pct_debt
        if total != 100:
            raise BucketPercentagesInvalidError(total)
        return self

    @model_validator(mode="after")
    def one_limit_per_category(self) -> Self:
        # `budget_limit_one_per_category_per_period` is a unique index, and ON CONFLICT
        # (id) cannot suppress it — two limits on one category would be a 500.
        category_ids = [limit.category_id for limit in self.limits]
        if len(set(category_ids)) != len(category_ids):
            raise ValueError("limits contains more than one entry for the same category")
        return self


class OnboardingCompleteIn(BaseModel):
    """Everything the bootstrap needs, in one body, committed in one transaction.

    Every id is client-generated so a queued offline POST is safe to replay.
    `budget.budget_period_id` is the idempotency key for the whole call: it is present in
    every request, and it is the one whose duplicate would otherwise trip the
    `period_no_overlap` exclusion constraint, which no `ON CONFLICT` clause can absorb.
    """

    profile: OnboardingProfileIn
    categories: list[OnboardingCategoryIn] = Field(min_length=1)
    accounts: list[OnboardingAccountIn] = Field(default_factory=list)
    budget: OnboardingBudgetIn

    @model_validator(mode="after")
    def ids_and_templates_are_distinct(self) -> Self:
        category_ids = [category.id for category in self.categories]
        if len(set(category_ids)) != len(category_ids):
            raise ValueError("categories contains a duplicate id")

        template_keys = [category.template_key for category in self.categories]
        if len(set(template_keys)) != len(template_keys):
            raise ValueError("categories contains a duplicate template_key")

        account_ids = [account.id for account in self.accounts]
        if len(set(account_ids)) != len(account_ids):
            raise ValueError("accounts contains a duplicate id")

        return self

    @model_validator(mode="after")
    def at_most_one_default_account(self) -> Self:
        # `account_one_default` is a partial unique index; a second default is a 23505.
        defaults = [account for account in self.accounts if account.is_default]
        if len(defaults) > 1:
            raise ValueError("only one account may be the default")
        return self


class CategoryOut(BaseModel):
    id: UUID
    name: str
    short_label: str | None
    icon: str
    colour: str
    kind: Kind
    default_bucket: Bucket | None
    flexibility: int | None
    template_key: str | None
    is_pinned: bool
    is_system: bool
    is_archived: bool
    parent_id: UUID | None
    created_at: datetime
    updated_at: datetime
    version: int


class AccountOut(BaseModel):
    id: UUID
    name: str
    type: AccountType
    icon: str | None
    is_default: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    version: int


class BudgetPeriodOut(BaseModel):
    id: UUID
    starts_on: date
    ends_on: date
    days_in_period: int
    expected_income_minor: int
    pct_needs: int
    pct_wants: int
    pct_future: int
    pct_debt: int
    carried_from_budget_period_id: UUID | None
    review_dismissed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class BudgetLimitOut(BaseModel):
    """`effective_limit_minor` is computed, not stored — there is no such column."""

    id: UUID
    category_id: UUID
    limit_minor: int
    carried_in_minor: int
    effective_limit_minor: int
    rollover: Rollover
    rollover_cap_minor: int | None
    version: int


class OnboardingCompleteOut(BaseModel):
    """Everything the client needs to render Home and the entry screen without a second
    call."""

    currency_code: str
    minor_unit: int
    profile: ProfileOut
    categories: list[CategoryOut]
    accounts: list[AccountOut]
    budget_period: BudgetPeriodOut
    budget_limits: list[BudgetLimitOut]
