"""Category rules — ordering, duplicate detection, merge arithmetic — framework-free.

Everything the Categories page decides lives here and nothing here touches a session or an
HTTP object. That is what lets the interesting parts be tested against a table of name
pairs rather than against a database: the similarity threshold was tuned by running
`tests/test_category_duplicates.py`, not by feel.
"""

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.schemas.category import (
    BucketChangeOut,
    BudgetLimitsMergedOut,
    CategoryArchiveOut,
    CategoryCreate,
    CategoryItemOut,
    CategoryListOut,
    CategoryPatch,
    CategoryRestoreOut,
    CategoryStatsOut,
    CategorySuggestionListOut,
    CategorySuggestionOut,
    CategoryUpdateOut,
    MergeOut,
)
from app.services.errors import (
    CategoryHasBudgetLimitsError,
    CategoryNotEmptyError,
    DuplicateCategoryNameError,
    KindMismatchError,
    MergeIntoSelfError,
    NearDuplicateCategoryError,
    PinLimitReachedError,
    RuleViolationError,
    SystemCategoryImmutableError,
    UnknownIconTokenError,
    VersionConflictError,
)
from app.services.icon_catalog import is_writable_token, resolve_for_render
from app.services.me import RowDict, as_int, as_str_or_none

# "usage frequency over the last thirty days" (spec 3.1, 6.1). Inclusive of today and of the
# day thirty days back, which the spec does not specify either way — a boundary transaction
# moving a tile by one position is not worth a narrower read.
USAGE_WINDOW_DAYS = 30

# Normalised similarity at or above this is a near-duplicate. Not a guess: it is the lowest
# value that separates the pairs in `tests/test_category_duplicates.py`, where `Food`/`Foods`
# must match and `Food`/`Fuel` must not.
NEAR_DUPLICATE_THRESHOLD = 0.70

# `flexibility` when the client omits it (spec 1286). EXCLUDED gets null and can never reach
# `is_committed()` anyway, because an EXCLUDED category cannot hold a budget limit.
FLEXIBILITY_BY_BUCKET: dict[str, int | None] = {
    "NEEDS": 2,
    "WANTS": 4,
    "FUTURE": 3,
    "DEBT": 1,
    "EXCLUDED": None,
}

NO_RETRO_NOTE = "Existing transactions keep the bucket they were saved with."
RETRO_NOTE = "Every transaction in this category was moved to the new bucket."


def usage_window_start(local_today: date) -> date:
    return local_today - timedelta(days=USAGE_WINDOW_DAYS)


# ---------- names -------------------------------------------------------


def normalise_name(name: str) -> str:
    """`lower(trim(name))` — the same expression `category.name_normalized` stores.

    Computed here rather than by the database because `category_unique_name` indexes the
    stored column and applies no expression of its own (0002:136). If these two ever
    disagree, the index stops catching duplicates and nothing announces it.
    """
    return name.strip().lower()


def _tokens(name: str) -> list[str]:
    """Word tokens, punctuation dropped. `Rent & Housing` -> `['rent', 'housing']`."""
    cleaned = "".join(char if char.isalnum() else " " for char in name.lower())
    return [token for token in cleaned.split() if token]


def _stem(token: str) -> str:
    """Crudest possible plural stripping, and deliberately no more than that.

    `Gifts`/`Gift cards` and `Food`/`Foods` are the pairs this has to catch. A real stemmer
    would be a dependency, and an aggressive one starts merging words a user meant to keep
    apart — the cost of a false near-duplicate is an overridable speed bump, but the cost of
    a false *exact* match would be a hard block, so this stays timid.
    """
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _edit_distance(first: str, second: str) -> int:
    if first == second:
        return 0
    if not first:
        return len(second)
    if not second:
        return len(first)

    previous = list(range(len(second) + 1))
    for i, left in enumerate(first, start=1):
        current = [i]
        for j, right in enumerate(second, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left != right),
                )
            )
        previous = current
    return previous[-1]


def _ratio(first: str, second: str) -> float:
    longest = max(len(first), len(second))
    if longest == 0:
        return 1.0
    return 1.0 - _edit_distance(first, second) / longest


def similarity(first: str, second: str) -> float:
    """Normalised similarity in 0..1. Higher means more alike.

    Three measures, best one wins, because plain edit distance gets one important case
    badly wrong: `Gifts` against `Gift cards` scores 0.4, well under the threshold, even
    though one name obviously contains the other. Containment catches that; stemming
    catches `Groceries`/`Grocery`, which edit distance alone puts at 0.67.

    Taking the maximum makes the test more willing to flag, never less. That is the right
    direction for an overridable warning and the wrong direction for a hard block, which is
    exactly why the exact-match test below does not use this function at all.
    """
    left, right = normalise_name(first), normalise_name(second)
    if left == right:
        return 1.0

    left_tokens = [_stem(token) for token in _tokens(left)]
    right_tokens = [_stem(token) for token in _tokens(right)]

    scores = [_ratio(left, right), _ratio(" ".join(left_tokens), " ".join(right_tokens))]

    smaller, larger = sorted((set(left_tokens), set(right_tokens)), key=len)
    if smaller and smaller <= larger:
        # Every word of the shorter name appears in the longer one. Scaled by how much of
        # the longer name is accounted for, so `Gifts` scores higher against `Gift cards`
        # than against `Gift cards for the office party`.
        scores.append(0.70 + 0.30 * (len(smaller) / len(larger)))

    return max(scores)


def synonym_keys(name: str, synonym_terms: list[RowDict]) -> set[str]:
    """Which seeded groups this name belongs to.

    Matched on the whole normalised name, not per token: the groups hold phrases like
    `eating out`, and matching token-wise would put every category containing the word
    "out" in the food group.
    """
    normalised = normalise_name(name)
    return {
        str(row["group_key"])
        for row in synonym_terms
        if str(row["term"]) == normalised
    }


def derive_short_label(name: str) -> str:
    """The entry-grid tile text. First word of the name.

    A **spec delta**: `short_label` appears nowhere in the written spec, but the column
    exists, the entry grid reads it, and `category_template.short_label` is NOT NULL. The
    sheet has no field for it, so it is derived rather than asked for — "Rent & Housing"
    becomes "Rent", which is what the seeded templates chose by hand.

    Reads the raw name, not the tokeniser's output: `_tokens` lowercases, and running this
    through it turned "EMI & Loans" into "Emi". Acronyms are the whole reason the case of
    what the user typed is worth preserving.

    A wholly lowercase word is capitalised, because "outside food" should tile as "Outside"
    and the seeded templates are all title case. A word with any capital is left exactly as
    typed.

    Truncated to the column's practical width so a long first word cannot overflow a 34px
    tile. Never returns empty: the caller has already rejected a blank name.
    """
    cleaned = "".join(char if char.isalnum() else " " for char in name)
    words = [word for word in cleaned.split() if word]
    first = words[0] if words else name.strip()
    return first[:20].capitalize() if first.islower() else first[:20]


def _short_label_was_derived(category: RowDict) -> bool:
    """Whether the stored `short_label` is still what the current name would produce.

    The column cannot say whether a human typed it, so this reconstructs the answer: if
    deriving from the name the row has now gives the label the row has now, nobody has
    overridden it and a rename may re-derive freely.

    The seeded templates are the case that makes this worth doing carefully. "Rent & Housing"
    ships with the hand-written label "Rent", which is exactly what `derive_short_label`
    returns, so those rows correctly read as derived. But "Freelance / Side" ships as
    "Freelance" while the derivation gives "Freelance" too — and "Personal care" ships as
    "Personal", also a match. Where a seed chose something the derivation would not produce,
    the label is treated as deliberate and left alone.
    """
    stored = as_str_or_none(category["short_label"])
    if stored is None:
        return True
    name = as_str_or_none(category["name"])
    if name is None:
        return True
    return stored == derive_short_label(name)


# ---------- duplicate detection -----------------------------------------


def check_exact_duplicate(name: str, kind: str, siblings: list[RowDict]) -> None:
    """Case-insensitive, whitespace-trimmed, against non-archived same-kind categories.

    Not overridable, and `force` is not a parameter here so it cannot become overridable by
    accident. Callers pass only non-archived siblings, matching the partial unique index.
    """
    normalised = normalise_name(name)
    for sibling in siblings:
        if str(sibling["name_normalized"]) == normalised:
            raise DuplicateCategoryNameError(name, kind)


def check_near_duplicate(
    name: str,
    siblings: list[RowDict],
    synonym_terms: list[RowDict],
    *,
    force: bool,
) -> None:
    """Similarity >= 0.70 **or** a shared synonym group. Overridable with `force: true`.

    The two arms are joined by OR (spec 1284), so a synonym hit fires regardless of score —
    `Food` and `Lunch` look nothing alike and are the same category to a user.
    """
    if force:
        return

    own_groups = synonym_keys(name, synonym_terms)
    matches: list[dict[str, Any]] = []

    for sibling in siblings:
        sibling_name = str(sibling["name"])
        score = similarity(name, sibling_name)
        shared = own_groups & synonym_keys(sibling_name, synonym_terms)

        if score < NEAR_DUPLICATE_THRESHOLD and not shared:
            continue

        matches.append(
            {
                "id": str(sibling["id"]),
                "name": sibling_name,
                "similarity": round(score, 2),
                # The spec only ever exampled the synonym form. A score-driven match says
                # so plainly rather than inventing a second vocabulary.
                "reason": f"synonym_group:{sorted(shared)[0]}" if shared else "similarity",
                "total_transaction_count": as_int(
                    sibling["total_transaction_count"], "total_transaction_count"
                ),
            }
        )

    if matches:
        matches.sort(key=lambda match: match["similarity"], reverse=True)
        raise NearDuplicateCategoryError(name, matches)


# ---------- create and edit ---------------------------------------------


def require_known_icon(token: str, icons_by_token: dict[str, RowDict]) -> str:
    if not is_writable_token(token, icons_by_token):
        raise UnknownIconTokenError(token)
    from app.services.icon_catalog import normalise_token

    return normalise_token(token)


def require_known_template(template_key: str | None, exists: bool) -> None:
    """A create claiming a preset must name one that exists.

    `RuleViolationError` rather than a new class, matching
    `services.onboarding.select_expense_templates`, which refuses an unknown template key the
    same way. The existence check is a repository call because this module may not import
    `sqlalchemy`, so the router looks it up and passes the answer in.
    """
    if template_key is not None and not exists:
        raise RuleViolationError(
            f"Unknown category template: {template_key}.", field="template_key"
        )


def suggestions_out(
    templates: list[RowDict], icons_by_token: dict[str, RowDict]
) -> CategorySuggestionListOut:
    """Presets shaped for the picker, each with a glyph the client can draw.

    Resolution goes through the same `icon_catalog` resolver the category list uses, so a
    suggestion tile and the category it becomes cannot draw different glyphs.
    """
    items: list[CategorySuggestionOut] = []
    for template in templates:
        token, render_value = resolve_for_render(str(template["icon"]), icons_by_token)
        items.append(
            CategorySuggestionOut(
                template_key=str(template["template_key"]),
                name=str(template["name"]),
                short_label=str(template["short_label"]),
                icon=token,
                icon_pack_key=token.split(":", 1)[0],
                resolved_render_value=render_value,
                colour=str(template["colour"]),
                default_bucket=template["default_bucket"],
                flexibility=template["flexibility"],
            )
        )
    return CategorySuggestionListOut(items=items)


def resolve_bucket_and_flexibility(
    kind: str, default_bucket: str | None, flexibility: int | None
) -> tuple[str | None, int | None]:
    """The kind/bucket/flexibility rules, in one place, ahead of the database.

    `category_bucket_matches_kind` is the backstop and would answer with a 23514, which is
    not something a user can act on. An expense needs a bucket; an income category must
    carry neither a bucket nor a flexibility (spec 1287).
    """
    if kind == "income":
        if default_bucket is not None:
            raise KindMismatchError("income", "income category with a bucket")
        return None, None

    if default_bucket is None:
        raise KindMismatchError("expense", "expense category without a bucket")

    if flexibility is not None:
        return default_bucket, flexibility
    return default_bucket, FLEXIBILITY_BY_BUCKET[default_bucket]


def build_create_row(
    payload: CategoryCreate, resolved_icon: str, *, now: datetime
) -> RowDict:
    bucket, flexibility = resolve_bucket_and_flexibility(
        payload.kind, payload.default_bucket, payload.flexibility
    )
    return {
        "id": payload.id,
        "name": payload.name,
        "name_normalized": normalise_name(payload.name),
        "short_label": payload.short_label or derive_short_label(payload.name),
        "icon": resolved_icon,
        "colour": payload.colour,
        "kind": payload.kind,
        "default_bucket": bucket,
        "flexibility": flexibility,
        "is_pinned": False,
        "is_system": False,
        "template_key": payload.template_key,
        "is_archived": False,
        "archived_at": None,
        "sort_order": None,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }


def raise_for_outcome(outcome: str, *, name: str, kind: str) -> None:
    """Turn the repository's classification of a database refusal into a domain error.

    The other half of the split described in `repositories/category.py`: that layer may not
    import `app.services`, this one may not import `sqlalchemy`, so the constraint travels
    between them as a plain string. Every branch ends the request, which is what makes it
    safe not to open a savepoint — Postgres has aborted the transaction by this point and
    no further statement would succeed.
    """
    if outcome == "ok":
        return
    if outcome == "duplicate_name":
        raise DuplicateCategoryNameError(name, kind)
    if outcome == "pin_limit_reached":
        raise PinLimitReachedError()
    if outcome == "has_budget_limits":
        raise CategoryHasBudgetLimitsError()
    raise AssertionError(f"unclassified write outcome: {outcome}")


def require_mutable(category: RowDict) -> None:
    if bool(category["is_system"]):
        raise SystemCategoryImmutableError()


def require_version(if_match: int | None, category: RowDict) -> None:
    """Optional concurrency check. Omitting `If-Match` is last-write-wins (spec 2.1)."""
    if if_match is None:
        return
    actual = as_int(category["version"], "version")
    if actual != if_match:
        raise VersionConflictError(if_match, actual, entity="Category")


def build_patch_row(
    payload: CategoryPatch,
    category: RowDict,
    resolved_icon: str | None,
    *,
    now: datetime,
) -> RowDict:
    """Only the keys present in the body (spec 2.1).

    Two derived columns follow the same rule: a change re-derives them only when the client
    did not send one, so moving NEEDS -> WANTS updates `flexibility`, and renaming updates
    `short_label`, rather than stranding a value the user never chose. Sending either
    explicitly keeps the explicit value.
    """
    patch: RowDict = {"updated_at": now}

    if payload.name is not None:
        patch["name"] = payload.name
        patch["name_normalized"] = normalise_name(payload.name)

    if payload.short_label is not None:
        patch["short_label"] = payload.short_label
    elif payload.name is not None and _short_label_was_derived(category):
        # A rename otherwise leaves the entry grid tiling the old name: rename "Food" to
        # "Outside food" and the tile still reads "Food". Only re-derived when the stored
        # label still matches what the old name would have produced — once a user has set
        # their own short label, a later rename must not silently discard it. The sheet has
        # no field for it, so this is the only path that keeps the two in step.
        patch["short_label"] = derive_short_label(payload.name)

    if resolved_icon is not None:
        patch["icon"] = resolved_icon
    if payload.colour is not None:
        patch["colour"] = payload.colour
    if payload.is_pinned is not None:
        patch["is_pinned"] = payload.is_pinned

    if payload.default_bucket is not None:
        kind = str(category["kind"])
        if kind == "income":
            raise KindMismatchError("income", "income category with a bucket")
        if payload.default_bucket == "EXCLUDED" and bool(category["has_live_budget_limit"]):
            raise CategoryHasBudgetLimitsError()
        patch["default_bucket"] = payload.default_bucket
        patch["flexibility"] = (
            payload.flexibility
            if payload.flexibility is not None
            else FLEXIBILITY_BY_BUCKET[payload.default_bucket]
        )
    elif payload.flexibility is not None:
        patch["flexibility"] = payload.flexibility

    return patch


def bucket_actually_changed(payload: CategoryPatch, category: RowDict) -> bool:
    """`apply_to_past` is meaningful only alongside a real bucket change (spec 1344).

    Sending the bucket it already has is not a change, so it does not arm the rewrite. The
    spec is silent on whether that should be a 422; treating it as a no-op is the reading
    that cannot destroy anything.
    """
    return (
        payload.default_bucket is not None
        and payload.default_bucket != as_str_or_none(category["default_bucket"])
    )


def bucket_change_out(*, applied: bool, rows_updated: int) -> BucketChangeOut:
    return BucketChangeOut(
        applied_to_past=applied,
        past_transactions_updated=rows_updated,
        note=RETRO_NOTE if applied else NO_RETRO_NOTE,
    )


# ---------- merge --------------------------------------------------------


def validate_merge(source: RowDict, target: RowDict) -> None:
    if source["id"] == target["id"]:
        raise MergeIntoSelfError()
    require_mutable(source)
    require_mutable(target)
    if str(source["kind"]) != str(target["kind"]):
        raise KindMismatchError(str(source["kind"]), str(target["kind"]))


def merged_limit(source_limit: RowDict | None, target_limit: RowDict | None) -> dict[str, Any]:
    """Current-period limit arithmetic (spec 1420). Returns the plan, performs nothing.

    Two arms. If the target already has a limit the amounts are summed and the source's row
    is soft-deleted — `budget_limit_one_per_category_per_period` is a partial unique index
    over live rows, so leaving both alive would violate it. If the target has none, the
    source's row is repointed rather than copied, which keeps its `carried_in_minor` and
    rollover settings attached to the money they describe.

    Only `limit_minor` is summed. `carried_in_minor`, `rollover` and `rollover_cap_minor`
    are not mentioned by the contract and are not ours to combine.
    """
    if source_limit is None:
        return {"action": "none", "block": None}

    source_minor = as_int(source_limit["limit_minor"], "limit_minor")

    if target_limit is None:
        return {
            "action": "reassign",
            "source_limit_id": source_limit["id"],
            "block": BudgetLimitsMergedOut(
                budget_period_id=source_limit["budget_period_id"],  # type: ignore[arg-type]
                source_limit_minor=source_minor,
                target_limit_before_minor=0,
                target_limit_after_minor=source_minor,
            ),
        }

    before = as_int(target_limit["limit_minor"], "limit_minor")
    return {
        "action": "sum",
        "source_limit_id": source_limit["id"],
        "target_limit_id": target_limit["id"],
        "target_limit_after": before + source_minor,
        "block": BudgetLimitsMergedOut(
            budget_period_id=target_limit["budget_period_id"],  # type: ignore[arg-type]
            source_limit_minor=source_minor,
            target_limit_before_minor=before,
            target_limit_after_minor=before + source_minor,
        ),
    }


def merge_out(
    *,
    source_id: UUID,
    target_id: UUID,
    transactions_moved: int,
    limits: BudgetLimitsMergedOut | None,
    source_archived: bool,
    audit_log_id: UUID,
) -> MergeOut:
    return MergeOut(
        source_category_id=source_id,
        target_category_id=target_id,
        transactions_moved=transactions_moved,
        budget_limits_merged=limits,
        # Always true, and stated rather than assumed: it is the guarantee the merge sheet
        # repeats back to the user, and a regression here would be silent otherwise.
        past_periods_untouched=True,
        source_archived=source_archived,
        audit_log_id=audit_log_id,
    )


# ---------- delete -------------------------------------------------------


def require_deletable(
    category: RowDict, *, historical_txn_count: int, past_limit_count: int
) -> None:
    """The only hard delete in the system, and the two things that forbid it.

    `historical_txn_count` counts `txn` rows **regardless of `deleted_at`**, which is not
    what the list's `total_transaction_count` reports. That is deliberate and forced:
    `txn.category_id` carries no cascade, so a soft-deleted row still references the
    category and a hard delete would raise 23503. "Has never held a transaction" has to mean
    ever, or the statement fails at the database instead of here.

    `past_limit_count` is a **spec delta**. §6.6 names only transactions, but
    `budget_limit.category_id` is ON DELETE RESTRICT precisely because cascading it "silently
    erases the allocated total of every closed period" (migrations README). A category that
    was allocated money in a closed month has history, even with nothing spent against it.
    """
    require_mutable(category)

    if historical_txn_count > 0:
        raise CategoryNotEmptyError(historical_txn_count, "transaction")
    if past_limit_count > 0:
        raise CategoryNotEmptyError(historical_txn_count, "past_budget_limit")


# ---------- ordering -----------------------------------------------------


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
    """Four figures on three different scopes, which the spec only half explains.

    `spent_minor` is period-scoped — that much is stated. `usage_rank_30d` is the rolling
    window, by definition. `transaction_count` is undocumented; it is period-scoped here
    because it sits beside `spent_minor` in the same object, and a count that disagreed with
    the spend next to it would be read as a bug by anyone looking at the two together.
    `last_txn_at` is all-time, because "last 11 Aug" means the last one, not the last one
    this month.
    """
    return CategoryStatsOut(
        transaction_count=as_int(row["period_transaction_count"], "period_transaction_count"),
        spent_minor=as_int(row["period_spent_minor"], "period_spent_minor"),
        usage_rank_30d=ranks.get(str(row["name"])),
        last_txn_at=row["last_txn_at"],  # type: ignore[arg-type]
    )


def item_out(
    row: RowDict, stats: CategoryStatsOut | None, icons_by_token: dict[str, RowDict]
) -> CategoryItemOut:
    token, render_value = resolve_for_render(str(row["icon"]), icons_by_token)
    return CategoryItemOut(
        id=row["id"],  # type: ignore[arg-type]
        name=str(row["name"]),
        short_label=as_str_or_none(row["short_label"]),
        icon=str(row["icon"]),
        icon_pack_key=token.split(":", 1)[0],
        resolved_render_value=render_value,
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


def update_out(
    row: RowDict,
    icons_by_token: dict[str, RowDict],
    change: BucketChangeOut,
) -> CategoryUpdateOut:
    item = item_out(row, None, icons_by_token)
    return CategoryUpdateOut(**item.model_dump(), bucket_change=change)


def archive_out(row: RowDict) -> CategoryArchiveOut:
    return CategoryArchiveOut(
        id=row["id"],  # type: ignore[arg-type]
        name=str(row["name"]),
        is_archived=True,
        archived_at=row["archived_at"],  # type: ignore[arg-type]
        total_transaction_count=as_int(
            row["total_transaction_count"], "total_transaction_count"
        ),
        # Both asserted, not computed: archive deliberately leaves the current period's
        # limit alone, because removing it mid-month moves the unallocated figure under the
        # user (spec 1386). The next period simply does not carry it.
        current_period_limit_retained=True,
        carried_to_next_period=False,
        version=as_int(row["version"], "version"),
    )


def restore_out(row: RowDict) -> CategoryRestoreOut:
    return CategoryRestoreOut(
        id=row["id"],  # type: ignore[arg-type]
        name=str(row["name"]),
        is_archived=False,
        archived_at=None,
        total_transaction_count=as_int(
            row["total_transaction_count"], "total_transaction_count"
        ),
        version=as_int(row["version"], "version"),
    )


def list_out(
    rows: list[RowDict],
    *,
    currency_code: str,
    minor_unit: int,
    budget_period_id: object | None,
    sort: str,
    with_stats: bool,
    icons_by_token: dict[str, RowDict],
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
            item_out(row, stats_for(row, ranks) if with_stats else None, icons_by_token)
            for row in ordered
        ],
        # Always null: sixteen seeded categories against a default page size of fifty means
        # the second page is unreachable. See the note on `CategoryListOut`.
        next_cursor=None,
    )
