from datetime import date, time
from datetime import datetime as DateTimeType
from typing import Any
from uuid import UUID

from sqlalchemy import CHAR, BigInteger, Date, DateTime, Integer, SmallInteger, Text, Time
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, aliased, mapped_column

from app.models.base import Base, live_metadata


class Profile(Base):
    """Mirrors `profile` in 0002_core_money.sql. Primary key is `user_id`, not `id`."""

    __tablename__ = "profile"

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(Text)
    preferred_currency_code: Mapped[str] = mapped_column(CHAR(3))
    monthly_income_minor: Mapped[int | None] = mapped_column(BigInteger)
    month_start_day: Mapped[int] = mapped_column(SmallInteger)
    month_start_day_effective_from: Mapped[date | None] = mapped_column(Date)
    notify_time_local: Mapped[time | None] = mapped_column(Time)
    quiet_hours_start_local: Mapped[time | None] = mapped_column(Time)
    quiet_hours_end_local: Mapped[time | None] = mapped_column(Time)
    implementation_intention: Mapped[str | None] = mapped_column(Text)
    theme: Mapped[str] = mapped_column(Text)

    # UPDATE is revoked at column level and guarded by a trigger — read only.
    role: Mapped[str] = mapped_column(Text)

    household_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    onboarding_completed_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    onboarding_first_completed_at: Mapped[DateTimeType | None] = mapped_column(
        DateTime(timezone=True)
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)


profile_live = Profile.__table__.to_metadata(live_metadata, name="profile_live")

# adapt_on_names because to_metadata copies the columns rather than aliasing them, so
# the mapper has to match view to table by column name. Without it every SELECT
# compiles to "no columns with which to SELECT from".
ProfileLive = aliased(Profile, profile_live, adapt_on_names=True)
"""Read alias. Same columns, `deleted_at IS NULL` applied by the view."""
