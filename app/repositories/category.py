"""SQL for the categories list.

Counting happens here; ranking and ordering happen in the service, so the ordering rule is
testable without a database.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.core import CategoryLive, TxnLive
from app.repositories.txn import summed_paise

RowValue = str | int | bool | UUID | date | datetime | None
RowDict = dict[str, RowValue]


def list_categories(
    db: Session,
    user_id: UUID,
    *,
    kind: str,
    include_archived: bool,
    usage_since: date,
    budget_period_id: UUID | None,
    limit: int,
) -> list[RowDict]:
    """Every category of one kind, with the counts the ordering and stats need.

    Three different counts, three different scopes, which is why they are three subqueries:

    * `total_transaction_count` — all time, unconditional. It decides Delete versus Archive.
    * `usage_count_30d` — the rolling window that drives `sort=entry_order`.
    * `period_transaction_count` / `period_spent_minor` — this period, for `stats`.

    The usage window is keyed on `created_on_local` and filters nothing but soft deletes.
    That is not a guess: `0002_core_money.sql` carries an index built for exactly this
    ordering —

        -- Entry-grid ordering: 30-day usage frequency per category.
        CREATE INDEX txn_category_recent
          ON txn (user_id, category_id, created_on_local DESC) WHERE deleted_at IS NULL;

    — while the streak index two lines above it *does* carry `AND source = 'manual'` and is
    commented "keyed on created, not occurred - it measures logging". The author drew the
    distinction deliberately, so this follows the entry-grid index and filters no direction,
    no `is_excluded` and no `source`. The written spec is silent on all three.
    """
    total_counts = (
        select(
            TxnLive.category_id.label("category_id"),
            func.count().label("total_count"),
        )
        .where(TxnLive.user_id == user_id)
        .group_by(TxnLive.category_id)
        .subquery()
    )

    usage_counts = (
        select(
            TxnLive.category_id.label("category_id"),
            func.count().label("usage_count"),
        )
        .where(TxnLive.user_id == user_id, TxnLive.created_on_local >= usage_since)
        .group_by(TxnLive.category_id)
        .subquery()
    )

    # `spent_minor` is scoped to the period, per the 6.1 computation contract. Only `out`
    # rows and only non-excluded ones count toward spend — `is_excluded` is the single
    # authoritative exclusion flag, and an income row is not spending.
    period_spend = (
        select(
            TxnLive.category_id.label("category_id"),
            func.count().label("period_count"),
            summed_paise(TxnLive.amount_minor).label("period_spent"),
        )
        .where(
            TxnLive.user_id == user_id,
            TxnLive.budget_period_id == budget_period_id,
            TxnLive.direction == "out",
            TxnLive.is_excluded.is_(False),
        )
        .group_by(TxnLive.category_id)
        .subquery()
    )

    stmt = (
        select(
            CategoryLive.id,
            CategoryLive.name,
            CategoryLive.short_label,
            CategoryLive.icon,
            CategoryLive.colour,
            CategoryLive.kind,
            CategoryLive.default_bucket,
            CategoryLive.flexibility,
            CategoryLive.is_pinned,
            CategoryLive.is_system,
            CategoryLive.is_archived,
            CategoryLive.parent_id,
            CategoryLive.template_key,
            CategoryLive.created_at,
            CategoryLive.updated_at,
            CategoryLive.version,
            func.coalesce(total_counts.c.total_count, 0).label("total_transaction_count"),
            func.coalesce(usage_counts.c.usage_count, 0).label("usage_count_30d"),
            func.coalesce(period_spend.c.period_count, 0).label("period_transaction_count"),
            func.coalesce(period_spend.c.period_spent, 0).label("period_spent_minor"),
        )
        .outerjoin(total_counts, total_counts.c.category_id == CategoryLive.id)
        .outerjoin(usage_counts, usage_counts.c.category_id == CategoryLive.id)
        .outerjoin(period_spend, period_spend.c.category_id == CategoryLive.id)
        .where(CategoryLive.user_id == user_id, CategoryLive.kind == kind)
        # Ordered by name here so the service's sort has a deterministic starting point; the
        # real ordering rule is applied there, where it can be tested without Postgres.
        .order_by(CategoryLive.name)
        .limit(limit)
    )

    if not include_archived:
        stmt = stmt.where(CategoryLive.is_archived.is_(False))

    return [dict(row) for row in db.execute(stmt).mappings()]
