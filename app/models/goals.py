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
from sqlalchemy.orm import Mapped, aliased, mapped_column

from app.models.base import Base, live_metadata


class Attachment(Base):
    """Mirrors `attachment` in 0006_phase3_goals_and_debt.sql."""

    __tablename__ = "attachment"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    storage_path: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class Goal(Base):
    """Mirrors `goal` in 0006_phase3_goals_and_debt.sql.

    A goal is a virtual envelope: `funding_account_id` is advisory, powering the
    over-commitment warning only — it does not own the money.
    """

    __tablename__ = "goal"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    household_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(Text)
    photo_attachment_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    target_amount_minor: Mapped[int] = mapped_column(BigInteger)
    current_amount_minor: Mapped[int] = mapped_column(BigInteger)
    # The planned monthly earmark. Without it FR-3 has nothing to subtract from
    # safe-to-spend and the payday reminder has no amount to name.
    monthly_contribution_minor: Mapped[int | None] = mapped_column(BigInteger)
    earmark_active: Mapped[bool] = mapped_column(Boolean)
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    target_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[int] = mapped_column(SmallInteger)
    funding_account_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    is_emergency_fund: Mapped[bool] = mapped_column(Boolean)
    feasibility: Mapped[str | None] = mapped_column(Text)
    feasibility_computed_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    achieved_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)


goal_live = Goal.__table__.to_metadata(live_metadata, name="goal_live")
GoalLive = aliased(Goal, goal_live, adapt_on_names=True)
"""Read alias. Same columns, `deleted_at IS NULL` applied by the view."""


class GoalContribution(Base):
    """Mirrors `goal_contribution` in 0006_phase3_goals_and_debt.sql."""

    __tablename__ = "goal_contribution"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    goal_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    occurred_on_local: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class WishlistItem(Base):
    """Mirrors `wishlist_item` in 0006_phase3_goals_and_debt.sql.

    `decide_after` is `added_at` plus 48 hours — the cooling-off window is the
    feature.
    """

    __tablename__ = "wishlist_item"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    added_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    decide_after: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    bought_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    swept_to_goal_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class SinkingFund(Base):
    """Mirrors `sinking_fund` in 0006_phase3_goals_and_debt.sql."""

    __tablename__ = "sinking_fund"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    annual_estimate_minor: Mapped[int] = mapped_column(BigInteger)
    monthly_accrual_minor: Mapped[int] = mapped_column(BigInteger)
    balance_minor: Mapped[int] = mapped_column(BigInteger)
    expected_month: Mapped[int | None] = mapped_column(SmallInteger)
    category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class EmiSchedule(Base):
    """Mirrors `emi_schedule` in 0006_phase3_goals_and_debt.sql.

    `principal_share_minor` and `interest_share_minor` are averages; the
    per-instalment truth lives in `emi_instalment`.
    """

    __tablename__ = "emi_schedule"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    lender_name: Mapped[str | None] = mapped_column(Text)
    account_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    recurring_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    monthly_amount_minor: Mapped[int] = mapped_column(BigInteger)
    remaining_months: Mapped[int] = mapped_column(SmallInteger)
    started_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    principal_share_minor: Mapped[int | None] = mapped_column(BigInteger)
    interest_share_minor: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class EmiInstalment(Base):
    """Mirrors `emi_instalment` in 0006_phase3_goals_and_debt.sql.

    Two averaged columns on the schedule cannot produce a debt-free-date curve,
    which is why FR-79's split needs per-instalment rows.
    """

    __tablename__ = "emi_instalment"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    emi_schedule_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    seq: Mapped[int] = mapped_column(SmallInteger)
    due_on: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    principal_minor: Mapped[int] = mapped_column(BigInteger)
    interest_minor: Mapped[int] = mapped_column(BigInteger)
    balance_after_minor: Mapped[int | None] = mapped_column(BigInteger)
    paid_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)


class ChitFund(Base):
    """Mirrors `chit_fund` in 0006_phase3_goals_and_debt.sql.

    A chit is savings before the pot is taken and a loan being repaid after, so
    one `category.default_bucket` cannot describe it. The two `*_payout_bucket`
    columns carry the flip, and `txn.bucket` is stored at write time — past
    months genuinely were savings and stay that way.
    """

    __tablename__ = "chit_fund"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    organiser_name: Mapped[str | None] = mapped_column(Text)
    member_count: Mapped[int | None] = mapped_column(SmallInteger)
    monthly_contribution_minor: Mapped[int] = mapped_column(BigInteger)
    cycle_months: Mapped[int] = mapped_column(SmallInteger)
    started_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    draw_type: Mapped[str] = mapped_column(Text)
    foreman_commission_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    payout_month_index: Mapped[int | None] = mapped_column(SmallInteger)
    expected_payout_minor: Mapped[int | None] = mapped_column(BigInteger)
    actual_payout_minor: Mapped[int | None] = mapped_column(BigInteger)
    payout_received_on: Mapped[date | None] = mapped_column(Date)
    payout_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    account_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    recurring_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    pre_payout_bucket: Mapped[str] = mapped_column(Text)
    post_payout_bucket: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class ChitInstalment(Base):
    """Mirrors `chit_instalment` in 0006_phase3_goals_and_debt.sql.

    Deliberately the same shape as `emi_instalment`, so the EMI runway and the
    debt-free date are one UNION with no chit-specific logic. `dividend_minor`
    stays separate from the contribution because that is what makes the
    effective rate — and therefore whether the chit is a good deal — derivable.
    """

    __tablename__ = "chit_instalment"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    chit_fund_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    month_index: Mapped[int] = mapped_column(SmallInteger)
    due_on: Mapped[date] = mapped_column(Date)
    contribution_minor: Mapped[int] = mapped_column(BigInteger)
    dividend_minor: Mapped[int] = mapped_column(BigInteger)
    net_payable_minor: Mapped[int] = mapped_column(BigInteger)
    is_payout_month: Mapped[bool] = mapped_column(Boolean)
    payout_minor: Mapped[int | None] = mapped_column(BigInteger)
    txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)


class RegretScore(Base):
    """Mirrors `regret_score` in 0006_phase3_goals_and_debt.sql. One rating per txn."""

    __tablename__ = "regret_score"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    txn_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    worth_it: Mapped[bool] = mapped_column(Boolean)
    rated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class ReviewSession(Base):
    """Mirrors `review_session` in 0006_phase3_goals_and_debt.sql.

    FR-116's Sunday review ends in one commit tap; the commitment and whether it
    was kept is the whole point, and without a row it is just a screen.
    """

    __tablename__ = "review_session"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    week_start_on: Mapped[date] = mapped_column(Date)
    insight_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    cards_seen: Mapped[int] = mapped_column(SmallInteger)
    commit_text: Mapped[str | None] = mapped_column(Text)
    commit_category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    outcome: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))


class RefundWatch(Base):
    """Mirrors `refund_watch` in 0006_phase3_goals_and_debt.sql.

    `source = 'auto'` is what links F2 to F8: a confirmed double charge is an
    expected refund, so confirming one opens a watch.
    """

    __tablename__ = "refund_watch"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    purchase_txn_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    expected_amount_minor: Mapped[int] = mapped_column(BigInteger)
    matched_amount_minor: Mapped[int] = mapped_column(BigInteger)
    expected_by_local: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    reminded_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))


class RefundMatch(Base):
    """Mirrors `refund_match` in 0006_phase3_goals_and_debt.sql.

    `UNIQUE (credit_txn_id)` — one credit settles at most one watch.
    """

    __tablename__ = "refund_match"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    refund_watch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    credit_txn_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    match_method: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    matched_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
