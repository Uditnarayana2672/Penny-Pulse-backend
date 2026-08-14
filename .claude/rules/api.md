---
paths:
  - "app/routers/**/*.py"
  - "app/schemas/**/*.py"
  - "app/main.py"
  - "app/auth.py"
---

# Routers, schemas and auth — the edge

Routers may import `fastapi`, `app.schemas` and `app.services`. **Never `sqlalchemy`.**
A router parses, calls one service, maps errors, returns. No business logic, no SQL, no
money arithmetic.

## Shape

- Base path `/api/v1`. REST over JSON — no GraphQL, no RPC-style verbs in paths.
- Plural nouns, kebab-case: `/api/v1/transactions`, `/api/v1/me/habit-log`.
- Bodies are `snake_case` in and out. No case-transformation layer.
- No `include=` graph expansion. An endpoint returns what it was asked for.

## Errors

Always this envelope, no exceptions:

```json
{"error": {"code": "snake_case_code", "message": "human readable", "field": "amount_minor"}}
```

`code` is stable and machine-readable; `message` is for logs, not for UI copy — the client
owns its own wording.

| Status | Use |
|---|---|
| `422` | Validation failure |
| `403` | Authenticated but not permitted |
| `404` | Row does not exist **or belongs to another user** — never `403`, it leaks existence |
| `401` | Missing, invalid or expired token |
| `503` | Token signing keys unreachable — the token may be fine, so never `401` |

Exceptions are logged with a trace and an ID. The ID goes to the client; the trace does not.

## Writes

- `POST` bodies carry a **client-generated** `id`. Re-POSTing the same `id` returns `200`
  with the existing row, not `409`. This is what makes optimistic save safe to retry over
  a flaky mobile connection.
- List endpoints use cursor pagination on `(occurred_at, id)`, never `OFFSET`.

## Schemas

Pydantic v2, **separate from the ORM models** (delta D7). One class per direction —
`TxnCreate`, `TxnOut`. Never reuse a SQLAlchemy model as a schema, and never expose
internal columns: `entry_duration_ms`, `edited_before_save`, `merchant_normalized`,
`version`, `household_id`, `deleted_at` stay server-side unless a screen needs them.

Validators here check shape, type and range only — `amount_minor: int = Field(gt=0)`,
`direction: Literal["in", "out", "transfer"]`. No database access.

## Auth

The client authenticates with `supabase-js` and sends the Supabase access token as
`Authorization: Bearer <jwt>`.

- Verify the signature **locally** against the project's published public key, fetched
  once from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` and cached in-process. No
  network call to Supabase on the request path.
- Tokens are **ES256**. There is no shared JWT secret — Supabase marks the legacy HS256
  secret as not for production, and this project does not carry one. The accepted
  algorithm list is exactly `["ES256"]`; widening it is how algorithm confusion gets in,
  because the verifying key is public by definition.
- Rotating the signing key in the dashboard needs no redeploy and signs nobody out: an
  unrecognised `kid` refetches the key set once before failing.
- `iss` and `aud` are both verified. A trailing slash on `SUPABASE_URL` breaks `iss` for
  every token, so `app/config.py` strips it.
- `sub` claim becomes `user_id`. It is the only source of identity — never a body field,
  query param or path segment.
- Valid token but no `profile` row → `403` with `code: "onboarding_required"`.
- JWKS unreachable → `503` with `code: "auth_unavailable"`. Never `401`: the token is
  probably valid, and "logged out" sends four users to a login screen that cannot work
  either.
- The client uses Supabase for **auth only** and never reads or writes tables directly
  (delta D1). If a feature seems to need direct table access, it needs an endpoint here.
- The client bundle ships the `sb_publishable_...` key, not the legacy `anon` key.
- Tokens, signing keys and the database password never appear in a response, a log, or an
  error message.

## Session

`app/db.py` provides one request-scoped session and commits once on success. Routers do
not commit. Do not open a second session inside a request.
