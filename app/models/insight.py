from datetime import date
from datetime import datetime as DateTimeType
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
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


class InsightRule(Base):
    """Mirrors `insight_rule` in 0004_insights_and_notifications.sql.

    There is deliberately no `sql` column: `code` is a key into SQL that lives in
    git, is unit-tested and is code-reviewed. A SQL string in a row has no diff,
    no test and no rollback — and against a service-role key it is an escalation
    primitive.
    """

    __tablename__ = "insight_rule"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    feature: Mapped[str] = mapped_column(Text)
    phase: Mapped[int] = mapped_column(SmallInteger)
    # One integer cannot express the real gates: 6 txns per merchant (F2),
    # 4 weeks (F4), 3 months (F6), 3 income cycles (F4). Hence count + unit.
    min_observations: Mapped[int] = mapped_column(SmallInteger)
    min_observation_unit: Mapped[str] = mapped_column(Text)
    observation_scope: Mapped[str] = mapped_column(Text)
    lookback_days: Mapped[int] = mapped_column(SmallInteger)
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    comparator: Mapped[str | None] = mapped_column(Text)
    cooldown_days: Mapped[int] = mapped_column(SmallInteger)
    max_per_iso_week: Mapped[int | None] = mapped_column(SmallInteger)
    suppress_same_subject_consecutive: Mapped[bool] = mapped_column(Boolean)
    priority_weight: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    learned_weight: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    action_label: Mapped[str] = mapped_column(Text)
    action_deeplink: Mapped[str] = mapped_column(Text)
    notification_slot: Mapped[str | None] = mapped_column(Text)
    llm_allowed: Mapped[bool] = mapped_column(Boolean)
    # NOT NULL so that every insight can still render with AI switched off.
    fallback_template: Mapped[str] = mapped_column(Text)
    gate_copy_template: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class Insight(Base):
    """Mirrors `insight` in 0004_insights_and_notifications.sql.

    `dedupe_key` is empty for one-per-window rules and carries the triggering
    txn id for per-event rules — without that, two genuinely different high bills
    at the same merchant on the same day collapse into one.
    """

    __tablename__ = "insight"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    rule_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    budget_period_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    period_start_local: Mapped[date] = mapped_column(Date)
    period_end_local: Mapped[date] = mapped_column(Date)
    subject_type: Mapped[str | None] = mapped_column(Text)
    subject_key: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    computed_value_minor: Mapped[int | None] = mapped_column(BigInteger)
    rupee_impact_minor: Mapped[int] = mapped_column(BigInteger)
    actionability: Mapped[int] = mapped_column(SmallInteger)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    score: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    evidence_filter_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence_txn_count: Mapped[int | None] = mapped_column(Integer)
    evidence_sum_minor: Mapped[int | None] = mapped_column(BigInteger)
    evidence_is_truncated: Mapped[bool] = mapped_column(Boolean)
    llm_used: Mapped[bool] = mapped_column(Boolean)
    llm_model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    fallback_used: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text)
    shown_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    acted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    expires_on_local: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class InsightEvidence(Base):
    """Mirrors `insight_evidence` in 0004_insights_and_notifications.sql.

    Amounts are frozen at calculation time. A pointer-only design breaks the
    moment the user edits a transaction: the explanation stops summing to the
    headline, which is the trust failure the feature exists to prevent.
    """

    __tablename__ = "insight_evidence"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    insight_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    role: Mapped[str] = mapped_column(Text)
    txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    amount_minor_at_calc: Mapped[int] = mapped_column(BigInteger)
    occurred_on_local: Mapped[date] = mapped_column(Date)
    merchant_snapshot: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    category_name_snapshot: Mapped[str | None] = mapped_column(Text)
    contribution_minor: Mapped[int] = mapped_column(BigInteger)
    rank: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class InsightFeedback(Base):
    """Mirrors `insight_feedback` in 0004_insights_and_notifications.sql.

    Append-only — the user changes their mind and both events matter. `rule_id`
    is denormalised so the ranking loop can group by rule without joining back.
    """

    __tablename__ = "insight_feedback"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    insight_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    rule_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    verdict: Mapped[int] = mapped_column(SmallInteger)
    reason_code: Mapped[str | None] = mapped_column(Text)
    free_text: Mapped[str | None] = mapped_column(Text)
    surface: Mapped[str] = mapped_column(Text)
    seconds_visible: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class InsightReadiness(Base):
    """Mirrors `insight_readiness` in 0004_insights_and_notifications.sql.

    Materialised nightly because it is read on every screen open; computing it
    live turns "3 more needed" into a full-history scan.
    """

    __tablename__ = "insight_readiness"

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    rule_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    subject_key: Mapped[str] = mapped_column(Text, primary_key=True)
    observations_have: Mapped[int] = mapped_column(SmallInteger)
    observations_need: Mapped[int] = mapped_column(SmallInteger)
    remaining: Mapped[int] = mapped_column(
        SmallInteger,
        Computed("GREATEST(observations_need - observations_have, 0)", persisted=True),
    )
    is_unlocked: Mapped[bool] = mapped_column(
        Boolean, Computed("observations_have >= observations_need", persisted=True)
    )
    eta_unlock_on: Mapped[date | None] = mapped_column(Date)
    computed_on_local: Mapped[date] = mapped_column(Date)


class NotificationLedger(Base):
    """Mirrors `notification_ledger` in 0004_insights_and_notifications.sql.

    Each cap is its own partial unique index, so the notification budget is
    enforced by the database under concurrency with no counting query to race.
    The two failure modes are not the same thing: `23505` on `notif_cap_event`
    means two writers picked the same slot index — recompute and retry, at most
    twice. `23514` on `event_index_required` means the week's budget is genuinely
    spent — drop the notification, never queue it.
    """

    __tablename__ = "notification_ledger"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    slot: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    local_date: Mapped[date] = mapped_column(Date)
    local_week: Mapped[date] = mapped_column(
        Date,
        Computed("date_trunc('week', local_date::timestamp)::date", persisted=True),
    )
    week_slot_index: Mapped[int | None] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    action_deeplink: Mapped[str | None] = mapped_column(Text)
    insight_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    rupee_impact_minor: Mapped[int | None] = mapped_column(BigInteger)
    push_subscription_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    sent_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    delivery_status: Mapped[str] = mapped_column(Text)
    # A suppressed or failed notification must not consume a slot.
    consumes_budget: Mapped[bool] = mapped_column(Boolean)
