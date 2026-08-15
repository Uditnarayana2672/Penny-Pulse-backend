"""`POST /transactions` — the write the whole app is built around.

The response carries `budget_impact` and `habit` so the entry screen can show the updated
category remaining figure and the incremented streak without a second round trip. That is
what makes a sub-300ms confirmation possible.

No arithmetic here and no SQL: the router reads rows, hands them to the service, writes what
comes back, and reads the result. Everything below happens in one transaction, committed once
by the session dependency — the transaction, the period it may have had to materialise, and
the habit day are only correct together.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Response

from app.auth import DbSession, UserId
from app.repositories import onboarding as onboarding_repo
from app.repositories import profile as profile_repo
from app.repositories import txn as txn_repo
from app.repositories.txn import RowDict
from app.schemas.txn import TxnCreate, TxnCreateOut
from app.services import me as me_service
from app.services import txn as txn_service
from app.services.errors import OnboardingRequiredError, RuleViolationError

router = APIRouter(tags=["transactions"])

# Only a manual entry earns a habit day. A Phase 2 recurring row posts itself, and a streak
# that ticks without the user doing anything measures nothing.
HABIT_EARNING_SOURCE = "manual"


@router.post("/transactions", response_model=TxnCreateOut, status_code=201)
def create_transaction(
    payload: TxnCreate,
    user_id: UserId,
    db: DbSession,
    response: Response,
) -> TxnCreateOut:
    """Create an expense or an income row. Idempotent on the client-supplied `id`.

    Replay is decided by a read before any insert, the same shape
    `POST /onboarding/complete` uses and for the same reason: `ON CONFLICT (id) DO NOTHING`
    guards only the primary key, so every other outcome has to be recognised beforehand
    rather than inferred from a write that quietly did nothing.

    `budget_impact` and `habit` are recomputed on a replay rather than stored and replayed.
    The spec says "the identical body is returned", which would require storing every
    response; recomputing gives the client the truth as of now, which is what it is about to
    render anyway.
    """
    profile = profile_repo.get_profile(db, user_id)
    if profile is None:
        # No profile means no timezone and no salary day, so there is nothing to derive
        # period membership or a habit day from.
        raise OnboardingRequiredError()

    now = datetime.now(UTC)
    timezone = str(profile["timezone"])
    local_today = me_service.local_today_for(profile, now)

    existing = txn_repo.get_txn(db, user_id, payload.id)
    if existing is not None:
        stored, category, period = _replayed(db, user_id, existing)
        newly_logged = False
        # `api.md` requires 200 with the existing row rather than a conflict, which is what
        # makes a queued POST safe to retry over a flaky connection. Nothing is written, so
        # the habit day cannot tick twice for one transaction.
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    else:
        stored, category, period, newly_logged = _created(
            db, user_id, payload, profile, now, timezone
        )

    window = txn_service.window_from_period(period, local_today)
    category_id = me_service.as_uuid(stored["category_id"], "txn.category_id")
    currency_code = me_service.currency_code_for(profile)

    return txn_service.create_out(
        currency_code=currency_code,
        minor_unit=me_service.require_minor_unit(
            currency_code, onboarding_repo.currency_minor_unit(db, currency_code)
        ),
        txn=stored,
        category=category,
        window=window,
        limit=txn_repo.get_budget_limit(db, user_id, window.id, category_id),
        category_spent_minor=txn_repo.category_spent_minor(
            db, user_id, window.id, category_id
        ),
        flexible_limits=txn_repo.list_period_limits_with_spend(db, user_id, window.id),
        streak=txn_repo.get_streak(db, user_id),
        newly_logged=newly_logged,
    )


def _replayed(
    db: DbSession, user_id: UUID, existing: RowDict
) -> tuple[RowDict, RowDict, RowDict]:
    """A transaction that was already written. Read it back; validate nothing.

    Re-running the create-time checks here would be wrong: a category archived *after* the
    original write would turn a replay into a 409, so a client retrying a queued POST would
    be told its already-saved transaction is invalid.
    """
    period_id = me_service.as_uuid(existing["budget_period_id"], "txn.budget_period_id")
    period = onboarding_repo.get_budget_period(db, user_id, period_id)
    category = txn_repo.get_category_for_write(
        db, user_id, me_service.as_uuid(existing["category_id"], "txn.category_id")
    )
    if period is None or category is None:
        raise AssertionError("a stored transaction's period and category must both exist")
    return existing, category, period


def _created(
    db: DbSession,
    user_id: UUID,
    payload: TxnCreate,
    profile: RowDict,
    now: datetime,
    timezone: str,
) -> tuple[RowDict, RowDict, RowDict, bool]:
    """Validate, materialise a period if the date needs one, write, and count the day."""
    if txn_repo.txn_id_exists_for_anyone(db, payload.id):
        # Someone else's id. Not a 409 and not a 404: the client's recovery is to mint a
        # fresh UUIDv7 and retry. Nothing confirms whose id it is.
        raise RuleViolationError(
            "Transaction id is already in use. Generate a new one and retry.", field="id"
        )

    category = txn_service.check_category_is_usable(
        txn_repo.get_category_for_write(db, user_id, payload.category_id), payload.direction
    )

    occurred_at = payload.occurred_at or now
    occurred_on_local, created_on_local = txn_service.local_dates(occurred_at, now, timezone)
    period = _period_covering(db, user_id, profile, occurred_on_local)

    txn_repo.insert_txn(
        db,
        user_id,
        txn_service.build_txn_row(
            payload,
            user_id,
            category,
            me_service.as_uuid(period["id"], "budget_period.id"),
            occurred_at,
            now,
            timezone,
        ),
    )

    newly_logged = False
    if payload.source == HABIT_EARNING_SOURCE:
        # Read before the write: "did this save turn today from unlogged to logged" is
        # unanswerable once the row has been incremented.
        newly_logged = not txn_repo.habit_day_is_logged(db, user_id, created_on_local)
        txn_repo.increment_habit_day(db, user_id, created_on_local, now)

    stored = txn_repo.get_txn(db, user_id, payload.id)
    if stored is None:
        raise AssertionError("the transaction just written is not readable")
    return stored, category, period, newly_logged


def _period_covering(
    db: DbSession, user_id: UUID, profile: RowDict, occurred_on_local: date
) -> RowDict:
    """The period this date belongs to, creating it first if it does not exist yet.

    Periods are materialised lazily and inside this same transaction. A missing period is
    not an error: it is the ordinary state for the first write after the current period ends,
    and also for a backfill dated before the first period — `period_bounds` derives the
    window from the date and the salary day, with no notion of which periods happen to exist.
    """
    existing = profile_repo.get_period_containing(db, user_id, occurred_on_local)
    if existing is not None:
        return existing

    starts_on, ends_on = txn_service.resolve_period_bounds(
        occurred_on_local,
        me_service.as_int(profile["month_start_day"], "profile.month_start_day"),
    )
    txn_repo.materialise_period(
        db, user_id, txn_service.build_period_row(user_id, starts_on, ends_on, profile)
    )

    created = profile_repo.get_period_containing(db, user_id, occurred_on_local)
    if created is None:
        raise AssertionError("the period just materialised is not readable")
    return created
