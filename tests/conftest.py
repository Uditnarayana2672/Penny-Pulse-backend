import os
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest

# Set before anything imports app.config, so lib and service tests need no real
# database and no real project. Repository and router tests use TEST_DATABASE_URL with
# a Postgres that has ../penny-pulse-migrations applied — never create_all().
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
# Never the real project. Auth tests stub the JWK client, so nothing here is fetched.
os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "penny-pulse-migrations"

LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})

# Anything that would mean the URL is not a disposable local database.
PRODUCTION_MARKERS = ("supabase", "pooler", "aicizmcvkqcbrsxmdpyj")

# `auth` is Supabase's, not ours, and a local Postgres has none. Only the columns the
# application actually reads are shimmed: `profile.user_id` references `auth.users(id)`,
# `auth.uid()` appears in 23 RLS policy expressions that are parsed at CREATE POLICY time,
# and `GET /me` reads email and providers. The three roles exist because
# `REVOKE ... FROM anon, authenticated` and `CREATE POLICY ... TO authenticated` both
# hard-fail on a missing role.
SUPABASE_SHIM = """
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
DROP SCHEMA IF EXISTS auth CASCADE;
CREATE SCHEMA auth;

CREATE TABLE auth.users (
  id                 uuid PRIMARY KEY,
  email              text,
  raw_app_meta_data  jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
  LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$;

DO $$ BEGIN CREATE ROLE anon         NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role  NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""

# One statement clears every table that references auth.users — all user data — while
# leaving the reference rows migration 0009 seeded, because `currency` and
# `category_template` have no foreign key to it.
TRUNCATE_USER_DATA = "TRUNCATE TABLE auth.users CASCADE"


@pytest.fixture
def client() -> Iterator[object]:
    """The app with no database. Used by tests that never touch one."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _refuse_unless_disposable(url: str) -> None:
    """Raise — never skip — if the URL could be anything but a throwaway local database.

    These fixtures DROP the public schema and TRUNCATE user data. A developer whose `.env`
    holds production credentials is the exact accident being guarded against, and a skip
    would let that mistake pass in silence. `DATABASE_URL` is deliberately not consulted:
    it points at the real project, and `app.db.get_engine` is `lru_cache`d on it.
    """
    lowered = url.lower()
    for marker in PRODUCTION_MARKERS:
        if marker in lowered:
            raise RuntimeError(
                f"TEST_DATABASE_URL contains {marker!r} and may be the real project. "
                "Point it at a local throwaway database whose name ends in _test."
            )

    parsed = urlparse(url)
    if (parsed.hostname or "") not in LOCAL_HOSTNAMES:
        raise RuntimeError(f"TEST_DATABASE_URL host {parsed.hostname!r} is not local.")

    name = (parsed.path or "").lstrip("/")
    if not name.endswith("_test"):
        raise RuntimeError(
            f"TEST_DATABASE_URL database {name!r} must end in '_test', so that a URL "
            "naming a real database cannot be used by accident."
        )


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url is None:
        pytest.skip(
            "TEST_DATABASE_URL is not set. Repository and router tests need a local "
            "Postgres; see 'Prerequisites' in CLAUDE.md."
        )
    _refuse_unless_disposable(url)
    return url


def _migration_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("0*.sql"))
    if not files:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR}.")
    return files


def _apply_verbatim(engine: object, sql: str) -> None:
    """Send SQL exactly as written, with no client-side placeholder parsing.

    `exec_driver_sql` hands psycopg an empty parameter collection, and that is what switches
    on its `%` placeholder scanner: a literal `%` anywhere in the file then raises
    "incomplete placeholder" before a single byte reaches the server. Three migrations carry
    one legitimately — `100%` inside a comment in 0003, `format('%I', ...)` in 0008's policy
    loop, and `LIKE '%:%'` in 0011's backfill — so this is not a niche case, it is most of
    the schema. Omitting the parameters entirely is what makes psycopg send the string
    through untouched.

    Doubling the `%` in the migrations instead would be the wrong repair: the Supabase CLI
    applies those files literally, so a file edited to suit this harness would no longer be
    the file production runs.
    """
    raw = engine.raw_connection()  # type: ignore[attr-defined]
    try:
        with raw.cursor() as cursor:
            cursor.execute(sql)
        raw.commit()
    finally:
        raw.close()


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[object]:
    """A migrated Postgres, rebuilt once per session from the real SQL files.

    `create_all()` is banned: a suite passing against a schema SQLAlchemy invented proves
    nothing about the CHECKs, triggers and exclusion constraints production actually has.
    """
    from sqlalchemy import create_engine, event, text

    built = create_engine(database_url)

    @event.listens_for(built, "connect")
    def _put_extensions_on_the_path(dbapi_connection: object, _record: object) -> None:
        # Migration 0001 installs btree_gist into `extensions` rather than `public`, and
        # budget_period's `EXCLUDE USING gist (user_id WITH =, ...)` cannot resolve an
        # operator class for uuid without it on the search path. Supabase sets this
        # database-wide; a local Postgres does not, and 0002 fails without it.
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("SET search_path TO public, extensions")
        cursor.close()

    expected_name = urlparse(database_url).path.lstrip("/").split("?")[0]
    with built.connect() as connection:
        actual_name = connection.execute(text("SELECT current_database()")).scalar_one()
    if actual_name != expected_name:
        # Catches a URL that lies about its target via a socket or an alias.
        raise RuntimeError(
            f"Connected to {actual_name!r} but TEST_DATABASE_URL names {expected_name!r}."
        )

    _apply_verbatim(built, SUPABASE_SHIM)

    for path in _migration_files():
        # The whole file at once: dollar-quoted function bodies make splitting on
        # semicolons wrong, and psycopg accepts a multi-statement string.
        _apply_verbatim(built, path.read_text(encoding="utf-8"))

    yield built
    built.dispose()


@pytest.fixture
def truncate_between_tests(engine: object) -> Iterator[None]:
    """Real commits and a TRUNCATE, rather than a rolled-back transaction.

    Savepoint isolation would turn the session dependency's commit into a savepoint
    release, and DEFERRABLE INITIALLY DEFERRED constraints are not checked at savepoint
    release — they are checked at an outer commit that would never arrive. Four of the
    cases `.claude/rules/tests.md` lists as having already bitten this schema are deferred
    constraint cases, so rollback isolation would make those tests pass falsely.
    """
    from sqlalchemy import text

    yield
    with engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(text(TRUNCATE_USER_DATA))


@pytest.fixture
def make_user(engine: object, truncate_between_tests: None) -> Callable[..., UUID]:
    """Create an `auth.users` row and return its id.

    Every Phase-1 table references it, so a profile cannot exist without one. Supabase
    creates these; in a test we are standing in for Supabase.
    """
    from sqlalchemy import text

    def _make_user(email: str | None = None) -> UUID:
        user_id = uuid4()
        with engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "INSERT INTO auth.users (id, email, raw_app_meta_data) "
                    "VALUES (:id, :email, :meta)"
                ),
                {
                    "id": user_id,
                    "email": email or f"{user_id}@example.test",
                    "meta": '{"provider": "email", "providers": ["email"]}',
                },
            )
        return user_id

    return _make_user


@pytest.fixture
def db_client(engine: object, truncate_between_tests: None) -> Iterator[object]:
    """A TestClient whose session dependency talks to the migrated test database.

    The override mirrors `app/db.py::get_db` rather than reusing it, because that function
    is bound to the `lru_cache`d production engine. Mirroring keeps the commit-once
    behaviour under test: these router tests exercise the same commit path production does.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session, sessionmaker

    from app.db import get_db
    from app.main import app

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    def override_get_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def sign_in() -> Iterator[Callable[[UUID], None]]:
    """Authenticate as a given user id without minting a token.

    `current_user_id` is tested on its own in `test_auth.py` against real ES256 tokens and
    a stubbed JWK client. Re-deriving a token per request here would test PyJWT rather than
    onboarding, and `.claude/rules/tests.md` allows mocking only the clock and outbound
    HTTP — the JWKS fetch is the outbound HTTP this stands in for.
    """
    from app.auth import current_user_id
    from app.main import app

    def _sign_in(user_id: UUID) -> None:
        app.dependency_overrides[current_user_id] = lambda: user_id

    yield _sign_in
    app.dependency_overrides.pop(current_user_id, None)
