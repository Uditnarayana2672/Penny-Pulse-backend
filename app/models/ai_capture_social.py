from datetime import date
from datetime import datetime as DateTimeType
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QueryIntent(Base):
    """Mirrors `query_intent` in 0007_phase4to6_ai_capture_social.sql.

    A state mirror only. `intent_name` keys into a code registry of
    parameterised queries with golden tests; there is no `sql` column and there
    never will be one.
    """

    __tablename__ = "query_intent"

    intent_name: Mapped[str] = mapped_column(Text, primary_key=True)
    display_label: Mapped[str] = mapped_column(Text)
    param_schema_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_enabled: Mapped[bool] = mapped_column(Boolean)
    chip_rank: Mapped[int | None] = mapped_column(SmallInteger)
    code_version: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    """Mirrors `chat_message` in 0007_phase4to6_ai_capture_social.sql.

    `question_text` is retention-capped and nulled after 30 days. A refusal is a
    first-class outcome rather than an error, which is why `refusal_reason` sits
    beside `answered` instead of raising.
    """

    __tablename__ = "chat_message"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    local_date: Mapped[date] = mapped_column(Date)
    question_text: Mapped[str | None] = mapped_column(Text)
    question_len: Mapped[int] = mapped_column(SmallInteger)
    matched_intent: Mapped[str | None] = mapped_column(Text)
    intent_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    answered: Mapped[bool] = mapped_column(Boolean)
    refusal_reason: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_minor: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    rows_returned: Mapped[int | None] = mapped_column(Integer)
    entered_via: Mapped[str] = mapped_column(Text)
    feedback: Mapped[int | None] = mapped_column(SmallInteger)


class LlmUsageDay(Base):
    """Mirrors `llm_usage_day` in 0007_phase4to6_ai_capture_social.sql.

    The caps are CHECK constraints, not a `COUNT(*)` in application code, so no
    future code path can bypass them. Primary key is `(user_id, local_date)`.
    """

    __tablename__ = "llm_usage_day"

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    messages_used: Mapped[int] = mapped_column(SmallInteger)
    cap_messages: Mapped[int] = mapped_column(SmallInteger)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_minor: Mapped[int] = mapped_column(Integer)
    cap_cost_minor: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class RawSignal(Base):
    """Mirrors `raw_signal` in 0007_phase4to6_ai_capture_social.sql.

    A hash and nothing else. A user's bank SMS or email body never reaches the
    server — that is a privacy stance and a liability decision, not an
    optimisation.
    """

    __tablename__ = "raw_signal"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    source: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(Text)
    received_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class IngestSource(Base):
    """Mirrors `ingest_source` in 0007_phase4to6_ai_capture_social.sql.

    `raw_signal` stores hashes, not sync position; Gmail historyId and statement
    cursors live here or every re-sync starts from zero.
    """

    __tablename__ = "ingest_source"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    error_count: Mapped[int] = mapped_column(SmallInteger)
    last_error: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class PendingTxn(Base):
    """Mirrors `pending_txn` in 0007_phase4to6_ai_capture_social.sql. The inbox."""

    __tablename__ = "pending_txn"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    raw_signal_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    ingest_source_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    merchant_guess: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    account_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    suggested_category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    status: Mapped[str] = mapped_column(Text)
    accepted_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class Household(Base):
    """Mirrors `household` in 0007_phase4to6_ai_capture_social.sql.

    No `user_id` column, so RLS here is membership-based rather than
    owner-based — and so is every query the application writes against it.
    """

    __tablename__ = "household"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text)
    owner_user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class HouseholdMember(Base):
    """Mirrors `household_member` in 0007_phase4to6_ai_capture_social.sql."""

    __tablename__ = "household_member"

    household_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    role: Mapped[str] = mapped_column(Text)
    joined_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class Cohort(Base):
    """Mirrors `cohort` in 0007_phase4to6_ai_capture_social.sql.

    Shares streaks and logging only. Never amounts — non-negotiable, and the
    reason nothing here joins to `txn`.
    """

    __tablename__ = "cohort"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text)
    owner_user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    join_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class CohortMember(Base):
    """Mirrors `cohort_member` in 0007_phase4to6_ai_capture_social.sql."""

    __tablename__ = "cohort_member"

    cohort_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    joined_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
