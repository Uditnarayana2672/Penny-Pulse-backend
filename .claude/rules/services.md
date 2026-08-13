---
paths:
  - "app/services/**/*.py"
  - "app/lib/**/*.py"
---

# Services — business rules, framework-free

**Must not import `fastapi` or `sqlalchemy`.** Receives plain data, returns plain data.
Anything from the request the service needs is passed as an argument by the router.

That ban is the whole point of the layer: every rule below is testable with no database
and no HTTP. If a rule is hard to test, the layering is wrong, not the test.

`app/lib/` is stricter still — pure functions, no imports from `app/` at all. Money
arithmetic, local-date conversion and period maths live there.

## Money

`amount_minor` is `int` paise and **always positive**. Direction carries the sign. Never
`float`, never `Decimal`. Rounding is explicit and toward zero; if a rule needs banker's
rounding, say so rather than assuming.

## Local dates are computed here, not by the database

- `occurred_on_local` = `occurred_at` rendered in `profile.timezone` (default
  `Asia/Kolkata`). Same for `created_on_local`. Postgres does not do this.
- `budget_period_id` is derived from `occurred_on_local` at write time and is
  **authoritative** — never re-derive period membership at read time. Recompute it
  whenever `occurred_at` changes.
- Day-of-month ranges are 1..31 plus an explicit `on_last_day` flag. Indian EMIs and card
  statements land on the 29th–31st routinely.

## Transaction semantics

| Rule | Detail |
|---|---|
| **Transfers** | Two rows, both `direction='transfer'`, both positive, distinguished by `transfer_role` (`from`/`to`), linked via `transfer_peer_txn_id`. Neither has a `category_id`. |
| **`category_id` nullable** | Only for transfers; a CHECK enforces the rest. Never assume a category exists when reading a transfer leg. |
| **Exclusion** | `is_excluded` is the single authoritative flag. `bucket='EXCLUDED'` is a categorisation. Transfers and adjustments are forced excluded. |
| **Refunds** | `direction='in'` + `is_refund=TRUE` + `refunds_txn_id`, categorised to the **expense** category being reversed. Booking a refund as income poisons the three-month median every budget recommendation reads. |
| **Splits** | Children carry `split_parent_txn_id`; the parent is auto-excluded so it is not double-counted. |

## Habit

- `habit_log.logged` is a **GENERATED** column — never write it. It derives from
  `txn_count > 0 OR zero_spend_confirmed`. Increment `txn_count`; set
  `zero_spend_confirmed`.
- Zero-spend is first-class (delta D6): offered only when no transaction exists for that
  day. A transaction logged afterwards resets `zero_spend_confirmed` to `false` — the real
  transaction always wins.
- `zero_spend_confirmed` stays a separate column from `logged`. Collapsing them makes
  Phase 2 capture rate unmeasurable.

## Validation belongs here when, and only when, it needs context

Shape, type and range are Pydantic's job at the edge. A rule belongs in a service if it
needs another row, the clock, or a decision — "a refund must point at an existing `out`
transaction owned by the same user" is a service rule. A Pydantic validator that queries
the database is in the wrong layer.

DB CHECK constraints are the backstop, never the primary path: a `23514` is not an error
message a user can act on.

## Errors

Services raise domain exceptions from `app/services/errors.py`, never `HTTPException`.
The router maps them to status codes and the error envelope.
