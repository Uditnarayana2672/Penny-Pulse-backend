"""`GET /categories/suggestions` against a real migrated Postgres.

A new file rather than an addition to `test_category.py`, `test_category_duplicates.py` or
`test_category_rules.py`: all three open by declaring they run with no database and no HTTP,
and that is the contract that lets them be fast. This endpoint is the opposite kind — the
whole feature is "which templates does this user not have a row for", which is a join, so
testing it without Postgres would test nothing. Category tests are already split by concern
(ordering, duplicates, rules); this is the fourth concern.

`build_payload` is imported rather than reimplemented because it drives onboarding through
the real preview endpoint. A local copy would be a second definition of what a valid
bootstrap looks like, free to drift from the one the other file defends.
"""

from uuid import UUID, uuid4

import pytest

from tests.test_onboarding import SPEC_SELECTION, build_payload

pytestmark = pytest.mark.db

# 0012's suggestion-only presets. Every one carries a NULL `suggested_share_pct`, which is
# what keeps them out of onboarding and out of the budget split.
WIDER_SET = {
    "fuel",
    "cafe",
    "subscriptions",
    "insurance",
    "mobile_internet",
    "gifts",
    "family_support",
    "festivals",
    "fitness",
    "pets",
    "repairs",
    "trips",
}

# In 0009 but absent from `SPEC_SELECTION`, so an account onboarded the documented way has
# neither. Recovering these is the reason the endpoint exists.
SKIPPED_STARTERS = {"education", "emi_loans"}


def onboard(client, *, selected: list[str] | None = None) -> None:
    client.post("/api/v1/onboarding/complete", json=build_payload(client, selected=selected))


def suggested(client, kind: str = "expense") -> set[str]:
    response = client.get(f"/api/v1/categories/suggestions?kind={kind}")

    assert response.status_code == 200
    return {item["template_key"] for item in response.json()["items"]}


def add(client, item: dict) -> object:
    """Add a suggestion the way the screen does: an ordinary create carrying the preset."""
    return client.post(
        "/api/v1/categories",
        json={
            "id": str(uuid4()),
            "name": item["name"],
            "short_label": item["short_label"],
            "icon": item["icon"],
            "colour": item["colour"],
            "kind": "expense",
            "default_bucket": item["default_bucket"],
            "flexibility": item["flexibility"],
            "template_key": item["template_key"],
        },
    )


def one(client, template_key: str) -> dict:
    items = client.get("/api/v1/categories/suggestions?kind=expense").json()["items"]
    return next(item for item in items if item["template_key"] == template_key)


# ---------- what gets suggested -------------------------------------------


def test_a_preset_the_user_skipped_during_onboarding_is_suggested(
    db_client, sign_in, make_user
):
    """The gap this endpoint closes. Onboarding offers a preset once and never again."""
    sign_in(make_user())
    onboard(db_client)

    assert SKIPPED_STARTERS <= suggested(db_client)


def test_the_wider_preset_set_is_suggested(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)

    assert WIDER_SET <= suggested(db_client)


def test_a_preset_the_user_already_has_is_not_suggested(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)

    assert suggested(db_client).isdisjoint(SPEC_SELECTION)


def test_the_reserved_unaccounted_pair_is_never_suggested(db_client, sign_in, make_user):
    """EXCLUDED bucket: a category that can never hold a limit is not worth offering."""
    sign_in(make_user())
    onboard(db_client)

    assert "unaccounted_out" not in suggested(db_client)
    assert "unaccounted_in" not in suggested(db_client, "income")


def test_income_has_nothing_left_to_suggest(db_client, sign_in, make_user):
    """Onboarding creates every income template, so this list is empty by construction.

    Asserted rather than assumed: it is what makes the Income tab's empty section correct
    instead of merely untested.
    """
    sign_in(make_user())
    onboard(db_client)

    assert suggested(db_client, "income") == set()


# ---------- tenancy -------------------------------------------------------


def test_a_second_users_categories_do_not_change_these_suggestions(
    db_client, sign_in, make_user
):
    """Mandatory per `.claude/rules/tests.md`, and it bites differently here.

    The rows being subtracted belong to a user, so a missing `user_id` filter would not leak a
    name — it would silently *hide* a preset because somebody else had added it. A wrong
    answer nobody would think to check.
    """
    first = make_user()
    sign_in(first)
    onboard(db_client)
    before = suggested(db_client)

    second = make_user()
    sign_in(second)
    onboard(db_client)
    add(db_client, one(db_client, "fuel"))

    sign_in(first)
    assert "fuel" in suggested(db_client)
    assert suggested(db_client) == before


# ---------- adding one ----------------------------------------------------


def test_adding_a_suggestion_removes_it_from_the_list(db_client, sign_in, make_user):
    sign_in(make_user())
    onboard(db_client)

    response = add(db_client, one(db_client, "education"))

    assert response.status_code == 201
    assert "education" not in suggested(db_client)


def test_an_added_suggestion_records_the_template_it_came_from(
    db_client, sign_in, make_user
):
    """`template_key` is the whole mechanism: without it the suggestion never disappears."""
    sign_in(make_user())
    onboard(db_client)

    body = add(db_client, one(db_client, "gifts")).json()

    assert body["template_key"] == "gifts"


def test_an_added_suggestion_keeps_the_glyph_it_was_shown_with(
    db_client, sign_in, make_user
):
    """A tile that draws one icon and creates another is a bug the user sees immediately."""
    sign_in(make_user())
    onboard(db_client)
    item = one(db_client, "fuel")

    body = add(db_client, item).json()

    assert (body["icon"], body["resolved_render_value"]) == (
        item["icon"],
        item["resolved_render_value"],
    )


def test_every_suggestion_carries_a_glyph_the_client_can_draw(
    db_client, sign_in, make_user
):
    """The client holds no token table, so a blank `render_value` is a blank tile."""
    sign_in(make_user())
    onboard(db_client)

    items = db_client.get("/api/v1/categories/suggestions?kind=expense").json()["items"]

    assert items != []
    for item in items:
        assert item["resolved_render_value"].startswith("ti ti-")
        assert item["icon"].startswith("tabler:")


def test_an_archived_category_is_not_suggested_again(db_client, sign_in, make_user):
    """Archived is still live, so the preset stays spoken for.

    Offering it would invite a second row that `category_unique_name` then refuses — the
    screen would be proposing something the database rejects. Restore is the way back.
    """
    sign_in(make_user())
    onboard(db_client)
    created = add(db_client, one(db_client, "pets")).json()

    db_client.post(f"/api/v1/categories/{created['id']}/archive")

    assert "pets" not in suggested(db_client)


# ---------- refusals ------------------------------------------------------


def test_a_create_naming_an_unknown_template_is_refused(db_client, sign_in, make_user):
    """`category.template_key` is a foreign key, so the alternative is a 23503 and a 500."""
    sign_in(make_user())
    onboard(db_client)

    response = db_client.post(
        "/api/v1/categories",
        json={
            "id": str(uuid4()),
            "name": "Invented",
            "icon": "tabler:dots",
            "colour": "#889096",
            "kind": "expense",
            "default_bucket": "NEEDS",
            "flexibility": 3,
            "template_key": "no_such_template",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_violation"


def test_suggestions_need_a_profile(db_client, sign_in, make_user):
    """Unlike the icon catalog, this answer is per-user, so there is nothing to say yet."""
    sign_in(make_user())

    response = db_client.get("/api/v1/categories/suggestions?kind=expense")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "onboarding_required"


def test_the_kind_filter_is_required(db_client, sign_in, make_user):
    """Same reason as the list endpoint: the screen is tabbed and a mixed list has no order."""
    sign_in(make_user())
    onboard(db_client)

    assert db_client.get("/api/v1/categories/suggestions").status_code == 422


def test_an_unauthenticated_request_is_refused(db_client):
    assert db_client.get("/api/v1/categories/suggestions?kind=expense").status_code == 401


# ---------- the count is not an accident ---------------------------------


def test_the_suggestion_count_is_exactly_what_is_missing(db_client, sign_in, make_user):
    """Guards the arithmetic: 24 active expense presets, 10 taken by `SPEC_SELECTION`.

    A bare `<=` in the tests above would still pass if the endpoint returned every template
    including the ones already owned.
    """
    sign_in(make_user())
    onboard(db_client)

    assert suggested(db_client) == WIDER_SET | SKIPPED_STARTERS
    assert len(WIDER_SET | SKIPPED_STARTERS) == 14


def test_a_user_who_took_everything_offered_is_still_shown_the_wider_set(
    db_client, sign_in, make_user
):
    """Onboarding cannot reach the 0012 presets, so ticking every box still leaves these."""
    sign_in(make_user())
    templates = db_client.get("/api/v1/onboarding/category-templates").json()
    every_starter = [template["template_key"] for template in templates["expense"]]

    onboard(db_client, selected=every_starter)

    assert suggested(db_client) == WIDER_SET


def test_a_uuid_is_never_parsed_out_of_the_suggestions_path(
    db_client, sign_in, make_user
):
    """`/categories/suggestions` must not be read as `/categories/{category_id}`.

    Route order is load-bearing and invisible: declared after the PATCH/DELETE routes,
    `suggestions` would be parsed as a UUID and answer 422 forever.
    """
    sign_in(make_user())
    onboard(db_client)

    assert db_client.get("/api/v1/categories/suggestions?kind=expense").status_code == 200


def test_the_archived_category_can_be_restored_instead(db_client, sign_in, make_user):
    """The other half of the archive rule: restore is reachable, so nothing is stranded."""
    sign_in(make_user())
    onboard(db_client)
    created = add(db_client, one(db_client, "trips")).json()
    db_client.post(f"/api/v1/categories/{created['id']}/archive")

    response = db_client.post(f"/api/v1/categories/{created['id']}/restore")

    assert response.status_code == 200
    assert UUID(created["id"])
    assert "trips" not in suggested(db_client)
