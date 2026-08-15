"""SQL for the `profile` row.

Returns plain dicts rather than ORM rows. `.claude/rules/repositories.md` allows either, and
dicts are what let the layers above stay free of `sqlalchemy` — a router that has to annotate
a `Profile` parameter is a router importing the ORM, which `api.md` forbids outright.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.profile import ProfileLive

RowValue = str | int | bool | UUID | date | datetime | None
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
