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
│  │  │  /auth /events   │    │   │  events           │  │
│  │  │  /accounts       │    │   │  rsvp_log         │  │
│  │  │  /spond-accounts │    │   └───────────────────┘  │
│  │  │  /admin /stream  │    │                          │
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

**Sniper:** Each event with a known future `invite_time` and an active choice gets a one-shot APScheduler `DateTrigger` job scheduled at `invite_time` minus `RSVP_LEAD_TIME_MS` (default 0, i.e. exactly at `invite_time`). This provides millisecond-precision RSVP timing without polling overhead. Snipers are rescheduled on:
- User setting/changing a decision (`PATCH /events/{id}`)
- Discovery finding an updated `invite_time`
- Application startup (in-memory jobs don't survive restarts)

Both paths converge on `_process_event()`, which handles status transitions, retries and error recording.

**Warmup and prepared connection:** 10 s before the opening, `run_warmup()` warms the database pool (`database.warm_pool`), resolves the member ID (cached in `events.resolved_recipient_id`) and `_open_prepared()` opens the HTTPS connection to Spond and proves the token with `GET /profile` (re-logging in if rejected). The result (`_Prepared`: token, event ID, member ID, connection, decision) is kept in memory in `_PREPARED`.

**Sniper timing:** the job starts `_SNIPER_HEADSTART_S` (0.25 s) early and then waits for the exact instant on the event loop's clock, so scheduler jitter is spent before the opening. With prepared data `run_sniper()` sends the PUT *without any database access*; `_process_event(presend=…)` then claims the event, writes the answer log and audit entry and notifies. Without prepared data (warmup failed or too late) the normal path runs: read, claim, send with retries. The sniper re-checks the event on that path, because a running job cannot be cancelled.

**No double answers:** the sniper registers the event in `_INFLIGHT` first. The executioner skips those events (no database work, no claim race), and a second sniper for the same event returns at once. Changing or removing a decision drops the prepared data (`schedule_sniper` / `cancel_sniper`); unused connections are closed by a 120 s sweep. The atomic claim (`UPDATE … WHERE status = 'pending'`) remains the guard across paths. Accepted trade-off: a decision changed within a few ms of the opening can still be answered with the old choice.

**Retries (`_submit_with_retries`):** a 401 re-logs in and retries once at once. Transient failures (5xx, 429/408/425, timeouts, connection errors) follow a ladder of 50 ms … 2 s for up to 20 s. Other 4xx get 3 quick retries (covers firing slightly early); a real refusal fails fast. A failed prepared send continues on this path (a 401 starts with a fresh login); later attempts open fresh sessions and reuse the resolved member ID. Each attempt has a 5 s timeout.

**Timings:** `rsvp.sent` / `rsvp.failed` audit details carry `fire_ms`, `prep_ms`, `request_ms`, `response_ms`, `attempts` and `prepared` (ms relative to registration opening) in addition to `latency_ms`.

### `app/services/reminders.py` — Registration reminders

A scheduler job (`reminders`, every minute at :30) finds undecided events (`user_choice=manual`, `status=pending`, registration opening within 8 h, event not over). `due_threshold()` picks the smallest of 8/4/1 h the remaining time has fallen under, so an event found late gets one reminder. `_claim()` inserts the `reminder_log` row first (primary key event + hours), which makes sending at-most-once across restarts and overlapping runs. `push.send_to_spond_user(..., kind="reminder_4h")` then delivers only to logins that have that kind on (`notification_settings`); a claimed reminder nobody wanted is not repeated later. The answer notifications use the same `kind` filter (`answer_sent`, `answer_failed`). Each sent reminder is an audit event `reminder.sent`.

### `app/services/audit.py` — Audit trail

Two ways in. **Explicit events:** handlers call `audit.record("event.choice_set", target_type=…, details=…)` *after* the change was committed; the row is staged on the request and written once the response is out, together with IP, user agent, method, path, status and request id. The bot uses `await audit.record_system(...)`: the executioner's `_notify_member()` writes `rsvp.sent` / `rsvp.failed` with member, choice, latency, retries and error, which is what the admin panel's answer view shows (the `rsvp_log` table remains the source for stats and charts; migration 008 copied its last 90 days into the trail). **Safety net:** `AuditMiddleware` (pure ASGI) gives every write request, every 403/429, every 401 on a write and every 5xx that no handler described a generic `http.<method>` row, so nothing a person does goes unrecorded. Successful reads are not logged. A staged success is downgraded to `failed`/`denied` if the request ended in an error. `scrub()` drops secret-looking keys (password, token, key, …) and bounds sizes. Writing never breaks a request (errors are logged and swallowed). A nightly job (`audit_purge`, 03:17) deletes entries older than `AUDIT_RETENTION_DAYS`. The client IP is `scope["client"]`, which uvicorn derives from `X-Forwarded-For` only for peers in `TRUSTED_PROXIES`.

### `app/services/push.py` — Web Push

After every finished RSVP attempt `_notify_member()` in the executioner publishes to the SSE stream and calls `push.dispatch_rsvp_notification()`, which sends in the background and never raises into the RSVP path. Devices are found through `push_subscriptions → frontend_users.linked_user_id = event.user_id`. Payloads are encrypted per device and signed with the VAPID key (`pywebpush`). A 404/410 from the push service deletes the subscription. Subscription endpoints must belong to a known push service (`ALLOWED_HOST_SUFFIXES`), because the server POSTs to them. Without `VAPID_PRIVATE_KEY` everything is off and the dashboard hides the option.

### `app/services/auth.py` — Token Lifecycle

`ensure_fresh_token(db, user, force=False)` is the single entry point for obtaining a valid token. Strategy:
- Token age < 23 hours → decrypt and return as-is (no network call)
- Token missing or stale → re-login with stored (Fernet-decrypted) password, persist new encrypted token
- `force=True` → re-login unconditionally (used after an unexpected 401)

### `app/core/security.py` — Credential Encryption

Fernet symmetric encryption wraps all sensitive values before they touch the database: the Spond password and the Spond access token. The `FERNET_KEY` env var is the single key. **Rotating this key requires re-entering all user credentials** — there is no migration path.

## API Routes

All routes are prefixed with `/api/v1/`.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/login` | — | Dashboard login (sets `sb_session` cookie) |
| `POST` | `/auth/logout` | user | Clear session cookie |
| `GET` | `/auth/me` | user | Current user info |
| `PATCH` | `/auth/me/password` | user | Change own password |
| `GET` | `/accounts` | admin | List dashboard (frontend) users |
| `POST` | `/accounts` | admin | Create dashboard user |
| `PATCH` | `/accounts/{id}` | admin | Update dashboard user |
| `DELETE` | `/accounts/{id}` | admin | Delete dashboard user |
| `GET` | `/spond-accounts` | admin | List Spond credential accounts |
| `POST` | `/spond-accounts` | admin | Create Spond account (credentials verified with Spond) |
| `POST` | `/spond-accounts/me` | user | Connect a Spond account to your own unlinked login; re-issues the session cookie |
| `GET` | `/spond-accounts/{id}` | admin or own | Get one Spond account |
| `PATCH` | `/spond-accounts/{id}` | admin or own | Update display name or active flag |
| `PUT` | `/spond-accounts/{id}/password` | admin or own | Replace the stored Spond password; verified with Spond first, refused if the login now belongs to another Spond profile (rate-limited) |
| `DELETE` | `/spond-accounts/{id}` | admin | Delete Spond account (cascades to its events) |
| `POST` | `/invites` | admin | Create a single-use invite; returns the token once |
| `GET` | `/invites` | admin | List invites with status (pending, used, expired) |
| `DELETE` | `/invites/{id}` | admin | Revoke an invite |
| `POST` | `/invites/check` | — | Is an invite token usable? (rate-limited) |
| `POST` | `/invites/accept` | — | Create login + connect Spond + sign in (rate-limited) |
| `GET` | `/admin/audit` | admin | Audit trail: filters `q`, `category`, `outcome`, `actor_id`, `since`, `until`; cursor pagination |
| `GET` | `/admin/audit/export.csv` | admin | The same filters as a CSV download (up to 50,000 rows); the export itself is logged |
| `GET` | `/push/config` | user | Whether Web Push is set up, and the VAPID public key to subscribe with |
| `GET` | `/push/preferences` | user | Which notifications this login wants (all on if never saved) |
| `PUT` | `/push/preferences` | user | Save them; all five fields required, shared by all devices; audited |
| `POST` | `/push/subscribe` | user | Register this browser (`PushSubscription.toJSON()`); only known push services are accepted |
| `POST` | `/push/unsubscribe` | user | Forget this browser (own devices only) |
| `POST` | `/push/test` | user | Send a test notification to your own devices (rate-limited) |
| `GET` | `/events` | user | List events for current user (`all=true` for admins) |
| `GET` | `/events/{id}` | user | Get one event |
| `PATCH` | `/events/{id}` | user | Set RSVP decision (arms/disarms sniper) |
| `GET` | `/health` | — | 200 when database and scheduler are OK, 503 otherwise |
| `GET` | `/admin/rsvp-log` | admin | RSVP audit log |
| `GET` | `/admin/stats` | admin | System health stats (p50/p95 timing latency) |
| `POST` | `/admin/sync` | admin | Trigger discovery sync immediately |
| `GET` | `/admin/charts` | admin | Latency scatter, daily outcomes, per-user breakdown |
| `GET` | `/admin/scheduler` | admin | List armed sniper jobs |
| `DELETE` | `/admin/scheduler/{job_id}` | admin | Cancel a sniper job |
| `POST` | `/admin/scheduler/{job_id}/fire` | admin | Fire a sniper immediately |
| `GET` | `/admin/stream` | admin | SSE stream for admin dashboard |
| `GET` | `/user/stream` | user | SSE stream for user dashboard |

Swagger UI is at `/docs` and ReDoc at `/redoc` — both require an active admin session.

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
id                    UUID PK
spond_event_id        VARCHAR                         ← Spond's own event ID
user_id               UUID FK → users.id (CASCADE DELETE)
heading               VARCHAR
start_timestamp       TIMESTAMPTZ
invite_time           TIMESTAMPTZ                     ← when RSVP window opens (sniper target)
rsvp_date             TIMESTAMPTZ                     ← RSVP deadline
user_choice           VARCHAR  (accept|decline|manual)
status                VARCHAR  (pending|processing|processed|failed)
resolved_recipient_id VARCHAR                         ← cached by warmup job 10s before fire
error_message         VARCHAR
created_at            TIMESTAMPTZ
updated_at            TIMESTAMPTZ

UNIQUE (spond_event_id, user_id)   ← same group event = one row per user
INDEX  (invite_time, status)       ← executioner query is instant

rsvp_log  (append-only audit log)
──────────────────────────────────────────────────────────────
id               UUID PK
event_id         UUID FK → events.id
user_id          UUID FK → users.id
spond_event_id   VARCHAR
choice           VARCHAR  (accept|decline)
fired_at         TIMESTAMPTZ   ← when the job began
submitted_at     TIMESTAMPTZ   ← when the Spond API call returned
outcome          VARCHAR  (success|retry_success|failed)
retry_count      INT
error_detail     VARCHAR

invites  (single-use signup links)
──────────────────────────────────────────────────────────────
id               UUID PK
token_hash       VARCHAR UNIQUE  ← SHA-256 of the token; the token itself is never stored
note             VARCHAR         ← admin's label, pre-fills the display name
created_by_id    UUID FK → frontend_users.id (SET NULL)
created_at       TIMESTAMPTZ
expires_at       TIMESTAMPTZ
used_at          TIMESTAMPTZ     ← set atomically when accepted
used_by_id       UUID FK → frontend_users.id (SET NULL)

audit_log  (append-only trail of who did what; no foreign keys, so it outlives what it describes)
──────────────────────────────────────────────────────────────
id               UUID PK
occurred_at      TIMESTAMPTZ  INDEX
actor_type       VARCHAR  (user|anonymous|system)
actor_id         UUID        ← no FK: survives deleting the login
actor_username   VARCHAR     ← copied, same reason
actor_is_admin   BOOL
action           VARCHAR  e.g. auth.login.failed, event.choice_set, rsvp.sent, http.post  INDEX
category         VARCHAR  (first part of action)
outcome          VARCHAR  (success|denied|failed)
target_type, target_id, target_label  VARCHAR
details          JSON        ← e.g. {"from": "manual", "to": "accept"}; secrets are scrubbed
ip, user_agent, method, path, status_code, request_id

notification_settings  (which notifications a login wants; no row = everything on)
──────────────────────────────────────────────────────────────
frontend_user_id UUID PK FK → frontend_users.id (CASCADE DELETE)
answer_sent, answer_failed, reminder_8h, reminder_4h, reminder_1h   BOOL (default true)

reminder_log  (which reminders were already sent: at most once per event and step)
──────────────────────────────────────────────────────────────
event_id         UUID PK FK → events.id (CASCADE DELETE)
hours            INT  PK       ← 8, 4 or 1; inserting the row claims the reminder
sent_at          TIMESTAMPTZ

push_subscriptions  (one row per browser/device that gets notifications)
──────────────────────────────────────────────────────────────
id               UUID PK
frontend_user_id UUID FK → frontend_users.id (CASCADE DELETE)  ← the login, not the Spond account
endpoint         TEXT UNIQUE    ← the browser's push channel (FCM, Mozilla, Apple, WNS)
p256dh, auth     VARCHAR        ← keys the payload is encrypted with
user_agent       VARCHAR
created_at       TIMESTAMPTZ
```

## Auth Model

SpondBot has two completely separate authentication systems:

**Frontend auth (dashboard login)**
- `frontend_users` table: `username` + `hashed_password` (bcrypt)
- Login sets an `HttpOnly, Secure, SameSite=Strict` cookie (`sb_session`) containing a signed JWT (joserfc)
- JWT payload: `sub`, `username`, `is_admin`, `linked_user_id`
- The claims are fixed when the cookie is issued, so endpoints that change them (accepting an invite, connecting your own Spond account) issue a fresh cookie; `POST /spond-accounts/me` checks the database, not the cookie, for an existing link
- Rate-limited to 5 login attempts per minute per IP; invite accept and self-connect are limited the same way

**Spond auth (API access)**
- `users` table: email/phone + Fernet-encrypted password
- `ensure_fresh_token()` decrypts the password, calls Spond login, stores the new encrypted token
- Token lifetime: 24h (Spond). Proactively refreshed at 23h. Force-refreshed on 401.
- Spond access tokens are opaque Base64 strings — passed as-is in `Authorization: Bearer`

The two systems are linked by `frontend_users.linked_user_id → users.id`. An admin frontend account typically has no `linked_user_id`. Links are created by an admin, by a member accepting an invite (`/join#<token>`), or by a signed-in login connecting its own account.

## Request Lifecycle: RSVP Submission

```
1.  User logs into dashboard → sb_session cookie set
2.  User views event list → GET /api/v1/events
3.  User sets choice → PATCH /api/v1/events/{id} (choice=accept)
4.    DB: event.user_choice = "accept"
5.    Sniper job scheduled at event.invite_time (DateTrigger, id=sniper_{event_id})
6.    Warmup job scheduled 10s before sniper (id=warmup_{event_id})
7.  At invite_time - 10s:
8.    APScheduler fires warmup → run_warmup(event_id)
9.    GET /sponds/getBulk + GET /groups → resolve recipient_id
10.   DB: event.resolved_recipient_id = recipient_id (cached)
    Warmup then opens the HTTPS connection and checks the token (GET /profile);
    token, member ID and connection are kept in memory (_PREPARED)
11. At invite_time:
12.   APScheduler fires sniper → run_sniper(event_id)
13.   Event re-read; prepared data used only if choice and invite_time still match
14.   Claim: UPDATE status pending → processing (only one caller wins)
15.   Prepared: PUT straight on the open connection.
      Otherwise: ensure_fresh_token() (POST /auth2/login if stale), then the
      cached member ID, or GET /sponds/getBulk → GET /groups to resolve it
16.   PUT /sponds/{eventId}/responses/{memberId} {"accepted": true}
17.   DB: event.status = "processed"
18.   rsvp_log row written (outcome=success, fired_at, submitted_at)
19. If 401 at step 16: force token refresh → retry once, immediately
20. If 5xx/429/timeout/connection error: retry on the ladder (50 ms … 2 s, up to 20 s);
    other 4xx: 3 quick retries
21.   rsvp_log row written (outcome=retry_success or failed, retry_count)
22. If still fails: event.status = "failed", error_message recorded
23.   rsvp_log row written (outcome=failed, error_detail)
```

On startup, `reschedule_pending_snipers()` in `scheduler.py` re-arms sniper + warmup jobs for all events with `status=pending`, `invite_time > now`, and an active choice — restoring precision scheduling after a restart.

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
