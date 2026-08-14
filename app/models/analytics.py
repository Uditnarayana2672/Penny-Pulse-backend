from datetime import date
from datetime import datetime as DateTimeType
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
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
from sqlalchemy.orm import Mapped, aliased, mapped_column

from app.models.base import Base, live_metadata


class BalanceAnchor(Base):
    """Mirrors `balance_anchor` in 0005_phase2_truth_and_analytics.sql.

    Immutable observations, never recomputed. The opening anchor has no prior
    estimate, so `gap_minor` is only required when `is_opening` is false —
    writing 0 there would report a gap equal to the whole balance and discredit
    capture rate on its first data point.
    """

    __tablename__ = "balance_anchor"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    account_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    observed_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    observed_on_local: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    is_opening: Mapped[bool] = mapped_column(Boolean)
    estimated_at_time_minor: Mapped[int | None] = mapped_column(BigInteger)
    gap_minor: Mapped[int | None] = mapped_column(BigInteger)
    adjustment_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class Recurring(Base):
    """Mirrors `recurring` in 0005_phase2_truth_and_analytics.sql.

    `day_of_month` is 1..31 with an explicit last-day flag because Indian EMIs
    debit on the 30th and 31st. `bucket` is stored here as well as on the
    category so an auto-posted row and a `merchant_rule` override cannot give two
    answers for the same charge.
    """

    __tablename__ = "recurring"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    category_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    account_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    merchant_normalized: Mapped[str | None] = mapped_column(Text)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    day_of_month: Mapped[int | None] = mapped_column(SmallInteger)
    on_last_day: Mapped[bool] = mapped_column(Boolean)
    interval_days: Mapped[int | None] = mapped_column(SmallInteger)
    next_due_on: Mapped[date] = mapped_column(Date)
    last_posted_on: Mapped[date | None] = mapped_column(Date)
    bucket: Mapped[str | None] = mapped_column(Text)
    auto_post: Mapped[bool] = mapped_column(Boolean)
    end_on: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean)
    is_subscription: Mapped[bool] = mapped_column(Boolean)
    annualised_cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    previous_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    price_last_changed_on: Mapped[date | None] = mapped_column(Date)
    review_due_on: Mapped[date | None] = mapped_column(Date)
    last_reviewed_on: Mapped[date | None] = mapped_column(Date)
    last_review_outcome: Mapped[str | None] = mapped_column(Text)
    cancel_url: Mapped[str | None] = mapped_column(Text)
    detected_from_candidate_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)


recurring_live = Recurring.__table__.to_metadata(live_metadata, name="recurring_live")
RecurringLive = aliased(Recurring, recurring_live, adapt_on_names=True)
"""Read alias. Same columns, `deleted_at IS NULL` applied by the view."""


class RecurringCandidate(Base):
    """Mirrors `recurring_candidate` in 0005_phase2_truth_and_analytics.sql.

    The `rejected` status is load-bearing: without it the nightly detector
    re-proposes the same subscription forever.
    """

    __tablename__ = "recurring_candidate"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    merchant_normalized: Mapped[str] = mapped_column(Text)
    category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    typical_amount_minor: Mapped[int] = mapped_column(BigInteger)
    amount_variance_minor: Mapped[int] = mapped_column(BigInteger)
    interval_days: Mapped[int] = mapped_column(SmallInteger)
    interval_stddev_days: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    observation_count: Mapped[int] = mapped_column(SmallInteger)
    first_seen_on: Mapped[date] = mapped_column(Date)
    last_seen_on: Mapped[date] = mapped_column(Date)
    next_expected_on: Mapped[date] = mapped_column(Date)
    annualised_cost_minor: Mapped[int] = mapped_column(
        BigInteger,
        Computed("typical_amount_minor * 365 / GREATEST(interval_days, 1)", persisted=True),
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    status: Mapped[str] = mapped_column(Text)
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    promoted_recurring_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    detected_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class RecurringOccurrence(Base):
    """Mirrors `recurring_occurrence` in 0005_phase2_truth_and_analytics.sql.

    `UNIQUE (recurring_id, due_on_local)` is what makes the nightly cron
    re-runnable and guarantees the user is never asked twice about one charge.
    """

    __tablename__ = "recurring_occurrence"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    recurring_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    due_on_local: Mapped[date] = mapped_column(Date)
    expected_amount_minor: Mapped[int] = mapped_column(BigInteger)
    prompt_sent_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    notification_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)
    responded_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    actual_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class MerchantRule(Base):
    """Mirrors `merchant_rule` in 0005_phase2_truth_and_analytics.sql."""

    __tablename__ = "merchant_rule"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    merchant_pattern: Mapped[str] = mapped_column(Text)
    category_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    bucket: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    source: Mapped[str] = mapped_column(Text)
    hit_count: Mapped[int] = mapped_column(Integer)
    last_used_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class IncomeEvent(Base):
    """Mirrors `income_event` in 0005_phase2_truth_and_analytics.sql.

    F4 payday burn needs the observed credit date. `budget_period.starts_on` is
    the nominal salary day, which is often days out and is not the same thing.
    """

    __tablename__ = "income_event"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    budget_period_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    received_on_local: Mapped[date] = mapped_column(Date)
    is_primary_income: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class DailyClose(Base):
    """Mirrors `daily_close` in 0005_phase2_truth_and_analytics.sql.

    One row per user per local date, so "today's number" and "an overspend day"
    stay reproducible tomorrow. Primary key is `(user_id, local_date)`.
    """

    __tablename__ = "daily_close"

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    budget_period_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    spent_minor: Mapped[int] = mapped_column(BigInteger)
    income_minor: Mapped[int] = mapped_column(BigInteger)
    allowance_minor: Mapped[int] = mapped_column(BigInteger)
    safe_to_spend_minor: Mapped[int] = mapped_column(BigInteger)
    flexible_remaining_minor: Mapped[int] = mapped_column(BigInteger)
    committed_unpaid_minor: Mapped[int] = mapped_column(BigInteger)
    pace_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    is_overspend_day: Mapped[bool] = mapped_column(Boolean)
    txn_count: Mapped[int] = mapped_column(SmallInteger)
    computed_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class StatBaseline(Base):
    """Mirrors `stat_baseline` in 0005_phase2_truth_and_analytics.sql.

    Materialised nightly rather than computed live: an alert that fired at 2.3
    sigma last night must not read 1.9 sigma on the detail screen today.
    """

    __tablename__ = "stat_baseline"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    scope: Mapped[str] = mapped_column(Text)
    scope_key: Mapped[str] = mapped_column(Text)
    category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    window_days: Mapped[int] = mapped_column(SmallInteger)
    n: Mapped[int] = mapped_column(SmallInteger)
    mean_minor: Mapped[int] = mapped_column(BigInteger)
    stddev_minor: Mapped[int] = mapped_column(BigInteger)
    median_minor: Mapped[int] = mapped_column(BigInteger)
    q1_minor: Mapped[int] = mapped_column(BigInteger)
    q3_minor: Mapped[int] = mapped_column(BigInteger)
    iqr_minor: Mapped[int] = mapped_column(
        BigInteger, Computed("q3_minor - q1_minor", persisted=True)
    )
    mad_minor: Mapped[int] = mapped_column(BigInteger)
    min_minor: Mapped[int] = mapped_column(BigInteger)
    max_minor: Mapped[int] = mapped_column(BigInteger)
    prev_median_minor: Mapped[int | None] = mapped_column(BigInteger)
    prev_window_end: Mapped[date | None] = mapped_column(Date)
    first_seen_on: Mapped[date] = mapped_column(Date)
    last_seen_on: Mapped[date] = mapped_column(Date)
    median_interval_days: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    interval_stddev_days: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    # The F2 gate: no alert until six prior observations exist, so month one
    # stays quiet instead of guessing.
    is_sufficient: Mapped[bool] = mapped_column(
        Boolean, Computed("n >= 6", persisted=True)
    )
    computed_on_local: Mapped[date] = mapped_column(Date)
    computed_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class TxnAnomaly(Base):
    """Mirrors `txn_anomaly` in 0005_phase2_truth_and_analytics.sql.

    The baseline is frozen at detection time, and `UNIQUE NULLS NOT DISTINCT
    (txn_id, kind, peer_txn_id)` is dismissal memory — a pair the user has
    already judged is never re-flagged.
    """

    __tablename__ = "txn_anomaly"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    txn_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    peer_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    z_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    delta_minor: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(Text)
    baseline_n: Mapped[int] = mapped_column(SmallInteger)
    baseline_mean_minor: Mapped[int | None] = mapped_column(BigInteger)
    baseline_stddev_minor: Mapped[int | None] = mapped_column(BigInteger)
    baseline_median_minor: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    detected_on_local: Mapped[date] = mapped_column(Date)
    resolved_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    insight_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class BudgetRecommendation(Base):
    """Mirrors `budget_recommendation` in 0005_phase2_truth_and_analytics.sql.

    Raw and scaled are both stored because F6 has to show what was scaled, not
    only the final number.
    """

    __tablename__ = "budget_recommendation"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    budget_period_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    category_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    basis: Mapped[str] = mapped_column(Text)
    months_used: Mapped[int] = mapped_column(SmallInteger)
    raw_recommendation_minor: Mapped[int] = mapped_column(BigInteger)
    scaled_recommendation_minor: Mapped[int] = mapped_column(BigInteger)
    scale_factor: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    recurring_addon_minor: Mapped[int] = mapped_column(BigInteger)
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    computed_on_local: Mapped[date] = mapped_column(Date)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class CategorySuggestionEvent(Base):
    """Mirrors `category_suggestion_event` in 0005_phase2_truth_and_analytics.sql.

    Suggested-versus-chosen is the only thing that says whether F3 works at all.
    """

    __tablename__ = "category_suggestion_event"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    suggested_category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    chosen_category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    suggestion_source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    stage: Mapped[str] = mapped_column(Text)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class RebalanceProposal(Base):
    """Mirrors `rebalance_proposal` in 0005_phase2_truth_and_analytics.sql.

    A rebalance is a multi-row donor-to-recipient plan; it cannot live in
    `insight.body` text if [Apply] is to write real `budget_limit` rows.
    """

    __tablename__ = "rebalance_proposal"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    insight_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    budget_period_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    overspent_category_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    overspend_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    applied_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class RebalanceLine(Base):
    """Mirrors `rebalance_line` in 0005_phase2_truth_and_analytics.sql.

    `donor_score` is remaining x flexibility x days_left, frozen alongside the
    two `*_at_calc` columns so the proposal still explains itself after the
    underlying limits move.
    """

    __tablename__ = "rebalance_line"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    rebalance_proposal_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    donor_category_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    reduce_by_minor: Mapped[int] = mapped_column(BigInteger)
    donor_score: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    flexibility_at_calc: Mapped[int] = mapped_column(SmallInteger)
    remaining_at_calc_minor: Mapped[int] = mapped_column(BigInteger)
    rank: Mapped[int] = mapped_column(SmallInteger)
