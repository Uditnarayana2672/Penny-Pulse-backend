from datetime import date
from datetime import datetime as DateTimeType
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    DateTime,
    Integer,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class HabitLog(Base):
    """Mirrors `habit_log` in 0003_habit_and_ops.sql.

    `zero_spend_confirmed` and `txn_count` stay separate because Phase 2 capture
    rate is unmeasurable if a captured day stops being distinguishable from a
    confirmed-empty one.
    """

    __tablename__ = "habit_log"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    local_date: Mapped[date] = mapped_column(Date)
    zero_spend_confirmed: Mapped[bool] = mapped_column(Boolean)
    txn_count: Mapped[int] = mapped_column(SmallInteger)
    # GENERATED in the database. `Computed` keeps it out of every INSERT and
    # UPDATE, which is the point — the natural write path is
    # `SET txn_count = txn_count + 1`, and a writer that also sends `logged`
    # would be writing a value the database is about to overwrite.
    logged: Mapped[bool] = mapped_column(
        Boolean, Computed("txn_count > 0 OR zero_spend_confirmed", persisted=True)
    )
    freeze_used: Mapped[bool] = mapped_column(Boolean)
    first_txn_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class Streak(Base):
    """Mirrors `streak` in 0003_habit_and_ops.sql. Primary key is `user_id`."""

    __tablename__ = "streak"

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    current: Mapped[int] = mapped_column(Integer)
    longest: Mapped[int] = mapped_column(Integer)
    freezes_left: Mapped[int] = mapped_column(SmallInteger)
    # Without a reset marker a re-run job or a second device double-grants the
    # two monthly freezes and the allowance silently inflates.
    freezes_reset_on: Mapped[date | None] = mapped_column(Date)
    last_active_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class PushSubscription(Base):
    """Mirrors `push_subscription` in 0003_habit_and_ops.sql."""

    __tablename__ = "push_subscription"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean)
    failure_count: Mapped[int] = mapped_column(SmallInteger)
    last_success_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Mirrors `audit_log` in 0003_habit_and_ops.sql.

    `before` and `after` hold whole rows, so they hold amounts and notes. Never
    log their contents.
    """

    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    row_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class DataDeletionToken(Base):
    """Mirrors `data_deletion_token` in 0003_habit_and_ops.sql.

    `token` is the secret and `id` is not; a guessable primary key must not
    double as one. The table is RLS tier NONE — the client cannot read it at all.
    """

    __tablename__ = "data_deletion_token"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    token: Mapped[str] = mapped_column(
        Text, server_default=text("encode(extensions.gen_random_bytes(32), 'hex')")
    )
    expires_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class DataJob(Base):
    """Mirrors `data_job` in 0003_habit_and_ops.sql.

    Export, backup and restore share one table because a restore has the same
    status machine as an export.
    """

    __tablename__ = "data_job"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(Text)
    is_encrypted: Mapped[bool] = mapped_column(Boolean)
    schema_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(Text)
    row_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
