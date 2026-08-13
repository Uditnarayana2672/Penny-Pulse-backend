---
paths:
  - "tests/**/*.py"
---

# Tests

pytest. Fast by default, honest where it matters.

## Two kinds, and know which you are writing

**Service and lib tests** run with no database and no HTTP — that is the entire reason
`services/` may not import `sqlalchemy` or `fastapi`. Money arithmetic, local-date
conversion, refund and transfer rules, habit derivation: all pure-function tests. These
should be the majority.

**Repository and router tests** run against a real Postgres with the migrations applied.
Never `create_all()` — apply `../penny-pulse-migrations/000*.sql` so the CHECKs, triggers,
generated columns and DEFERRABLE constraints are the ones production has. A test suite
that passes against a schema built by SQLAlchemy proves nothing about this database.

## Mandatory per endpoint

1. Happy path.
2. **A second user cannot see or modify the first user's row.** Not optional — this is the
   one bug that matters and the one nobody writes a test for. It goes in every endpoint's
   test file.
3. Idempotency: POSTing the same client-generated `id` twice yields one row and `200`.

## Cases that have already bitten this schema

- Transfer pair written in one statement under DEFERRABLE FKs.
- Soft-delete of a `txn` — this fails with `42501` if anyone puts `deleted_at IS NULL`
  into an RLS policy.
- Refund pointing at the reversed expense category, and its absence from income totals.
- `habit_log` increment, decrement, and `zero_spend_confirmed` being reset by a later real
  transaction.
- Period extension when `month_start_day` changes, under deferred `period_no_overlap`.
- Split parent auto-excluded from totals.

## Style

- Real objects over mocks. Mock only the clock and outbound HTTP.
- One assertion subject per test. A test name states the rule it defends.
- No shared mutable fixtures across test files.
- Never assert on a `message` string — assert on the error `code`.
- Amounts in tests are paise integers. A test containing a float amount is itself a bug.
