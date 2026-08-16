"""Duplicate detection for `POST /categories`, with no database and no HTTP.

The threshold is tuned against the table below, not by feel. If a pair here starts failing,
the fix is to look at the pair and decide what the right answer is — not to nudge
`NEAR_DUPLICATE_THRESHOLD` until the suite goes green.

The asymmetry between the two tests is the point of the feature (spec decision 1486): an
exact clash is a hard block because the two categories would be indistinguishable in the
entry grid, and a near match is an overridable speed bump because sometimes `Petrol` really
is not `Transport`.
"""

from uuid import uuid4

import pytest

from app.services.category import (
    NEAR_DUPLICATE_THRESHOLD,
    check_exact_duplicate,
    check_near_duplicate,
    derive_short_label,
    normalise_name,
    similarity,
    synonym_keys,
)
from app.services.errors import DuplicateCategoryNameError, NearDuplicateCategoryError

# The seeded groups, in the shape `list_synonym_terms` returns.
SYNONYMS = [
    {"group_key": "food", "term": term}
    for term in ("food", "meals", "lunch", "dinner", "eating out", "outside food")
] + [
    {"group_key": "travel", "term": term}
    for term in ("travel", "transport", "commute", "taxi", "cab")
] + [
    {"group_key": "groceries", "term": term}
    for term in ("groceries", "grocery", "supermarket")
]


def sibling(name: str, *, transactions: int = 0):
    return {
        "id": uuid4(),
        "name": name,
        "name_normalized": normalise_name(name),
        "total_transaction_count": transactions,
    }


# ---------- the fixture table -------------------------------------------

# (left, right, should_be_near) — the pairs the threshold is answerable to.
NAME_PAIRS = [
    ("Food", "Foods", True),
    ("Food", "Fuel", False),
    ("Gifts", "Gift cards", True),
    ("Petrol", "Transport", False),
    ("Groceries", "Grocery", True),
    ("Rent", "Rant", True),
    ("Rent", "Rent & Housing", True),
    ("Coffee", "Cofee", True),
    ("Bills", "Skills", False),
    # Semantically unrelated, but a single substitution at six letters — arithmetically
    # indistinguishable from `Rent`/`Rant` and `Coffee`/`Cofee`, which this table requires
    # to match. No threshold separates them, so this is a deliberate false positive: the
    # cost is one tap on "Add anyway", and the same rule is what catches `Helth`.
    ("Health", "Wealth", True),
    ("Fun", "Fund", True),
    ("Salary", "Celery", False),
    ("Transport", "Shopping", False),
    ("Entertainment", "Entertainmnet", True),
]


@pytest.mark.parametrize(("left", "right", "expected_near"), NAME_PAIRS)
def test_the_similarity_table_holds(left: str, right: str, expected_near: bool):
    assert (similarity(left, right) >= NEAR_DUPLICATE_THRESHOLD) is expected_near


def test_similarity_is_symmetric():
    for left, right, _ in NAME_PAIRS:
        assert similarity(left, right) == pytest.approx(similarity(right, left))


def test_an_identical_name_scores_one():
    assert similarity("Food", "food") == 1.0


# ---------- the exact-match arm -----------------------------------------


def test_an_exact_name_is_a_hard_block():
    with pytest.raises(DuplicateCategoryNameError):
        check_exact_duplicate("Food", "expense", [sibling("Food")])


def test_the_exact_match_is_case_insensitive():
    with pytest.raises(DuplicateCategoryNameError):
        check_exact_duplicate("FOOD", "expense", [sibling("food")])


def test_the_exact_match_trims_whitespace():
    with pytest.raises(DuplicateCategoryNameError):
        check_exact_duplicate("  Food  ", "expense", [sibling("Food")])


def test_a_different_name_passes_the_exact_test():
    check_exact_duplicate("Fuel", "expense", [sibling("Food")])


def test_force_cannot_reach_the_exact_test():
    """`check_exact_duplicate` takes no `force` parameter, so it cannot be bypassed.

    Asserted as a signature fact rather than a behaviour, because the failure mode this
    guards against is somebody adding the parameter later.
    """
    import inspect

    assert "force" not in inspect.signature(check_exact_duplicate).parameters


# ---------- the near-match arm ------------------------------------------


def test_a_near_name_is_flagged():
    with pytest.raises(NearDuplicateCategoryError) as caught:
        check_near_duplicate("Foods", [sibling("Food")], SYNONYMS, force=False)

    assert caught.value.details["overridable"] is True
    assert caught.value.details["matches"][0]["name"] == "Food"
    assert caught.value.details["matches"][0]["reason"] == "similarity"


def test_a_synonym_fires_regardless_of_spelling():
    """`Food` and `Lunch` look nothing alike and are the same category to a user."""
    with pytest.raises(NearDuplicateCategoryError) as caught:
        check_near_duplicate("Lunch", [sibling("Food")], SYNONYMS, force=False)

    assert caught.value.details["matches"][0]["reason"] == "synonym_group:food"


def test_petrol_does_not_collide_with_transport():
    """The seeded `travel` group deliberately excludes petrol and fuel."""
    check_near_duplicate("Petrol", [sibling("Transport")], SYNONYMS, force=False)


def test_force_overrides_a_near_duplicate():
    check_near_duplicate("Foods", [sibling("Food")], SYNONYMS, force=True)


def test_the_match_payload_carries_what_the_sheet_needs():
    existing = sibling("Food", transactions=214)

    with pytest.raises(NearDuplicateCategoryError) as caught:
        check_near_duplicate("Foods", [existing], SYNONYMS, force=False)

    match = caught.value.details["matches"][0]
    assert match["id"] == str(existing["id"])
    assert match["total_transaction_count"] == 214
    assert 0.0 <= match["similarity"] <= 1.0


def test_matches_are_ordered_most_similar_first():
    with pytest.raises(NearDuplicateCategoryError) as caught:
        check_near_duplicate(
            "Foods", [sibling("Lunch"), sibling("Food")], SYNONYMS, force=False
        )

    names = [match["name"] for match in caught.value.details["matches"]]
    assert names[0] == "Food"


def test_an_unrelated_name_passes():
    check_near_duplicate("Petrol", [sibling("Shopping")], SYNONYMS, force=False)


# ---------- synonym lookup ----------------------------------------------


def test_a_phrase_term_matches_the_whole_name():
    assert synonym_keys("Eating out", SYNONYMS) == {"food"}


def test_a_word_inside_a_name_is_not_a_synonym_hit():
    """Matched on the whole name, or every category containing "out" joins the food group."""
    assert synonym_keys("Nights out", SYNONYMS) == set()


# ---------- short_label derivation --------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Rent & Housing", "Rent"),
        ("Food", "Food"),
        ("outside food", "Outside"),
        ("EMI & Loans", "EMI"),
        ("Personal care", "Personal"),
    ],
)
def test_short_label_is_the_first_word(name: str, expected: str):
    assert derive_short_label(name) == expected
