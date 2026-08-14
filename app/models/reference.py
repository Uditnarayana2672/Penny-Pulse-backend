from datetime import datetime as DateTimeType
from uuid import UUID

from sqlalchemy import CHAR, Boolean, DateTime, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Currency(Base):
    """Mirrors `currency` in 0001_extensions_and_reference.sql."""

    __tablename__ = "currency"

    code: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(Text)
    minor_unit: Mapped[int] = mapped_column(SmallInteger)
    symbol_note: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean)


class CategoryTemplate(Base):
    """Mirrors `category_template` in 0001_extensions_and_reference.sql."""

    __tablename__ = "category_template"

    template_key: Mapped[str] = mapped_column(Text, primary_key=True)
    template_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    short_label: Mapped[str] = mapped_column(Text)
    icon: Mapped[str] = mapped_column(Text)
    colour: Mapped[str] = mapped_column(Text)
    default_bucket: Mapped[str | None] = mapped_column(Text)
    flexibility: Mapped[int | None] = mapped_column(SmallInteger)
    suggested_share_pct: Mapped[int | None] = mapped_column(SmallInteger)
    sort_order: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean)


class AnalysisBlock(Base):
    """Mirrors `analysis_block` in 0001_extensions_and_reference.sql.

    `message` is a template holding {remaining_months}, {remaining_days}, {feature}.
    """

    __tablename__ = "analysis_block"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    requires_months: Mapped[int] = mapped_column(SmallInteger)
    requires_feature: Mapped[str | None] = mapped_column(Text)
    default_status: Mapped[str] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(SmallInteger)


class SynonymGroup(Base):
    """Mirrors `synonym_group` in 0001_extensions_and_reference.sql."""

    __tablename__ = "synonym_group"

    group_key: Mapped[str] = mapped_column(Text, primary_key=True)
    term: Mapped[str] = mapped_column(Text, primary_key=True)


class FeatureFlagDefinition(Base):
    """Mirrors `feature_flag_definition` in 0001_extensions_and_reference.sql."""

    __tablename__ = "feature_flag_definition"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    depends_on_key: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[int] = mapped_column(SmallInteger)
    is_user_visible: Mapped[bool] = mapped_column(Boolean)
    sort_order: Mapped[int] = mapped_column(SmallInteger)


class FeatureFlag(Base):
    """Mirrors `feature_flag` in 0001_extensions_and_reference.sql.

    The table has no PRIMARY KEY — it has `UNIQUE NULLS NOT DISTINCT (key, scope,
    user_id)` instead, so that one global row and one optional row per user can
    coexist. The mapper needs *some* identity, so those three columns carry
    `primary_key=True` here. `user_id` stays nullable, which is what makes the
    global row expressible; it is a mapper-level key, not a database constraint.
    """

    __tablename__ = "feature_flag"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[DateTimeType] = mapped_column(DateTime(timezone=True))
