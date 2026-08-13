from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.profile import Profile, ProfileLive


def get_profile(db: Session, user_id: UUID) -> Profile | None:
    """A missing row returns None. What that means is the service's decision."""
    stmt = select(ProfileLive).where(ProfileLive.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()
