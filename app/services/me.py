"""Identity and profile rules, plus the wire shapes built from them.

Imports neither `fastapi` nor `sqlalchemy`, so everything here is testable with no database
and no HTTP. Rows arrive as plain dicts from the repository; this module decides what they
mean and returns the response schema.

`services/onboarding.py` imports from this module, never the other way round. That single
direction is what keeps `profile_out` in one place: the onboarding response carries a
profile too, and before this module existed the onboarding *router* imported it from the
`/me` router.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from app.lib.dates import next_occurrence_on_or_after, to_local_date
from app.schemas.me import MeOut, MeUpdate, MeUpdateOut, MonthStartDayChangeOut, ProfileOut
from app.services.errors import CurrencyNotSupportedError, DomainError, VersionConflictError

# Third occurrence of this alias; the other two are in `services/onboarding.py` and
# `repositories/onboarding.py`, each explaining why it is restated rather than shared. By
# the three-before-extracting rule it has now earned a module of its own — but that is a
# new file outside the five, which `CLAUDE.md` says to ask about. Flagged, not done.
#
# `time` is in the union because `notify_time_local` is a TIME column, so `get_profile`
# has always returned one.
RowValue = str | int | bool | UUID | date | datetime | time | None
RowDict = dict[str, RowValue]

# What to report before a profile exists to state a preference. Phase 1 seeds only INR, and
# `enabled_currency_codes` is what actually gates writes.
FALLBACK_CURRENCY_CODE = "INR"


def currency_code_for(profile: RowDict | None) -> str:
    """The currency this response should report.

    `preferred_currency_code` is `CHAR(3)` and therefore blank-padded by Postgres. The pad
    must not reach the wire: a client comparing `"INR "` to `"INR"` fails a check nobody
    thinks to look at.
    """
    if profile is None:
        return FALLBACK_CURRENCY_CODE
    return str(profile["preferred_currency_code"]).strip()


def require_minor_unit(code: str, minor_unit: int | None) -> int:
    """Assert the currency reference row was actually found.

    Migration 0009 seeds INR and every write path checks the code against
    `enabled_currency_codes` first, so a missing row here means the reference data is gone —
    a broken database rather than a bad request, hence a 500 and not a 422.
    """
    if minor_unit is None:
        raise DomainError(f"Currency {code} is missing from the reference table.")
    return minor_unit


def profile_out(profile: RowDict) -> ProfileOut:
    """One `profile` row as the wire shape.

    The repository selects exactly the columns this schema declares; Pydantic ignores any
    extra, so adding a column to the select cannot break the response.
    """
    return ProfileOut(**{**profile, "preferred_currency_code": currency_code_for(profile)})


def me_out(
    user_id: UUID,
    email: str | None,
    auth_providers: list[str],
    profile: RowDict | None,
    minor_unit: int,
) -> MeOut:
    """Identity plus onboarding state.

    `profile is None` is the whole answer to "has onboarding run". There is nothing finer to
    report: `POST /onboarding/complete` is the only path that creates a profile and it is one
    transaction, so a half-finished setup is not a state the database can hold.
    """
    return MeOut(
        user_id=user_id,
        email=email,
        auth_providers=auth_providers,
        onboarding_required=profile is None,
        currency_code=currency_code_for(profile),
        minor_unit=minor_unit,
        profile=None if profile is None else profile_out(profile),
    )


@dataclass(frozen=True)
class MonthStartDayChange:
    """A salary-date move, and everything it implies.

    `current_period_extended_to` and `period_to_extend` are both None when no period covers
    today — periods are materialised lazily, so a user who has not opened Budget since
    onboarding may have only the first one, and one whose first period has closed may have
    none. They are always either both set or both None.
    """

    previous_value: int
    new_value: int
    current_period_extended_to: date | None
    effective_from_period_starts_on: date
    period_to_extend: UUID | None


# Narrowing a `RowValue` to the type its column actually has. Public rather than private
# because `services/txn.py` needs the same three, and reaching into another module's
# underscored names is worse than sharing them deliberately. A row that fails one of these
# means the repository's select and this module disagree, which is a bug here and not a bad
# request — hence AssertionError and a 500, not a 422.
def as_int(value: RowValue, column: str) -> int:
    # `bool` is a subclass of `int`, and a boolean arriving where an integer belongs is
    # exactly the kind of column mix-up worth catching.
    if not isinstance(value, int) or isinstance(value, bool):
        raise AssertionError(f"{column} is an integer column")
    return value


def as_date(value: RowValue, column: str) -> date:
    # `datetime` subclasses `date`, so the order of these checks matters.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise AssertionError(f"{column} is a DATE column")
    return value


def as_uuid(value: RowValue, column: str) -> UUID:
    if not isinstance(value, UUID):
        raise AssertionError(f"{column} is a UUID column")
    return value


def as_str_or_none(value: RowValue) -> str | None:
    """A nullable TEXT column, without turning None into the string "None"."""
    return None if value is None else str(value)


def require_version_match(if_match: int | None, profile: RowDict) -> None:
    """Optimistic concurrency, opt-in.

    `If-Match` is optional by design (spec §2.1): omitting it is last-write-wins, which is
    what a single-device Phase 1 client does. Sending it asks to be told when another
    device got there first.
    """
    current_version = as_int(profile["version"], "profile.version")
    if if_match is not None and if_match != current_version:
        raise VersionConflictError(expected=if_match, actual=current_version)


def local_today_for(profile: RowDict, now: datetime) -> date:
    """Today, in the profile's own timezone.

    Which day it is decides which period gets extended, and a server in another region must
    not change that answer.
    """
    return to_local_date(now, str(profile["timezone"]))


def require_supported_currency(code: str, enabled_currency_codes: set[str]) -> str:
    """The enabled set is read from the `currency` table, never hardcoded to INR.

    Same rule and same normalisation as the onboarding bootstrap, so a code accepted at
    signup cannot be rejected on the Settings screen. Enabling a second currency stays a
    data change.
    """
    normalised = code.upper()
    if normalised not in enabled_currency_codes:
        raise CurrencyNotSupportedError(normalised)
    return normalised


def plan_month_start_day_change(
    profile: RowDict,
    patch: RowDict,
    current_period: RowDict | None,
    local_today: date,
) -> MonthStartDayChange | None:
    """None when the salary date did not move.

    The next period begins at the first occurrence of the new day **after the current
    period ends**, and the current period is extended to the day before it. Anchoring on
    `ends_on` rather than on today is what makes the move strictly forward, which is the
    only direction the spec describes: "moves the current period's `ends_on` forward".

    Anchoring on today instead would let a later salary date *shrink* the current period —
    moving 1 to 20 on the 11th would end the period on the 19th, orphaning nothing but
    re-dating a period the user has already been spending against. The spec says nothing
    about shrinking, so this implementation cannot produce it.

    No period is created here. `effective_from_period_starts_on` says where the next one
    will start; it comes into existence on the first request for a date inside it (spec
    5.5), and creating it now would also mean guessing its limits.
    """
    previous_value = as_int(profile["month_start_day"], "profile.month_start_day")
    if "month_start_day" not in patch:
        return None

    requested = as_int(patch["month_start_day"], "month_start_day")
    if requested == previous_value:
        return None

    if current_period is None:
        return MonthStartDayChange(
            previous_value=previous_value,
            new_value=requested,
            current_period_extended_to=None,
            effective_from_period_starts_on=next_occurrence_on_or_after(
                local_today, requested
            ),
            period_to_extend=None,
        )

    ends_on = as_date(current_period["ends_on"], "budget_period.ends_on")
    next_start = next_occurrence_on_or_after(ends_on + timedelta(days=1), requested)
    return MonthStartDayChange(
        previous_value=previous_value,
        new_value=requested,
        current_period_extended_to=next_start - timedelta(days=1),
        effective_from_period_starts_on=next_start,
        period_to_extend=as_uuid(current_period["id"], "budget_period.id"),
    )


def profile_patch(payload: MeUpdate, change: MonthStartDayChange | None) -> RowDict:
    """Exactly the columns to write, and nothing else.

    `exclude_unset` is the whole mechanism for "send only what changes": an absent key is
    untouched and an explicit null clears a nullable column. `exclude_none` would collapse
    those two into one and make `notify_time_local` unclearable.

    `preferred_currency_code` is normalised by the caller before this runs, so it is
    overwritten here rather than validated again.

    `version` and `updated_at` are absent because the `profile_touch` trigger sets both —
    `touch_row()` does `NEW.version := OLD.version + 1`, so an application that also wrote
    `version` would either fight the trigger or silently double-count.
    """
    patch: RowDict = dict(payload.model_dump(exclude_unset=True))

    if change is not None:
        # The column's own migration comment is the reason it exists: "a month_start_day
        # change applies from the NEXT period, never retroactively. Storing the effective
        # date makes that structural rather than a rule the application has to remember."
        # Nothing reads it in Phase 1; writing it keeps that promise cheap to keep.
        patch["month_start_day_effective_from"] = change.effective_from_period_starts_on

    return patch


def month_start_day_change_out(change: MonthStartDayChange | None) -> MonthStartDayChangeOut:
    """Always a block, with nulls inside when nothing moved.

    The spec shows only the `changed: true` shape. Reporting a consistent object rather
    than sometimes-null lets the client read `.changed` without a null check first.
    """
    if change is None:
        return MonthStartDayChangeOut(
            changed=False,
            previous_value=None,
            new_value=None,
            current_period_extended_to=None,
            effective_from_period_starts_on=None,
        )

    return MonthStartDayChangeOut(
        changed=True,
        previous_value=change.previous_value,
        new_value=change.new_value,
        current_period_extended_to=change.current_period_extended_to,
        effective_from_period_starts_on=change.effective_from_period_starts_on,
    )


def update_out(
    profile: RowDict,
    minor_unit: int,
    change: MonthStartDayChange | None,
) -> MeUpdateOut:
    """The stored profile plus what the salary-date move did."""
    return MeUpdateOut(
        currency_code=currency_code_for(profile),
        minor_unit=minor_unit,
        profile=profile_out(profile),
        month_start_day_change=month_start_day_change_out(change),
    )
