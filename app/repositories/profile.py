"""SQL for the `profile` row.

Returns plain dicts rather than ORM rows. `.claude/rules/repositories.md` allows either, and
dicts are what let the layers above stay free of `sqlalchemy` — a router that has to annotate
a `Profile` parameter is a router importing the ORM, which `api.md` forbids outright.
"""

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models.core import BudgetPeriod, BudgetPeriodLive
from app.models.profile import Profile, ProfileLive

# `time` is in the union because `notify_time_local` is a TIME column.
RowValue = str | int | bool | UUID | date | datetime | time | None
RowDict = dict[str, RowValue]


def get_profile(db: Session, user_id: UUID) -> RowDict | None:
    """A missing row returns None. What that means is the service's decision.

    Selects exactly the columns `ProfileOut` declares. `user_id`, `locale`, `household_id`,
    `metadata`, `deleted_at`, the quiet-hours pair, `month_start_day_effective_from` and
    `onboarding_first_completed_at` are all deliberately absent: they are server-side, and a
    column that never reaches the wire should not be read into it by accident.
    """
    stmt = select(
        ProfileLive.display_name,
        ProfileLive.timezone,
        ProfileLive.preferred_currency_code,
        ProfileLive.monthly_income_minor,
        ProfileLive.month_start_day,
        ProfileLive.notify_time_local,
        ProfileLive.implementation_intention,
        ProfileLive.theme,
        ProfileLive.role,
        ProfileLive.onboarding_completed_at,
        ProfileLive.created_at,
        ProfileLive.updated_at,
        ProfileLive.version,
    ).where(ProfileLive.user_id == user_id)

    row = db.execute(stmt).mappings().one_or_none()
    return None if row is None else dict(row)


def profile_exists(db: Session, user_id: UUID) -> bool:
    """Whether onboarding has run, without reading thirteen columns to find out.

    `POST /onboarding/complete` needs this and nothing else on its first branch.
    """
    stmt = select(ProfileLive.user_id).where(ProfileLive.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none() is not None


def update_profile(db: Session, user_id: UUID, patch: RowDict) -> None:
    """Apply an already-validated set of column values.

    Writes to `profile`, not `profile_live` — the view is for reads. No commit: the session
    dependency commits once, and a `month_start_day` change is two statements that are only
    valid together.

    An empty patch would compile to `UPDATE profile SET WHERE ...`, which is a syntax
    error, so the caller must not call this with nothing to write.
    """
    if not patch:
        raise ValueError("update_profile needs at least one column; the caller decides")

    db.execute(update(Profile).where(Profile.user_id == user_id).values(**patch))


def get_period_containing(db: Session, user_id: UUID, on: date) -> RowDict | None:
    """The budget period whose inclusive range covers `on`, if one exists.

    Lives here rather than in `repositories/onboarding.py` because the only caller is
    `PATCH /me`: moving `month_start_day` has to know which period to extend. Periods are
    materialised lazily, so None is an ordinary answer and not an error.
    """
    stmt = select(
        BudgetPeriodLive.id,
        BudgetPeriodLive.starts_on,
        BudgetPeriodLive.ends_on,
    ).where(
        BudgetPeriodLive.user_id == user_id,
        BudgetPeriodLive.starts_on <= on,
        BudgetPeriodLive.ends_on >= on,
    )

    row = db.execute(stmt).mappings().one_or_none()
    return None if row is None else dict(row)


def extend_period_end(
    db: Session, user_id: UUID, budget_period_id: UUID, new_ends_on: date
) -> None:
    """Move one period's inclusive end forward, under a deferred exclusion constraint.

    `period_no_overlap` is `DEFERRABLE INITIALLY **IMMEDIATE**` (0002_core_money.sql:230),
    not `INITIALLY DEFERRED` like the three `txn` self-references. Deferral is available but
    is not automatic, so it has to be asked for per transaction — the migration's own
    comment says the alternative is "shrinking the next period first, in a hand-remembered
    order, at every call site, forever".

    `SET CONSTRAINTS` lasts for the rest of the transaction and Postgres applies it to
    EXCLUDE constraints as well as to keys, so the check happens at the single commit the
    session dependency issues. Without it, extending a period that abuts an already
    materialised next period raises `23P01` mid-statement even though the end state is
    valid.
    """
    db.execute(text("SET CONSTRAINTS period_no_overlap DEFERRED"))
    db.execute(
        update(BudgetPeriod)
        .where(BudgetPeriod.user_id == user_id, BudgetPeriod.id == budget_period_id)
        .values(ends_on=new_ends_on)
    )
