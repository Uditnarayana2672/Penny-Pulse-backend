"""Category ordering and the list response, framework-free.

The ranking rule is the interesting part of this endpoint and it is pure: rows and counts go
in, an ordered list comes out, with no session and no HTTP anywhere near it.
"""

from datetime import date, timedelta

from app.schemas.category import (
    CategoryItemOut,
    CategoryListOut,
    CategoryStatsOut,
)
from app.services.me import RowDict, as_int, as_str_or_none

# "usage frequency over the last thirty days" (spec 3.1, 6.1). Inclusive of today and of the
# day thirty days back, which the spec does not specify either way — a boundary transaction
# moving a tile by one position is not worth a narrower read.
USAGE_WINDOW_DAYS = 30


def usage_window_start(local_today: date) -> date:
    return local_today - timedelta(days=USAGE_WINDOW_DAYS)


def rank_by_usage(rows: list[RowDict]) -> dict[str, int | None]:
    """`usage_rank_30d` per category name, or None for a category with no usage.

    Rank 1 is the most used. Ties are broken alphabetically, and tied categories take
    *distinct* consecutive ranks rather than sharing one — the rank exists to produce a total
    order for the entry grid, and a shared rank would leave the grid's order undefined for
    exactly the categories a new user has (all of them at zero).

    Keyed by name because name is unique per user per kind (`category_unique_name`), and the
    caller already has the row.
    """
    used = [row for row in rows if as_int(row["usage_count_30d"], "usage_count_30d") > 0]
    used.sort(
        key=lambda row: (
            -as_int(row["usage_count_30d"], "usage_count_30d"),
            str(row["name"]).lower(),
        )
    )

    ranks: dict[str, int | None] = {str(row["name"]): None for row in rows}
    for position, row in enumerate(used, start=1):
        ranks[str(row["name"])] = position
    return ranks


def sort_for_entry(rows: list[RowDict], ranks: dict[str, int | None]) -> list[RowDict]:
    """Pinned first, then usage rank ascending, then name.

    An unranked category sorts after every ranked one, which is why None becomes a sentinel
    larger than any real rank rather than being treated as zero.

    The spec does not say how the pinned group orders internally, so it gets the same rule as
    everything else: a user who pinned four categories still sees their most-used first.
    """
    unranked = len(rows) + 1

    def key(row: RowDict) -> tuple[int, int, str]:
        rank = ranks.get(str(row["name"]))
        return (
            0 if row["is_pinned"] is True else 1,
            unranked if rank is None else rank,
            str(row["name"]).lower(),
        )

    return sorted(rows, key=key)


def sort_by_name(rows: list[RowDict]) -> list[RowDict]:
    """Alphabetical, case-insensitive. The spec says "alphabetical" and nothing more."""
    return sorted(rows, key=lambda row: str(row["name"]).lower())


def order_categories(rows: list[RowDict], sort: str) -> list[RowDict]:
    if sort == "entry_order":
        return sort_for_entry(rows, rank_by_usage(rows))
    return sort_by_name(rows)


def stats_for(row: RowDict, ranks: dict[str, int | None]) -> CategoryStatsOut:
    """Three figures on three different scopes, which the spec only half explains.

    `spent_minor` is period-scoped — that much is stated. `usage_rank_30d` is the rolling
    window, by definition. `transaction_count` is undocumented; it is period-scoped here
    because it sits beside `spent_minor` in the same object, and a count that disagreed with
    the spend next to it would be read as a bug by anyone looking at the two together.
    """
    return CategoryStatsOut(
        transaction_count=as_int(row["period_transaction_count"], "period_transaction_count"),
        spent_minor=as_int(row["period_spent_minor"], "period_spent_minor"),
        usage_rank_30d=ranks.get(str(row["name"])),
    )


def item_out(row: RowDict, stats: CategoryStatsOut | None) -> CategoryItemOut:
    return CategoryItemOut(
        id=row["id"],  # type: ignore[arg-type]
        name=str(row["name"]),
        short_label=as_str_or_none(row["short_label"]),
        icon=str(row["icon"]),
        colour=str(row["colour"]),
        kind=str(row["kind"]),  # type: ignore[arg-type]
        default_bucket=row["default_bucket"],  # type: ignore[arg-type]
        flexibility=None
        if row["flexibility"] is None
        else as_int(row["flexibility"], "flexibility"),
        is_pinned=bool(row["is_pinned"]),
        is_system=bool(row["is_system"]),
        is_archived=bool(row["is_archived"]),
        parent_id=row["parent_id"],  # type: ignore[arg-type]
        template_key=as_str_or_none(row["template_key"]),
        total_transaction_count=as_int(
            row["total_transaction_count"], "total_transaction_count"
        ),
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
        version=as_int(row["version"], "version"),
        stats=stats,
    )


def list_out(
    rows: list[RowDict],
    *,
    currency_code: str,
    minor_unit: int,
    budget_period_id: object | None,
    sort: str,
    with_stats: bool,
) -> CategoryListOut:
    """The ordered list, with stats only when they were asked for.

    Ranking happens whether or not stats are returned. `with_stats` governs what reaches the
    client, not what the server computes — `sort=entry_order` is defined in terms of
    `usage_rank_30d`, and the entry grid asks for that ordering with `with_stats=false`. The
    only reading under which both hold is that the rank is always computed and sometimes
    reported.
    """
    ranks = rank_by_usage(rows)
    ordered = order_categories(rows, sort)

    return CategoryListOut(
        currency_code=currency_code,
        minor_unit=minor_unit,
        budget_period_id=budget_period_id,  # type: ignore[arg-type]
        items=[
            item_out(row, stats_for(row, ranks) if with_stats else None) for row in ordered
        ],
        # Always null: sixteen seeded categories against a default page size of fifty means
        # the second page is unreachable. See the note on `CategoryListOut`.
        next_cursor=None,
    )
