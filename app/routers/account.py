"""Accounts — the Settings screen behind the entry chip.

An account is a label (delta D3), and the endpoints here are deliberately unexciting: list,
create, edit, archive, restore. The one thing that needed a decision is the opening amount,
which is not a column on `account` and never will be. It is written as a `balance_anchor` row
with `is_opening`, and this router refuses to change it once the account has transactions —
past that point the honest correction is a Phase-2 reconciliation that posts the difference as
an adjustment, not a rewrite of what the user originally observed.

Nothing here returns a current balance or a total across accounts. Both would be the number D3
refuses.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.auth import DbSession, UserId
from app.repositories import account as account_repo
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.schemas.account import (
    AccountArchiveOut,
    AccountCreate,
    AccountListOut,
    AccountOut,
    AccountPatch,
)
from app.services import account as account_service
from app.services import me as me_service
from app.services.errors import AccountNotFoundError, OnboardingRequiredError

router = APIRouter(tags=["accounts"])


def _require_profile(db: DbSession, user_id: UUID):
    profile = profile_repo.get_profile(db, user_id)
    if profile is None:
        raise OnboardingRequiredError()
    return profile


def _require_account(db: DbSession, user_id: UUID, account_id: UUID):
    account = account_repo.get_account(db, user_id, account_id)
    if account is None:
        # 404 for another user's row too — a 403 would confirm it exists (api.md).
        raise AccountNotFoundError()
    return account


def _one(db: DbSession, user_id: UUID, account_id: UUID) -> AccountOut:
    """Re-read through the list query, so a response is never assembled from a guess.

    The list row is the only shape that carries `transaction_count` and the joined opening
    anchor, and those two decide what the screen renders. Re-reading also means `version` is
    whatever `touch_row()` wrote rather than the application's arithmetic.
    """
    rows = account_repo.list_accounts(db, user_id, include_archived=True)
    row = next((candidate for candidate in rows if candidate["id"] == account_id), None)
    if row is None:
        raise AccountNotFoundError()
    return account_service.item_out(row)


def _apply_opening_balance(
    db: DbSession, user_id: UUID, account_id: UUID, amount_minor: int | None, profile
) -> None:
    """Write or replace the opening observation, if the request carried one.

    Refused once the account has transactions: a `balance_anchor` row is an immutable
    observation, and the schema agrees — `anchor_one_opening_per_account` allows one opening
    anchor and `anchor_gap_required_unless_opening` demands an estimate and a gap for any
    later one.
    """
    if amount_minor is None:
        return

    account_service.require_unlocked_opening(
        account_repo.count_transactions_ever(db, user_id, account_id)
    )

    now = datetime.now(UTC)
    account_repo.replace_opening_anchor(
        db,
        user_id,
        account_id,
        account_service.build_opening_anchor_row(
            account_id,
            amount_minor,
            now=now,
            # The local day comes from the profile's timezone, never from the database: an
            # amount recorded at 1am IST belongs to that local date.
            today_local=me_service.local_today_for(profile, now),
        ),
    )


@router.get("/accounts", response_model=AccountListOut)
def list_accounts(
    user_id: UserId,
    db: DbSession,
    include_archived: bool = False,
) -> AccountListOut:
    """Every account, each with its opening amount and the date it was observed.

    `include_archived` defaults to false because the entry chip wants only what a user can
    spend from; the Settings screen asks for true so its counts are true.
    """
    profile = _require_profile(db, user_id)
    currency_code = me_service.currency_code_for(profile)

    return account_service.list_out(
        account_repo.list_accounts(db, user_id, include_archived=include_archived),
        currency_code=currency_code,
        minor_unit=me_service.require_minor_unit(
            currency_code, onboarding_repo.currency_minor_unit(db, currency_code)
        ),
    )


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    payload: AccountCreate,
    user_id: UserId,
    db: DbSession,
    response: Response,
) -> AccountOut:
    """Create, optionally with the opening amount in the same request."""
    profile = _require_profile(db, user_id)

    existing = account_repo.get_account(db, user_id, payload.id)
    if existing is not None:
        # Replay of a queued POST. Returning the stored row is what makes an offline retry
        # safe (spec 2.1) — a fresh 409 would strand the client.
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
        return _one(db, user_id, payload.id)

    if payload.is_default:
        # Before the insert, not after. `account_one_default` is a partial unique index, so a
        # second row claiming the default is refused at write time — the demotion has to already
        # have happened. Both statements are in the request's one transaction, so there is no
        # moment where the user has no default.
        account_repo.clear_default(db, user_id, except_id=payload.id)

    outcome = account_repo.insert_account(
        db, user_id, account_service.build_create_row(payload, now=datetime.now(UTC))
    )
    account_service.raise_for_outcome(outcome, name=payload.name)

    _apply_opening_balance(db, user_id, payload.id, payload.opening_balance_minor, profile)

    return _one(db, user_id, payload.id)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: UUID,
    payload: AccountPatch,
    user_id: UserId,
    db: DbSession,
) -> AccountOut:
    """Rename, retype, make default, or set the opening amount while it is still unlocked."""
    profile = _require_profile(db, user_id)
    _require_account(db, user_id, account_id)

    if payload.is_default is True:
        # Before the update, for the same reason as create: `account_one_default` refuses a
        # second default at write time, so the previous holder is demoted first.
        account_repo.clear_default(db, user_id, except_id=account_id)

    patch = account_service.build_patch_row(payload, now=datetime.now(UTC))
    # `updated_at` is always present, so a body of `{}` would still bump the version for no
    # change. Only write when something the user asked for is in it.
    if len(patch) > 1:
        outcome = account_repo.update_account(db, user_id, account_id, patch)
        account_service.raise_for_outcome(outcome, name=str(patch.get("name", "")))

    _apply_opening_balance(db, user_id, account_id, payload.opening_balance_minor, profile)

    return _one(db, user_id, account_id)


@router.post("/accounts/{account_id}/archive", response_model=AccountArchiveOut)
def archive_account(
    account_id: UUID, user_id: UserId, db: DbSession
) -> AccountArchiveOut:
    """Hide from the pickers, keep every row.

    Archive rather than delete, always: `txn.account_id` has no `ON DELETE` clause, so a hard
    delete past a transaction would raise 23503 — and the history of where money went out from
    is worth more than a tidy list.
    """
    _require_profile(db, user_id)
    _require_account(db, user_id, account_id)

    account_repo.set_archived(
        db, user_id, account_id, archived=True, now=datetime.now(UTC)
    )

    return account_service.archive_out(_require_account(db, user_id, account_id))


@router.post("/accounts/{account_id}/restore", response_model=AccountArchiveOut)
def restore_account(
    account_id: UUID, user_id: UserId, db: DbSession
) -> AccountArchiveOut:
    """Back into the pickers. The default is not handed back — there may be one already."""
    _require_profile(db, user_id)
    _require_account(db, user_id, account_id)

    account_repo.set_archived(
        db, user_id, account_id, archived=False, now=datetime.now(UTC)
    )

    return account_service.archive_out(_require_account(db, user_id, account_id))
