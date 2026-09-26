# SpondBot — AI Context

SpondBot is a self-hosted multi-user automation backend that submits Spond RSVP responses at the exact millisecond the invite window opens. Users pre-set their choice (accept/decline/manual) via a web dashboard; the bot handles timing, token management, and API calls autonomously.

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (async), Python 3.11 (Docker base image) |
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
  main.py                  FastAPI app, lifespan, page routes (/, /dashboard, /admin, /join)
  config.py                All env vars via pydantic-settings
  database.py              SQLAlchemy async engine + session factory
  core/
    spond_client.py        Stateless Spond API functions (login, events, RSVP)
    security.py            Fernet encrypt/decrypt + bcrypt helpers
    jwt.py                 joserfc JWT creation/validation
    session.py             Session cookie issue/clear (re-issue when claims change)
    rate_limit.py          Shared slowapi limiter (per client IP)
    event_bus.py           In-process pub/sub feeding the SSE streams
  models/
    user.py                Spond account row (encrypted creds + token)
    frontend_user.py       Dashboard login account (bcrypt hash, optional linked_user_id)
    event.py               One event-per-user row (choice + status)
    rsvp_log.py            Audit log of every RSVP attempt
    invite.py              Single-use invite link (token stored as SHA-256 hash)
  services/
    auth.py                ensure_fresh_token() — token lifecycle
    spond_accounts.py      Verify Spond credentials + build encrypted User row
  workers/
    discovery.py           Worker A: sync events from Spond for all users
    executioner.py         Worker B: fire RSVPs + sniper DateTrigger helpers
    scheduler.py           APScheduler setup + startup sniper recovery
  api/
    auth.py                /auth/* (login, logout, me, change own password)
    accounts.py            /accounts/* (dashboard logins, admin only)
    users.py               /spond-accounts/* (Spond account CRUD; POST /me = connect own account; PUT /{id}/password)
    invites.py             /invites/* (admin create/list/revoke; public check/accept)
    events.py              /events/* (list, set decision) and /health
    admin.py               /admin/* (stats, charts, RSVP log, scheduler jobs, sync)
    stream.py              /admin/stream and /user/stream (SSE)
    deps.py                FastAPI dependency injectors (CurrentUser, DbDep, AdminDep)
  schemas/                 Pydantic request/response models
scripts/
  healthcheck.py           Docker HEALTHCHECK probe (exit 0 only on HTTP 200)
  backup.sh                pg_dump loop/once/check for the compose `backup` service
frontend/                  Static pages, no build step (served by app/main.py)
  index.html               Sign-in page
  join.html/.js            Invite signup: create login + connect Spond account
  dashboard.html/.js       Member dashboard: decision inbox + day-grouped agenda
  admin.html/.js           Admin console: queue, timeline, users, log, charts
  core.js                  Pure view logic (event state, grouping, formatting); unit tested
  app.js                   Shared browser layer: API calls, auth guards, dialogs, toasts
  member.css / admin.css   Light member theme / dark admin theme
```

## Tests and CI

- `pytest` — backend tests (`tests/`); `tests/conftest.py` sets safe env defaults, so no `.env` is needed.
- GitHub Actions (`.github/workflows/ci.yml`) runs backend tests, frontend tests and a Docker build on every PR.

## Dependencies

`requirements.in` lists direct dependencies; `requirements.txt` is the pinned lock that Docker and CI install.
After editing `requirements.in`, regenerate: `uv pip compile requirements.in --python-version 3.11 -o requirements.txt`.
CI fails if the lock is out of date. Dependabot (`.github/dependabot.yml`) proposes weekly updates.

## Frontend Tests

Frontend changes are test-driven. `npm install` once, then:

- `npm run test:unit` — `node:test` unit tests for `frontend/core.js` (`tests/frontend/unit/`)
- `npm run test:e2e` — Playwright tests against the real pages with a stateful mock API
  (`tests/frontend/e2e/fixtures.js`); desktop and phone viewports; no backend needed.
  Set `PW_CHROMIUM_PATH` to use a preinstalled Chromium.

Member-facing labels: `accept`/`decline`/`manual` are shown as Going / Not going / Leave to me.

## Event Lifecycle

```
Discovery fetches event → upsert to DB (choice=manual, status=pending)
User sets choice via PATCH /events/{id}
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
