"""The models mirror ../penny-pulse-migrations 1:1. This is what proves it.

No database: the migration SQL is the source of truth, and it is a file. When
this fails the migration is right and the model is the bug.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  — registers every model on Base.metadata
from app.models.base import Base, live_metadata

MIGRATIONS = Path(__file__).resolve().parents[2] / "penny-pulse-migrations"

# Table-level clauses, as opposed to column definitions.
TABLE_LEVEL = ("constraint", "primary", "unique", "check", "foreign", "exclude")


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def _split_top_level(body: str) -> list[str]:
    """Split on commas that are not inside parentheses — CHECK (a IN ('x','y'))."""
    parts: list[str] = []
    depth = 0
    buffer: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    parts.append("".join(buffer))
    return parts


def _parse_migrations() -> dict[str, list[str]]:
    # `0*.sql`, not `000*.sql`: the narrower glob silently stopped matching at 0009, so
    # 0010 and everything after it was invisible to this file. 0010 adds no tables, which
    # is why nothing caught it until 0011 did.
    sql = _strip_comments(
        "\n".join(f.read_text(encoding="utf-8") for f in sorted(MIGRATIONS.glob("0*.sql")))
    )
    tables: dict[str, list[str]] = {}
    for match in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\(", sql):
        cursor = match.end()
        depth = 1
        while depth:
            if sql[cursor] == "(":
                depth += 1
            elif sql[cursor] == ")":
                depth -= 1
            cursor += 1
        columns = []
        for item in _split_top_level(sql[match.end() : cursor - 1]):
            item = item.strip()
            if item and item.split()[0].lower() not in TABLE_LEVEL:
                columns.append(item.split()[0].strip('"'))
        tables[match.group(1)] = columns

    # A column added after its table was created is still a column. 0012 adds
    # `category_template.is_starter` this way, and reading only CREATE TABLE would report the
    # model as having one column too many — the exact inversion of this file's purpose, which
    # is to trust the SQL. Appended rather than inserted because that is where Postgres puts
    # it, and this test compares ordered lists.
    #
    # Single-column form only. A comma-separated `ADD COLUMN a, ADD COLUMN b` would need
    # splitting; no migration uses one, and a silent miss here would fail loudly in the test
    # rather than pass quietly.
    for table_name, column_name in re.findall(
        r"ALTER TABLE\s+(?:public\.)?(\w+)\s+ADD COLUMN\s+(?:IF NOT EXISTS\s+)?(\w+)", sql
    ):
        if column_name not in tables[table_name]:
            tables[table_name].append(column_name)
    return tables


MIGRATION_TABLES = _parse_migrations()


def test_the_migrations_still_create_sixty_four_tables():
    """A changed count means a migration landed and the models have not caught up.

    61 through 0010; 0011 adds `icon_pack`, `icon_asset` and `colour_swatch`. 0012 adds a
    column rather than a table, so this count is deliberately unchanged by it.
    """
    assert len(MIGRATION_TABLES) == 64


def test_every_table_has_exactly_one_model():
    mapped = set(Base.metadata.tables)

    assert mapped == set(MIGRATION_TABLES)


@pytest.mark.parametrize("table_name", sorted(MIGRATION_TABLES))
def test_model_columns_match_the_migration(table_name: str):
    mapped = [c.name for c in Base.metadata.tables[table_name].columns]

    assert mapped == MIGRATION_TABLES[table_name]


def test_generated_columns_are_never_written():
    """A writer that sends these sends a value the database is about to overwrite."""
    generated = [
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.computed is not None
    ]

    assert sorted(generated) == [
        ("habit_log", "logged"),
        ("insight_readiness", "is_unlocked"),
        ("insight_readiness", "remaining"),
        ("notification_ledger", "local_week"),
        ("recurring_candidate", "annualised_cost_minor"),
        ("stat_baseline", "iqr_minor"),
        ("stat_baseline", "is_sufficient"),
    ]


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("habit_log", "logged"),
        ("stat_baseline", "iqr_minor"),
        ("notification_ledger", "local_week"),
    ],
)
def test_a_generated_column_is_absent_from_insert(table_name: str, column_name: str):
    table = Base.metadata.tables[table_name]
    writable = {c.name: None for c in table.columns if c.computed is None}

    statement = str(insert(table).values(**writable).compile(dialect=postgresql.dialect()))

    assert not re.search(rf"\b{column_name}\b", statement.split("VALUES")[0])


def test_the_eight_live_views_exist():
    assert sorted(live_metadata.tables) == [
        "account_live",
        "budget_limit_live",
        "budget_period_live",
        "category_live",
        "goal_live",
        "profile_live",
        "recurring_live",
        "txn_live",
    ]


@pytest.mark.parametrize("view_name", sorted(live_metadata.tables))
def test_reads_go_through_the_live_view(view_name: str):
    """`deleted_at IS NULL` lives in the view. A read of the base table skips it."""
    sql = str(select(live_metadata.tables[view_name]).compile(dialect=postgresql.dialect()))

    assert f"FROM {view_name}" in sql


def test_no_model_shadows_sqlalchemy_metadata():
    """`metadata` is reserved on the declarative base; those columns map to `metadata_`."""
    for mapper in Base.registry.mappers:
        assert "metadata" not in mapper.class_.__dict__
