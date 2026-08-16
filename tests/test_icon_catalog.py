"""The icon catalog: migration 0011 and `GET /icon-catalog`.

Two halves, defending different things, and neither replaces the other.

The **static** half reads `0011` as a file: that it is re-runnable, that every INSERT row
matches its column list, and that every glyph it names exists in the webfont `index.html`
pins. That last one cannot be checked against a database at all — Postgres stores
`ti ti-nonsense` perfectly happily, and only a phone shows the empty box — so the vendored
class list in `fixtures/` is the only place it can ever be caught.

The **database** half applies `0011` for real through conftest's migration fixture and
serves it through the router. That is what proves the file produces the rows the endpoint
needs, which no amount of parsing can. It needs `TEST_DATABASE_URL` and skips without it;
see 'Prerequisites' in CLAUDE.md.

`tests/test_models_mirror_migrations.py` established the static approach: the SQL is the
source of truth, and it is a file.
"""

import re
from pathlib import Path

import pytest

from app.services import icon_catalog as icon_service

MIGRATIONS = Path(__file__).resolve().parents[2] / "penny-pulse-migrations"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ICON_CATALOG_SQL = (MIGRATIONS / "0011_icon_catalog.sql").read_text(encoding="utf-8")
SEED_SQL = (MIGRATIONS / "0009_seed_reference_data.sql").read_text(encoding="utf-8")
SUGGESTION_SQL = (MIGRATIONS / "0012_category_suggestions.sql").read_text(encoding="utf-8")


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def _namespaced(icon: str) -> str:
    """A template icon in the form the catalog keys on, whichever shape the seed wrote.

    The same tolerant rule as `services.icon_catalog.normalise_token`, restated here because
    these assertions read the SQL rather than calling the app. A bare token means `tabler:`.
    """
    return icon if ":" in icon else f"tabler:{icon}"


def _value_tuples(sql: str, table: str) -> list[list[str]]:
    """Every VALUES row of every INSERT into `table`, as lists of quoted literals.

    Quote-aware rather than a naive split: the seed carries `'Rent & Housing'` and
    `ARRAY['a','b']`, both of which contain characters a comma-split would trip on.
    """
    rows: list[list[str]] = []
    for insert in re.finditer(
        rf"INSERT INTO {table}\s*\((.*?)\)\s*VALUES(.*?);", sql, re.DOTALL | re.IGNORECASE
    ):
        # `ON CONFLICT (template_key) DO NOTHING` is a parenthesised group too, and reading
        # it as a value row yields an empty tuple that every index below then trips on.
        body = re.split(r"\bON CONFLICT\b", insert.group(2), flags=re.IGNORECASE)[0]
        depth = 0
        in_quote = False
        current: list[str] = []
        buffer: list[str] = []
        index = 0
        while index < len(body):
            char = body[index]
            if in_quote:
                if char == "'" and index + 1 < len(body) and body[index + 1] == "'":
                    buffer.append("'")
                    index += 2
                    continue
                if char == "'":
                    in_quote = False
                    current.append("".join(buffer))
                    buffer = []
                else:
                    buffer.append(char)
            elif char == "'":
                in_quote = True
            elif char == "(":
                depth += 1
                if depth == 1:
                    current = []
            elif char == ")":
                depth -= 1
                if depth == 0:
                    rows.append(current)
            index += 1
    return rows


def _split_row(body: str) -> list[list[str]]:
    """Every VALUES row as its raw values, one entry per column.

    Separate from `_value_tuples` because the two answer different questions. That one
    collects quoted literals, which is what the content assertions want and is why it can
    read `ARRAY['a','b']` as two strings and skip `NULL` entirely. Counting columns needs
    the opposite: one entry per top-level comma, whatever sits between them. The income
    seed rows end `NULL, NULL, NULL` and every icon row carries an `ARRAY[...]`, so a
    literal count and a column count are not the same number.
    """
    rows: list[list[str]] = []
    current: list[str] = []
    buffer: list[str] = []
    depth = 0
    in_quote = False
    index = 0
    while index < len(body):
        char = body[index]
        if in_quote:
            buffer.append(char)
            if char == "'":
                if index + 1 < len(body) and body[index + 1] == "'":
                    buffer.append("'")
                    index += 2
                    continue
                in_quote = False
        elif char == "'":
            buffer.append(char)
            in_quote = True
        elif char in "([":
            # `ARRAY[...]` and any parenthesised expression go to depth 2, so their commas
            # are values inside one column rather than column separators.
            depth += 1
            if depth == 1:
                current = []
                buffer = []
            else:
                buffer.append(char)
        elif char in ")]":
            depth -= 1
            if depth == 0:
                current.append("".join(buffer).strip())
                rows.append(current)
                buffer = []
            else:
                buffer.append(char)
        elif char == "," and depth == 1:
            current.append("".join(buffer).strip())
            buffer = []
        elif depth >= 1:
            buffer.append(char)
        index += 1
    return rows


def _insert_statements(sql: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """Every INSERT in the file as `(table, columns, rows)`."""
    statements: list[tuple[str, list[str], list[list[str]]]] = []
    for insert in re.finditer(
        r"INSERT INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES(.*?);", sql, re.DOTALL | re.IGNORECASE
    ):
        columns = [column.strip() for column in insert.group(2).split(",")]
        body = re.split(r"\bON CONFLICT\b", insert.group(3), flags=re.IGNORECASE)[0]
        statements.append((insert.group(1), columns, _split_row(body)))
    return statements


# The structural assertions below must read statements, not prose: 0011's header comment
# describes its own guards ("every INSERT carries ON CONFLICT DO NOTHING"), and counting
# those sentences as SQL made this file pass itself.
ICON_CATALOG_BODY = _strip_comments(ICON_CATALOG_SQL)

# Both seeds, because a template's icon has to resolve wherever the template came from. 0009
# writes bare tokens and relies on 0011's backfill; 0012 writes them already namespaced. The
# assertions below therefore normalise rather than assuming either shape — blindly prefixing
# would turn `tabler:fuel` into `tabler:tabler:fuel` and fail every 0012 row.
TEMPLATE_ROWS = _value_tuples(
    _strip_comments(SEED_SQL) + "\n" + _strip_comments(SUGGESTION_SQL), "category_template"
)
ICON_ASSET_ROWS = _value_tuples(_strip_comments(ICON_CATALOG_SQL), "icon_asset")
SWATCH_ROWS = _value_tuples(_strip_comments(ICON_CATALOG_SQL), "colour_swatch")
SYNONYM_ROWS = _value_tuples(_strip_comments(ICON_CATALOG_SQL), "synonym_group")

# `(template_key, kind, name, short_label, icon, colour, ...)` — icon is 5th, colour 6th.
TEMPLATE_ICONS = {row[4] for row in TEMPLATE_ROWS}
TEMPLATE_COLOURS = {row[5] for row in TEMPLATE_ROWS}
SEEDED_TOKENS = {row[0] for row in ICON_ASSET_ROWS}
SEEDED_SWATCHES = {row[0] for row in SWATCH_ROWS}

# `(template_key, kind, ...)` — kind is 2nd. Which kind each template's icon has to serve.
TEMPLATE_KIND_BY_ICON = {row[4]: row[1] for row in TEMPLATE_ROWS}

# `kind_hint` cannot be read positionally: it sits after a variable-length ARRAY of keywords,
# and two of those keyword lists contain the literal words 'income' and 'expense'. Matched
# structurally instead — after the closing bracket of the ARRAY, before the sort order.
ICON_HINTS = {
    token: (None if hint == "NULL" else hint.strip("'"))
    for token, hint in re.findall(
        r"\('([^']+)',\s*'[^']+',\s*[^,]+,\s*'[^']*',\s*ARRAY\[[^\]]*\],\s*(NULL|'income'|'expense')",
        ICON_CATALOG_BODY,
    )
}
ICON_RENDER_VALUES = {row[0]: row[2] for row in ICON_ASSET_ROWS}

ICON_CATALOG_INSERTS = _insert_statements(ICON_CATALOG_BODY)

# Every glyph class that exists in the webfont `index.html` pins. Vendored, because a test
# that fetches this from the CDN fails offline and passes against whatever version is
# current rather than the one the app loads.
TABLER_GLYPHS = frozenset(
    line.strip()
    for line in (FIXTURES / "tabler_icons_3.7.0.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
)


# ---------- the seed actually parsed -------------------------------------


def test_the_seed_templates_were_found():
    """Guards the parser: an empty parse would make every assertion below vacuous.

    18 from 0009 plus 12 from 0012, and no icon is shared between the two files — the
    suggestion set was chosen from the icons `0011` seeded and nothing else had claimed.
    """
    assert len(TEMPLATE_ROWS) == 30
    assert len(TEMPLATE_ICONS) == 28


def test_the_catalog_seeds_were_found():
    assert len(ICON_ASSET_ROWS) == 63  # 39 expense + 14 income + 2 either, tabler; 8 emoji
    assert len(SWATCH_ROWS) == 12
    assert len(SYNONYM_ROWS) > 40
    assert len(ICON_HINTS) == len(ICON_ASSET_ROWS)  # the hint parser saw every row


def test_no_token_is_seeded_twice():
    """`ON CONFLICT DO NOTHING` would swallow a duplicate silently, keeping the first row."""
    assert len(SEEDED_TOKENS) == len(ICON_ASSET_ROWS)


# ---------- income and expense get separate icon sets --------------------


def test_every_icon_declares_a_kind_or_declares_neither():
    assert set(ICON_HINTS.values()) == {"expense", "income", None}


def test_both_kinds_have_a_set_worth_picking_from():
    """A one-icon picker is a label, not a choice.

    The floor for income is deliberate: the four seeded income templates are only a starting
    point, and a user adding "Rent received" or "FD interest" by hand needs a glyph that
    means it rather than a repurposed wallet.
    """
    hints = list(ICON_HINTS.values())

    assert hints.count("income") >= 10
    assert hints.count("expense") >= 20


def test_only_genuinely_neutral_icons_are_offered_to_both_kinds():
    """`kind_hint` NULL is what puts an icon on both tabs, so it has to stay a short list.

    Other and Unaccounted earn it — every category set needs an Other, and 0009 seeds
    `unaccounted_out` and `unaccounted_in` onto the same `question` token. A third NULL would
    almost certainly be a decision nobody made.
    """
    neutral = {token for token, hint in ICON_HINTS.items() if hint is None}

    assert neutral == {"tabler:dots", "tabler:question"}


@pytest.mark.parametrize("icon", sorted(TEMPLATE_ICONS))
def test_every_seeded_template_icon_is_offered_for_its_own_kind(icon: str):
    """The invariant that makes filtering by `kind_hint` safe rather than merely tidier.

    Every seeded category already wears one of these icons. If the picker filtered that icon
    out of the tab its own category lives on, a user opening Salary would find its glyph
    missing from the grid and could not choose it back after trying another.
    """
    hint = ICON_HINTS[_namespaced(icon)]

    assert hint in (TEMPLATE_KIND_BY_ICON[icon], None)


# ---------- the glyphs are real -------------------------------------------


def test_the_freelance_icon_names_a_glyph_that_exists():
    """`ti-laptop` is not a Tabler class. It rendered as an empty box, which review cannot see.

    Pinned as its own test rather than left to the general rule below because this one shipped
    broken: `src/lib/icons.ts` mapped `laptop` to the non-existent `ti-laptop`, the prototype
    had `ti-device-laptop` right, and the seeded Freelance income category wore the wrong one.
    """
    assert ICON_RENDER_VALUES["tabler:laptop"] == "ti ti-device-laptop"


def test_every_tabler_render_value_is_a_webfont_class():
    """`glyph_font` assets are drawn as `<i className={render_value}>`, so the class is the API."""
    for token, render_value in ICON_RENDER_VALUES.items():
        if token.startswith("tabler:"):
            assert re.fullmatch(r"ti ti-[a-z0-9-]+", render_value), token


def test_the_vendored_glyph_list_is_the_whole_webfont():
    """Guards the fixture: a truncated list would fail honest rows, an empty one pass everything."""
    assert len(TABLER_GLYPHS) == 5377


@pytest.mark.parametrize(
    "token", sorted(token for token in ICON_RENDER_VALUES if token.startswith("tabler:"))
)
def test_every_seeded_glyph_exists_in_the_webfont(token: str):
    """The check that cannot be done by reading the SQL, and the one that has already failed.

    An unknown Tabler class renders as an empty box rather than a missing-glyph mark, so a
    typo is invisible in review and obvious only on a phone. `ti-laptop` reached seeded data
    exactly this way. Checked against the class list of the version `index.html` pins, so
    this fails in CI instead.
    """
    glyph = ICON_RENDER_VALUES[token].removeprefix("ti ti-")

    assert glyph in TABLER_GLYPHS


@pytest.mark.parametrize(
    "absent", ["laptop", "utensils", "cart", "film", "undo", "fuel", "heartbeat"]
)
def test_the_tokens_that_look_like_glyph_names_are_not_glyph_names(absent: str):
    """The negative control, and the reason `token` and `render_value` are separate columns.

    Six seeded tokens have no same-named glyph, which is what a naming convention would have
    produced. If any of these ever starts existing, the test above stops proving anything by
    itself — a token interpolated straight into a class would begin to work by accident.

    `heartbeat` is the exception that earns its place: it DOES exist, and the prototype used
    it for `heart`. It is listed here so a future edit cannot quietly claim it is missing.
    """
    if absent == "heartbeat":
        assert absent in TABLER_GLYPHS
    else:
        assert absent not in TABLER_GLYPHS


# ---------- the seed rows are well formed --------------------------------


def test_every_insert_was_parsed():
    """Seven statements: one pack, four asset batches, one swatch, one synonym."""
    assert [table for table, _, _ in ICON_CATALOG_INSERTS] == [
        "icon_pack",
        "icon_asset",
        "icon_asset",
        "icon_asset",
        "icon_asset",
        "colour_swatch",
        "synonym_group",
    ]


@pytest.mark.parametrize("statement", range(7))
def test_every_insert_row_matches_its_column_list(statement: int):
    """A short row shifts every value after the gap into the wrong column.

    Postgres would reject the arity outright, but only when the migration is applied — and
    this one is 63 hand-written rows wide enough to wrap. Counting here means a missing comma
    fails in CI rather than halfway through a production apply, with the statements before it
    already committed.
    """
    table, columns, rows = ICON_CATALOG_INSERTS[statement]

    for row in rows:
        assert len(row) == len(columns), f"{table}: {row}"


def test_no_seeded_value_is_empty():
    """An empty string would satisfy the arity count and still be wrong — `,,` parses as a gap."""
    for table, _, rows in ICON_CATALOG_INSERTS:
        for row in rows:
            assert all(value != "" for value in row), f"{table}: {row}"


# ---------- every existing icon resolves ---------------------------------


@pytest.mark.parametrize("icon", sorted(TEMPLATE_ICONS))
def test_every_seeded_template_icon_resolves_to_an_icon_asset(icon: str):
    """The claim the migration is for. An unresolvable token is a blank tile on a phone.

    0009 holds bare tokens (`utensils`) and 0011 backfills them to `tabler:utensils`; 0012
    writes the namespaced form directly. Normalising covers both, because the backfill and the
    seed have to agree or the data lands somewhere the catalog cannot describe.
    """
    assert _namespaced(icon) in SEEDED_TOKENS


@pytest.mark.parametrize("colour", sorted(TEMPLATE_COLOURS))
def test_every_seeded_template_colour_is_a_swatch(colour: str):
    assert colour in SEEDED_SWATCHES


def test_every_token_is_namespaced():
    """A bare token in the catalog would defeat the namespacing the migration introduces."""
    assert all(":" in token for token in SEEDED_TOKENS)


def test_every_icon_asset_names_a_seeded_pack():
    packs = {row[0] for row in _value_tuples(_strip_comments(ICON_CATALOG_SQL), "icon_pack")}

    assert {row[1] for row in ICON_ASSET_ROWS} <= packs


# ---------- idempotency ---------------------------------------------------


def test_every_created_table_is_guarded():
    created = re.findall(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", ICON_CATALOG_BODY)
    guarded = re.findall(r"CREATE TABLE\s+IF NOT EXISTS\s+(\w+)", ICON_CATALOG_BODY)

    assert created == guarded


def test_every_created_index_is_guarded():
    created = re.findall(r"CREATE INDEX(?:\s+IF NOT EXISTS)?\s+(\w+)", ICON_CATALOG_BODY)
    guarded = re.findall(r"CREATE INDEX\s+IF NOT EXISTS\s+(\w+)", ICON_CATALOG_BODY)

    assert created == guarded


def test_every_insert_tolerates_a_rerun():
    inserts = re.findall(r"INSERT INTO\s+(\w+)", ICON_CATALOG_BODY)
    conflicts = re.findall(r"ON CONFLICT[^;]*DO NOTHING", ICON_CATALOG_BODY)

    assert len(inserts) == len(conflicts)


def test_the_policy_is_dropped_before_it_is_created():
    """`CREATE POLICY` has no IF NOT EXISTS, so a re-run fails without the DROP."""
    assert "DROP POLICY IF EXISTS" in ICON_CATALOG_BODY
    assert ICON_CATALOG_BODY.index("DROP POLICY IF EXISTS") < ICON_CATALOG_BODY.index(
        "CREATE POLICY"
    )


def test_the_backfill_cannot_double_prefix():
    """Second run must match nothing. Without the guard it would produce `tabler:tabler:x`."""
    backfills = re.findall(
        r"UPDATE\s+(\w+)\s+SET icon = 'tabler:' \|\| icon\s+WHERE icon NOT LIKE '%:%'",
        ICON_CATALOG_BODY,
    )

    assert sorted(backfills) == ["category", "category_template"]


def test_the_migration_opens_no_transaction():
    """House style: each file is a bare statement list applied as one Supabase migration."""
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", ICON_CATALOG_BODY, re.MULTILINE)


# ---------- synonym groups ------------------------------------------------


def _terms(group_key: str) -> set[str]:
    return {row[1] for row in SYNONYM_ROWS if row[0] == group_key}


def test_the_food_group_matches_the_one_named_in_the_spec():
    """spec.txt:1284 names this group explicitly, so it is contract, not decoration."""
    assert {"food", "meals", "lunch", "dinner", "eating out"} <= _terms("food")


def test_petrol_is_not_grouped_with_transport():
    """Keeps `Petrol` / `Transport` a non-match in the 6.2 fixture table.

    Budgeting fuel separately from commuting is a real distinction, and a synonym group
    that merged them would override the similarity score and force a false near-duplicate.
    """
    travel = _terms("travel")

    assert "petrol" not in travel
    assert "fuel" not in travel


def test_every_synonym_term_is_normalised():
    """Terms are compared against `name_normalized`, i.e. lower(trim(name))."""
    for _, term in ((row[0], row[1]) for row in SYNONYM_ROWS):
        assert term == term.lower().strip()


def test_the_seeded_groups_cover_the_common_sprawl():
    keys = {row[0] for row in SYNONYM_ROWS}

    assert keys == {"food", "travel", "groceries", "bills", "health", "shopping"}


# ---------- the resolver (pure: no database, no HTTP) ---------------------


def _icon(token, pack="tabler", render="ti ti-x", enabled=True):
    return {
        "token": token,
        "pack_key": pack,
        "render_value": render,
        "label": token,
        "keywords": [],
        "kind_hint": None,
        "is_enabled": enabled,
        "sort_order": 1,
    }


CATALOG = icon_service.index_by_token(
    [
        _icon("tabler:utensils", render="ti ti-tools-kitchen-2"),
        _icon("tabler:dots", render="ti ti-dots"),
        _icon("emoji:ramen", pack="emoji_food", render="\N{STEAMING BOWL}"),
        _icon("tabler:retired", render="ti ti-old", enabled=False),
    ]
)


def test_a_bare_token_resolves_as_tabler():
    """Pre-0011 data and any client that has not refreshed still send bare tokens."""
    assert icon_service.normalise_token("utensils") == "tabler:utensils"


def test_a_namespaced_token_is_left_alone():
    assert icon_service.normalise_token("emoji:ramen") == "emoji:ramen"


def test_a_token_is_trimmed_but_not_lowercased():
    assert icon_service.normalise_token("  emoji:ramen  ") == "emoji:ramen"


def test_a_known_token_renders_its_own_glyph():
    assert icon_service.resolve_for_render("tabler:utensils", CATALOG) == (
        "tabler:utensils",
        "ti ti-tools-kitchen-2",
    )


def test_a_bare_known_token_renders_its_own_glyph():
    assert icon_service.resolve_for_render("utensils", CATALOG)[1] == "ti ti-tools-kitchen-2"


def test_an_unknown_token_never_breaks_a_screen():
    """The rule the whole read path depends on: resolution cannot fail."""
    assert icon_service.resolve_for_render("tabler:not-a-real-icon", CATALOG) == (
        "tabler:dots",
        "ti ti-dots",
    )


def test_a_disabled_asset_still_renders_its_own_glyph():
    """Disabling a pack stops it being offered. Rewriting existing categories is separate."""
    assert icon_service.resolve_for_render("tabler:retired", CATALOG)[1] == "ti ti-old"


def test_a_disabled_asset_cannot_be_written():
    assert icon_service.is_writable_token("tabler:retired", CATALOG) is False


def test_an_unknown_token_cannot_be_written():
    """The strict half of the pair — this is what keeps the fallback rare."""
    assert icon_service.is_writable_token("tabler:not-a-real-icon", CATALOG) is False


def test_an_enabled_token_can_be_written_bare_or_namespaced():
    assert icon_service.is_writable_token("utensils", CATALOG) is True
    assert icon_service.is_writable_token("emoji:ramen", CATALOG) is True


# ---------- pack visibility ----------------------------------------------


def _pack(key, enabled=True, minimum=None):
    return {
        "pack_key": key,
        "label": key,
        "renderer": "glyph_font",
        "is_enabled": enabled,
        "min_app_build": minimum,
        "sort_order": 1,
    }


def test_a_disabled_pack_is_never_served():
    assert icon_service.visible_packs([_pack("old", enabled=False)], 99) == []


def test_an_ungated_pack_is_served_to_a_client_that_sent_no_build():
    assert len(icon_service.visible_packs([_pack("tabler")], None)) == 1


def test_a_gated_pack_is_hidden_from_a_client_that_sent_no_build():
    """Unknown build is treated as old: a pack the client cannot draw is the worse failure."""
    assert icon_service.visible_packs([_pack("images", minimum=42)], None) == []


def test_a_gated_pack_is_hidden_from_an_older_client():
    assert icon_service.visible_packs([_pack("images", minimum=42)], 41) == []


def test_a_gated_pack_reaches_a_new_enough_client():
    assert len(icon_service.visible_packs([_pack("images", minimum=42)], 42)) == 1


# ---------- the catalog response -----------------------------------------


def test_an_asset_whose_pack_is_hidden_is_not_served():
    """Otherwise the client gets a render value and no renderer to draw it with."""
    catalog = icon_service.catalog_out(
        [_pack("tabler")],
        [_icon("tabler:dots"), _icon("emoji:ramen", pack="emoji_food")],
        [],
    )

    assert [icon.token for icon in catalog.icons] == ["tabler:dots"]


def test_the_version_ignores_row_order():
    """Postgres makes no ordering promise the digest should depend on."""
    icons = [_icon("tabler:a"), _icon("tabler:b")]

    assert icon_service.catalog_version([], icons, []) == icon_service.catalog_version(
        [], list(reversed(icons)), []
    )


def test_the_version_changes_when_an_icon_is_added():
    """This is what makes a new pack reach the client on the next revalidate."""
    before = icon_service.catalog_version([], [_icon("tabler:a")], [])
    after = icon_service.catalog_version([], [_icon("tabler:a"), _icon("emoji:ramen")], [])

    assert before != after


def test_the_version_changes_when_a_glyph_changes():
    before = icon_service.catalog_version([], [_icon("tabler:a", render="ti ti-one")], [])
    after = icon_service.catalog_version([], [_icon("tabler:a", render="ti ti-two")], [])

    assert before != after


def test_a_new_pack_needs_no_code_change():
    """The claim the migration exists to support, asserted rather than asserted-about.

    Nothing below names the pack, the renderer or the tokens: they arrive as rows and are
    served as rows. Adding `emoji_food` to the response took seed data and no edit here.
    """
    catalog = icon_service.catalog_out(
        [_pack("tabler"), _pack("brand_new_pack")],
        [_icon("tabler:dots"), _icon("brand_new_pack:whatever", pack="brand_new_pack")],
        [],
    )

    assert {icon.token for icon in catalog.icons} == {
        "tabler:dots",
        "brand_new_pack:whatever",
    }
    assert {pack.pack_key for pack in catalog.packs} == {"tabler", "brand_new_pack"}


# ---------- the endpoint, against a real migrated database ----------------
#
# Everything above reads 0011 as a file. These read it as a database: applied by conftest's
# migration fixture, and served through the real router. Between them they close the gap the
# static half admits to — a file can be well formed and still not produce the rows the
# endpoint needs. This is also the closest thing to opening the page: the request the screen
# makes, against the schema production will have.


def test_the_catalog_serves_the_seeded_rows(db_client, sign_in, make_user):
    """Nothing here is mocked, so a 200 is the proof the tables and the seed both landed."""
    sign_in(make_user())

    response = db_client.get("/api/v1/icon-catalog")

    assert response.status_code == 200
    body = response.json()
    assert len(body["icons"]) == 63
    assert len(body["colours"]) == 12
    assert {pack["pack_key"] for pack in body["packs"]} == {"tabler", "emoji_food"}


@pytest.mark.parametrize("icon", sorted(TEMPLATE_ICONS))
def test_every_seeded_template_icon_is_actually_served(icon: str, db_client, sign_in, make_user):
    """The end-to-end form of the static claim, and the one a blank tile would fail.

    The static test proves the token appears in an INSERT. This proves it survives the seed,
    the backfill and the enabled-pack filter, and reaches the client that has to draw it.
    """
    sign_in(make_user())

    body = db_client.get("/api/v1/icon-catalog").json()

    assert _namespaced(icon) in {served["token"] for served in body["icons"]}


def test_the_freelance_glyph_is_corrected_in_the_database(db_client, sign_in, make_user):
    """0011's UPDATE has to have run, not merely been written.

    It is separate from the INSERT precisely because `ON CONFLICT DO NOTHING` would keep a
    broken `render_value` on a database an earlier draft had already touched, so the value
    served is the only place that distinction is visible.
    """
    sign_in(make_user())

    body = db_client.get("/api/v1/icon-catalog").json()

    served = {icon["token"]: icon["render_value"] for icon in body["icons"]}
    assert served["tabler:laptop"] == "ti ti-device-laptop"


def test_the_emoji_pack_is_served_without_a_glyph_class(db_client, sign_in, make_user):
    """A second renderer reaching the client is what makes a new pack seed data.

    Asserted on the payload rather than the seed: `renderer` travels on the pack, and an
    asset whose pack the client never received is a render value with no way to draw it.
    """
    sign_in(make_user())

    body = db_client.get("/api/v1/icon-catalog").json()

    renderers = {pack["pack_key"]: pack["renderer"] for pack in body["packs"]}
    assert renderers["emoji_food"] == "unicode"
    ramen = next(icon for icon in body["icons"] if icon["token"] == "emoji:ramen")
    assert ramen["render_value"] == "\N{STEAMING BOWL}"


def test_the_catalog_is_reachable_before_onboarding(db_client, sign_in, make_user):
    """No `profile` row exists yet here.

    The picker is how the first category gets an icon, so requiring onboarding to have
    finished would make the first category the one that cannot have one.
    """
    sign_in(make_user())

    assert db_client.get("/api/v1/icon-catalog").status_code == 200


def test_an_unauthenticated_request_is_refused(db_client):
    """Reference data, but not public: the client sends a token for every other call too."""
    assert db_client.get("/api/v1/icon-catalog").status_code == 401


def test_a_revalidating_client_is_told_nothing_changed(db_client, sign_in, make_user):
    sign_in(make_user())
    first = db_client.get("/api/v1/icon-catalog")

    second = db_client.get(
        "/api/v1/icon-catalog", headers={"If-None-Match": first.headers["ETag"]}
    )

    assert second.status_code == 304
    # A 304 carries no body, so dropping the ETag would cost the client the validator it
    # just proved it had and turn every revalidation into a full fetch.
    assert second.headers["ETag"] == first.headers["ETag"]


def test_two_users_get_the_same_catalog(db_client, sign_in, make_user):
    """The tenancy test `.claude/rules/tests.md` makes mandatory, asserting the opposite.

    Reference data is deliberately not tenant-scoped — `icon_asset` has no `user_id` and the
    repository takes none — so the honest version of "a second user cannot see the first
    user's row" is that there is no such row. Written down so that a later per-user icon
    cannot arrive unnoticed.
    """
    sign_in(make_user())
    first = db_client.get("/api/v1/icon-catalog").json()

    sign_in(make_user())
    second = db_client.get("/api/v1/icon-catalog").json()

    assert first == second
