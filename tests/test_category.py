"""`GET /categories` ordering, with no database and no HTTP.

The interesting half of this endpoint is the entry-grid ordering rule, and it is pure: rows
and counts in, an ordered list out.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

from app.services.category import (
    USAGE_WINDOW_DAYS,
    list_out,
    order_categories,
    rank_by_usage,
    sort_by_name,
    sort_for_entry,
    usage_window_start,
)

NOW = datetime(2026, 8, 11, 9, 2, tzinfo=UTC)
TODAY = date(2026, 8, 11)


def row(name: str, *, usage: int = 0, pinned: bool = False, **overrides):
    """Exactly the columns `repositories/category.list_categories` selects."""
    base = {
        "id": uuid4(),
        "name": name,
        "short_label": name[:6],
        "icon": "dots",
        "colour": "#889096",
        "kind": "expense",
        "default_bucket": "NEEDS",
        "flexibility": 3,
        "is_pinned": pinned,
        "is_system": False,
        "is_archived": False,
        "parent_id": None,
        "template_key": name.lower(),
        "created_at": NOW,
        "updated_at": NOW,
        "version": 1,
        "total_transaction_count": 0,
        "usage_count_30d": usage,
        "period_transaction_count": 0,
        "period_spent_minor": 0,
    }
    return {**base, **overrides}


def names(rows) -> list[str]:
    return [str(item["name"]) for item in rows]


# ---------- the usage window ----------


def test_the_usage_window_is_thirty_days_back_from_today():
    assert usage_window_start(TODAY) == date(2026, 7, 12)
    assert (TODAY - usage_window_start(TODAY)).days == USAGE_WINDOW_DAYS


# ---------- ranking ----------


def test_the_most_used_category_ranks_first():
    ranks = rank_by_usage([row("Food", usage=14), row("Transport", usage=3)])

    assert ranks["Food"] == 1
    assert ranks["Transport"] == 2


def test_a_category_with_no_usage_is_not_ranked_at_all():
    """The spec is explicit: it "is not ranked at all and returns null"."""
    ranks = rank_by_usage([row("Food", usage=14), row("Education", usage=0)])

    assert ranks["Education"] is None


def test_equal_usage_breaks_alphabetically():
    ranks = rank_by_usage([row("Transport", usage=5), row("Bills", usage=5)])

    assert ranks["Bills"] == 1
    assert ranks["Transport"] == 2


def test_tied_categories_take_distinct_ranks_rather_than_sharing_one():
    """A shared rank would leave the grid's order undefined for exactly the case a new user
    has — every category tied at zero."""
    ranks = rank_by_usage([row("A", usage=2), row("B", usage=2), row("C", usage=2)])

    assert sorted(filter(None, ranks.values())) == [1, 2, 3]


def test_nothing_is_ranked_when_nothing_has_been_used():
    ranks = rank_by_usage([row("Food"), row("Bills")])

    assert set(ranks.values()) == {None}


# ---------- entry order ----------


def test_pinned_categories_come_first_however_little_they_are_used():
    ordered = sort_for_entry(
        [row("Food", usage=90), row("Rent", usage=1, pinned=True)],
        rank_by_usage([row("Food", usage=90), row("Rent", usage=1, pinned=True)]),
    )

    assert names(ordered) == ["Rent", "Food"]


def test_within_the_unpinned_group_the_most_used_comes_first():
    rows = [row("Bills", usage=2), row("Food", usage=20), row("Transport", usage=9)]

    assert names(order_categories(rows, "entry_order")) == ["Food", "Transport", "Bills"]


def test_an_unused_category_sorts_after_every_used_one():
    rows = [row("Education", usage=0), row("Food", usage=1)]

    assert names(order_categories(rows, "entry_order")) == ["Food", "Education"]


def test_unused_categories_fall_back_to_alphabetical_among_themselves():
    rows = [row("Shopping"), row("Bills"), row("Food")]

    assert names(order_categories(rows, "entry_order")) == ["Bills", "Food", "Shopping"]


def test_the_pinned_group_is_itself_ordered_by_usage():
    rows = [row("Rent", usage=1, pinned=True), row("Food", usage=30, pinned=True)]

    assert names(order_categories(rows, "entry_order")) == ["Food", "Rent"]


def test_a_brand_new_users_grid_is_alphabetical_and_deterministic():
    """Nothing logged yet, so every category is unranked. The order must still be stable —
    a grid that reshuffles between renders is unusable."""
    rows = [row("Transport"), row("Food"), row("Groceries")]

    first = names(order_categories(rows, "entry_order"))
    second = names(order_categories(list(reversed(rows)), "entry_order"))

    assert first == second == ["Food", "Groceries", "Transport"]


# ---------- name order ----------


def test_name_order_is_case_insensitive():
    rows = [row("bills"), row("Apples")]

    assert names(sort_by_name(rows)) == ["Apples", "bills"]


def test_name_order_ignores_usage_entirely():
    rows = [row("Transport", usage=99), row("Bills", usage=0)]

    assert names(order_categories(rows, "name")) == ["Bills", "Transport"]


# ---------- the response ----------


def test_stats_are_withheld_unless_asked_for():
    result = list_out(
        [row("Food", usage=4)],
        currency_code="INR",
        minor_unit=2,
        budget_period_id=uuid4(),
        sort="entry_order",
        with_stats=False,
    )

    assert result.items[0].stats is None


def test_the_ranking_still_orders_the_grid_when_stats_are_off():
    """The contradiction in the spec: `entry_order` is defined by `usage_rank_30d`, and the
    entry grid asks for that ordering with `with_stats=false`. The rank is always computed and
    only sometimes reported."""
    rows = [row("Bills", usage=1), row("Food", usage=50)]

    result = list_out(
        rows,
        currency_code="INR",
        minor_unit=2,
        budget_period_id=None,
        sort="entry_order",
        with_stats=False,
    )

    assert [item.name for item in result.items] == ["Food", "Bills"]
    assert all(item.stats is None for item in result.items)


def test_stats_carry_the_rank_when_asked_for():
    result = list_out(
        [row("Food", usage=4, period_transaction_count=14, period_spent_minor=264_600)],
        currency_code="INR",
        minor_unit=2,
        budget_period_id=uuid4(),
        sort="entry_order",
        with_stats=True,
    )

    stats = result.items[0].stats
    assert stats is not None
    assert stats.usage_rank_30d == 1
    assert stats.transaction_count == 14
    assert stats.spent_minor == 264_600


def test_the_total_count_is_present_whether_or_not_stats_were_asked_for():
    """It is what the client uses to choose Delete versus Archive, so it must never need a
    second request to obtain."""
    for with_stats in (True, False):
        result = list_out(
            [row("Food", total_transaction_count=214)],
            currency_code="INR",
            minor_unit=2,
            budget_period_id=None,
            sort="name",
            with_stats=with_stats,
        )
        assert result.items[0].total_transaction_count == 214


def test_short_label_survives_to_the_wire_though_no_spec_mentions_it():
    """It exists only in the migration, commented "entry-grid tile text; falls back to name",
    and migrations outrank the spec."""
    result = list_out(
        [row("Groceries", short_label="Grocery")],
        currency_code="INR",
        minor_unit=2,
        budget_period_id=None,
        sort="name",
        with_stats=False,
    )

    assert result.items[0].short_label == "Grocery"


def test_a_missing_short_label_stays_null_rather_than_becoming_the_string_none():
    result = list_out(
        [row("Groceries", short_label=None)],
        currency_code="INR",
        minor_unit=2,
        budget_period_id=None,
        sort="name",
        with_stats=False,
    )

    assert result.items[0].short_label is None


def test_the_second_page_is_never_offered_in_phase_one():
    result = list_out(
        [row("Food")],
        currency_code="INR",
        minor_unit=2,
        budget_period_id=None,
        sort="name",
        with_stats=False,
    )

    assert result.next_cursor is None


def test_an_income_category_carries_no_bucket_or_flexibility():
    result = list_out(
        [row("Salary", kind="income", default_bucket=None, flexibility=None)],
        currency_code="INR",
        minor_unit=2,
        budget_period_id=None,
        sort="name",
        with_stats=False,
    )

    assert result.items[0].default_bucket is None
    assert result.items[0].flexibility is None


# NOTE — router-level tests against a migrated Postgres are still owed: the happy path, a
# second user seeing none of the first user's categories, `include_archived` both ways, and
# `stats.spent_minor` scoping to the supplied period. All need TEST_DATABASE_URL, which is
# unset, so `tests/conftest.py::database_url` skips them.
