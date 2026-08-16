"""Category write rules — create, edit, archive, merge, delete — with no database.

One test per line of the 6.2–6.6 computation contracts that does not need Postgres to be
true. The ones that do (period membership, cross-user isolation, the trigger classifier)
live in the db-marked file and are listed in PLAN.md.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.schemas.category import CategoryCreate, CategoryPatch
from app.services.category import (
    FLEXIBILITY_BY_BUCKET,
    archive_out,
    bucket_actually_changed,
    bucket_change_out,
    build_create_row,
    build_patch_row,
    merged_limit,
    raise_for_outcome,
    require_deletable,
    require_mutable,
    require_version,
    resolve_bucket_and_flexibility,
    validate_merge,
)
from app.services.errors import (
    CategoryHasBudgetLimitsError,
    CategoryNotEmptyError,
    DuplicateCategoryNameError,
    KindMismatchError,
    MergeIntoSelfError,
    PinLimitReachedError,
    SystemCategoryImmutableError,
    VersionConflictError,
)

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def category(**overrides):
    base = {
        "id": uuid4(),
        "name": "Food",
        # Matches what `derive_short_label("Food")` returns, so this fixture reads as a
        # category whose label nobody has overridden — the common case.
        "short_label": "Food",
        "kind": "expense",
        "default_bucket": "WANTS",
        "flexibility": 4,
        "is_system": False,
        "is_archived": False,
        "version": 3,
        "has_live_budget_limit": False,
        "total_transaction_count": 0,
        "archived_at": None,
    }
    return {**base, **overrides}


def limit(minor: int, **overrides):
    base = {
        "id": uuid4(),
        "budget_period_id": uuid4(),
        "category_id": uuid4(),
        "limit_minor": minor,
    }
    return {**base, **overrides}


# ---------- 6.2 flexibility defaults ------------------------------------


@pytest.mark.parametrize(
    ("bucket", "expected"),
    [("NEEDS", 2), ("WANTS", 4), ("FUTURE", 3), ("DEBT", 1), ("EXCLUDED", None)],
)
def test_flexibility_defaults_from_the_bucket(bucket: str, expected):
    assert resolve_bucket_and_flexibility("expense", bucket, None) == (bucket, expected)


def test_the_default_table_covers_every_bucket():
    """A new bucket without a default would KeyError at create time, not at import."""
    assert set(FLEXIBILITY_BY_BUCKET) == {"NEEDS", "WANTS", "FUTURE", "DEBT", "EXCLUDED"}


def test_a_supplied_flexibility_wins_over_the_bucket_default():
    assert resolve_bucket_and_flexibility("expense", "WANTS", 5) == ("WANTS", 5)


def test_an_income_category_carries_neither_bucket_nor_flexibility():
    assert resolve_bucket_and_flexibility("income", None, None) == (None, None)


def test_an_income_category_with_a_bucket_is_rejected_before_the_check_constraint():
    """`category_bucket_matches_kind` would answer with a 23514, which nobody can act on."""
    with pytest.raises(KindMismatchError):
        resolve_bucket_and_flexibility("income", "NEEDS", None)


def test_an_expense_category_without_a_bucket_is_rejected():
    with pytest.raises(KindMismatchError):
        resolve_bucket_and_flexibility("expense", None, None)


def test_a_created_category_is_never_pinned_or_system():
    """6.2 accepts no pin field, so the four-per-kind cap has exactly one path through it."""
    payload = CategoryCreate(
        id=uuid4(),
        name="Outside food",
        icon="tabler:utensils",
        colour="#E5484D",
        kind="expense",
        default_bucket="WANTS",
    )

    row = build_create_row(payload, "tabler:utensils", now=NOW)

    assert row["is_pinned"] is False
    assert row["is_system"] is False
    assert row["version"] == 1
    assert row["name_normalized"] == "outside food"
    assert row["short_label"] == "Outside"


# ---------- 6.3 edit -----------------------------------------------------


def test_apply_to_past_defaults_to_false():
    assert CategoryPatch().apply_to_past is False


def test_sending_the_bucket_it_already_has_is_not_a_change():
    """So `apply_to_past` cannot be armed by a no-op write."""
    assert bucket_actually_changed(CategoryPatch(default_bucket="WANTS"), category()) is False


def test_a_real_bucket_change_is_detected():
    assert bucket_actually_changed(CategoryPatch(default_bucket="NEEDS"), category()) is True


def test_a_patch_touches_only_the_keys_it_carries():
    """Plus the columns those keys derive: a name change carries `short_label` with it."""
    patch = build_patch_row(CategoryPatch(name="Groceries"), category(), None, now=NOW)

    assert set(patch) == {"updated_at", "name", "name_normalized", "short_label"}


def test_a_patch_that_carries_nothing_still_touches_nothing():
    patch = build_patch_row(CategoryPatch(), category(), None, now=NOW)

    assert set(patch) == {"updated_at"}


def test_a_rename_re_derives_the_short_label():
    """Otherwise the entry grid keeps tiling the old name (`Food` after renaming to Snacks)."""
    patch = build_patch_row(CategoryPatch(name="Outside food"), category(), None, now=NOW)

    assert patch["short_label"] == "Outside"


def test_a_rename_keeps_a_short_label_the_user_chose():
    """A label that the name would not produce was typed on purpose; a rename must not eat it."""
    patch = build_patch_row(
        CategoryPatch(name="Outside food"),
        category(name="Food", short_label="Zomato"),
        None,
        now=NOW,
    )

    assert "short_label" not in patch


def test_an_explicit_short_label_wins_over_the_derived_one():
    patch = build_patch_row(
        CategoryPatch(name="Outside food", short_label="Dining"), category(), None, now=NOW
    )

    assert patch["short_label"] == "Dining"


def test_a_bucket_change_re_derives_flexibility_when_none_was_sent():
    patch = build_patch_row(CategoryPatch(default_bucket="DEBT"), category(), None, now=NOW)

    assert patch["flexibility"] == 1


def test_a_bucket_change_keeps_an_explicit_flexibility():
    patch = build_patch_row(
        CategoryPatch(default_bucket="DEBT", flexibility=5), category(), None, now=NOW
    )

    assert patch["flexibility"] == 5


def test_making_a_limit_holding_category_excluded_is_refused():
    """Mirrors the `category_stays_budgetable` trigger, which would otherwise be a 500."""
    with pytest.raises(CategoryHasBudgetLimitsError):
        build_patch_row(
            CategoryPatch(default_bucket="EXCLUDED"),
            category(has_live_budget_limit=True),
            None,
            now=NOW,
        )


def test_kind_is_not_a_patchable_field():
    """Immutable (spec 1348) — `extra="forbid"` turns an attempt into a named 422."""
    with pytest.raises(ValueError):
        CategoryPatch(kind="income")


def test_a_blank_name_is_rejected_rather_than_normalising_to_empty():
    with pytest.raises(ValueError):
        CategoryPatch(name="   ")


def test_if_match_is_optional_and_omitting_it_is_last_write_wins():
    require_version(None, category(version=9))


def test_a_stale_if_match_is_a_version_conflict():
    with pytest.raises(VersionConflictError):
        require_version(2, category(version=3))


def test_a_matching_if_match_passes():
    require_version(3, category(version=3))


def test_a_system_category_rejects_every_change():
    with pytest.raises(SystemCategoryImmutableError):
        require_mutable(category(is_system=True))


def test_the_bucket_change_block_says_nothing_happened_by_default():
    change = bucket_change_out(applied=False, rows_updated=0)

    assert change.applied_to_past is False
    assert change.past_transactions_updated == 0
    assert "keep the bucket they were saved with" in change.note


def test_the_bucket_change_block_reports_its_blast_radius():
    change = bucket_change_out(applied=True, rows_updated=37)

    assert change.applied_to_past is True
    assert change.past_transactions_updated == 37


# ---------- 6.4 archive --------------------------------------------------


def test_archiving_asserts_the_current_period_limit_is_retained():
    """The rule the whole feature exists to protect: removing a limit mid-month would move
    the unallocated figure under the user (spec 1386)."""
    out = archive_out(category(archived_at=NOW, total_transaction_count=37))

    assert out.current_period_limit_retained is True
    assert out.carried_to_next_period is False
    assert out.total_transaction_count == 37


# ---------- 6.5 merge ----------------------------------------------------


def test_merging_a_category_into_itself_is_refused():
    same = category()
    with pytest.raises(MergeIntoSelfError):
        validate_merge(same, same)


def test_merging_across_kinds_is_refused():
    with pytest.raises(KindMismatchError):
        validate_merge(category(kind="expense"), category(kind="income"))


def test_merging_a_system_category_is_refused():
    with pytest.raises(SystemCategoryImmutableError):
        validate_merge(category(is_system=True), category())


def test_a_merge_with_no_source_limit_does_nothing_to_limits():
    plan = merged_limit(None, limit(315_000))

    assert plan["action"] == "none"
    assert plan["block"] is None


def test_a_target_with_a_limit_gets_the_amounts_summed():
    plan = merged_limit(limit(120_000), limit(315_000))

    assert plan["action"] == "sum"
    assert plan["target_limit_after"] == 435_000
    assert plan["block"].target_limit_before_minor == 315_000
    assert plan["block"].target_limit_after_minor == 435_000
    assert plan["block"].source_limit_minor == 120_000


def test_a_target_without_a_limit_takes_the_source_row_over():
    """Reassigned, not copied, so `carried_in_minor` and rollover stay with their money."""
    source = limit(120_000)

    plan = merged_limit(source, None)

    assert plan["action"] == "reassign"
    assert plan["source_limit_id"] == source["id"]
    assert plan["block"].target_limit_before_minor == 0
    assert plan["block"].target_limit_after_minor == 120_000


# ---------- 6.6 delete ---------------------------------------------------


def test_a_never_used_category_may_be_deleted():
    require_deletable(category(), historical_txn_count=0, past_limit_count=0)


def test_a_category_with_transactions_is_refused():
    with pytest.raises(CategoryNotEmptyError) as caught:
        require_deletable(category(), historical_txn_count=214, past_limit_count=0)

    assert caught.value.details["total_transaction_count"] == 214
    assert caught.value.details["alternatives"] == ["archive", "merge"]
    assert caught.value.details["blocked_by"] == "transaction"


def test_only_soft_deleted_transactions_still_block_the_delete():
    """`txn.category_id` has no cascade, so a soft-deleted row would raise 23503.

    This is the case where the guard's count and the list's `total_transaction_count`
    disagree on purpose — the list says 0, this says 3, and the delete is still refused.
    """
    with pytest.raises(CategoryNotEmptyError) as caught:
        require_deletable(
            category(total_transaction_count=0), historical_txn_count=3, past_limit_count=0
        )

    assert caught.value.details["total_transaction_count"] == 3


def test_a_past_period_budget_limit_blocks_the_delete():
    """Spec delta. Allocating money in a closed month is history, even with nothing spent."""
    with pytest.raises(CategoryNotEmptyError) as caught:
        require_deletable(category(), historical_txn_count=0, past_limit_count=1)

    assert caught.value.details["blocked_by"] == "past_budget_limit"


def test_a_system_category_cannot_be_deleted():
    with pytest.raises(SystemCategoryImmutableError):
        require_deletable(
            category(is_system=True), historical_txn_count=0, past_limit_count=0
        )


# ---------- constraint classification ------------------------------------


def test_a_clean_write_raises_nothing():
    raise_for_outcome("ok", name="Food", kind="expense")


def test_a_unique_violation_becomes_a_named_duplicate_error():
    """23505 must never reach the client — a user cannot act on a SQLSTATE."""
    with pytest.raises(DuplicateCategoryNameError):
        raise_for_outcome("duplicate_name", name="Food", kind="expense")


def test_the_pin_trigger_becomes_a_named_error():
    with pytest.raises(PinLimitReachedError):
        raise_for_outcome("pin_limit_reached", name="Food", kind="expense")


def test_the_budgetable_trigger_becomes_a_named_error():
    with pytest.raises(CategoryHasBudgetLimitsError):
        raise_for_outcome("has_budget_limits", name="Food", kind="expense")


def test_an_unclassified_outcome_is_a_bug_not_a_silent_pass():
    with pytest.raises(AssertionError):
        raise_for_outcome("something_new", name="Food", kind="expense")
