"""Paise arithmetic. Pure functions, no imports from `app/`.

`amount_minor` is an `int` of paise and is always positive; the sign lives in
`direction`. Never float, never Decimal — a rupee amount that cannot be written as a
whole number of paise does not exist in this app.
"""

Direction = str  # "in" | "out" | "transfer" — validated as a Literal at the edge.


def signed_minor(amount_minor: int, direction: Direction) -> int:
    """Signed value for aggregation only. Never store the result.

    A transfer leg has no meaningful sign until `transfer_role` says which side left
    the account, so it contributes zero to any total.
    """
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive; the sign lives in direction")
    if direction == "in":
        return amount_minor
    if direction == "out":
        return -amount_minor
    if direction == "transfer":
        return 0
    raise ValueError(f"unknown direction: {direction!r}")


def split_evenly(total_minor: int, parts: int) -> list[int]:
    """Split a bill so the children sum back to the parent exactly.

    The remainder goes to the earliest parts one paisa at a time. Dropping it instead
    would leave the split parent and its children disagreeing by up to `parts - 1` paise.
    """
    if total_minor <= 0:
        raise ValueError("total_minor must be positive")
    if parts < 1:
        raise ValueError("parts must be at least 1")

    base, remainder = divmod(total_minor, parts)
    return [base + 1 if index < remainder else base for index in range(parts)]


def apply_basis_points(amount_minor: int, basis_points: int) -> int:
    """`basis_points` of `amount_minor`, truncated toward zero.

    Basis points rather than a float percentage so the input itself is exact — 33.33%
    of a rupee has no float spelling that rounds the same way twice.
    """
    if amount_minor < 0:
        raise ValueError("amount_minor must not be negative")
    if basis_points < 0:
        raise ValueError("basis_points must not be negative")

    return amount_minor * basis_points // 10_000
