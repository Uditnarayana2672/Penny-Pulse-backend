import pytest

from app.lib.money import apply_basis_points, signed_minor, split_evenly


def test_out_is_negative_for_aggregation_only():
    assert signed_minor(12_000, "out") == -12_000


def test_transfer_contributes_nothing_to_a_total():
    assert signed_minor(500_00, "transfer") == 0


def test_a_negative_amount_is_rejected_rather_than_flipped():
    with pytest.raises(ValueError):
        signed_minor(-100, "out")


def test_split_children_sum_back_to_the_parent():
    parts = split_evenly(1000, 3)
    assert sum(parts) == 1000
    assert parts == [334, 333, 333]


def test_basis_points_truncate_toward_zero():
    assert apply_basis_points(999, 3333) == 332
