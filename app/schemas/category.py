"""Wire types for `GET /categories`.

One category shape, not four. Feature 6.1's item is the onboarding response's category plus
exactly two fields — `total_transaction_count` and an optional `stats` block — so this extends
that shape rather than declaring a second one (delta D7: two classes per resource, not four).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

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
    """

    transaction_count: int
    spent_minor: int
    usage_rank_30d: int | None


class CategoryItemOut(BaseModel):
    """One category as the categories list returns it.

    `total_transaction_count` is present whether or not stats were asked for, because it is
    what the client uses to choose between Delete and Archive on this row. Returning it
    unconditionally removes a probe request and the race that comes with one.

    `short_label` is in no version of the written spec — it exists only in the applied
    migration, commented "entry-grid tile text; falls back to name", and the migrations
    outrank the spec. The fallback is left to the client, because the spec never says the
    server resolves it.
    """

    id: UUID
    name: str
    short_label: str | None
    icon: str
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
