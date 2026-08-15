"""The four onboarding endpoints against a real migrated Postgres.

Every test here is marked `db`: the CHECKs, triggers and the `period_no_overlap` exclusion
constraint are the point, and none of them exist in a schema SQLAlchemy built.
"""

from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db

SPEC_SELECTION = [
    "food",
    "groceries",
    "transport",
    "rent_housing",
    "bills",
    "health",
    "personal_care",
    "shopping",
    "entertainment",
    "other",
]


def build_payload(
    client,
    *,
    monthly_income_minor: int = 2_625_000,
    month_start_day: int = 28,
    selected: list[str] | None = None,
    accounts: list[dict] | None = None,
    ids: dict[str, UUID] | None = None,
    budget_period_id: UUID | None = None,
) -> dict:
    """Build a complete-onboarding body the way the client will: from a live preview.

    Going through the real preview endpoint rather than hardcoding limits means these tests
    also catch a preview and a commit that disagree.
    """
    selected = selected or SPEC_SELECTION
    templates = client.get("/api/v1/onboarding/category-templates").json()
    preview = client.post(
        "/api/v1/onboarding/preview-budget",
        json={
            "monthly_income_minor": monthly_income_minor,
            "month_start_day": month_start_day,
            "selected_expense_template_keys": selected,
        },
    ).json()

    income_keys = [template["template_key"] for template in templates["income"]]
    category_ids = ids or {key: uuid4() for key in [*selected, *income_keys]}

    return {
        "profile": {
            "display_name": "Udit",
            "monthly_income_minor": monthly_income_minor,
            "month_start_day": month_start_day,
        },
        "categories": [
            {"id": str(category_ids[key]), "template_key": key}
            for key in [*selected, *income_keys]
        ],
        "accounts": accounts if accounts is not None else [
            {"id": str(uuid4()), "name": "Cash", "type": "cash", "is_default": True}
        ],
        "budget": {
            "budget_period_id": str(budget_period_id or uuid4()),
            "expected_income_minor": monthly_income_minor,
            "pct_needs": preview["bucket_allocation"]["pct_needs"],
            "pct_wants": preview["bucket_allocation"]["pct_wants"],
            "pct_future": preview["bucket_allocation"]["pct_future"],
            "pct_debt": preview["bucket_allocation"]["pct_debt"],
            "limits": [
                {
                    "id": str(uuid4()),
                    "category_id": str(category_ids[limit["template_key"]]),
                    "limit_minor": limit["limit_minor"],
                }
                for limit in preview["suggested_limits"]
            ],
        },
    }


def count(engine, table: str, user_id: UUID) -> int:
    with engine.begin() as connection:
        return connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).scalar_one()


def test_a_user_with_no_profile_is_told_onboarding_is_required(db_client, sign_in, make_user):
    sign_in(make_user(email="fresh@example.test"))

    body = db_client.get("/api/v1/me").json()

    assert body["onboarding_required"] is True
    assert body["profile"] is None
    assert body["email"] == "fresh@example.test"
    # Present before onboarding so the income field can render a currency.
    assert (body["currency_code"], body["minor_unit"]) == ("INR", 2)


def test_the_offered_templates_exclude_the_reserved_unaccounted_pair(db_client, sign_in, make_user):
    sign_in(make_user())

    body = db_client.get("/api/v1/onboarding/category-templates").json()

    assert len(body["expense"]) == 12
    assert len(body["income"]) == 4
    offered = {t["template_key"] for t in [*body["expense"], *body["income"]]}
    # sort_order 99: Phase-2 balance-anchor machinery, and unaccounted_out is EXCLUDED so
    # it could never hold a budget limit.
    assert "unaccounted_out" not in offered
    assert "unaccounted_in" not in offered
    assert all(t["default_bucket"] != "EXCLUDED" for t in body["expense"])


def test_income_templates_carry_no_bucket_and_no_suggested_share(db_client, sign_in, make_user):
    sign_in(make_user())

    body = db_client.get("/api/v1/onboarding/category-templates").json()

    for template in body["income"]:
        assert template["default_bucket"] is None
        assert template["suggested_share_pct"] is None


def test_completing_onboarding_writes_every_object_in_one_transaction(
    db_client, sign_in, make_user, engine
):
    user_id = make_user()
    sign_in(user_id)

    response = db_client.post(
        "/api/v1/onboarding/complete", json=build_payload(db_client)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["profile"]["monthly_income_minor"] == 2_625_000
    # The literal dates are asserted in test_onboarding_math against a fixed day. Here the
    # clock is real, so the rule is the assertion: the first period starts on the salary
    # date and contains today, or the expense logged at the next step falls outside every
    # period and Home opens in its no_budget state straight after a successful setup.
    period = body["budget_period"]
    today = date.today().isoformat()
    assert period["starts_on"] <= today <= period["ends_on"]
    assert date.fromisoformat(period["starts_on"]).day == 28
    assert len(body["categories"]) == 14
    assert len(body["accounts"]) == 1
    assert len(body["budget_limits"]) == 10
    assert count(engine, "profile", user_id) == 1
    assert count(engine, "category", user_id) == 14
    assert count(engine, "budget_period", user_id) == 1
    assert count(engine, "budget_limit", user_id) == 10


def test_the_committed_limits_match_what_the_preview_offered(db_client, sign_in, make_user):
    sign_in(make_user())
    payload = build_payload(db_client)

    body = db_client.post("/api/v1/onboarding/complete", json=payload).json()

    requested = {limit["category_id"]: limit["limit_minor"] for limit in payload["budget"]["limits"]}
    stored = {limit["category_id"]: limit["limit_minor"] for limit in body["budget_limits"]}
    assert stored == requested


def test_me_reports_the_profile_once_onboarding_has_completed(db_client, sign_in, make_user):
    sign_in(make_user())
    db_client.post("/api/v1/onboarding/complete", json=build_payload(db_client))

    body = db_client.get("/api/v1/me").json()

    assert body["onboarding_required"] is False
    assert body["profile"]["month_start_day"] == 28
    assert body["profile"]["onboarding_completed_at"] is not None
    # Never a client input, always the default.
    assert body["profile"]["role"] == "user"


def test_replaying_the_same_bootstrap_creates_nothing_and_answers_two_hundred(
    db_client, sign_in, make_user, engine
):
    user_id = make_user()
    sign_in(user_id)
    payload = build_payload(db_client)

    first = db_client.post("/api/v1/onboarding/complete", json=payload)
    second = db_client.post("/api/v1/onboarding/complete", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.json()["budget_period"]["id"] == payload["budget"]["budget_period_id"]
    assert count(engine, "category", user_id) == 14
    assert count(engine, "budget_period", user_id) == 1
    assert count(engine, "budget_limit", user_id) == 10


def test_a_second_different_bootstrap_is_refused_rather_than_duplicating_everything(
    db_client, sign_in, make_user, engine
):
    user_id = make_user()
    sign_in(user_id)
    db_client.post("/api/v1/onboarding/complete", json=build_payload(db_client))

    # New ids throughout: a genuinely different setup, not a replayed one.
    response = db_client.post("/api/v1/onboarding/complete", json=build_payload(db_client))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "onboarding_already_complete"
    assert count(engine, "category", user_id) == 14
    assert count(engine, "budget_period", user_id) == 1


def test_a_second_user_cannot_see_the_first_users_profile_or_categories(
    db_client, sign_in, make_user, engine
):
    first_user = make_user(email="first@example.test")
    sign_in(first_user)
    db_client.post("/api/v1/onboarding/complete", json=build_payload(db_client))

    second_user = make_user(email="second@example.test")
    sign_in(second_user)
    body = db_client.get("/api/v1/me").json()

    assert body["onboarding_required"] is True
    assert body["profile"] is None
    assert body["email"] == "second@example.test"
    assert count(engine, "category", second_user) == 0


def test_a_second_user_onboarding_gets_their_own_rows_not_the_first_users(
    db_client, sign_in, make_user, engine
):
    first_user = make_user()
    sign_in(first_user)
    first_body = db_client.post(
        "/api/v1/onboarding/complete", json=build_payload(db_client)
    ).json()

    second_user = make_user()
    sign_in(second_user)
    second_body = db_client.post(
        "/api/v1/onboarding/complete", json=build_payload(db_client)
    ).json()

    first_ids = {category["id"] for category in first_body["categories"]}
    second_ids = {category["id"] for category in second_body["categories"]}
    assert first_ids.isdisjoint(second_ids)
    assert count(engine, "category", first_user) == 14
    assert count(engine, "category", second_user) == 14


def test_a_second_user_reusing_the_first_users_period_id_cannot_modify_it(
    db_client, sign_in, make_user, engine
):
    first_user = make_user()
    sign_in(first_user)
    stolen = uuid4()
    db_client.post(
        "/api/v1/onboarding/complete",
        json=build_payload(db_client, budget_period_id=stolen),
    )

    second_user = make_user()
    sign_in(second_user)
    db_client.post(
        "/api/v1/onboarding/complete",
        json=build_payload(db_client, monthly_income_minor=99_00_000, budget_period_id=stolen),
    )

    # Whatever the second call answered, the period still belongs to the first user and
    # still holds their income, and the second user owns nothing.
    with engine.begin() as connection:
        owner, income = connection.execute(
            text(
                "SELECT user_id, expected_income_minor FROM budget_period WHERE id = :id"
            ),
            {"id": stolen},
        ).one()
    assert owner == first_user
    assert income == 2_625_000
    assert count(engine, "profile", second_user) == 0
    assert count(engine, "category", second_user) == 0


def test_a_budget_limit_on_an_income_category_is_refused(db_client, sign_in, make_user):
    sign_in(make_user())
    payload = build_payload(db_client)
    salary_id = next(
        category["id"] for category in payload["categories"] if category["template_key"] == "salary"
    )
    payload["budget"]["limits"].append(
        {"id": str(uuid4()), "category_id": salary_id, "limit_minor": 1_000}
    )

    response = db_client.post("/api/v1/onboarding/complete", json=payload)

    # 409, not 422: the body is well-formed and the conflict is with the category it
    # points at. Mirrors the `budget_limit_budgetable` trigger.
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "income_category_not_budgetable"


def test_a_budget_limit_pointing_outside_the_request_is_refused(db_client, sign_in, make_user):
    sign_in(make_user())
    payload = build_payload(db_client)
    payload["budget"]["limits"].append(
        {"id": str(uuid4()), "category_id": str(uuid4()), "limit_minor": 1_000}
    )

    response = db_client.post("/api/v1/onboarding/complete", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_violation"


def test_bucket_percentages_that_do_not_sum_to_one_hundred_are_refused(
    db_client, sign_in, make_user
):
    sign_in(make_user())
    payload = build_payload(db_client)
    payload["budget"]["pct_needs"] = 60

    response = db_client.post("/api/v1/onboarding/complete", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "bucket_percentages_invalid"


def test_a_month_start_day_past_the_twenty_eighth_is_refused(db_client, sign_in, make_user):
    sign_in(make_user())
    payload = build_payload(db_client)
    payload["profile"]["month_start_day"] = 31

    response = db_client.post("/api/v1/onboarding/complete", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_month_start_day"


def test_an_unsupported_currency_is_refused(db_client, sign_in, make_user):
    sign_in(make_user())
    payload = build_payload(db_client)
    payload["profile"]["preferred_currency_code"] = "USD"

    response = db_client.post("/api/v1/onboarding/complete", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "currency_not_supported"


def test_two_accounts_with_the_same_name_are_refused(db_client, sign_in, make_user):
    sign_in(make_user())
    payload = build_payload(
        db_client,
        accounts=[
            {"id": str(uuid4()), "name": "Cash", "type": "cash", "is_default": True},
            {"id": str(uuid4()), "name": " cash ", "type": "wallet", "is_default": False},
        ],
    )

    response = db_client.post("/api/v1/onboarding/complete", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_violation"


def test_onboarding_without_accounts_is_allowed_because_the_step_is_skippable(
    db_client, sign_in, make_user, engine
):
    user_id = make_user()
    sign_in(user_id)

    response = db_client.post(
        "/api/v1/onboarding/complete", json=build_payload(db_client, accounts=[])
    )

    assert response.status_code == 201
    assert response.json()["accounts"] == []
    assert count(engine, "account", user_id) == 0


def test_a_credit_card_account_is_recorded_as_a_liability(db_client, sign_in, make_user, engine):
    user_id = make_user()
    sign_in(user_id)

    db_client.post(
        "/api/v1/onboarding/complete",
        json=build_payload(
            db_client,
            accounts=[{"id": str(uuid4()), "name": "HDFC card", "type": "credit_card"}],
        ),
    )

    with engine.begin() as connection:
        is_liability = connection.execute(
            text("SELECT is_liability FROM account WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).scalar_one()
    assert is_liability is True
