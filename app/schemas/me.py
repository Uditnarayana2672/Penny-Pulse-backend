"""Wire types for `/me`. Separate from the ORM model on purpose (delta D7)."""

from datetime import date, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.lib.dates import DEFAULT_TIMEZONE
from app.services.errors import InvalidMonthStartDayError

Theme = Literal["system", "light", "dark"]


def _check_month_start_day(value: int) -> int:
    """1..28, with its own error code rather than a generic validation failure.

    The range is enforced here instead of as `Field(ge=1, le=28)` because a `Field`
    constraint fails before any validator runs and can only ever report
    `validation_failed`. The bound is a real rule the client explains to the user — 29, 30
    and 31 do not exist in February — so it gets the code the spec names for it.

    Lives on `/me` rather than on onboarding because `month_start_day` is a `profile`
    column: `PATCH /me` and the two onboarding bodies all need it, and `schemas/onboarding`
    already imports from here, so the reverse direction would be a cycle.
    """
    if not 1 <= value <= 28:
        raise InvalidMonthStartDayError(value)
    return value


MonthStartDay = Annotated[int, AfterValidator(_check_month_start_day)]


class ProfileOut(BaseModel):
    """The profile as a client may see it.

    `role` is readable because Settings shows it, and read-only everywhere: column UPDATE
    is revoked from the client roles and a trigger guards it. No endpoint accepts it as
    input — that is the only defence that actually applies here, because the trigger is
    gated on `current_user IN ('authenticated','anon')` and this backend connects as
    `postgres`.

    `notify_time_local` is a wall-clock time in `timezone` with no date and no offset. It
    deliberately does not end in `_at`, because every `_at` field in this API is a UTC
    instant and a client parsing by suffix would break.
    """

    display_name: str | None
    timezone: str
    preferred_currency_code: str
    monthly_income_minor: int | None
    month_start_day: int
    notify_time_local: time | None
    implementation_intention: str | None
    theme: str
    role: str
    onboarding_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class MeOut(BaseModel):
    """Identity and onboarding state in one call.

    `profile` is null until onboarding has run, and `onboarding_required` is the flag the
    client routes on. Those two answer the single question this endpoint exists for: has
    onboarding happened. There is nothing finer to report — `POST /onboarding/complete` is
    the only path that creates a profile and it is one atomic transaction, so a
    half-finished setup is not a state the database can hold.

    `currency_code` and `minor_unit` are present even before onboarding, though there is no
    money in the payload yet, because the income field on step 2 has to render a currency
    before a profile exists and nothing should hardcode either value.
    """

    user_id: UUID
    email: str | None
    auth_providers: list[str]
    onboarding_required: bool
    currency_code: str
    minor_unit: int
    profile: ProfileOut | None


class MeUpdate(BaseModel):
    """A partial profile update: only the keys present in the body change.

    **Absent and explicit null are different.** A key sent as `null` clears a nullable
    field; a key left out is untouched. That is why the router dumps this with
    `exclude_unset=True` and never `exclude_none` — the latter would make
    `notify_time_local` and `implementation_intention` impossible to clear once set, which
    is exactly the pair a user is most likely to want to turn off.

    `extra="forbid"` so a misspelled key is a 422 rather than a silent no-op. A PATCH that
    reports success and changed nothing is the worst available outcome.

    The four fields below the blank line map to NOT NULL columns. The default on each
    exists only because Pydantic needs one to treat the field as optional, and
    `exclude_unset` means it is never read; sending an explicit `null` for any of them is a
    422, which is correct — there is no "unset" state for `theme` or `timezone` in the
    database.

    `role`, `onboarding_completed_at` and `version` are absent by design: server-owned on
    every endpoint. `locale`, `household_id`, `month_start_day_effective_from`,
    `onboarding_first_completed_at` and the quiet-hours pair are columns no Phase 1 screen
    reads, so they are not on the wire either.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    monthly_income_minor: int | None = Field(default=None, ge=0)
    notify_time_local: time | None = None
    implementation_intention: str | None = None

    timezone: str = DEFAULT_TIMEZONE
    preferred_currency_code: str = Field(default="INR", min_length=3, max_length=3)
    month_start_day: MonthStartDay = 1
    theme: Theme = "system"


class MonthStartDayChangeOut(BaseModel):
    """What moving the salary date did to the periods, stated rather than implied.

    The block is always present, with nulls inside when nothing changed, so a client can
    read `.changed` without first testing the block for null. The spec shows only the
    `changed: true` shape and never says what the quiet case looks like.

    `current_period_extended_to` is the current period's new inclusive `ends_on`. It is
    null when no period covers today — periods are materialised lazily, so there may be
    nothing to extend yet.

    `effective_from_period_starts_on` is where the next period will begin. It is a
    statement of intent, not a row: nothing is created here, because a period comes into
    existence on first request for a date inside it.
    """

    changed: bool
    previous_value: int | None
    new_value: int | None
    current_period_extended_to: date | None
    effective_from_period_starts_on: date | None


class MeUpdateOut(BaseModel):
    """The stored profile, plus the consequences of a salary-date change.

    `profile` is the same `ProfileOut` that `GET /me` and `POST /onboarding/complete`
    return, so a client has one profile shape to parse rather than three.
    """

    currency_code: str
    minor_unit: int
    profile: ProfileOut
    month_start_day_change: MonthStartDayChangeOut
