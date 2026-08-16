"""SQL for the Categories page.

Counting happens here; ranking, ordering and every rule happen in the service, so the
interesting logic is testable without a database.

**Constraint classification.** Two of this page's rules are enforced by the database and
cannot be pre-read without a race: `category_unique_name` and the `category_pin_limit()`
trigger. The brief requires catching them rather than checking first, and the layering makes
that awkward — a router may not import `sqlalchemy`, so it cannot catch `IntegrityError`,
and a repository may not import `app.services`, so it cannot raise the domain error. The
split: this layer catches and *classifies* into a plain string; the service decides what it
means. That is the same contract as "a missing row returns `None`; the service decides".

A classified failure always ends the request — the service raises immediately and nothing
else runs SQL. That matters because Postgres aborts the transaction on a constraint error,
so any statement after one would fail too. No `SAVEPOINT` is opened, because opening one
would mean `begin_nested()`, and this layer does not begin transactions.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, func, select, update
# The dialect `insert`, not `sqlalchemy.insert`: `on_conflict_do_nothing` is Postgres-specific
# and the generic construct has no such method. `repositories/onboarding.py` and
# `repositories/txn.py` both import it from here for the same reason.
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models.core import BudgetLimit, BudgetLimitLive, Category, CategoryLive, Txn, TxnLive
from app.models.habit import AuditLog
from app.models.reference import CategoryTemplate, SynonymGroup
from app.repositories.txn import summed_paise

RowValue = str | int | bool | UUID | date | datetime | None
RowDict = dict[str, RowValue]

# `unaccounted_out` and `unaccounted_in`, Phase-2 balance-anchor machinery rather than
# categories a person picks. Deliberately restated rather than imported from
# `repositories/onboarding.py`, which holds the same constant: two occurrences, and the house
# rule is three before extracting.
RESERVED_TEMPLATE_SORT_ORDER = 99

# What a write hit, in the vocabulary the service understands. `ok` means the statement
# landed; everything else is a constraint the database refused.
WriteOutcome = Literal["ok", "duplicate_name", "pin_limit_reached", "has_budget_limits"]

# `category_pin_limit()` and `category_stays_budgetable()` both `RAISE EXCEPTION` with a bare
# token and no ERRCODE, so they arrive as P0001 and must be matched on message text (0002).
_TRIGGER_OUTCOMES: dict[str, WriteOutcome] = {
    "PIN_LIMIT_REACHED": "pin_limit_reached",
    "CATEGORY_HAS_BUDGET_LIMITS": "has_budget_limits",
}


def _classify(error: Exception) -> WriteOutcome:
    """Map a database refusal onto a domain outcome, or re-raise if it is a real fault.

    Anything unrecognised is re-raised deliberately: swallowing an unknown constraint would
    turn a bug into a silent wrong answer, and the 500 handler already reports it with an id.
    """
    if isinstance(error, IntegrityError):
        if getattr(error.orig, "sqlstate", None) == "23505":
            return "duplicate_name"
        raise error

    message = str(getattr(error, "orig", error))
    for token, outcome in _TRIGGER_OUTCOMES.items():
        if token in message:
            return outcome
    raise error


# ---------- reads --------------------------------------------------------


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

    Four figures on three scopes, which is why they are three subqueries rather than three
    queries per row — stats for the whole list is one aggregate, not one per category.

    * `total_transaction_count` / `last_txn_at` — all time, non-deleted. The count decides
      Delete versus Archive; the timestamp is the row's "last 11 Aug".
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
    totals = (
        select(
            TxnLive.category_id.label("category_id"),
            func.count().label("total_count"),
            func.max(TxnLive.occurred_at).label("last_txn_at"),
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

    # `spent_minor` is scoped to the period, per the 6.1 computation contract, and period
    # membership is the STORED `budget_period_id` — never `occurred_at` compared against the
    # period's dates. Spec 2.1: "There is exactly one test in the system, and this is it."
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
            func.coalesce(totals.c.total_count, 0).label("total_transaction_count"),
            totals.c.last_txn_at.label("last_txn_at"),
            func.coalesce(usage_counts.c.usage_count, 0).label("usage_count_30d"),
            func.coalesce(period_spend.c.period_count, 0).label("period_transaction_count"),
            func.coalesce(period_spend.c.period_spent, 0).label("period_spent_minor"),
        )
        .outerjoin(totals, totals.c.category_id == CategoryLive.id)
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


def get_category(db: Session, user_id: UUID, category_id: UUID) -> RowDict | None:
    """One category with the two derived facts every write path needs.

    `has_live_budget_limit` answers the `category_stays_budgetable` trigger before it fires;
    `total_transaction_count` is the live count the response carries.
    """
    live_limit = (
        select(func.count())
        .select_from(BudgetLimitLive)
        .where(
            BudgetLimitLive.user_id == user_id,
            BudgetLimitLive.category_id == CategoryLive.id,
        )
        .scalar_subquery()
    )
    live_txns = (
        select(func.count())
        .select_from(TxnLive)
        .where(TxnLive.user_id == user_id, TxnLive.category_id == CategoryLive.id)
        .scalar_subquery()
    )

    stmt = select(
        CategoryLive.id,
        CategoryLive.name,
        CategoryLive.name_normalized,
        CategoryLive.short_label,
        CategoryLive.icon,
        CategoryLive.colour,
        CategoryLive.kind,
        CategoryLive.default_bucket,
        CategoryLive.flexibility,
        CategoryLive.is_pinned,
        CategoryLive.is_system,
        CategoryLive.is_archived,
        CategoryLive.archived_at,
        CategoryLive.parent_id,
        CategoryLive.template_key,
        CategoryLive.created_at,
        CategoryLive.updated_at,
        CategoryLive.version,
        live_txns.label("total_transaction_count"),
        (live_limit > 0).label("has_live_budget_limit"),
    ).where(CategoryLive.user_id == user_id, CategoryLive.id == category_id)

    row = db.execute(stmt).mappings().one_or_none()
    return None if row is None else dict(row)


def list_duplicate_candidates(
    db: Session, user_id: UUID, kind: str, *, exclude_id: UUID | None = None
) -> list[RowDict]:
    """Non-archived same-kind categories — the candidate set both duplicate tests use.

    Scoped exactly like `category_unique_name` (`WHERE deleted_at IS NULL AND is_archived =
    FALSE`), so the application test and the database backstop agree about what a duplicate
    is. An archived "Food" does not block a new one; restoring it later is where that
    collision surfaces, which is why 6.4 can return the same error.
    """
    total_txns = (
        select(func.count())
        .select_from(TxnLive)
        .where(TxnLive.user_id == user_id, TxnLive.category_id == CategoryLive.id)
        .scalar_subquery()
    )

    stmt = select(
        CategoryLive.id,
        CategoryLive.name,
        CategoryLive.name_normalized,
        total_txns.label("total_transaction_count"),
    ).where(
        CategoryLive.user_id == user_id,
        CategoryLive.kind == kind,
        CategoryLive.is_archived.is_(False),
    )
    if exclude_id is not None:
        stmt = stmt.where(CategoryLive.id != exclude_id)

    return [dict(row) for row in db.execute(stmt).mappings()]


def list_synonym_terms(db: Session) -> list[RowDict]:
    """Reference data with no tenant column — the documented `user_id`-free exception."""
    stmt = select(SynonymGroup.group_key, SynonymGroup.term)
    return [dict(row) for row in db.execute(stmt).mappings()]


def list_unused_templates(db: Session, user_id: UUID, *, kind: str) -> list[RowDict]:
    """Presets of one kind that this user has no category for.

    `NOT EXISTS` against `category_live`, not `category`, and the difference is the behaviour:
    an **archived** category is still live, so its preset stays out of the suggestions and the
    user restores it rather than being offered a second copy that would collide on
    `category_unique_name`. A **soft-deleted** one has left the view, so the preset comes back
    — which is the only way back after a delete.

    `sort_order < 99` drops `unaccounted_out` and `unaccounted_in`: they sit in the EXCLUDED
    bucket for Phase-2 balance anchors, and a category that can never hold a budget limit is
    not something to suggest.

    Starters are not excluded. A preset the user unticked during onboarding is exactly what
    this list is for — that is how an account with no Education gets one back.
    """
    stmt = (
        select(
            CategoryTemplate.template_key,
            CategoryTemplate.name,
            CategoryTemplate.short_label,
            CategoryTemplate.icon,
            CategoryTemplate.colour,
            CategoryTemplate.default_bucket,
            CategoryTemplate.flexibility,
        )
        .where(
            CategoryTemplate.kind == kind,
            CategoryTemplate.is_active.is_(True),
            CategoryTemplate.sort_order < RESERVED_TEMPLATE_SORT_ORDER,
            ~select(CategoryLive.id)
            .where(
                CategoryLive.user_id == user_id,
                CategoryLive.template_key == CategoryTemplate.template_key,
            )
            .exists(),
        )
        .order_by(CategoryTemplate.sort_order, CategoryTemplate.template_key)
    )
    return [dict(row) for row in db.execute(stmt).mappings()]


def template_exists(db: Session, template_key: str) -> bool:
    """Reference data with no tenant column — the documented `user_id`-free exception.

    A primary-key lookup, and only made when a create actually carries a `template_key`.
    `category.template_key` has a foreign key, so the alternative is a 23503 arriving at
    `_classify`, which re-raises anything it does not recognise and would answer 500 to what
    is really a bad request.
    """
    stmt = select(CategoryTemplate.template_key).where(
        CategoryTemplate.template_key == template_key
    )
    return db.execute(stmt).first() is not None


def count_transactions_ever(db: Session, user_id: UUID, category_id: UUID) -> int:
    """Every `txn` row that has ever pointed here, **soft-deleted ones included**.

    Reads the base table rather than `txn_live` on purpose, and it is the only read on this
    page that does. `txn.category_id` carries no `ON DELETE` clause, so a soft-deleted row
    still physically references the category: hard-deleting past it raises 23503. "Never
    held a transaction" has to mean ever.
    """
    stmt = (
        select(func.count())
        .select_from(Txn)
        .where(Txn.user_id == user_id, Txn.category_id == category_id)
    )
    return int(db.execute(stmt).scalar_one())


def count_limits_outside_period(
    db: Session, user_id: UUID, category_id: UUID, current_period_id: UUID | None
) -> int:
    """Live budget limits belonging to any period other than the current one.

    Blocks the hard delete. `budget_limit.category_id` is ON DELETE RESTRICT because
    cascading it "silently erases the allocated total of every closed period" (migrations
    README), and deleting the rows by hand would do the same damage the FK choice prevents.
    """
    stmt = (
        select(func.count())
        .select_from(BudgetLimitLive)
        .where(
            BudgetLimitLive.user_id == user_id,
            BudgetLimitLive.category_id == category_id,
        )
    )
    if current_period_id is not None:
        stmt = stmt.where(BudgetLimitLive.budget_period_id != current_period_id)
    return int(db.execute(stmt).scalar_one())


def get_limit(
    db: Session, user_id: UUID, category_id: UUID, budget_period_id: UUID
) -> RowDict | None:
    stmt = select(
        BudgetLimitLive.id,
        BudgetLimitLive.budget_period_id,
        BudgetLimitLive.category_id,
        BudgetLimitLive.limit_minor,
    ).where(
        BudgetLimitLive.user_id == user_id,
        BudgetLimitLive.category_id == category_id,
        BudgetLimitLive.budget_period_id == budget_period_id,
    )
    row = db.execute(stmt).mappings().one_or_none()
    return None if row is None else dict(row)


# ---------- writes -------------------------------------------------------


def insert_category(db: Session, user_id: UUID, row: RowDict) -> WriteOutcome:
    """`ON CONFLICT (id) DO NOTHING` — re-inserting the same id is a replay, not a clash."""
    try:
        db.execute(
            insert(Category)
            .values({**row, "user_id": user_id})
            .on_conflict_do_nothing(index_elements=["id"])
        )
    except (IntegrityError, DBAPIError) as error:
        return _classify(error)
    return "ok"


def update_category(
    db: Session, user_id: UUID, category_id: UUID, patch: RowDict
) -> WriteOutcome:
    try:
        db.execute(
            update(Category)
            .where(Category.user_id == user_id, Category.id == category_id)
            .values(**patch)
        )
    except (IntegrityError, DBAPIError) as error:
        return _classify(error)
    return "ok"


def set_archived(
    db: Session, user_id: UUID, category_id: UUID, *, archived: bool, now: datetime
) -> WriteOutcome:
    """Archive and restore are the same statement with different values.

    `is_archived` is part of `category_unique_name`'s partial predicate, so a restore can
    collide with a name created while this one was away — which is the 409 the spec lists on
    restore and never explains.
    """
    try:
        db.execute(
            update(Category)
            .where(Category.user_id == user_id, Category.id == category_id)
            .values(
                is_archived=archived,
                archived_at=now if archived else None,
                updated_at=now,
            )
        )
    except (IntegrityError, DBAPIError) as error:
        return _classify(error)
    return "ok"


def rewrite_buckets(db: Session, user_id: UUID, category_id: UUID, bucket: str) -> int:
    """`apply_to_past`: set `bucket` on every non-deleted transaction in this category.

    Scoped to non-deleted rows (spec 1345). No period floor, which the spec does not give
    one — see the note in PLAN.md; this rewrites closed months and that is the documented
    behaviour of the opt-in.
    """
    result = db.execute(
        update(Txn)
        .where(
            Txn.user_id == user_id,
            Txn.category_id == category_id,
            Txn.deleted_at.is_(None),
        )
        .values(bucket=bucket)
    )
    return int(result.rowcount or 0)


def move_transactions(db: Session, user_id: UUID, source_id: UUID, target_id: UUID) -> int:
    """Reassign every non-deleted transaction from source to target.

    **`bucket` is not in the SET clause and must never be.** The transaction owns its bucket
    (spec 1419); rewriting it here would silently restate every historical Needs/Wants split
    the user has already seen. This is the single most likely rule to be "helpfully" broken,
    and `tests/test_category_merge.py` asserts it directly.
    """
    result = db.execute(
        update(Txn)
        .where(
            Txn.user_id == user_id,
            Txn.category_id == source_id,
            Txn.deleted_at.is_(None),
        )
        .values(category_id=target_id)
    )
    return int(result.rowcount or 0)


def reassign_limit(db: Session, user_id: UUID, limit_id: UUID, target_id: UUID) -> None:
    """Repoint the source's limit row at the target — target held none.

    Reassigned rather than copied so `carried_in_minor`, `rollover` and `rollover_cap_minor`
    stay attached to the money they describe.
    """
    db.execute(
        update(BudgetLimit)
        .where(BudgetLimit.user_id == user_id, BudgetLimit.id == limit_id)
        .values(category_id=target_id)
    )


def sum_limit_into_target(
    db: Session, user_id: UUID, *, source_limit_id: UUID, target_limit_id: UUID, total: int
) -> None:
    """Target already had a limit: add the amounts and soft-delete the source's row.

    Both statements are required together — `budget_limit_one_per_category_per_period` is a
    partial unique index over live rows, so two live limits for one category in one period
    is a constraint violation, not merely untidy.
    """
    db.execute(
        update(BudgetLimit)
        .where(BudgetLimit.user_id == user_id, BudgetLimit.id == target_limit_id)
        .values(limit_minor=total)
    )
    db.execute(
        update(BudgetLimit)
        .where(BudgetLimit.user_id == user_id, BudgetLimit.id == source_limit_id)
        .values(deleted_at=func.now())
    )


def set_merged_into(db: Session, user_id: UUID, source_id: UUID, target_id: UUID) -> None:
    """Provenance. Without it a merged category is indistinguishable from an archived one."""
    db.execute(
        update(Category)
        .where(Category.user_id == user_id, Category.id == source_id)
        .values(merged_into_category_id=target_id)
    )


def delete_current_period_limits(
    db: Session, user_id: UUID, category_id: UUID, current_period_id: UUID | None
) -> None:
    """Hard-delete the current period's limits so the category itself can go.

    Only the current period, and only after `count_limits_outside_period` has confirmed
    there are no others. `ON DELETE RESTRICT` means this has to happen explicitly; the guard
    is what keeps it from touching a closed month.
    """
    stmt = delete(BudgetLimit).where(
        BudgetLimit.user_id == user_id, BudgetLimit.category_id == category_id
    )
    if current_period_id is not None:
        stmt = stmt.where(BudgetLimit.budget_period_id == current_period_id)
    db.execute(stmt)


def delete_category(db: Session, user_id: UUID, category_id: UUID) -> None:
    """The only hard delete in the system (spec 2.1)."""
    db.execute(
        delete(Category).where(Category.user_id == user_id, Category.id == category_id)
    )


def insert_audit_log(
    db: Session,
    user_id: UUID,
    *,
    entity_id: UUID,
    action: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    row_count: int,
) -> UUID:
    """One row, and the id comes back because the merge response reports it.

    `audit_log.id` is the one primary key in the schema with a database default, so this is
    the one insert that does not supply one.
    """
    result = db.execute(
        insert(AuditLog)
        .values(
            user_id=user_id,
            entity_type="category",
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
            row_count=row_count,
        )
        .returning(AuditLog.id)
    )
    return UUID(str(result.scalar_one()))
