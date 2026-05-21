# SpondBot — AI Context

SpondBot is a self-hosted multi-user automation backend that submits Spond RSVP responses at the exact millisecond the invite window opens. Users pre-set their choice (accept/decline/manual) via a web dashboard; the bot handles timing, token management, and API calls autonomously.

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (async), Python 3.12+ |
| Database | PostgreSQL 16 via SQLAlchemy async + asyncpg |
| Scheduler | APScheduler 3.x (AsyncIOScheduler) |
| Credentials at rest | Fernet symmetric encryption |
| Frontend auth | bcrypt + joserfc JWT in HttpOnly cookie |
| Frontend | Vanilla JS / HTML / CSS (no build step) |
| Deployment | Docker Compose (app + db services) |

## Key Architectural Decisions

**Two auth layers** — `frontend_users` (dashboard login, bcrypt+JWT) and `users` (Spond accounts, Fernet-encrypted credentials). They are linked via `frontend_users.linked_user_id → users.id`. An admin account has no `linked_user_id`.

**Stateless Spond client** — `app/core/spond_client.py` is a pure-function module. No state, no class. Every function takes `(session, token, ...)`. The DB (via `app/services/auth.py`) is the single source of truth for tokens.

**Dual RSVP dispatch** — Discovery runs every N minutes; the Executioner polls every minute as a fallback. The "Sniper" pattern schedules a one-shot APScheduler `DateTrigger` at `invite_time` for millisecond precision. Both paths call the same `_process_event()` function.

**Token lifecycle** — Tokens are proactively refreshed after 23 hours (Spond tokens last 24h). On unexpected 401, the executioner forces a re-login and retries once automatically.

## Spond API Quirks (important for future changes)

- Login endpoint: `POST /core/v1/auth2/login` (migrated from `/core/v1/login` on 2026-05-21)
- New login response: `{ "accessToken": { "token": "<raw-base64-string>" } }` — the token must be passed **as-is** in `Authorization: Bearer`. Do NOT base64-decode it.
- Old login response (`loginToken` field) is still handled for backwards compatibility.
- RSVPs require the per-group **member ID**, not the global profile ID. `resolve_recipient_id()` in `spond_client.py` handles this by fetching `GET /groups` and matching by `profile.id` first, then email/phone.
- User-agent header must mimic a mobile Spond client or requests may be rejected.

## Module Map

```
app/
  main.py                  FastAPI app, lifespan, static file serving
  config.py                All env vars via pydantic-settings
  database.py              SQLAlchemy async engine + session factory
  core/
    spond_client.py        Stateless Spond API functions (login, events, RSVP)
    security.py            Fernet encrypt/decrypt helpers
    jwt.py                 joserfc JWT creation/validation
  models/
    user.py                Spond account row (encrypted creds + token)
    frontend_user.py       Dashboard login account (bcrypt hash)
    event.py               One event-per-user row (choice + status)
  services/
    auth.py                ensure_fresh_token() — token lifecycle
  workers/
    discovery.py           Worker A: sync events from Spond for all users
    executioner.py         Worker B: fire RSVPs + sniper DateTrigger helpers
    scheduler.py           APScheduler setup + startup sniper recovery
  api/
    auth.py                /auth/* endpoints (login, logout, user management)
    events.py              /events/* endpoints (list, decision, sync trigger)
    users.py               /users/* endpoints (Spond user CRUD)
    deps.py                FastAPI dependency injectors (CurrentUser, DbDep, AdminDep)
  schemas/                 Pydantic request/response models
```

## Event Lifecycle

```
Discovery fetches event → upsert to DB (choice=manual, status=pending)
User sets choice via PATCH /events/{id}/decision
  → sniper scheduled at invite_time (DateTrigger)
  → fallback: executioner polls every minute
At invite_time:
  sniper fires → ensure_fresh_token → resolve_recipient_id → PUT .../responses/{id}
  → status = processed | failed
```

## Event Status / Choice Values

| Field | Values |
|-------|--------|
| `user_choice` | `accept` / `decline` / `manual` |
| `status` | `pending` / `processing` / `processed` / `failed` |

## Configuration (env vars)

See `docs/setup.md` for the full reference. Critical vars: `DATABASE_URL`, `FERNET_KEY`, `API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `RSVP_LEAD_TIME_MS`.

## Further Reading

- `docs/architecture.md` — full component diagram, data model, request lifecycle
- `docs/setup.md` — Docker deployment, env vars, first-run guide
- `docs/codebase-review.md` — code quality notes, known gotchas, design rationale
- `docs/feature-ideas.md` — prioritized feature backlog with design sketches
