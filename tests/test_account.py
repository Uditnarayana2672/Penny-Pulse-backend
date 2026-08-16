"""The accounts endpoints against a real migrated Postgres.

DB-backed rather than pure, and for a specific reason: nearly every rule here is a constraint.
`account_unique_name` is a partial unique index, `account_one_default` is another,
`anchor_one_opening_per_account` is a third, and `touch_row()` is a trigger. A version of these
tests that stubbed the database would assert the shape of my own code and prove nothing about
the four things that actually enforce the behaviour.

The line these tests defend hardest is delta D3: **no balance.** An account carries an opening
observation and a date, and nothing anywhere adds those up or nets them against spending. Two
tests below exist purely to fail if someone later adds a total.
"""

from uuid import uuid4

import pytest

from tests.test_onboarding import build_payload

pytestmark = pytest.mark.db


def onboard(client) -> None:
    client.post("/api/v1/onboarding/complete", json=build_payload(client))


def accounts(client, *, include_archived: bool = True) -> list[dict]:
    response = client.get(
        f"/api/v1/accounts?include_archived={str(include_archived).lower()}"
    )

    assert response.status_code == 200
    return response.json()["items"]


def named(client, name: str) -> dict:
    return next(account for account in accounts(client) if account["name"] == name)


def create(client, name: str, **extra) -> object:
    return client.post(
        "/api/v1/accounts", json={"id": str(uuid4()), "name": name, **extra}
    )


def spend_from(client, account_id: str, *, amount_minor: int = 5_000) -> object:
    """A real transaction through the real endpoint, so the lock is triggered the way it will be."""
    category_id = client.get("/api/v1/categories?kind=expense").json()["items"][0]["id"]
    return client.post(
        "/api/v1/transactions",
        json={
            "id": str(uuid4()),
            "direction": "out",
            "amount_minor": amount_minor,
            "category_id": category_id,
            "account_id": account_id,
        },
    )


# ---------- the list ------------------------------------------------------


def test_onboarding_accounts_are_listed(db_client, sign_in, make_user):
    """Onboarding already creates accounts, so the screen has rows before it is ever used."""
    sign_in(make_user())
    onboard(db_client)

    assert [account["name"] for account in accounts(db_client)] == ["Cash"]


def test_an_account_starts_with_no_opening_amount(db_client, sign_in, make_user):
    """Null, not zero. "Not set" and "empty" are different claims and the screen says so."""
    sign_in(make_user())
    onboard(db_client)

    assert named(db_client, "Cash")["opening_balance"] is None


def test_the_list_carries_no_total(db_client, sign_in, make_user):
    """D3, asserted rather than trusted.

    Summing opening amounts across accounts is a net-worth figure that is stale the moment the
    next transaction lands — precisely the number the app must not own the correctness of. If
    someone adds one, this fails.
    """
    sign_in(make_user())
    onboard(db_client)

    body = db_client.get("/api/v1/accounts").json()

    assert set(body) == {"currency_code", "minor_unit", "items"}


def test_no_account_reports_a_current_balance(db_client, sign_in, make_user):
    """The other half of D3: an account may carry an observation, never a derived balance."""
    sign_in(make_user())
    onboard(db_client)
    cash = named(db_client, "Cash")

    assert "balance_minor" not in cash
    assert "current_balance_minor" not in cash
    assert set(cash["opening_balance"] or {}) <= {
        "amount_minor",
        "observed_at",
        "observed_on_local",
        "is_locked",
    }


# ---------- creating -----------------------------------------------------


def test_an_account_can_be_created_with_its_amount(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)

    response = create(db_client, "HDFC", type="bank", opening_balance_minor=1_240_000)

    assert response.status_code == 201
    opening = response.json()["opening_balance"]
    assert opening["amount_minor"] == 1_240_000
    assert opening["is_locked"] is False
    # The date travels with the figure, so it reads as a note the user wrote rather than a
    # balance the app is standing behind.
    assert opening["observed_on_local"] is not None


def test_an_account_can_be_created_without_an_amount(db_client, sign_in, make_user):
    """Most people name the account first and find the figure later."""
    sign_in(make_user())
    onboard(db_client)

    response = create(db_client, "ICICI", type="bank")

    assert response.status_code == 201
    assert response.json()["opening_balance"] is None


def test_a_zero_amount_is_a_real_observation(db_client, sign_in, make_user):
    """An emptied wallet is something a user can truthfully say. Only omission means unset."""
    sign_in(make_user())
    onboard(db_client)

    response = create(db_client, "Paytm", type="wallet", opening_balance_minor=0)

    assert response.json()["opening_balance"]["amount_minor"] == 0


def test_a_negative_amount_is_refused(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)

    assert create(db_client, "Overdrawn", opening_balance_minor=-100).status_code == 422


def test_reposting_the_same_id_returns_the_same_account(db_client, sign_in, make_user):
    """Idempotency, mandatory per `.claude/rules/tests.md`: a retry is a replay, not a second row."""
    sign_in(make_user())
    onboard(db_client)
    account_id = str(uuid4())
    body = {"id": account_id, "name": "Axis", "type": "bank"}

    first = db_client.post("/api/v1/accounts", json=body)
    second = db_client.post("/api/v1/accounts", json=body)

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.headers["Idempotent-Replay"] == "true"
    assert len([a for a in accounts(db_client) if a["name"] == "Axis"]) == 1


def test_a_duplicate_name_is_refused(db_client, sign_in, make_user):
    """`account_unique_name` is the backstop; a user cannot act on a 23505."""
    sign_in(make_user())
    onboard(db_client)
    create(db_client, "Kotak", type="bank")

    response = create(db_client, "kotak", type="bank")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_account_name"


def test_a_blank_name_is_refused(db_client, sign_in, make_user):
    """`name_normalized` would be "" and the next blank one would collide with it."""
    sign_in(make_user())
    onboard(db_client)

    assert create(db_client, "   ").status_code == 422


# ---------- liability is derived -----------------------------------------


@pytest.mark.parametrize(
    ("account_type", "expected"),
    [
        ("credit_card", True),
        ("loan", True),
        ("cash", False),
        ("bank", False),
        ("wallet", False),
        ("investment", False),
    ],
)
def test_liability_follows_the_type(
    account_type: str, expected: bool, db_client, sign_in, make_user
):
    """A credit card is a liability whatever a request claims."""
    sign_in(make_user())
    onboard(db_client)

    body = create(db_client, f"Acct {account_type}", type=account_type).json()

    assert body["is_liability"] is expected


def test_a_client_cannot_set_liability_itself(db_client, sign_in, make_user):
    """`extra="forbid"`: a field the endpoint does not accept is reported, never dropped."""
    sign_in(make_user())
    onboard(db_client)

    assert create(db_client, "Sneaky", type="cash", is_liability=True).status_code == 422


def test_changing_the_type_re_derives_liability(db_client, sign_in, make_user):
    """Otherwise the flag contradicts the type it was derived from."""
    sign_in(make_user())
    onboard(db_client)
    account_id = create(db_client, "Amex", type="cash").json()["id"]

    body = db_client.patch(
        f"/api/v1/accounts/{account_id}", json={"type": "credit_card"}
    ).json()

    assert (body["type"], body["is_liability"]) == ("credit_card", True)


# ---------- the opening amount, and when it freezes ----------------------


def test_the_amount_can_be_set_after_the_fact(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)
    cash = named(db_client, "Cash")

    body = db_client.patch(
        f"/api/v1/accounts/{cash['id']}", json={"opening_balance_minor": 50_000}
    ).json()

    assert body["opening_balance"]["amount_minor"] == 50_000


def test_the_amount_can_be_corrected_while_nothing_has_been_spent(
    db_client, sign_in, make_user
):
    """A typo in a starting figure is not history. One anchor, replaced, never two."""
    sign_in(make_user())
    onboard(db_client)
    cash = named(db_client, "Cash")

    db_client.patch(f"/api/v1/accounts/{cash['id']}", json={"opening_balance_minor": 50_000})
    body = db_client.patch(
        f"/api/v1/accounts/{cash['id']}", json={"opening_balance_minor": 60_000}
    ).json()

    assert body["opening_balance"]["amount_minor"] == 60_000


def test_the_amount_freezes_once_the_account_has_a_transaction(
    db_client, sign_in, make_user
):
    """The rule that keeps a `balance_anchor` an observation rather than a mutable field.

    Past this point the honest correction is a Phase-2 reconciliation that posts the difference
    as an adjustment — which is also what the schema demands, since any non-opening anchor must
    carry an estimate and a gap.
    """
    sign_in(make_user())
    onboard(db_client)
    cash = named(db_client, "Cash")
    db_client.patch(f"/api/v1/accounts/{cash['id']}", json={"opening_balance_minor": 50_000})

    assert spend_from(db_client, cash["id"]).status_code == 201

    response = db_client.patch(
        f"/api/v1/accounts/{cash['id']}", json={"opening_balance_minor": 60_000}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "opening_balance_locked"


def test_a_locked_amount_says_so_in_the_list(db_client, sign_in, make_user):
    """So the screen can hide the edit rather than let the user type into a 409."""
    sign_in(make_user())
    onboard(db_client)
    cash = named(db_client, "Cash")
    db_client.patch(f"/api/v1/accounts/{cash['id']}", json={"opening_balance_minor": 50_000})
    spend_from(db_client, cash["id"])

    after = named(db_client, "Cash")

    assert after["opening_balance"]["is_locked"] is True
    assert after["transaction_count"] == 1


def test_a_first_amount_is_still_refused_after_spending(db_client, sign_in, make_user):
    """Not just changes — setting one late is the same claim about an unmeasured start."""
    sign_in(make_user())
    onboard(db_client)
    cash = named(db_client, "Cash")
    spend_from(db_client, cash["id"])

    response = db_client.patch(
        f"/api/v1/accounts/{cash['id']}", json={"opening_balance_minor": 50_000}
    )

    assert response.status_code == 409


def test_spending_from_another_account_does_not_freeze_this_one(
    db_client, sign_in, make_user
):
    """The count is per account, which is the whole point of the join."""
    sign_in(make_user())
    onboard(db_client)
    cash = named(db_client, "Cash")
    other = create(db_client, "SBI", type="bank").json()
    spend_from(db_client, cash["id"])

    response = db_client.patch(
        f"/api/v1/accounts/{other['id']}", json={"opening_balance_minor": 10_000}
    )

    assert response.status_code == 200


# ---------- exactly one default ------------------------------------------


def test_promoting_an_account_demotes_the_previous_default(db_client, sign_in, make_user):
    """`account_one_default` is a partial unique index: two defaults is a 23505, not a winner."""
    sign_in(make_user())
    onboard(db_client)
    new_account = create(db_client, "HDFC", type="bank", is_default=True).json()

    defaults = [a["name"] for a in accounts(db_client) if a["is_default"]]

    assert defaults == [new_account["name"]]


def test_promoting_by_patch_also_demotes_the_previous(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)
    other = create(db_client, "HDFC", type="bank").json()

    db_client.patch(f"/api/v1/accounts/{other['id']}", json={"is_default": True})

    assert [a["name"] for a in accounts(db_client) if a["is_default"]] == ["HDFC"]


# ---------- archive and restore ------------------------------------------


def test_archiving_hides_the_account_from_the_default_list(db_client, sign_in, make_user):
    """The entry chip wants only what a user can spend from."""
    sign_in(make_user())
    onboard(db_client)
    other = create(db_client, "Old wallet", type="wallet").json()

    assert db_client.post(f"/api/v1/accounts/{other['id']}/archive").status_code == 200

    assert "Old wallet" not in [a["name"] for a in accounts(db_client, include_archived=False)]
    assert "Old wallet" in [a["name"] for a in accounts(db_client)]


def test_archiving_clears_the_default(db_client, sign_in, make_user):
    """`account_one_default` excludes archived rows, and a hidden default chip is unusable."""
    sign_in(make_user())
    onboard(db_client)
    cash = named(db_client, "Cash")

    db_client.post(f"/api/v1/accounts/{cash['id']}/archive")

    assert named(db_client, "Cash")["is_default"] is False


def test_restoring_brings_it_back(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)
    other = create(db_client, "Old wallet", type="wallet").json()
    db_client.post(f"/api/v1/accounts/{other['id']}/archive")

    response = db_client.post(f"/api/v1/accounts/{other['id']}/restore")

    assert response.status_code == 200
    assert response.json()["is_archived"] is False
    assert "Old wallet" in [a["name"] for a in accounts(db_client, include_archived=False)]


def test_the_version_returned_is_the_one_the_trigger_wrote(db_client, sign_in, make_user):
    """`touch_row()` owns `version`. A computed guess is exactly what a concurrency token
    must not be, so this asserts the response matches a re-read rather than an increment."""
    sign_in(make_user())
    onboard(db_client)
    other = create(db_client, "Axis", type="bank").json()

    archived = db_client.post(f"/api/v1/accounts/{other['id']}/archive").json()

    assert archived["version"] == named(db_client, "Axis")["version"]
    assert archived["version"] > other["version"]


# ---------- tenancy -------------------------------------------------------


def test_a_second_user_cannot_see_the_first_users_accounts(db_client, sign_in, make_user):
    """Mandatory per `.claude/rules/tests.md`."""
    sign_in(make_user())
    onboard(db_client)
    create(db_client, "HDFC", type="bank", opening_balance_minor=999_999)

    sign_in(make_user())
    onboard(db_client)

    assert "HDFC" not in [a["name"] for a in accounts(db_client)]


def test_a_second_user_cannot_modify_the_first_users_account(db_client, sign_in, make_user):
    """404 rather than 403 — a 403 confirms the id exists."""
    sign_in(make_user())
    onboard(db_client)
    victim = create(db_client, "HDFC", type="bank").json()

    sign_in(make_user())
    onboard(db_client)

    assert db_client.patch(f"/api/v1/accounts/{victim['id']}", json={"name": "Stolen"}).status_code == 404
    assert db_client.post(f"/api/v1/accounts/{victim['id']}/archive").status_code == 404


def test_a_second_user_cannot_set_an_amount_on_the_first_users_account(
    db_client, sign_in, make_user
):
    """The opening amount is the one write here that touches a second table, so it gets its own."""
    sign_in(make_user())
    onboard(db_client)
    victim = create(db_client, "HDFC", type="bank").json()

    sign_in(make_user())
    onboard(db_client)
    response = db_client.patch(
        f"/api/v1/accounts/{victim['id']}", json={"opening_balance_minor": 1}
    )

    assert response.status_code == 404


def test_a_second_users_name_does_not_collide(db_client, sign_in, make_user):
    """`account_unique_name` is per user: two people may both bank with HDFC."""
    sign_in(make_user())
    onboard(db_client)
    create(db_client, "HDFC", type="bank")

    sign_in(make_user())
    onboard(db_client)

    assert create(db_client, "HDFC", type="bank").status_code == 201


# ---------- refusals ------------------------------------------------------


def test_an_unknown_account_is_not_found(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)

    response = db_client.patch(f"/api/v1/accounts/{uuid4()}", json={"name": "Ghost"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


def test_accounts_need_a_profile(db_client, sign_in, make_user):
    sign_in(make_user())

    response = db_client.get("/api/v1/accounts")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "onboarding_required"


def test_an_unauthenticated_request_is_refused(db_client):
    assert db_client.get("/api/v1/accounts").status_code == 401
