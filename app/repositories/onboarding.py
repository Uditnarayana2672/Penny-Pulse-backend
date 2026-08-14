"""All SQL for the onboarding bootstrap.

Nothing here decides anything: the service has already validated the payload and produced
exact column values. This module's one judgement is stamping `user_id` on every row from
its own argument rather than trusting the one in the row — see `insert_onboarding`.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.core import (
    Account,
    AccountLive,
    BudgetLimit,
    BudgetLimitLive,
    BudgetPeriod,
    BudgetPeriodLive,
    Category,
    CategoryLive,
)
from app.models.profile import Profile
from app.models.reference import CategoryTemplate, Currency

# The same union appears in the service. See the note there on why it is not extracted.
RowValue = str | int | bool | UUID | date | datetime | None
RowDict = dict[str, RowValue]

# `sort_order = 99` is `unaccounted_out` and `unaccounted_in` — Phase-2 machinery for
# balance-anchor adjustments, not categories a person picks.
RESERVED_TEMPLATE_SORT_ORDER = 99


def list_offered_templates(db: Session) -> list[RowDict]:
    """The templates onboarding may offer, as plain dicts.

    No `user_id` argument, and a deliberate exception to the rule in
    `.claude/rules/repositories.md` that every function takes one: `category_template` is
    global reference data with no `user_id` column and its own world-readable RLS policy.
    There is no tenancy to enforce because there is no tenant. This is the only function
    here without that filter.

    `unaccounted_out` sits in the EXCLUDED bucket, so offering it would let someone choose a
    category that can never hold a budget limit — the trigger would reject the limit and the
    user would see a 500 for a choice the screen invited.

    Ordered by `(kind, sort_order, template_key)`: `sort_order` restarts at 1 for income, so
    ordering by it alone is non-deterministic across kinds.

    The selected columns are exactly `app.services.onboarding.TEMPLATE_COLUMNS`, which is
    what lets that module build its dataclass with `**row`.
    """
    stmt = (
        select(
            CategoryTemplate.template_key,
            CategoryTemplate.name,
            CategoryTemplate.short_label,
            CategoryTemplate.icon,
            CategoryTemplate.colour,
            CategoryTemplate.kind,
            CategoryTemplate.default_bucket,
            CategoryTemplate.flexibility,
            CategoryTemplate.suggested_share_pct,
            CategoryTemplate.sort_order,
        )
        .where(
            CategoryTemplate.is_active.is_(True),
            CategoryTemplate.sort_order < RESERVED_TEMPLATE_SORT_ORDER,
        )
        .order_by(
            CategoryTemplate.kind,
            CategoryTemplate.sort_order,
            CategoryTemplate.template_key,
        )
    )
    return [dict(row) for row in db.execute(stmt).mappings()]


def offered_template_version(db: Session) -> int:
    """Version of the seed set, so a client can tell a corrected list from a cached one."""
    stmt = select(func.max(CategoryTemplate.template_version)).where(
        CategoryTemplate.is_active.is_(True)
    )
    return db.execute(stmt).scalar_one() or 1


def enabled_currency_codes(db: Session) -> set[str]:
    """Codes a profile may be created with.

    Read rather than hardcoded to `INR` so that enabling another currency stays a data
    change, which is the whole reason the reference table exists. `CHAR(3)` is
    blank-padded, hence the strip.
    """
    stmt = select(Currency.code).where(Currency.is_enabled.is_(True))
    return {code.strip() for code in db.execute(stmt).scalars()}


def currency_minor_unit(db: Session, code: str) -> int | None:
    """Digits after the decimal point, so no client hardcodes 2 for paise."""
    stmt = select(Currency.minor_unit).where(Currency.code == code)
    return db.execute(stmt).scalar_one_or_none()


def get_auth_identity(db: Session, user_id: UUID) -> tuple[str | None, list[str]]:
    """Email and sign-in providers, read from Supabase's own `auth.users`.

    `GET /me` reports both and neither is a column on `profile`. Reading Supabase's schema
    is a coupling, accepted because it is the authoritative copy and the alternative —
    threading decoded JWT claims out of `app/auth.py` — means changing the token
    verification path to obtain two display fields.

    Filtered by `id`, which *is* the tenancy filter here: `profile.user_id` references this
    table, so the row and the caller are the same person by construction.
    """
    stmt = text(
        "SELECT email, COALESCE(raw_app_meta_data -> 'providers', '[]'::jsonb) AS providers "
        "FROM auth.users WHERE id = :user_id"
    )
    row = db.execute(stmt, {"user_id": user_id}).mappings().one_or_none()
    if row is None:
        return None, []
    return row["email"], list(row["providers"])


def insert_onboarding(
    db: Session,
    user_id: UUID,
    *,
    profile: RowDict,
    categories: list[RowDict],
    accounts: list[RowDict],
    budget_period: RowDict,
    budget_limits: list[RowDict],
) -> None:
    """Write the whole bootstrap. One transaction, committed by the session dependency.

    **Order is the correctness mechanism.** `budget_limit` has a BEFORE INSERT trigger that
    runs its own `SELECT kind, default_bucket FROM category WHERE id = NEW.category_id`, so
    the categories must already be on the connection or every limit raises
    `CATEGORY_NOT_FOUND`. Categories before period before limits is required; profile and
    accounts depend on nothing.

    **No `flush()`, and no `db.add()`.** These are Core inserts, so `Session.execute` emits
    each statement immediately and they land in the order written. Mixing in a single
    `db.add()` would leave that row pending until some later autoflush and silently reorder
    the stages — which is why `.claude/rules/repositories.md` mandates the
    `on_conflict_do_nothing` form in the first place.

    `user_id` is stamped from the argument onto every row rather than trusted from the row
    itself. Rule 1 in `CLAUDE.md` is the unrecoverable one, and this is the single point
    where every onboarding row is written, so it is the cheapest place to make a service bug
    unable to cross tenants.

    `ON CONFLICT (id) DO NOTHING` makes a replay carrying the same ids a no-op, but it
    covers only the primary key. `period_no_overlap`, `category_unique_name`,
    `account_unique_name`, `account_one_default` and
    `budget_limit_one_per_category_per_period` are all still live — which is why the caller
    decides replay-versus-conflict with a read *before* calling this at all.
    """
    db.execute(
        insert(Profile)
        .values(_owned_by(profile, user_id))
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    db.execute(
        insert(Category)
        .values([_owned_by(row, user_id) for row in categories])
        .on_conflict_do_nothing(index_elements=["id"])
    )
    if accounts:
        db.execute(
            insert(Account)
            .values([_owned_by(row, user_id) for row in accounts])
            .on_conflict_do_nothing(index_elements=["id"])
        )
    db.execute(
        insert(BudgetPeriod)
        .values(_owned_by(budget_period, user_id))
        .on_conflict_do_nothing(index_elements=["id"])
    )
    if budget_limits:
        db.execute(
            insert(BudgetLimit)
            .values([_owned_by(row, user_id) for row in budget_limits])
            .on_conflict_do_nothing(index_elements=["id"])
        )


def _owned_by(row: RowDict, user_id: UUID) -> RowDict:
    return {**row, "user_id": user_id}


def get_budget_period(
    db: Session, user_id: UUID, budget_period_id: UUID
) -> BudgetPeriod | None:
    """Whether this exact period already exists for this user.

    This is the idempotency probe for `POST /onboarding/complete`. It has to run before any
    insert: a replay carrying a *different* period id would otherwise hit
    `period_no_overlap`, a gist exclusion constraint that `ON CONFLICT` cannot name as an
    arbiter, and the user would get a 500 instead of an answer.
    """
    stmt = select(BudgetPeriodLive).where(
        BudgetPeriodLive.user_id == user_id,
        BudgetPeriodLive.id == budget_period_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_categories(db: Session, user_id: UUID) -> list[Category]:
    stmt = (
        select(CategoryLive)
        .where(CategoryLive.user_id == user_id)
        .order_by(CategoryLive.kind, CategoryLive.sort_order, CategoryLive.name)
    )
    return list(db.execute(stmt).scalars())


def list_accounts(db: Session, user_id: UUID) -> list[Account]:
    stmt = (
        select(AccountLive)
        .where(AccountLive.user_id == user_id)
        .order_by(AccountLive.sort_order, AccountLive.name)
    )
    return list(db.execute(stmt).scalars())


def list_budget_limits(
    db: Session, user_id: UUID, budget_period_id: UUID
) -> list[BudgetLimit]:
    stmt = (
        select(BudgetLimitLive)
        .where(
            BudgetLimitLive.user_id == user_id,
            BudgetLimitLive.budget_period_id == budget_period_id,
        )
        .order_by(BudgetLimitLive.category_id)
    )
    return list(db.execute(stmt).scalars())
