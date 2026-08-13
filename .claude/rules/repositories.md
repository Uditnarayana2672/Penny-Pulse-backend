---
paths:
  - "app/repositories/**/*.py"
  - "app/db.py"
---

# Repositories — all SQL lives here

May import `sqlalchemy` and `app.models`. Must not import `fastapi` or `app.services`.
Nothing outside this directory writes a query.

## Signatures

Every function takes `user_id` as its **first argument**, after the session:

```python
def get_txn(db: Session, user_id: UUID, txn_id: UUID) -> Txn | None: ...
def list_txns(db: Session, user_id: UUID, *, cursor: Cursor | None, limit: int) -> list[Txn]: ...
```

If a helper does not need `user_id`, it should not be touching the database. A `select()`
without a `user_id` filter is a cross-user leak — see rule 1 in `CLAUDE.md`.

## Read from views, write to tables

| Read | Write |
|---|---|
| `txn_live` | `txn` |
| `account_live` | `account` |
| `category_live` | `category` |
| `profile_live` | `profile` |
| `budget_period_live` | `budget_period` |
| `budget_limit_live` | `budget_limit` |

Never add `deleted_at IS NULL` to an RLS policy: Postgres applies the SELECT policy to the
new row of an UPDATE, so soft-delete would fail with `42501` permanently.

## Never commit

No `commit()`, no `rollback()`, no `begin()`. The request-scoped session dependency in
`app/db.py` commits once on success. This is load-bearing:

- A transfer is two rows, both `direction='transfer'`, linked by `transfer_peer_txn_id`,
  written in **one statement** against a `DEFERRABLE INITIALLY DEFERRED` FK.
- Splits (`split_parent_txn_id`) and refunds (`refunds_txn_id`) are the same shape.
- A `month_start_day` change spans several statements under a deferred `period_no_overlap`.

A stray commit breaks all four, and only under real data.

## Inserts

IDs are **app-generated** — no table has a DB default for `id`. The UUID arrives from the
client and is the idempotency key for optimistic-save retries:

```python
stmt = insert(Txn).values(**row).on_conflict_do_nothing(index_elements=["id"])
```

Re-inserting the same `id` is a success, not a conflict.

## Reads

- Cursor pagination on `(occurred_at, id)`. Never `OFFSET`.
- Every aggregate filters `is_excluded = FALSE`. `bucket = 'EXCLUDED'` is a label, not a
  rule — transfers and adjustments are forced excluded by CHECK.
- No lazy loading across a request boundary. Load what the caller needs with an explicit
  join or `selectinload`, and return plain data.
- 209 indexes already exist. If a new query needs one, stop and say so — that is a
  migration, not a code change.

## Returning

Repositories return ORM objects or plain dicts to services. They do not build Pydantic
response models and they do not raise HTTP exceptions. A missing row returns `None`; the
service decides what that means.
