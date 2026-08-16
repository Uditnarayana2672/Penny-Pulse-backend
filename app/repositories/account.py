"""Accounts, and the single opening anchor each one may carry.

Reads go through `account_live`; writes go to `account`. The opening amount lives in
`balance_anchor`, which has no `_live` view because its rows are immutable observations —
nothing soft-deletes them.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models.analytics import BalanceAnchor
from app.models.core import Account, AccountLive, Txn

RowValue = str | int | bool | UUID | date | datetime | None
RowDict = dict[str, RowValue]

WriteOutcome = Literal["ok", "duplicate_name"]


def _classify(error: Exception) -> WriteOutcome:
    """Map a refusal onto a domain outcome, or re-raise if it is a real fault.

    Matched on the **constraint name**, not merely on `23505`. Two partial unique indexes can
    raise it — `account_unique_name` and `account_one_default` — and treating both as a
    duplicate name reports "an account named X already exists" when what actually happened is
    that two rows claimed the default. That is a lie about the user's input, and it is the bug
    the first version of this file shipped.

    `account_one_default` is deliberately not translated: the router clears the previous default
    inside the same transaction before writing, so reaching it means that ordering broke. A 500
    with an id is the right answer to "this should be impossible", per the same reasoning as
    `repositories/category.py`.
    """
    if isinstance(error, IntegrityError) and getattr(error.orig, "sqlstate", None) == "23505":
        diagnostic = getattr(error.orig, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "account_unique_name":
            return "duplicate_name"
    raise error


# ---------- reads --------------------------------------------------------


def list_accounts(db: Session, user_id: UUID, *, include_archived: bool) -> list[RowDict]:
    """Every live account, with its transaction count and its opening anchor if it has one.

    One statement rather than a list query plus an N+1: with two or three accounts the join is
    cheaper than the round trips, and the count is what decides whether the opening amount is
    still editable.

    The transaction count reads the base `txn` table, not `txn_live`. A soft-deleted
    transaction still means spending was measured against this opening figure, so the anchor
    stays frozen — "has ever had a transaction" is the question, exactly as
    `category.count_transactions_ever` asks it.
    """
    transaction_count = (
        select(func.count())
        .select_from(Txn)
        .where(Txn.user_id == user_id, Txn.account_id == AccountLive.id)
        .correlate(AccountLive)
        .scalar_subquery()
    )

    stmt = (
        select(
            AccountLive.id,
            AccountLive.name,
            AccountLive.type,
            AccountLive.icon,
            AccountLive.is_liability,
            AccountLive.is_default,
            AccountLive.is_archived,
            AccountLive.created_at,
            AccountLive.updated_at,
            AccountLive.version,
            transaction_count.label("transaction_count"),
            BalanceAnchor.amount_minor.label("opening_amount_minor"),
            BalanceAnchor.observed_at.label("opening_observed_at"),
            BalanceAnchor.observed_on_local.label("opening_observed_on_local"),
        )
        .join(
            BalanceAnchor,
            (BalanceAnchor.account_id == AccountLive.id)
            & (BalanceAnchor.user_id == user_id)
            & BalanceAnchor.is_opening.is_(True),
            isouter=True,
        )
        .where(AccountLive.user_id == user_id)
        # Default first, then name: the default is the one the entry chip preselects, so it is
        # the row a user looks for. `sort_order` is nullable and unused in Phase 1.
        .order_by(AccountLive.is_default.desc(), AccountLive.name)
    )
    if not include_archived:
        stmt = stmt.where(AccountLive.is_archived.is_(False))

    return [dict(row) for row in db.execute(stmt).mappings()]


def get_account(db: Session, user_id: UUID, account_id: UUID) -> RowDict | None:
    stmt = select(
        AccountLive.id,
        AccountLive.name,
        AccountLive.type,
        AccountLive.is_default,
        AccountLive.is_archived,
        AccountLive.version,
    ).where(AccountLive.user_id == user_id, AccountLive.id == account_id)
    row = db.execute(stmt).mappings().first()
    return None if row is None else dict(row)


def count_transactions_ever(db: Session, user_id: UUID, account_id: UUID) -> int:
    """Soft-deleted rows included: spending was still measured against the opening figure."""
    stmt = (
        select(func.count())
        .select_from(Txn)
        .where(Txn.user_id == user_id, Txn.account_id == account_id)
    )
    return int(db.execute(stmt).scalar_one())


def get_opening_anchor(db: Session, user_id: UUID, account_id: UUID) -> RowDict | None:
    stmt = select(
        BalanceAnchor.id,
        BalanceAnchor.amount_minor,
        BalanceAnchor.observed_at,
        BalanceAnchor.observed_on_local,
    ).where(
        BalanceAnchor.user_id == user_id,
        BalanceAnchor.account_id == account_id,
        BalanceAnchor.is_opening.is_(True),
    )
    row = db.execute(stmt).mappings().first()
    return None if row is None else dict(row)


# ---------- writes -------------------------------------------------------


def insert_account(db: Session, user_id: UUID, row: RowDict) -> WriteOutcome:
    """`ON CONFLICT (id) DO NOTHING` — re-inserting the same id is a replay, not a clash."""
    try:
        db.execute(
            insert(Account)
            .values({**row, "user_id": user_id})
            .on_conflict_do_nothing(index_elements=["id"])
        )
    except (IntegrityError, DBAPIError) as error:
        return _classify(error)
    return "ok"


def update_account(
    db: Session, user_id: UUID, account_id: UUID, patch: RowDict
) -> WriteOutcome:
    try:
        db.execute(
            update(Account)
            .where(Account.user_id == user_id, Account.id == account_id)
            .values(**patch)
        )
    except (IntegrityError, DBAPIError) as error:
        return _classify(error)
    return "ok"


def clear_default(db: Session, user_id: UUID, *, except_id: UUID) -> None:
    """Unset every other default.

    `account_one_default` is a partial unique index, so two defaults is a 23505 rather than a
    last-write-wins. Clearing first inside the same transaction is what makes "make this the
    default" a single user-visible action instead of an error they have to resolve.
    """
    db.execute(
        update(Account)
        .where(
            Account.user_id == user_id,
            Account.id != except_id,
            Account.is_default.is_(True),
        )
        .values(is_default=False)
    )


def replace_opening_anchor(db: Session, user_id: UUID, account_id: UUID, row: RowDict) -> None:
    """Delete then insert, because `anchor_one_opening_per_account` allows exactly one.

    A delete-and-insert rather than an UPDATE keeps `created_at` honest about when the figure
    the user is looking at was actually recorded. Both statements land in the request's single
    transaction, so there is no window where the account has no opening anchor.

    The caller has already refused this when the account has transactions, which is what keeps
    the "immutable observation" rule intact: nothing that spending has been measured against is
    ever rewritten here.
    """
    db.execute(
        delete(BalanceAnchor).where(
            BalanceAnchor.user_id == user_id,
            BalanceAnchor.account_id == account_id,
            BalanceAnchor.is_opening.is_(True),
        )
    )
    db.execute(insert(BalanceAnchor).values({**row, "user_id": user_id}))


def set_archived(
    db: Session, user_id: UUID, account_id: UUID, *, archived: bool, now: datetime
) -> None:
    db.execute(
        update(Account)
        .where(Account.user_id == user_id, Account.id == account_id)
        .values(
            is_archived=archived,
            archived_at=now if archived else None,
            # An archived account must not stay the entry chip's default, and
            # `account_one_default` excludes archived rows anyway. Restoring does not hand the
            # default back: there may be one already, and two would be a 23505.
            is_default=False,
            updated_at=now,
        )
    )
    # No `version` here — `touch_row()` bumps it, and setting it too would fight the trigger.
