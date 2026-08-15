"""Transaction rules, framework-free.

Imports neither `fastapi` nor `sqlalchemy`, so every rule below — bucket ownership, kind
matching, period membership, the safe-to-spend chain, whether today's streak just ticked —
is testable from plain dicts with no database and no HTTP.

The router reads rows, hands them here, and writes what comes back. Nothing in this module
touches a session or a clock: `now` arrives as an argument.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.lib.dates import days_in_period, period_bounds, to_local_date
from app.schemas.txn import (
    BudgetImpactOut,
    CategoryImpactOut,
    HabitOut,
    SafeToSpendOut,
    TxnCategoryOut,
    TxnCreate,
    TxnCreateOut,
    TxnOut,
)
from app.services.errors import (
    CategoryArchivedError,
    CategoryNotFoundError,
    KindMismatchError,
)
from app.services.me import RowDict, RowValue, as_date, as_int, as_uuid

# Phase 1 has no committed-unpaid tracking: every EMI is logged as it is paid rather than
# scheduled, so there is nothing outstanding to reserve out of safe-to-spend.
COMMITTED_UNPAID_MINOR = 0

# A day's spending may run to one and a half times the even daily share before the number
# stops being useful as a signal. Without the cap, an untouched budget early in the period
# shows a figure so large it reads as permission.
SAFE_TO_SPEND_CAP_MULTIPLE_BPS = 15_000

# Beyond five points either side of the elapsed share, "on pace" stops being true.
PACE_TOLERANCE_PCT = 5.0

# `flexibility = 1` is committed money — rent, an EMI. So is anything in DEBT or EXCLUDED.
# The seeded templates deliberately override the bucket default for exactly this reason:
# Rent seeds at 1 so safe-to-spend never treats it as day-to-day money.
COMMITTED_FLEXIBILITY = 1
COMMITTED_BUCKETS = frozenset({"DEBT", "EXCLUDED"})
DEFAULT_FLEXIBILITY = 3


@dataclass(frozen=True)
class PeriodWindow:
    """A period and where today sits inside it.

    `days_remaining` includes today and is never zero — it is a divisor, and a period whose
    last day offered a zero denominator would fail on the one day it matters.
    """

    id: UUID
    starts_on: date
    ends_on: date
    local_today: date

    @property
    def days_in_period(self) -> int:
        return days_in_period(self.starts_on, self.ends_on)

    @property
    def days_completed(self) -> int:
        """Whole days already gone. Today is in progress, so it does not count."""
        if self.local_today <= self.starts_on:
            return 0
        elapsed = (min(self.local_today, self.ends_on) - self.starts_on).days
        return elapsed

    @property
    def days_remaining(self) -> int:
        if self.local_today >= self.ends_on:
            return 1
        return (self.ends_on - self.local_today).days + 1


def window_from_period(period: RowDict, local_today: date) -> PeriodWindow:
    return PeriodWindow(
        id=as_uuid(period["id"], "budget_period.id"),
        starts_on=as_date(period["starts_on"], "budget_period.starts_on"),
        ends_on=as_date(period["ends_on"], "budget_period.ends_on"),
        local_today=local_today,
    )


def local_dates(
    occurred_at: datetime, created_at: datetime, timezone: str
) -> tuple[date, date]:
    """`occurred_on_local` and `created_on_local`, both in the profile's zone.

    They are separate on purpose. Period membership keys off `occurred_on_local`, so
    backfilling last Tuesday puts the spend in last Tuesday's period. The habit day keys off
    `created_on_local`, so backfilling does *not* retroactively earn last Tuesday's streak.
    """
    return to_local_date(occurred_at, timezone), to_local_date(created_at, timezone)


def resolve_period_bounds(occurred_on_local: date, month_start_day: int) -> tuple[date, date]:
    """The period that must exist for this transaction to belong somewhere.

    The same derivation onboarding used for the first period, so a backfilled date and a
    fresh one land on identical boundaries. It works for a date before the first period as
    well as after the current one — `period_bounds` is a pure function of the date and the
    salary day, with no notion of which periods happen to have been materialised.
    """
    return period_bounds(occurred_on_local, month_start_day)


def check_category_is_usable(category: RowDict | None, direction: str) -> RowDict:
    """Exists, is the caller's, is not archived, and matches the direction.

    The repository returns None for both "no such row" and "someone else's row", and both
    are a 404 — a 403 for a stranger's category would confirm that it exists.
    """
    if category is None:
        raise CategoryNotFoundError()

    if category["is_archived"] is True:
        raise CategoryArchivedError()

    kind = str(category["kind"])
    if direction == "out" and kind != "expense":
        raise KindMismatchError(direction, kind)
    if direction == "in" and kind != "income":
        raise KindMismatchError(direction, kind)

    return category


def resolve_bucket(payload: TxnCreate, category: RowDict) -> str | None:
    """The bucket this transaction owns, from now on.

    An explicit bucket overrides the category default for this one row. An absent one is
    filled from the category's `default_bucket` **at the moment of write** and then belongs
    to the transaction: later editing the category's default does not rewrite history. That
    is what stops the data becoming fiction by month two.

    Income has no bucket at all, which the schema has already enforced.
    """
    if payload.direction == "in":
        return None
    if payload.bucket is not None:
        return payload.bucket

    default_bucket = category["default_bucket"]
    return None if default_bucket is None else str(default_bucket)


def build_txn_row(
    payload: TxnCreate,
    user_id: UUID,
    category: RowDict,
    budget_period_id: UUID,
    occurred_at: datetime,
    created_at: datetime,
    timezone: str,
) -> RowDict:
    """Exactly the columns to insert.

    `is_excluded` is false: in Phase 1 nothing sets it true. Transfers and adjustments will,
    and both are Phase 2.

    `merchant_normalized` is written here rather than by a trigger because it is the key the
    Phase 2 merchant rollup groups on, and `lower(trim(...))` in one place is cheaper to
    reason about than the same expression in a trigger and a query.

    `currency_code` is absent: the column defaults to `'INR'`, and Phase 1 enables exactly
    one currency, so sending it would be the application restating a database default.
    """
    occurred_on_local, created_on_local = local_dates(occurred_at, created_at, timezone)
    merchant = payload.merchant.strip() if payload.merchant is not None else None

    return {
        "id": payload.id,
        "user_id": user_id,
        "category_id": as_uuid(category["id"], "category.id"),
        "account_id": payload.account_id,
        "budget_period_id": budget_period_id,
        "amount_minor": payload.amount_minor,
        "direction": payload.direction,
        "bucket": resolve_bucket(payload, category),
        "occurred_at": occurred_at,
        "occurred_on_local": occurred_on_local,
        "created_at": created_at,
        "created_on_local": created_on_local,
        "updated_at": created_at,
        "merchant": merchant,
        "merchant_normalized": None if merchant is None else merchant.lower(),
        "note": payload.note,
        "why": payload.why,
        "is_excluded": False,
        "source": payload.source,
        "entry_method": payload.entry_method,
        "entry_duration_ms": payload.entry_duration_ms,
        "edited_before_save": payload.edited_before_save,
    }


def build_period_row(
    user_id: UUID,
    starts_on: date,
    ends_on: date,
    profile: RowDict,
) -> RowDict:
    """A period materialised because a transaction needed one.

    No `id`: the repository mints it. This row is derived rather than client-supplied, so a
    service that generated the id would be non-deterministic and harder to test for no gain.

    Percentages come from the profile's baseline split rather than being copied from a
    neighbouring period, because there may not be one — this path also covers a date before
    the first period. `expected_income_minor` seeds from `profile.monthly_income_minor`,
    which is exactly what that field is documented to be: the baseline that seeds future
    periods.

    No limits are copied. Rollover carry is a Phase 2 concern (feature 5.5), and inventing
    limits here would silently create a budget the user never accepted.
    """
    income = profile["monthly_income_minor"]
    return {
        "user_id": user_id,
        "starts_on": starts_on,
        "ends_on": ends_on,
        "expected_income_minor": 0 if income is None else as_int(income, "income"),
        "pct_needs": 50,
        "pct_wants": 30,
        "pct_future": 20,
        "pct_debt": 0,
    }


def is_committed(flexibility: RowValue, default_bucket: RowValue) -> bool:
    """Money that is already promised, and so not day-to-day spendable.

    `COALESCE(flexibility, 3)` is the documented default. Using the bucket default naively
    would classify Rent as flexible, which is why the seed overrides it to 1.
    """
    effective = DEFAULT_FLEXIBILITY if flexibility is None else as_int(flexibility, "flex")
    if effective == COMMITTED_FLEXIBILITY:
        return True
    return default_bucket is not None and str(default_bucket) in COMMITTED_BUCKETS


def safe_to_spend_minor(limits: list[RowDict], window: PeriodWindow) -> int:
    """Today's spendable figure, from the flexible categories only.

    The chain: flexible categories are the expense ones that hold a limit in this period and
    are not committed. Their unspent remainder, divided by the days left including today,
    capped at one and a half times the even daily share.

    `MAX(0, ...)` per category matters: without it an overspent category lends its overspend
    to every other category and the figure quietly grows after a bad day.
    """
    flexible_remaining = 0
    flexible_limit_total = 0

    for row in limits:
        if is_committed(row["flexibility"], row["default_bucket"]):
            continue
        effective_limit = as_int(row["limit_minor"], "limit_minor") + as_int(
            row["carried_in_minor"], "carried_in_minor"
        )
        spent = as_int(row["spent_minor"], "spent_minor")
        flexible_limit_total += effective_limit
        flexible_remaining += max(0, effective_limit - spent)

    uncapped = max(0, (flexible_remaining - COMMITTED_UNPAID_MINOR) // window.days_remaining)
    baseline_daily = flexible_limit_total // window.days_in_period
    cap = baseline_daily * SAFE_TO_SPEND_CAP_MULTIPLE_BPS // 10_000

    return min(uncapped, cap)


def _round_one_dp(numerator: int, denominator: int) -> float:
    """`numerator / denominator` as a percentage to one decimal place.

    Integer arithmetic until the final division, so the result cannot depend on float
    association order, and rounded **half up** rather than truncated. Truncating is wrong
    here and visibly so: 14 days of a 31-day period is 45.161%, which the spec reports as
    45.2, and flooring yields 45.1.

    Python's own `round` is banker's rounding, which disagrees with the `ROUND(x, 1)` the
    spec specifies, so the half is added before the division instead.
    """
    tenths_of_a_percent = (numerator * 1000 + denominator // 2) // denominator
    return tenths_of_a_percent / 10


def pace_status(spent_pct: float | None, pace_pct: float, remaining_minor: int) -> str:
    """Breach first: an over-budget category is not merely off pace.

    With no limit to measure against there is no pace to be off, so a null `spent_pct`
    reports `on_pace` rather than inventing a verdict.
    """
    if remaining_minor < 0:
        return "breached"
    if spent_pct is None:
        return "on_pace"
    if spent_pct - pace_pct > PACE_TOLERANCE_PCT:
        return "over_pace"
    if pace_pct - spent_pct > PACE_TOLERANCE_PCT:
        return "under_pace"
    return "on_pace"


def category_impact(
    limit: RowDict | None, spent_minor: int, window: PeriodWindow
) -> CategoryImpactOut | None:
    """None when there is no limit to report against.

    An income row never has one, and neither does an expense in a category the user chose
    not to budget. Reporting zeroes instead would have the client render a remaining figure
    derived from a limit that does not exist.
    """
    if limit is None:
        return None

    effective_limit = as_int(limit["limit_minor"], "limit_minor") + as_int(
        limit["carried_in_minor"], "carried_in_minor"
    )
    remaining = effective_limit - spent_minor
    spent_pct = None if effective_limit == 0 else _round_one_dp(spent_minor, effective_limit)
    pace_pct = _round_one_dp(window.days_completed, window.days_in_period)

    return CategoryImpactOut(
        effective_limit_minor=effective_limit,
        spent_minor=spent_minor,
        remaining_minor=remaining,
        spent_pct=spent_pct,
        pace_pct=pace_pct,
        pace_status=pace_status(spent_pct, pace_pct, remaining),
        # Not defined anywhere in the spec, which uses it and never says what it means. Read
        # as the same condition `pace_status = 'breached'` tests, because two fields in one
        # block disagreeing about whether a category is over budget would be worse.
        breached=remaining < 0,
    )


def habit_out(
    *, streak: RowDict | None, local_today: date, newly_logged: bool
) -> HabitOut:
    """Streak state as the save confirmation should show it.

    **This does not write `streak`.** The habit day is derived from `habit_log`, which this
    endpoint does upsert; the `streak` row's own counters are maintained by the nightly
    `streak_rollover` job. Writing them here as well would double-count the moment the job
    ran over the same day.

    But the confirmation cannot show yesterday's number either, so when today has just
    become logged and the stored row has not yet caught up, the displayed count is advanced
    by one for this response only. That is a presentation decision, not a stored one.
    """
    if streak is None:
        return HabitOut(
            current=1 if newly_logged else 0,
            longest=1 if newly_logged else 0,
            logged_today=newly_logged,
            incremented=newly_logged,
        )

    current = as_int(streak["current"], "streak.current")
    longest = as_int(streak["longest"], "streak.longest")
    last_active = streak["last_active_date"]
    already_counted = last_active is not None and as_date(last_active, "last") >= local_today

    if newly_logged and not already_counted:
        current += 1
        longest = max(longest, current)

    return HabitOut(
        current=current,
        longest=longest,
        logged_today=True,
        incremented=newly_logged,
    )


def row_style(txn: RowDict) -> str:
    """Computed, never stored. In Phase 1 it is always `normal`.

    Derived rather than hardcoded so transfers and recurring rows need no contract change.
    """
    if txn.get("transfer_peer_txn_id") is not None:
        return "transfer"
    if txn.get("is_adjustment") is True:
        return "adjustment"
    if str(txn.get("source")) == "recurring":
        return "recurring"
    return "normal"


def txn_out(txn: RowDict, category: RowDict) -> TxnOut:
    """The stored row, with its category nested rather than flattened beside an id."""
    return TxnOut(
        id=as_uuid(txn["id"], "txn.id"),
        direction=str(txn["direction"]),  # type: ignore[arg-type]
        amount_minor=as_int(txn["amount_minor"], "amount_minor"),
        category=TxnCategoryOut(
            id=as_uuid(category["id"], "category.id"),
            name=str(category["name"]),
            icon=str(category["icon"]),
            colour=str(category["colour"]),
            kind=str(category["kind"]),  # type: ignore[arg-type]
            default_bucket=category["default_bucket"],  # type: ignore[arg-type]
        ),
        bucket=txn["bucket"],  # type: ignore[arg-type]
        occurred_at=txn["occurred_at"],  # type: ignore[arg-type]
        created_at=txn["created_at"],  # type: ignore[arg-type]
        updated_at=txn["updated_at"],  # type: ignore[arg-type]
        why=txn["why"],  # type: ignore[arg-type]
        note=None if txn["note"] is None else str(txn["note"]),
        merchant=None if txn["merchant"] is None else str(txn["merchant"]),
        source=str(txn["source"]),  # type: ignore[arg-type]
        is_excluded=bool(txn["is_excluded"]),
        row_style=row_style(txn),  # type: ignore[arg-type]
        budget_period_id=None
        if txn["budget_period_id"] is None
        else as_uuid(txn["budget_period_id"], "budget_period_id"),
        version=as_int(txn["version"], "txn.version"),
    )


def create_out(
    *,
    currency_code: str,
    minor_unit: int,
    txn: RowDict,
    category: RowDict,
    window: PeriodWindow,
    limit: RowDict | None,
    category_spent_minor: int,
    flexible_limits: list[RowDict],
    streak: RowDict | None,
    newly_logged: bool,
) -> TxnCreateOut:
    """Everything the entry screen needs to confirm a save without a second round trip."""
    return TxnCreateOut(
        currency_code=currency_code,
        minor_unit=minor_unit,
        transaction=txn_out(txn, category),
        budget_impact=BudgetImpactOut(
            budget_period_id=window.id,
            category=category_impact(limit, category_spent_minor, window),
            safe_to_spend=SafeToSpendOut(
                displayed_minor=safe_to_spend_minor(flexible_limits, window)
            ),
        ),
        habit=habit_out(
            streak=streak, local_today=window.local_today, newly_logged=newly_logged
        ),
    )
