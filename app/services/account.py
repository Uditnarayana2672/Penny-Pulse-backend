"""Account rules. No `fastapi`, no `sqlalchemy` — plain data in, plain data out.

The rule worth stating first is the one this module exists to hold the line on: an account is
a label (delta D3). There is no balance here, no total across accounts, and no arithmetic that
turns an opening amount plus some transactions into a current figure. That derivation is Phase
2, and doing it early would mean showing a number that drifts — which is the trust problem D3
was written to avoid.

What is here is the opening amount: one observation, with the date it was made, and a rule
about when it may still be changed.
"""

from datetime import date, datetime
from uuid import UUID

from app.schemas.account import (
    AccountArchiveOut,
    AccountCreate,
    AccountListOut,
    AccountOut,
    AccountPatch,
    OpeningBalanceOut,
)
from app.services.errors import DuplicateAccountNameError, OpeningBalanceLockedError
from app.services.me import RowDict, as_int, as_str_or_none
from app.services.onboarding import LIABILITY_ACCOUNT_TYPES

# `source` values `balance_anchor` accepts. An amount typed into the accounts screen is
# `opening` and nothing else — `manual` is the Phase-2 re-anchor, which carries an estimate
# and a gap, and `statement` and `notification` are imports that do not exist yet.
OPENING_SOURCE = "opening"


def normalise_name(name: str) -> str:
    """`lower(trim(name))`, matching what `account_unique_name` indexes.

    The column is NOT NULL with no default and no generated expression, so the application is
    the only thing that can produce it.
    """
    return name.strip().lower()


def is_liability_for(account_type: str) -> bool:
    """Derived from the type, never taken from the request.

    A credit card is a liability whatever a client claims, and `include_in_spendable` in Phase
    2 keys off this — so letting a body set it would let a wrong flag through into arithmetic
    nobody would re-check.
    """
    return account_type in LIABILITY_ACCOUNT_TYPES


def build_create_row(payload: AccountCreate, *, now: datetime) -> RowDict:
    return {
        "id": payload.id,
        "name": payload.name,
        "name_normalized": normalise_name(payload.name),
        "type": payload.type,
        "icon": payload.icon,
        "is_liability": is_liability_for(payload.type),
        "is_default": payload.is_default,
        "is_archived": False,
        "archived_at": None,
        "sort_order": None,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }


def build_patch_row(payload: AccountPatch, *, now: datetime) -> RowDict:
    """Only the keys the request sent, plus the bookkeeping every write owes.

    `opening_balance_minor` is deliberately absent: it is not a column on `account`. It becomes
    an anchor row, built by `build_opening_anchor_row`.

    `version` is never set here. `touch_row()` bumps it on every UPDATE, so an application
    value would either be overwritten or fight the trigger.
    """
    patch: RowDict = {"updated_at": now}

    if payload.name is not None:
        patch["name"] = payload.name
        patch["name_normalized"] = normalise_name(payload.name)
    if payload.type is not None:
        patch["type"] = payload.type
        # Re-derived rather than left stale: the two must agree or Phase 2 reads a liability
        # flag that contradicts the type it was derived from.
        patch["is_liability"] = is_liability_for(payload.type)
    if payload.is_default is not None:
        patch["is_default"] = payload.is_default
    if payload.icon is not None:
        patch["icon"] = payload.icon

    return patch


def require_unlocked_opening(transaction_count: int) -> None:
    """The opening amount may only change while nothing has been measured against it."""
    if transaction_count > 0:
        raise OpeningBalanceLockedError(transaction_count)


def build_opening_anchor_row(
    account_id: UUID, amount_minor: int, *, now: datetime, today_local: date
) -> RowDict:
    """One opening observation.

    `estimated_at_time_minor` and `gap_minor` stay NULL, which
    `anchor_gap_required_unless_opening` permits only because `is_opening` is true. Writing 0
    into them instead would report a gap equal to the whole balance and discredit capture rate
    on its first data point — the comment in 0005 says so, and it is the reason the column is
    nullable at all.

    `observed_on_local` is computed here from the profile's timezone, never by Postgres: a
    figure recorded at 1am IST belongs to that local day, and the database has no timezone to
    render it in.
    """
    return {
        "account_id": account_id,
        "amount_minor": amount_minor,
        "observed_at": now,
        "observed_on_local": today_local,
        "source": OPENING_SOURCE,
        "is_opening": True,
        "estimated_at_time_minor": None,
        "gap_minor": None,
        "adjustment_txn_id": None,
    }


def raise_for_outcome(outcome: str, *, name: str) -> None:
    if outcome == "duplicate_name":
        raise DuplicateAccountNameError(name)


def opening_out(row: RowDict) -> OpeningBalanceOut | None:
    """The opening amount from a joined list row, or None where the account has none.

    Absence is a state the screen renders as "not set". It is not zero, and collapsing the two
    would tell a user they have an empty account when they simply have not said.
    """
    if row.get("opening_amount_minor") is None:
        return None
    return OpeningBalanceOut(
        amount_minor=as_int(row["opening_amount_minor"], "opening_amount_minor"),
        observed_at=row["opening_observed_at"],
        observed_on_local=row["opening_observed_on_local"],
        is_locked=as_int(row["transaction_count"], "transaction_count") > 0,
    )


def item_out(row: RowDict) -> AccountOut:
    return AccountOut(
        id=row["id"],
        name=str(row["name"]),
        type=str(row["type"]),
        icon=as_str_or_none(row.get("icon")),
        is_liability=bool(row["is_liability"]),
        is_default=bool(row["is_default"]),
        is_archived=bool(row["is_archived"]),
        transaction_count=as_int(row["transaction_count"], "transaction_count"),
        opening_balance=opening_out(row),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=as_int(row["version"], "version"),
    )


def list_out(
    rows: list[RowDict], *, currency_code: str, minor_unit: int
) -> AccountListOut:
    """No total. Deliberately.

    Summing opening amounts across accounts would produce exactly the number D3 refuses — a
    net worth figure, stale from the moment the next transaction lands, that the app would then
    own the correctness of forever.
    """
    return AccountListOut(
        currency_code=currency_code,
        minor_unit=minor_unit,
        items=[item_out(row) for row in rows],
    )


def archive_out(row: RowDict) -> AccountArchiveOut:
    """Built from a re-read row, so `version` is what the trigger actually wrote.

    Computing `version + 1` here would be the client's own guess at what `touch_row()` did,
    and a guess is exactly what an optimistic-concurrency token must not be.
    """
    return AccountArchiveOut(
        id=row["id"],
        is_archived=bool(row["is_archived"]),
        version=as_int(row["version"], "version"),
    )
