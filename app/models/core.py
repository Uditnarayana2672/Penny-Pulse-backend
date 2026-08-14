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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, aliased, mapped_column

from app.models.base import Base, live_metadata


class Account(Base):
    """Mirrors `account` in 0002_core_money.sql.

    There is no balance column and there will not be one (delta D3). Balance is
    derived as latest `balance_anchor` + signed txns since.
    """

    __tablename__ = "account"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    household_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    name_normalized: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(Text)
    is_liability: Mapped[bool] = mapped_column(Boolean)
    include_in_spendable: Mapped[bool] = mapped_column(Boolean)
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    institution_name: Mapped[str | None] = mapped_column(Text)
    last4: Mapped[str | None] = mapped_column(CHAR(4))
    credit_limit_minor: Mapped[int | None] = mapped_column(BigInteger)
    statement_day: Mapped[int | None] = mapped_column(SmallInteger)
    statement_on_last_day: Mapped[bool] = mapped_column(Boolean)
    due_day: Mapped[int | None] = mapped_column(SmallInteger)
    due_on_last_day: Mapped[bool] = mapped_column(Boolean)
    principal_outstanding_minor: Mapped[int | None] = mapped_column(BigInteger)
    interest_rate_bps: Mapped[int | None] = mapped_column(Integer)
    lender_name: Mapped[str | None] = mapped_column(Text)
    opened_on: Mapped[date | None] = mapped_column(Date)
    matures_on: Mapped[date | None] = mapped_column(Date)
    is_default: Mapped[bool] = mapped_column(Boolean)
    is_archived: Mapped[bool] = mapped_column(Boolean)
    archived_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    sort_order: Mapped[int | None] = mapped_column(SmallInteger)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)


account_live = Account.__table__.to_metadata(live_metadata, name="account_live")
AccountLive = aliased(Account, account_live, adapt_on_names=True)
"""Read alias. Same columns, `deleted_at IS NULL` applied by the view."""


class Category(Base):
    """Mirrors `category` in 0002_core_money.sql.

    A pin limit of four per kind and a one-level parent depth are enforced by
    triggers, not by anything here.
    """

    __tablename__ = "category"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    household_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    parent_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    name_normalized: Mapped[str] = mapped_column(Text)
    short_label: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str] = mapped_column(Text)
    colour: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    default_bucket: Mapped[str | None] = mapped_column(Text)
    flexibility: Mapped[int | None] = mapped_column(SmallInteger)
    is_pinned: Mapped[bool] = mapped_column(Boolean)
    is_system: Mapped[bool] = mapped_column(Boolean)
    template_key: Mapped[str | None] = mapped_column(Text)
    included_in_category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    merged_into_category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    sort_order: Mapped[int | None] = mapped_column(SmallInteger)
    is_archived: Mapped[bool] = mapped_column(Boolean)
    archived_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)


category_live = Category.__table__.to_metadata(live_metadata, name="category_live")
CategoryLive = aliased(Category, category_live, adapt_on_names=True)
"""Read alias. Same columns, `deleted_at IS NULL` applied by the view."""


class BudgetPeriod(Base):
    """Mirrors `budget_period` in 0002_core_money.sql.

    `period_no_overlap` is a DEFERRABLE gist exclusion constraint: extending the
    current period when `month_start_day` changes is only writable inside one
    transaction, which is why nothing below the session dependency commits.
    """

    __tablename__ = "budget_period"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    household_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    expected_income_minor: Mapped[int] = mapped_column(BigInteger)
    pct_needs: Mapped[int] = mapped_column(SmallInteger)
    pct_wants: Mapped[int] = mapped_column(SmallInteger)
    pct_future: Mapped[int] = mapped_column(SmallInteger)
    pct_debt: Mapped[int] = mapped_column(SmallInteger)
    carried_from_budget_period_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    review_dismissed_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)


budget_period_live = BudgetPeriod.__table__.to_metadata(
    live_metadata, name="budget_period_live"
)
BudgetPeriodLive = aliased(BudgetPeriod, budget_period_live, adapt_on_names=True)
"""Read alias. Same columns, `deleted_at IS NULL` applied by the view."""


class BudgetLimit(Base):
    """Mirrors `budget_limit` in 0002_core_money.sql.

    `flexibility_override` is per-period on purpose: the limit editor is
    period-scoped, so writing flexibility back to the category would silently
    rewrite safe-to-spend for every closed period.
    """

    __tablename__ = "budget_limit"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    household_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    budget_period_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    category_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    limit_minor: Mapped[int] = mapped_column(BigInteger)
    carried_in_minor: Mapped[int] = mapped_column(BigInteger)
    flexibility_override: Mapped[int | None] = mapped_column(SmallInteger)
    rollover: Mapped[str] = mapped_column(Text)
    rollover_cap_minor: Mapped[int | None] = mapped_column(BigInteger)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)


budget_limit_live = BudgetLimit.__table__.to_metadata(live_metadata, name="budget_limit_live")
BudgetLimitLive = aliased(BudgetLimit, budget_limit_live, adapt_on_names=True)
"""Read alias. Same columns, `deleted_at IS NULL` applied by the view."""


class Txn(Base):
    """Mirrors `txn` in 0002_core_money.sql.

    `category_id` is nullable only because a transfer leg has no category · the
    self-references are DEFERRABLE because a transfer pair, a split and a refund
    each need two rows pointing at each other · `is_excluded` is the sole
    exclusion flag for every total, `bucket = 'EXCLUDED'` is only a label ·
    `source` is provenance and `entry_method` is the UI path, and they answer
    different questions.
    """

    __tablename__ = "txn"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    household_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    category_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    account_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    budget_period_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    base_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    direction: Mapped[str] = mapped_column(Text)
    bucket: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    occurred_on_local: Mapped[date] = mapped_column(Date)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    created_on_local: Mapped[date] = mapped_column(Date)
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[DateTimeType | None] = mapped_column(DateTime(timezone=True))
    instrument: Mapped[str | None] = mapped_column(Text)
    merchant: Mapped[str | None] = mapped_column(Text)
    merchant_normalized: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    why: Mapped[str | None] = mapped_column(Text)
    is_excluded: Mapped[bool] = mapped_column(Boolean)
    is_adjustment: Mapped[bool] = mapped_column(Boolean)
    is_refund: Mapped[bool] = mapped_column(Boolean)
    refunds_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    transfer_role: Mapped[str | None] = mapped_column(Text)
    transfer_peer_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    split_parent_txn_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    external_ref: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    entry_method: Mapped[str | None] = mapped_column(Text)
    entry_duration_ms: Mapped[int | None] = mapped_column(Integer)
    edited_before_save: Mapped[bool] = mapped_column(Boolean)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    version: Mapped[int] = mapped_column(Integer)


txn_live = Txn.__table__.to_metadata(live_metadata, name="txn_live")
TxnLive = aliased(Txn, txn_live, adapt_on_names=True)
"""Read alias. Same columns, `deleted_at IS NULL` applied by the view."""


class TxnTag(Base):
    """Mirrors `txn_tag` in 0002_core_money.sql. No soft delete, no `*_live` view."""

    __tablename__ = "txn_tag"

    txn_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    tag: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
