"""Wire types for `/me`. Separate from the ORM model on purpose (delta D7)."""

from datetime import datetime, time
from uuid import UUID

from pydantic import BaseModel


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
