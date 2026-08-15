"""All SQL for writing a transaction and reading back what it changed.

Nothing here decides anything: the service has already validated the payload and produced
exact column values. Every function filters by `user_id` from its own argument.

Reads go through the `*_live` views so soft-deleted rows are invisible without any query
repeating `deleted_at IS NULL`; writes go to the tables.
"""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.core import (
    BudgetLimitLive,
    BudgetPeriod,
    CategoryLive,
    Txn,
    TxnLive,
)
from app.models.habit import HabitLog, Streak

RowValue = str | int | bool | UUID | date | datetime | None
RowDict = dict[str, RowValue]

# The columns `services/txn.py::txn_out` reads. Selected explicitly rather than with `*` so
# that adding a column to `txn` cannot silently start shipping it to a client — several of
# them are internal by rule (`entry_duration_ms`, `edited_before_save`, `merchant_normalized`).
_TXN_COLUMNS = (
    TxnLive.id,
    TxnLive.category_id,
    TxnLive.account_id,
    TxnLive.budget_period_id,
    TxnLive.amount_minor,
    TxnLive.direction,
    TxnLive.bucket,
    TxnLive.occurred_at,
    TxnLive.created_at,
    TxnLive.updated_at,
    TxnLive.merchant,
    TxnLive.note,
    TxnLive.why,
    TxnLive.is_excluded,
    TxnLive.is_adjustment,
    TxnLive.transfer_peer_txn_id,
    TxnLive.source,
    TxnLive.version,
)


def get_txn(db: Session, user_id: UUID, txn_id: UUID) -> RowDict | None:
    """One transaction, or None if it does not exist or belongs to somebody else.

    Both answers are None on purpose: the service turns that into a 404, and distinguishing
    them would confirm the existence of another user's row.

    This is also the idempotency probe. A replayed POST is decided by a read here before any
    insert, so the second call returns the stored row rather than relying on
    `ON CONFLICT` having quietly done nothing.
    """
    stmt = select(*_TXN_COLUMNS).where(TxnLive.user_id == user_id, TxnLive.id == txn_id)
    row = db.execute(stmt).mappings().one_or_none()
    return None if row is None else dict(row)


def txn_id_exists_for_anyone(db: Session, txn_id: UUID) -> bool:
    """Whether this id is taken, ignoring who owns it.

    A deliberate exception to the every-function-filters-`user_id` rule, and the same one
    `repositories/onboarding.py::period_id_exists_for_anyone` makes for the same reason:
    `txn.id` is a global primary key, so a client-generated id already belonging to a
    stranger can never be inserted. Without this the caller cannot tell "replay mine" from
    "collides with somebody else's" and the second case writes nothing, then fails an
    assertion on the read-back — a 500 for what is really a conflict.

    Only the id is read, so nothing about the other user's transaction is exposed.
    """
    stmt = select(TxnLive.id).where(TxnLive.id == txn_id)
    return db.execute(stmt).scalar_one_or_none() is not None


def get_category_for_write(db: Session, user_id: UUID, category_id: UUID) -> RowDict | None:
    """The category a transaction is about to point at.

    `is_archived` comes back rather than being filtered out, because "archived" and "does
    not exist" are different answers to the client: 409 versus 404.
    """
    stmt = select(
        CategoryLive.id,
        CategoryLive.name,
        CategoryLive.icon,
        CategoryLive.colour,
        CategoryLive.kind,
        CategoryLive.default_bucket,
        CategoryLive.flexibility,
        CategoryLive.is_archived,
    ).where(CategoryLive.user_id == user_id, CategoryLive.id == category_id)

    row = db.execute(stmt).mappings().one_or_none()
    return None if row is None else dict(row)


def materialise_period(db: Session, user_id: UUID, period: RowDict) -> None:
    """Create a period a transaction needs, inside the caller's transaction.

    Periods are created lazily — the first write for a date outside every existing period
    creates it, in that same request. There is no background job.

    `id` is minted here because this row is derived rather than client-supplied: unlike the
    first period at onboarding, no client ever names it, so there is no idempotency key to
    honour and nothing for `ON CONFLICT (id)` to catch.

    The real arbiter is `period_no_overlap`, the gist exclusion constraint. Two devices
    posting into the same uncreated period at the same instant cannot both win: the loser's
    request fails and retries, then reads the winner's row.
    """
    db.execute(insert(BudgetPeriod).values({**period, "id": uuid4(), "user_id": user_id}))


def insert_txn(db: Session, user_id: UUID, txn: RowDict) -> None:
    """Write the transaction. No commit — the session dependency commits once.

    `user_id` is stamped from the argument rather than trusted from the row. This is the
    single point where a transaction is written, so it is the cheapest place to make a
    service bug unable to cross tenants.
    """
    db.execute(
        insert(Txn)
        .values({**txn, "user_id": user_id})
        .on_conflict_do_nothing(index_elements=["id"])
    )


def get_budget_limit(
    db: Session, user_id: UUID, budget_period_id: UUID, category_id: UUID
) -> RowDict | None:
    """The limit for one category in one period, if the user set one.

    None is an ordinary answer: a category with no limit is unbudgeted, not broken.
    """
    stmt = select(
        BudgetLimitLive.id,
        BudgetLimitLive.limit_minor,
        BudgetLimitLive.carried_in_minor,
    ).where(
        BudgetLimitLive.user_id == user_id,
        BudgetLimitLive.budget_period_id == budget_period_id,
        BudgetLimitLive.category_id == category_id,
    )

    row = db.execute(stmt).mappings().one_or_none()
    return None if row is None else dict(row)


def _spent_in_period(user_id: UUID, budget_period_id: UUID) -> Select[tuple[UUID, int]]:
    """Spend per category for one period, as a subquery.

    Membership is read from the STORED `budget_period_id`, never re-derived by comparing
    `occurred_at` against the period range. There is exactly one test for which period a
    transaction belongs to, and this is it.

    `is_excluded = FALSE` is the exclusion rule; `bucket = 'EXCLUDED'` is only a label.
    """
    return (
        select(
            TxnLive.category_id.label("category_id"),
            func.coalesce(func.sum(TxnLive.amount_minor), 0).label("spent_minor"),
        )
        .where(
            TxnLive.user_id == user_id,
            TxnLive.budget_period_id == budget_period_id,
            TxnLive.direction == "out",
            TxnLive.is_excluded.is_(False),
        )
        .group_by(TxnLive.category_id)
    )


def category_spent_minor(
    db: Session, user_id: UUID, budget_period_id: UUID, category_id: UUID
) -> int:
    """Total spent against one category in one period."""
    stmt = select(func.coalesce(func.sum(TxnLive.amount_minor), 0)).where(
        TxnLive.user_id == user_id,
        TxnLive.budget_period_id == budget_period_id,
        TxnLive.category_id == category_id,
        TxnLive.direction == "out",
        TxnLive.is_excluded.is_(False),
    )
    return int(db.execute(stmt).scalar_one())


def list_period_limits_with_spend(
    db: Session, user_id: UUID, budget_period_id: UUID
) -> list[RowDict]:
    """Every limit in the period, with its category's flexibility and its spend so far.

    One query rather than one per category: safe-to-spend sums across all of them, and a
    loop of round trips is how a sub-300ms save budget gets spent.

    The join is on `category_live`, so an archived-but-not-deleted category still
    contributes — its limit and its spend are both real.
    """
    spend = _spent_in_period(user_id, budget_period_id).subquery()

    stmt = (
        select(
            BudgetLimitLive.category_id,
            BudgetLimitLive.limit_minor,
            BudgetLimitLive.carried_in_minor,
            CategoryLive.flexibility,
            CategoryLive.default_bucket,
            func.coalesce(spend.c.spent_minor, 0).label("spent_minor"),
        )
        .join(CategoryLive, CategoryLive.id == BudgetLimitLive.category_id)
        .outerjoin(spend, spend.c.category_id == BudgetLimitLive.category_id)
        .where(
            BudgetLimitLive.user_id == user_id,
            BudgetLimitLive.budget_period_id == budget_period_id,
            CategoryLive.kind == "expense",
        )
    )
    return [dict(row) for row in db.execute(stmt).mappings()]


def habit_day_is_logged(db: Session, user_id: UUID, local_date: date) -> bool:
    """Whether the day already counted before this save.

    Read before the upsert, because `incremented` in the response means "this save is what
    turned today from unlogged to logged" and that is unanswerable afterwards.
    """
    stmt = select(HabitLog.logged).where(
        HabitLog.user_id == user_id, HabitLog.local_date == local_date
    )
    return db.execute(stmt).scalar_one_or_none() is True


def increment_habit_day(db: Session, user_id: UUID, local_date: date, now: datetime) -> None:
    """Count one manual transaction toward today's habit day.

    `logged` is a GENERATED column and is never written — `txn_count = txn_count + 1` is the
    natural write path and the whole reason it is generated rather than a CHECK.

    `zero_spend_confirmed` is forced back to false: a real transaction always wins over a
    "nothing today" claim (delta D6), and `habit_zero_spend_excludes_txns` would reject the
    increment otherwise.

    `freeze_used` is cleared for the same structural reason —
    `habit_freeze_only_on_empty_day` allows a freeze only on a day with no transactions. A
    late write for a day the rollover job already froze would fail the CHECK. Clearing the
    flag does not refund the freeze: the allowance lives in `streak.freezes_left`, which this
    does not touch. The spec does not cover this case at all.

    `id` is generated here rather than by the service, because the upsert is keyed on
    `(user_id, local_date)` and the id only ever matters on the insert branch — a service
    that minted one would be non-deterministic for no benefit.
    """
    stmt = insert(HabitLog).values(
        id=uuid4(),
        user_id=user_id,
        local_date=local_date,
        txn_count=1,
        zero_spend_confirmed=False,
        freeze_used=False,
        first_txn_at=now,
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "local_date"],
            set_={
                "txn_count": HabitLog.txn_count + 1,
                "zero_spend_confirmed": False,
                "freeze_used": False,
                # The first transaction of the day keeps its timestamp; later ones do not
                # move it. Phase 2 reads it to measure time-to-first-log.
                "first_txn_at": func.coalesce(HabitLog.first_txn_at, stmt.excluded.first_txn_at),
            },
        )
    )


def get_streak(db: Session, user_id: UUID) -> RowDict | None:
    """Streak counters, for the save confirmation to report.

    Read only. The counters are maintained by the nightly `streak_rollover` job; incrementing
    them here as well would double-count the day the job next ran.
    """
    stmt = select(
        Streak.current,
        Streak.longest,
        Streak.freezes_left,
        Streak.last_active_date,
    ).where(Streak.user_id == user_id)

    row = db.execute(stmt).mappings().one_or_none()
    return None if row is None else dict(row)
