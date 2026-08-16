"""Wire types for the accounts screen.

An account is a **label** in Phase 1 (delta D3): it answers "where did this go out from" and
carries no balance column, because displaying a balance means owning its correctness forever
and it drifts inside two weeks.

What this screen does carry is an **opening amount** — one immutable observation of what the
user says an account held at a moment, stored as a `balance_anchor` with `is_opening`. That is
not the same thing as a balance and must not be presented as one: it is reported alongside the
date it was observed and is never added up across accounts, never adjusted for spending since,
and never refreshed. Deriving a current balance from it is Phase 2, where the anchor and
estimate machinery makes the arithmetic honest.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.onboarding import AccountType


class OpeningBalanceOut(BaseModel):
    """What the user said an account held, and when they said it.

    `observed_on_local` travels with the amount deliberately. A figure with no date reads as
    "your balance", which is the claim Phase 1 does not make; with one it reads as a note the
    user wrote, which is exactly what it is.

    `is_locked` tells the screen whether to offer an edit at all, rather than letting the user
    type a correction and meet a 409. It turns true once the account has a transaction.
    """

    amount_minor: int
    observed_at: datetime
    observed_on_local: date
    is_locked: bool


class AccountOut(BaseModel):
    """One account row.

    `is_liability` is derived from `type` by the server, never sent by the client — a credit
    card is a liability whatever a request claims.

    `opening_balance` is null until the user sets one, and stays null for accounts they never
    bother with. That absence is a real state the screen shows as "not set", not a zero.
    """

    id: UUID
    name: str
    type: AccountType
    icon: str | None
    is_liability: bool
    is_default: bool
    is_archived: bool
    # Sent always, so the screen can choose Archive over Delete without a probe request, and
    # can explain why an opening amount is locked.
    transaction_count: int
    opening_balance: OpeningBalanceOut | None
    created_at: datetime
    updated_at: datetime
    version: int


class AccountListOut(BaseModel):
    """Not paginated: delta D3 confirms 2–3 accounts in normal use, and archived ones are
    included so the screen's counts are true without a second request."""

    currency_code: str
    minor_unit: int
    items: list[AccountOut]


class AccountCreate(BaseModel):
    """`id` is client-generated, so a retried POST is a replay rather than a second account."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=80)
    type: AccountType = "cash"
    is_default: bool = False
    icon: str | None = Field(default=None, min_length=1)
    # Optional at create because most people name the account first and find the figure later.
    # `ge=0` and not `gt=0`: an emptied wallet is a real observation, and 0 is not "unset" —
    # omitting the field is.
    opening_balance_minor: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def a_name_is_more_than_whitespace(cls, value: str) -> str:
        # `name_normalized` is lower(trim(name)); "   " normalises to "" and would collide
        # with the next blank one, so it is refused at the edge where the field can be named.
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class AccountPatch(BaseModel):
    """Partial update. Only the keys present are changed.

    `type` is editable, unlike a category's `kind`: picking `bank` when it was a wallet is an
    ordinary mistake, and nothing about a transaction already written depends on it. Changing
    it re-derives `is_liability`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    type: AccountType | None = None
    is_default: bool | None = None
    icon: str | None = Field(default=None, min_length=1)
    opening_balance_minor: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def a_name_is_more_than_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class AccountArchiveOut(BaseModel):
    id: UUID
    is_archived: bool
    version: int
