"""Wire types for the Categories page — features 6.1 to 6.6.

One category shape, not four. Feature 6.1's item is the onboarding response's category plus
`total_transaction_count`, an optional `stats` block and the resolved icon, so create, edit
and list all return the same `CategoryItemOut` rather than three near-identical classes
(delta D7: two classes per resource, not four).

Archive and restore are the exception, and that is the spec's doing, not ours: 6.4 returns a
seven-field summary rather than a category. A client that assumed every category response
had the same shape would break on them, so they get their own names.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.onboarding import Bucket, Kind

# Only these two. There is no `sort=usage`, no descending form, and deliberately no manual
# ordering: "manual drag ordering is a chore users do once and never maintain" (spec 6.1
# decisions), and `category.sort_order` is annotated "reserved: manual ordering is rejected
# in P1".
CategorySort = Literal["name", "entry_order"]


class CategoryStatsOut(BaseModel):
    """Usage figures, returned only when `with_stats=true`.

    `usage_rank_30d` is null for a category with no transactions in the window — it is not
    ranked at all, and sorts after every ranked category.

    `last_txn_at` is a **spec delta**. It appears nowhere in `spec.txt`, the DDL or the ARD
    corpus, but the row on the Categories screen reads `1 txns · ₹9,300 · last 11 Aug`, and
    the client is forbidden from deriving a number the API did not send. All-time and
    non-deleted, matching `total_transaction_count` rather than the period-scoped figures
    beside it.
    """

    transaction_count: int
    spent_minor: int
    usage_rank_30d: int | None
    last_txn_at: datetime | None


class CategoryItemOut(BaseModel):
    """One category as every categories endpoint returns it.

    `total_transaction_count` is present whether or not stats were asked for, because it is
    what the client uses to choose between Delete and Archive on this row. Returning it
    unconditionally removes a probe request and the race that comes with one.

    It counts **non-deleted** transactions. The hard-delete guard counts differently, on
    purpose — see `CategoryNotEmptyError`.

    `resolved_render_value` is what to draw; `icon` is the stored token. Both are sent so
    the client needs no token-to-glyph table, which is the entire point of `0011`. An
    unknown token resolves rather than failing, so this field is never empty.

    `short_label` is in no version of the written spec — it exists only in the applied
    migration, commented "entry-grid tile text; falls back to name". The server now derives
    it on create; the client keeps its fallback for rows written before that.
    """

    id: UUID
    name: str
    short_label: str | None
    icon: str
    icon_pack_key: str
    resolved_render_value: str
    colour: str
    kind: Kind
    default_bucket: Bucket | None
    flexibility: int | None
    is_pinned: bool
    is_system: bool
    is_archived: bool
    parent_id: UUID | None
    template_key: str | None
    total_transaction_count: int
    created_at: datetime
    updated_at: datetime
    version: int
    stats: CategoryStatsOut | None


class CategoryListOut(BaseModel):
    """`currency_code` and `minor_unit` are at the root because `stats.spent_minor` is money.

    `next_cursor` is always null in Phase 1. Four users hold sixteen seeded categories and
    the default page size is fifty, so the second page is unreachable; implementing an opaque
    cursor over a usage-ranked ordering would be machinery with no caller. The field is here
    because the contract declares it, and a client that honours it will simply never loop.
    """

    currency_code: str
    minor_unit: int
    budget_period_id: UUID | None
    items: list[CategoryItemOut]
    next_cursor: str | None


class CategorySuggestionOut(BaseModel):
    """A preset this user has no category for yet.

    Carries everything `POST /categories` needs, because adding one is an ordinary create
    rather than a second write path — the client posts these values back with a fresh
    `uuidv7()`. That is what gives a tapped suggestion the same idempotency and the same
    near-duplicate handling as a hand-typed category.

    `resolved_render_value` is here for the same reason it is on `CategoryItemOut`: the client
    holds no token-to-glyph table, so a suggestion tile would otherwise have nothing to draw.
    """

    template_key: str
    name: str
    short_label: str
    icon: str
    icon_pack_key: str
    resolved_render_value: str
    colour: str
    default_bucket: Bucket | None
    flexibility: int | None


class CategorySuggestionListOut(BaseModel):
    """Not paginated: the whole preset set is ~24 rows and shrinks as they are added."""

    items: list[CategorySuggestionOut]


class CategoryCreate(BaseModel):
    """`id` is client-generated, which is what makes a replayed POST a replay (spec 2.1).

    `is_pinned` is absent on purpose: 6.2 accepts no pin field and every create returns
    `is_pinned: false`. Pinning is a PATCH, so the four-per-kind cap has exactly one path
    through it.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=60)
    icon: str = Field(min_length=1)
    colour: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    kind: Kind
    default_bucket: Bucket | None = None
    flexibility: int | None = Field(default=None, ge=1, le=5)
    short_label: str | None = Field(default=None, max_length=20)
    force: bool = False
    # Provenance, set when this category came from a preset rather than from the New button.
    # It is what makes the suggestion disappear afterwards — `GET /categories/suggestions`
    # subtracts the templates already spoken for, and matches on this column. Validated
    # against `category_template` in the service: `category.template_key` carries a foreign
    # key, and an unrecognised value would otherwise surface as a 23503 the user cannot act on.
    template_key: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("name", "short_label")
    @classmethod
    def a_name_is_more_than_whitespace(cls, value: str | None) -> str | None:
        # `name_normalized` is lower(trim(name)); a name of "   " normalises to "" and
        # would collide with the next one, so it is rejected at the edge where the message
        # can name the field.
        if value is None:
            return None
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class CategoryPatch(BaseModel):
    """Partial update. Only the keys present are changed (spec 2.1).

    `kind` is absent from this model and `extra="forbid"` rejects it, so an attempt to
    change it is a 422 naming the field rather than a silently ignored key. It is immutable
    because an expense category's transactions would become nonsense as income (spec 1348).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=60)
    icon: str | None = Field(default=None, min_length=1)
    colour: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    short_label: str | None = Field(default=None, max_length=20)
    default_bucket: Bucket | None = None
    flexibility: int | None = Field(default=None, ge=1, le=5)
    is_pinned: bool | None = None
    apply_to_past: bool = False
    force: bool = False

    @field_validator("name", "short_label")
    @classmethod
    def a_name_is_more_than_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class BucketChangeOut(BaseModel):
    """What a `default_bucket` change did to history, stated rather than implied.

    Present on every PATCH response even when no bucket changed, so the client reads one
    shape. `note` exists because "nothing happened to your past transactions" is the
    answer users most need and least expect.
    """

    applied_to_past: bool
    past_transactions_updated: int
    note: str


class CategoryUpdateOut(CategoryItemOut):
    bucket_change: BucketChangeOut


class CategoryArchiveOut(BaseModel):
    """6.4's response is a summary, not a category — the spec's shape, kept deliberately.

    `current_period_limit_retained` and `carried_to_next_period` are asserted rather than
    left for the client to assume, because the whole point of archive is that it does *not*
    move the current period's numbers.
    """

    id: UUID
    name: str
    is_archived: bool
    archived_at: datetime | None
    total_transaction_count: int
    current_period_limit_retained: bool
    carried_to_next_period: bool
    version: int


class CategoryRestoreOut(BaseModel):
    id: UUID
    name: str
    is_archived: bool
    archived_at: datetime | None
    total_transaction_count: int
    version: int


class MergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_category_id: UUID
    # The spec shows `true` in its example and describes the archive as unconditional in
    # both the summary and the prose, so `true` is the default. `false` leaves a live,
    # empty source, which is a real if unusual choice.
    archive_source: bool = True


class BudgetLimitsMergedOut(BaseModel):
    budget_period_id: UUID
    source_limit_minor: int
    target_limit_before_minor: int
    target_limit_after_minor: int


class MergeOut(BaseModel):
    """Reports exactly what moved, so the sheet states facts instead of guessing.

    `budget_limits_merged` is null when neither side held a current-period limit, which is
    the common case and the only possible one for an EXCLUDED or income category.
    """

    source_category_id: UUID
    target_category_id: UUID
    transactions_moved: int
    budget_limits_merged: BudgetLimitsMergedOut | None
    past_periods_untouched: bool
    source_archived: bool
    audit_log_id: UUID
