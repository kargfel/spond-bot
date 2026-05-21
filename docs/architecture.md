# Architecture

## System Overview

SpondBot is a self-hosted backend that automates Spond RSVP responses for multiple users. Each user stores their Spond credentials once; the bot handles login, token refresh, event discovery, and RSVP submission autonomously — firing at the precise moment the invite window opens.

```
┌─────────────────────────────────────────────────────────┐
│                     Docker Compose                       │
│                                                         │
│  ┌──────────────────────────┐   ┌───────────────────┐  │
│  │       FastAPI App        │   │   PostgreSQL 16    │  │
│  │                          │◄──│                   │  │
│  │  ┌──────────────────┐    │   │  users            │  │
│  │  │  API Routers     │    │   │  frontend_users   │  │
│  │  │  /auth  /events  │    │   │  events           │  │
│  │  │  /users /health  │    │   └───────────────────┘  │
│  │  └──────────────────┘    │                          │
│  │                          │                          │
│  │  ┌──────────────────┐    │                          │
│  │  │  APScheduler     │    │                          │
│  │  │  ┌────────────┐  │    │                          │
│  │  │  │ Discovery  │  │    │                          │
│  │  │  │ (every Nm) │  │    │                          │
│  │  │  └────────────┘  │    │                          │
│  │  │  ┌────────────┐  │    │                          │
│  │  │  │Executioner │  │    │                          │
│  │  │  │(every 60s) │  │    │                          │
│  │  │  └────────────┘  │    │                          │
│  │  │  ┌────────────┐  │    │                          │
│  │  │  │  Snipers   │  │    │                          │
│  │  │  │(DateTrigger│  │    │                          │
│  │  │  │ per event) │  │    │                          │
│  │  │  └────────────┘  │    │                          │
│  │  └──────────────────┘    │                          │
│  └──────────────────────────┘                          │
│             │                                           │
└─────────────┼───────────────────────────────────────────┘
              │  HTTPS
              ▼
     api.spond.com/core/v1/
```

## Components

### `app/core/spond_client.py` — Spond API Client

Pure-function, stateless module. Every function takes `(aiohttp.ClientSession, token, ...)` and returns data or raises `SpondAuthError` / `SpondAPIError`. No state is stored here.

Key functions:
- `login()` — authenticates and returns `(token, acquired_at)`
- `get_profile_id()` — fetches the user's global Spond profile ID
- `resolve_recipient_id()` — resolves the per-group member ID required for RSVPs
- `get_upcoming_events()` — fetches upcoming event stubs
- `get_bulk_events()` — fetches full event details (including `inviteTime`)
- `rsvp()` — submits an RSVP via `PUT /sponds/{id}/responses/{memberId}`

### `app/workers/discovery.py` — Discovery Worker

Runs on a configurable interval (default: 60 minutes). For each active user:
1. Calls `ensure_fresh_token()` to get a valid token
2. Fetches upcoming event IDs from Spond
3. Fetches full details in chunks of 50 via `getBulk`
4. Upserts events to DB — new events get `choice=manual, status=pending`
5. Reschedules sniper jobs for events with an active decision and a future `invite_time`

The upsert never overwrites an existing `user_choice` — only metadata (heading, timestamps) is refreshed.

### `app/workers/executioner.py` — Executioner + Sniper

**Executioner:** Runs every 60 seconds. Finds events where `invite_time <= now AND status=pending AND choice IN (accept, decline)` and fires RSVPs concurrently via `asyncio.gather`. Acts as a fallback safety net.

**Sniper:** Each event with a known future `invite_time` and an active choice gets a one-shot APScheduler `DateTrigger` job scheduled at exactly `invite_time`. This provides millisecond-precision RSVP timing without polling overhead. Snipers are rescheduled on:
- User setting/changing a decision (`PATCH /events/{id}/decision`)
- Discovery finding an updated `invite_time`
- Application startup (in-memory jobs don't survive restarts)

Both paths converge on `_process_event()`, which handles status transitions, 401 retry, and error recording.

### `app/services/auth.py` — Token Lifecycle

`ensure_fresh_token(db, user, force=False)` is the single entry point for obtaining a valid token. Strategy:
- Token age < 23 hours → decrypt and return as-is (no network call)
- Token missing or stale → re-login with stored (Fernet-decrypted) password, persist new encrypted token
- `force=True` → re-login unconditionally (used after an unexpected 401)

### `app/core/security.py` — Credential Encryption

Fernet symmetric encryption wraps all sensitive values before they touch the database: the Spond password and the Spond access token. The `FERNET_KEY` env var is the single key. **Rotating this key requires re-entering all user credentials** — there is no migration path.

## Data Model

```
frontend_users                    users
──────────────────────            ─────────────────────────────
id            UUID PK             id               UUID PK
username      VARCHAR UNIQUE       display_name     VARCHAR
hashed_password VARCHAR           login            VARCHAR UNIQUE  ← email or phone
is_admin      BOOL                encrypted_password VARCHAR
linked_user_id UUID FK → users.id encrypted_access_token VARCHAR
                                  token_acquired_at TIMESTAMPTZ
                                  profile_id        VARCHAR      ← Spond global profile ID
                                  is_active         BOOL

events
──────────────────────────────────────────────────────────────
id                UUID PK
spond_event_id    VARCHAR                         ← Spond's own event ID
user_id           UUID FK → users.id (CASCADE DELETE)
heading           VARCHAR
start_timestamp   TIMESTAMPTZ
invite_time       TIMESTAMPTZ                     ← when RSVP window opens (sniper target)
rsvp_date         TIMESTAMPTZ                     ← RSVP deadline
user_choice       VARCHAR  (accept|decline|manual)
status            VARCHAR  (pending|processing|processed|failed)
error_message     VARCHAR
created_at        TIMESTAMPTZ
updated_at        TIMESTAMPTZ

UNIQUE (spond_event_id, user_id)   ← same group event = one row per user
INDEX  (invite_time, status)       ← executioner query is instant
```

## Auth Model

SpondBot has two completely separate authentication systems:

**Frontend auth (dashboard login)**
- `frontend_users` table: `username` + `hashed_password` (bcrypt)
- Login sets an `HttpOnly, Secure, SameSite=Strict` cookie (`sb_session`) containing a signed JWT (joserfc)
- JWT payload: `sub`, `username`, `is_admin`, `linked_user_id`
- Rate-limited to 5 login attempts per minute per IP

**Spond auth (API access)**
- `users` table: email/phone + Fernet-encrypted password
- `ensure_fresh_token()` decrypts the password, calls Spond login, stores the new encrypted token
- Token lifetime: 24h (Spond). Proactively refreshed at 23h. Force-refreshed on 401.
- Spond access tokens are opaque Base64 strings — passed as-is in `Authorization: Bearer`

The two systems are linked by `frontend_users.linked_user_id → users.id`. An admin frontend account typically has no `linked_user_id`.

## Request Lifecycle: RSVP Submission

```
1. User logs into dashboard → sb_session cookie set
2. User views event list → GET /api/v1/events
3. User sets choice → PATCH /api/v1/events/{id}/decision (choice=accept)
4.   DB: event.user_choice = "accept"
5.   Sniper job scheduled at event.invite_time (DateTrigger)
6. At invite_time:
7.   APScheduler fires sniper → run_sniper(event_id)
8.   ensure_fresh_token() → decrypt password → POST /auth2/login (if stale)
9.   GET /sponds/getBulk → get raw event (for group ID)
10.  GET /groups → resolve member ID for this user in this group
11.  PUT /sponds/{eventId}/responses/{memberId} {"accepted": true}
12.  DB: event.status = "processed"
13. If 401 at step 11: force token refresh → retry once
14. If still fails: event.status = "failed", error_message recorded
```

## Configuration Reference

All settings are loaded from `.env` via pydantic-settings (`app/config.py`).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | yes | — | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `FERNET_KEY` | yes | — | Base64 Fernet key for credential encryption |
| `API_KEY` | yes | — | Internal API key for direct backend access |
| `SITE_DOMAIN` | no | `localhost` | Public domain; controls `Secure` cookie flag |
| `DISCOVERY_INTERVAL_MINUTES` | no | `60` | How often the discovery worker runs |
| `EXECUTIONER_INTERVAL_SECONDS` | no | `60` | How often the executioner polls (fallback) |
| `TZ` | no | `Europe/Berlin` | Timezone for APScheduler |
| `ADMIN_USERNAME` | no | `admin` | Initial admin account username |
| `ADMIN_PASSWORD` | no | `changeme` | Initial admin account password — **change this** |
| `RSVP_LEAD_TIME_MS` | no | `0` | Fire RSVP this many ms before invite_time (compensates for network latency) |
