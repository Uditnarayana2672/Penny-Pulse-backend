---
paths:
  - "app/models/**/*.py"
---

# Models — SQLAlchemy 2.0, mirroring the migrations

SQLAlchemy 2.0 declarative with `Mapped[...]` / `mapped_column`. These classes **mirror**
`../penny-pulse-migrations/000*.sql` 1:1. They are not the API contract — Pydantic schemas
in `app/schemas/` are (delta D7).

## The migrations are the source of truth

- Never add, drop or alter a column here to make code work. If the model and the SQL
  disagree, the SQL is right and the model is a bug.
- A genuinely needed schema change is a migration in `../penny-pulse-migrations/`, decided
  deliberately. **Stop and ask.**
- No Alembic. No `Base.metadata.create_all()` anywhere, including in tests.

## Conventions

- `id` is `UUID` primary key with **no default** — the application supplies it.
- Money columns are `BigInteger`, named `*_minor`, always positive.
- Timestamps are `TIMESTAMPTZ` (`DateTime(timezone=True)`). Never naive datetimes.
- `deleted_at` exists on soft-deletable tables, but models do not filter it — reads go
  through the `*_live` views in the repository layer.
- Do not model `habit_log.logged`; it is a GENERATED column. Map it read-only
  (`Mapped[bool] = mapped_column(..., server_default=..., init=False)`) or omit it.
- Relationships are declared without `lazy="select"` surprises. Prefer no relationship at
  all over one that triggers a query outside the repository.

## Only Phase 1 tables get a model

61 tables exist; five phases' worth are intentionally empty. Do not add a model for a
Phase 2–6 table because the table happens to be there. Phase 1 set is listed in
`CLAUDE.md`.

## Column semantics worth knowing before you touch `txn`

`category_id` is nullable only for transfers · `transfer_role` says which leg left the
account · `is_excluded` is the sole exclusion flag · `is_refund` + `refunds_txn_id` point
at the reversed **expense** category · `budget_period_id` is set by the app at write time ·
`source` is provenance, `entry_method` is the UI path, and they answer different questions.

Full rationale for each: `../penny-pulse-migrations/README.md`.
