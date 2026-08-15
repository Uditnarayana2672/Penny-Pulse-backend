"""Wire types for `POST /transactions` — the single most important write in the system.

Only four fields are required: `id`, `direction`, `amount_minor`, `category_id`. Everything
else defaults server-side or hides behind the entry screen's "+ details" affordance, because
the target is under five seconds from tapping + to a saved transaction.

Validators here check shape, type and range only. "Does this category exist", "does its kind
match the direction", "which period does this date fall in" all need another row and are
therefore service rules.
"""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.onboarding import Bucket, Kind

# `transfer` exists in the database CHECK but is Phase 2: it needs two rows, two accounts
# and a `transfer_role`, so it is not reachable through this endpoint.
Direction = Literal["in", "out"]

Why = Literal["planned", "impulse", "social", "emergency"]

Source = Literal[
    "manual", "voice", "nl_text", "recurring", "inbox", "import", "adjustment"
]

# `source` is provenance — where the row came from. `entry_method` is which UI path the
# user took. They answer different questions and both are needed to measure the §5 gate.
EntryMethod = Literal[
    "keypad", "quick_add", "voice", "nl_text", "recurring_confirm", "inbox_accept", "import"
]

RowStyle = Literal["normal", "transfer", "adjustment", "recurring"]


class TxnCreate(BaseModel):
    """A new transaction. `id` is client-generated, which is what makes a replay safe.

    `extra="forbid"` so a client sending a field this endpoint does not accept — an
    `is_refund`, a `transfer_role`, a `user_id` — is told, rather than having it silently
    dropped. `user_id` in particular is never an input anywhere in this API.

    `occurred_at` is the one field a user controls that would otherwise be server-owned:
    absent means now, and a past date is a legitimate backfill.

    `entry_method`, `entry_duration_ms` and `edited_before_save` are accepted here and never
    returned. They exist because the §5 validation gate — median entry time under ten
    seconds — decides whether Phase 2 happens at all, and nothing else in the schema can
    measure it. `rules/api.md` bans them from *responses*, which is a different thing.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    direction: Direction
    amount_minor: int = Field(gt=0)
    category_id: UUID

    occurred_at: AwareDatetime | None = None
    bucket: Bucket | None = None
    why: Why | None = None
    note: str | None = None
    merchant: str | None = None
    source: Source = "manual"

    # Account is a label in v1 (delta D3) — a chip on the entry screen, no balance behind
    # it. The client sends the one it is showing; the server does not guess a last-used.
    account_id: UUID | None = None

    entry_method: EntryMethod | None = None
    entry_duration_ms: int | None = Field(default=None, ge=0)
    edited_before_save: bool = False

    @model_validator(mode="after")
    def income_carries_no_bucket(self) -> Self:
        # Mirrors `txn_bucket_matches_direction`. Buckets classify spending; an income row
        # has nothing to classify. Without this the CHECK answers with a 23514 instead.
        if self.direction == "in" and self.bucket is not None:
            raise ValueError("bucket must be omitted when direction is 'in'")
        return self

    @model_validator(mode="after")
    def why_belongs_to_spending(self) -> Self:
        # Mirrors `txn_why_only_on_spend`. Feature 3.5 says the API accepts `why` on any
        # transaction, but the applied migration constrains it to `direction = 'out'` and
        # the migrations win — so this is refused at the edge with a named field rather
        # than reaching the database as a check violation.
        if self.why is not None and self.direction != "out":
            raise ValueError("why applies only to spending, where direction is 'out'")
        return self


class TxnCategoryOut(BaseModel):
    """A category is always a nested object, never loose fields beside a `category_id`."""

    id: UUID
    name: str
    icon: str
    colour: str
    kind: Kind
    default_bucket: Bucket | None


class TxnOut(BaseModel):
    """One transaction as a client may see it.

    `row_style` is computed, not stored. In Phase 1 none of the three non-normal conditions
    can occur, so it is always `"normal"` — it is derived rather than hardcoded so that
    transfers and recurring rows need no contract change when they arrive.

    `occurred_on_local` and `created_on_local` are stored and deliberately not returned:
    they are an indexing decision, not information a screen needs.

    `budget_period_id` and `version` travel on every transaction representation, so a client
    never needs a second fetch before it can issue a conditional update.
    """

    id: UUID
    direction: Direction
    amount_minor: int
    category: TxnCategoryOut
    bucket: Bucket | None
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime
    why: Why | None
    note: str | None
    merchant: str | None
    source: Source
    is_excluded: bool
    row_style: RowStyle
    budget_period_id: UUID | None
    version: int


class CategoryImpactOut(BaseModel):
    """What this save did to the category's limit for this period.

    `spent_pct` and `pace_pct` are percentages to one decimal place, not paise.
    `spent_pct` is null when the effective limit is zero — there is no percentage of nothing.

    `remaining_minor` may be negative, and the client must render that case rather than
    clamping it: an over-budget category the UI shows as zero-remaining is a lie.
    """

    effective_limit_minor: int
    spent_minor: int
    remaining_minor: int
    spent_pct: float | None
    pace_pct: float
    pace_status: Literal["breached", "over_pace", "under_pace", "on_pace"]
    breached: bool


class SafeToSpendOut(BaseModel):
    """The single number the entry screen shows after a save.

    Only `displayed_minor` here. Home carries the whole block — uncapped, the cap, the
    baseline — because Home explains the number; the save confirmation only states it.
    """

    displayed_minor: int


class BudgetImpactOut(BaseModel):
    """Enough to update the category row and the safe-to-spend figure without a second call.

    `category` is null when there is nothing to report against a limit: an income row has
    no limit by definition, and an expense in a category with no limit in this period has
    none either. The spec shows only the expense-with-a-limit case and never says what the
    other two look like; null is the honest answer, and it keeps the client from rendering
    a remaining figure derived from a limit that does not exist.
    """

    budget_period_id: UUID
    category: CategoryImpactOut | None
    safe_to_spend: SafeToSpendOut


class HabitOut(BaseModel):
    """Streak state as of this save.

    `current` and `longest` use the same names Home uses — one concept, one name.

    `incremented` is whether *this* save is what turned today from unlogged to logged. It is
    false on a second transaction the same day, and false on a replay, so the client can
    animate the streak exactly once.
    """

    current: int
    longest: int
    logged_today: bool
    incremented: bool


class TxnCreateOut(BaseModel):
    """The saved row plus the two blocks that make the sub-300ms confirmation possible."""

    currency_code: str
    minor_unit: int
    transaction: TxnOut
    budget_impact: BudgetImpactOut
    habit: HabitOut
