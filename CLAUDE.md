# Penny Pulse — Backend

FastAPI REST API · SQLAlchemy 2.0 · Pydantic v2 · Supabase Postgres.
Serves the Penny Pulse PWA for ~4 users (UDIT + brothers). **Phase 1 only.**

Owns all database access, business rules, money math and authorization.
Owns no UI, no rendering, no client state.

## Commands

```bash
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -r requirements-dev.txt              # requirements.txt is runtime only
uvicorn app.main:app --reload --port 8000          # dev, docs at /docs
pytest                                             # all tests
pytest -k txn -x                                   # one area, stop on first failure
```

## Prerequisites

`pytest` alone runs the service and lib tests against no database. The repository and router
tests need `TEST_DATABASE_URL` and **skip** without it, so a green run that says `skipped`
has not touched the schema at all.

Any local Postgres 17 works, and it needs no service, no admin rights and no installer — the
EDB binaries zip is enough. A non-default port keeps it clear of anything already installed:

```bash
initdb   -D <data> -U postgres -A trust -E UTF8 --locale=C
pg_ctl   -D <data> -l <log> -o "-p 55432 -c listen_addresses=127.0.0.1" start
createdb -h 127.0.0.1 -p 55432 -U postgres penny_pulse_test

TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/penny_pulse_test pytest
```

`conftest` then drops `public`, shims the `auth` schema Supabase owns, and applies
`../penny-pulse-migrations/0*.sql` in order, so the CHECKs, triggers, generated columns and
DEFERRABLE constraints under test are production's. It **refuses** — never skips — a URL that
is not local or whose database name does not end in `_test`, because it drops schemas.

Migrations are applied through the raw DBAPI cursor with no parameters. `exec_driver_sql`
passes an empty parameter collection, which turns on psycopg's `%` placeholder scanner and
makes any file containing a literal `%` fail before it is sent — `100%` in a comment in
`0003`, `format('%I', ...)` in `0008`, `LIKE '%:%'` in `0011`. Escaping the migrations to
suit the harness would mean testing a file production does not run.

## The five that are unrecoverable

1. **Every query filters by `user_id`.** SQLAlchemy connects straight to Postgres over
   `DATABASE_URL` as the `postgres` role, which has `BYPASSRLS`. No API key is involved —
   `service_role` is a PostgREST/`supabase-js` concept and never enters this request
   path. So the database's 136 RLS policies guard the publishable key in the client
   bundle and do **nothing** for us. Tenancy is enforced here or nowhere. `user_id` comes
   only from the verified JWT, never from a body, param or path. A missing filter leaks
   one brother's spending to another.
2. **Money is `BIGINT` paise, always positive.** Never float. Sign lives in `direction`
   (`in`/`out`/`transfer`), never in the number. A negative amount is a bug, not a refund.
3. **Migrations only.** Schema lives in `../penny-pulse-migrations/`, applied by Supabase
   CLI. Never DDL from this repo, never a change through the dashboard UI.
4. **One project.** `Penny Pulse`, ref `aicizmcvkqcbrsxmdpyj`, `ap-south-1`.
   `Portfolio-with-AI-agent` and `aim-tracker` are unrelated — never touch them.
5. **Phase 1 tables only.** 61 tables exist; five phases' worth are empty on purpose.
   Check whether a table already exists before adding anything. Never create one.
   Write set: `profile`, `account`, `category`, `budget_period`, `budget_limit`, `txn`,
   `txn_tag`, `habit_log`, `streak`, `push_subscription`, `audit_log`,
   `notification_ledger`, `insight*`.

## Layering

`routers → services → repositories`, dependencies inward. **`services/` imports neither
`fastapi` nor `sqlalchemy`** — that single ban is what makes every rule testable without a
database. Pragmatic onion, and it stops there: no domain entities, no repository
Protocols, no DI container, no Unit of Work.

One request-scoped session; **the dependency commits once, services never do.** Transfer
pairs, splits and refunds write mutually-referencing rows against `DEFERRABLE` FKs and are
only valid inside one transaction.

**Five files per endpoint:** schema, service, repository, router, test. A sixth means stop
and ask.

## Ask before

Adding a dependency · adding a file outside the five · changing a signature used in more
than one place · introducing a new layer or abstraction · touching auth, sessions or
transaction boundaries · anything implying a migration.

One message to ask. A day to reverse.

## Boring code

No metaclasses, no custom decorators, no computed `getattr`, no comprehension nested more
than one deep. Comments explain *why*, never *what* — if a function needs a *what*
comment, rewrite it. Full words in names (`txn` excepted, it's the table name). Type hints
everywhere; no bare `Any`. Duplication beats the wrong abstraction — three occurrences
before extracting.

## Logging

Four real salaries are in this database. **Never log amounts, notes, merchant names or
category names** — log `txn.id`, `user_id` and the operation. Never log keys, JWTs or
`Authorization` headers.

## Do not

Add a queue, cache, worker or second service · add Alembic · write new spec documents ·
build Phase 2+ features (balances, capture rate, search, recurring, AI chat) because their
tables exist · scaffold for later.

This project's failure mode is documenting instead of shipping.

## Where the rest lives

Detailed conventions load automatically when you open a matching file, from
`.claude/rules/`: `repositories.md`, `services.md`, `api.md`, `models.md`, `tests.md`.

Read on demand, only when relevant:

| File | When |
|---|---|
| `../ARD-v2-Decisions-Delta.md` | A product-behaviour or "why is it built this way" question |
| `../penny-pulse-migrations/README.md` | Touching the schema, or unsure why a column exists |
| `../penny-pulse-migrations/000*.sql` | Exact columns, CHECKs or triggers |
| `../ARD-Budget-Tracker-v2.md` | Functional requirements by screen. Stale where the delta disagrees |

Precedence: **this file → `.claude/rules/` → decisions delta → migrations → ARD v2 → Phase 1 spec.**
