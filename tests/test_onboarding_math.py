"""The seeded-budget arithmetic, with no database and no HTTP.

These are the majority of the onboarding tests on purpose: `app/services/onboarding.py`
imports neither `fastapi` nor `sqlalchemy`, and this file is what that ban buys.
"""

from datetime import date

import pytest

from app.lib.money import split_by_shares
from app.services.errors import DomainError
from app.services.onboarding import (
    build_preview,
    bucket_percentages,
    decide_bootstrap,
    effective_limit_minor,
    raise_for_bootstrap,
    template_from_row,
    unallocated_note,
)

# The expense rows migration 0009 actually seeds, minus sort_order 99.
SEEDED_EXPENSE_TEMPLATES = {
    "food": ("Food", "Food", "utensils", "#E5484D", "WANTS", 4, 40, 1),
    "groceries": ("Groceries", "Grocery", "basket", "#30A46C", "NEEDS", 3, 22, 2),
    "transport": ("Transport", "Travel", "bus", "#0090FF", "NEEDS", 3, 12, 3),
    "rent_housing": ("Rent & Housing", "Rent", "home", "#8E4EC6", "NEEDS", 1, 45, 4),
    "bills": ("Bills & Utilities", "Bills", "bolt", "#F76B15", "NEEDS", 2, 9, 5),
    "health": ("Health", "Health", "heart", "#E54666", "NEEDS", 2, 6, 6),
    "personal_care": ("Personal care", "Personal", "sparkles", "#D6409F", "NEEDS", 3, 4, 7),
    "shopping": ("Shopping", "Shopping", "cart", "#0588F0", "WANTS", 4, 30, 8),
    "entertainment": ("Entertainment", "Fun", "film", "#6E56CF", "WANTS", 5, 30, 9),
    "education": ("Education", "Learn", "book", "#12A594", "NEEDS", 2, 2, 10),
    "emi_loans": ("EMI & Loans", "EMI", "receipt", "#5B5BD6", "DEBT", 1, 100, 11),
    "other": ("Other", "Other", "dots", "#889096", "NEEDS", 3, 2, 12),
}

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


def templates(*keys: str) -> list:
    """Build service templates from the seeded values, in the order given."""
    built = []
    for key in keys:
        name, short_label, icon, colour, bucket, flexibility, share, sort_order = (
            SEEDED_EXPENSE_TEMPLATES[key]
        )
        built.append(
            template_from_row(
                {
                    "template_key": key,
                    "name": name,
                    "short_label": short_label,
                    "icon": icon,
                    "colour": colour,
                    "kind": "expense",
                    "default_bucket": bucket,
                    "flexibility": flexibility,
                    "suggested_share_pct": share,
                    "sort_order": sort_order,
                }
            )
        )
    return built


def limits_by_key(preview) -> dict[str, int]:
    return {limit.template_key: limit.limit_minor for limit in preview.suggested_limits}


def test_the_first_period_contains_today_rather_than_starting_at_the_next_salary_date():
    preview = build_preview(2_625_000, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert (preview.starts_on, preview.ends_on) == (date(2026, 7, 28), date(2026, 8, 27))
    assert preview.starts_on <= date(2026, 8, 11) <= preview.ends_on


def test_a_thirty_one_day_period_reports_thirty_one_days():
    preview = build_preview(2_625_000, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert preview.days_in_period == 31


def test_the_specified_worked_example_reproduces_exactly():
    preview = build_preview(2_625_000, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert limits_by_key(preview) == {
        "rent_housing": 590_625,
        "groceries": 288_750,
        "transport": 157_500,
        "bills": 118_125,
        "health": 78_750,
        "personal_care": 52_500,
        "other": 26_250,
        "food": 315_000,
        "shopping": 236_250,
        "entertainment": 236_250,
    }


def test_allocated_and_unallocated_account_for_every_paisa_of_income():
    preview = build_preview(2_625_000, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert preview.allocated_total_minor == 2_100_000
    assert preview.unallocated_minor == 525_000
    assert preview.allocated_total_minor + preview.unallocated_minor == 2_625_000


def test_bucket_targets_sum_to_income_when_income_is_not_a_whole_number_of_rupees():
    # floor(income * pct / 100) would lose a paisa here and every total built on the
    # budget would then disagree with the income it came from.
    preview = build_preview(2_625_001, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert sum(preview.allocation.targets.values()) == 2_625_001
    assert preview.allocated_total_minor + preview.unallocated_minor == 2_625_001


def test_the_same_selection_in_a_different_order_produces_the_same_limits():
    # Largest-remainder breaks ties toward the earliest index, so without a canonical sort
    # the user would be shown one number and a different one would be saved.
    forwards = build_preview(2_625_000, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))
    backwards = build_preview(
        2_625_000, 28, templates(*reversed(SPEC_SELECTION)), date(2026, 8, 11)
    )

    assert limits_by_key(forwards) == limits_by_key(backwards)


def test_selecting_a_debt_category_moves_ten_points_from_needs_to_debt():
    assert bucket_percentages(templates("food")) == {
        "NEEDS": 50,
        "WANTS": 30,
        "FUTURE": 20,
        "DEBT": 0,
    }
    assert bucket_percentages(templates("food", "emi_loans")) == {
        "NEEDS": 40,
        "WANTS": 30,
        "FUTURE": 20,
        "DEBT": 10,
    }


def test_a_bucket_with_no_selected_categories_reports_its_whole_target_as_unallocated():
    # FUTURE has no seeded categories in Phase 1, so this is every user's first budget.
    preview = build_preview(2_625_000, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert preview.unallocated_buckets == ["FUTURE"]
    assert preview.allocation.targets["FUTURE"] == preview.unallocated_minor


def test_a_bucket_with_a_zero_target_is_not_reported_as_unallocated():
    # DEBT sits at 0% unless something debt-shaped was chosen. Naming a bucket owed
    # nothing would read as a problem rather than an absence.
    preview = build_preview(2_625_000, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert "DEBT" not in preview.unallocated_buckets


def test_the_unallocated_note_names_the_empty_bucket():
    preview = build_preview(2_625_000, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert unallocated_note(preview) == (
        "Your FUTURE bucket has nothing allocated to it yet."
    )


def test_selecting_only_a_debt_category_leaves_the_other_buckets_unallocated():
    preview = build_preview(2_625_000, 28, templates("emi_loans"), date(2026, 8, 11))

    assert limits_by_key(preview) == {"emi_loans": 262_500}
    assert preview.unallocated_buckets == ["NEEDS", "WANTS", "FUTURE"]
    assert preview.unallocated_minor == 2_362_500


def test_zero_income_allocates_zero_rather_than_failing():
    # profile.monthly_income_minor, budget_period.expected_income_minor and
    # budget_limit.limit_minor all CHECK >= 0, so zero is legal everywhere.
    preview = build_preview(0, 28, templates(*SPEC_SELECTION), date(2026, 8, 11))

    assert preview.allocated_total_minor == 0
    assert preview.unallocated_minor == 0
    assert set(limits_by_key(preview).values()) == {0}


def test_one_selected_category_receives_its_whole_bucket_target():
    preview = build_preview(1_000_000, 1, templates("food"), date(2026, 8, 11))

    assert limits_by_key(preview) == {"food": 300_000}


def test_a_month_start_day_past_the_twenty_eighth_is_refused():
    # 1..28 so that every calendar month contains one. February decides this.
    with pytest.raises(ValueError):
        build_preview(2_625_000, 29, templates("food"), date(2026, 8, 11))


def test_the_same_bootstrap_arriving_twice_is_a_replay():
    """The period id is the idempotency key, so its presence decides this on its own."""
    assert (
        decide_bootstrap(period_is_mine=True, profile_exists=True, period_id_is_taken=True)
        == "replay"
    )
    assert raise_for_bootstrap("replay") is None


def test_a_different_bootstrap_against_a_finished_profile_conflicts():
    action = decide_bootstrap(
        period_is_mine=False, profile_exists=True, period_id_is_taken=False
    )

    assert action == "already_complete"
    with pytest.raises(DomainError) as caught:
        raise_for_bootstrap(action)
    assert caught.value.status_code == 409
    assert caught.value.code == "onboarding_already_complete"


def test_a_period_id_belonging_to_another_user_is_refused_rather_than_crashing():
    """This is the case that used to answer 500.

    `budget_period.id` is a global primary key, so an id another user already holds can never
    be inserted: `ON CONFLICT (id) DO NOTHING` wrote nothing and the read-back then failed an
    assertion. The client's recovery is a fresh UUIDv7, so it is a 422.
    """
    action = decide_bootstrap(
        period_is_mine=False, profile_exists=False, period_id_is_taken=True
    )

    assert action == "period_id_taken"
    with pytest.raises(DomainError) as caught:
        raise_for_bootstrap(action)
    assert caught.value.status_code == 422
    assert caught.value.field == "budget.budget_period_id"


def test_a_first_bootstrap_with_a_free_id_is_created():
    action = decide_bootstrap(
        period_is_mine=False, profile_exists=False, period_id_is_taken=False
    )

    assert action == "create"
    assert raise_for_bootstrap(action) is None


def test_weighted_shares_sum_back_to_the_total_they_were_split_from():
    assert split_by_shares(1_312_500, [22, 12, 45, 9, 6, 4, 2]) == [
        288_750,
        157_500,
        590_625,
        118_125,
        78_750,
        52_500,
        26_250,
    ]
    assert sum(split_by_shares(100, [1, 1, 1])) == 100


def test_shares_that_sum_to_zero_are_a_bug_rather_than_an_equal_split():
    # Unreachable with the seeded set: every budgetable template has a positive share.
    # Inventing a product rule for it would only hide a bad seed.
    with pytest.raises(ValueError):
        split_by_shares(1000, [0, 0])


def test_a_negative_total_is_rejected_rather_than_distributed():
    with pytest.raises(ValueError):
        split_by_shares(-1, [1, 1])


def test_an_effective_limit_is_the_limit_plus_whatever_carried_in():
    assert effective_limit_minor(315_000, 0) == 315_000
    assert effective_limit_minor(315_000, 12_000) == 327_000
