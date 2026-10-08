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
    push_subscription.py   One browser/device that gets Web Push (belongs to a dashboard login)
    notification_setting.py Which notifications a login wants (PREFERENCE_KEYS); no row = all on
    reminder_log.py        Reminders already sent (event + hours): claiming a row makes sending at-most-once
    audit_log.py           Append-only audit trail row (who/what/target/outcome/IP, no FKs)
  services/
    auth.py                ensure_fresh_token() — token lifecycle
    spond_accounts.py      Verify Spond credentials + build encrypted User row
    push.py                Web Push: VAPID key, payloads, sending (honours notification settings via `kind`), expired-subscription cleanup
    reminders.py           "Registration opens in 8/4/1 h" reminders for undecided events (scheduler job every minute)
    audit.py               Audit trail: record()/record_system(), AuditMiddleware, scrubbing, nightly purge
  workers/
    discovery.py           Worker A: sync events from Spond for all users
    executioner.py         Worker B: fire RSVPs + sniper DateTrigger helpers
    scheduler.py           APScheduler setup + startup sniper recovery
  api/
    auth.py                /auth/* (login, logout, me, change own password)
    accounts.py            /accounts/* (dashboard logins, admin only)
    users.py               /spond-accounts/* (Spond account CRUD; POST /me = connect own account; PUT /{id}/password)
    invites.py             /invites/* (admin create/list/revoke; public check/accept)
    push.py                /push/* (config, subscribe, unsubscribe, test) for the signed-in login
    audit.py               /admin/audit (filters, cursor paging) and /admin/audit/export.csv
    events.py              /events/* (list, set decision) and /health
    admin.py               /admin/* (stats, charts, RSVP log, scheduler jobs, sync)
    stream.py              /admin/stream and /user/stream (SSE)
    deps.py                FastAPI dependency injectors (CurrentUser, DbDep, AdminDep)
  schemas/                 Pydantic request/response models
scripts/
  healthcheck.py           Docker HEALTHCHECK probe (exit 0 only on HTTP 200)
  backup.sh                pg_dump loop/once/check for the compose `backup` service
  generate_vapid_key.py    Prints VAPID_PRIVATE_KEY for Web Push
  build_icons.sh           Regenerates frontend/icons/ from docs/branding/ (ImageMagick)
  fetch_fonts.py           Downloads the latin font subsets into frontend/fonts/
frontend/                  Static pages, no build step (served by app/main.py)
  index.html               Sign-in page
  join.html/.js            Invite signup: create login + connect Spond account
  dashboard.html/.js       Member dashboard: decision inbox + day-grouped agenda
  admin.html/.js           Admin console: queue, timeline, users, log, charts
  core.js                  Pure view logic (event state, grouping, formatting, push state); unit tested
  app.js                   Shared browser layer: API calls, auth guards, dialogs, toasts, service worker registration, install helper
  push.js                  Web Push on this device (dashboard)
  member.css / admin.css   Light member theme / dark admin theme
  manifest.webmanifest     PWA manifest
  sw.js / sw-core.js       Service worker (offline page, asset cache, push) / its pure logic; unit tested
  offline.html             Self-contained "no connection" page, shown by the service worker
  icons/, fonts/           Generated: scripts/build_icons.sh, scripts/fetch_fonts.py
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

**Loading is windowed, never "everything".** `GET /events` takes `start_from`, `start_to`, `order` (`invite` | `start` | `-start`), `limit`, `offset`. The admin queue loads 2 days back (from midnight) to 60 days ahead plus failures of the last 30 days, and earlier events only on request (Show filter, 100 per page); the member dashboard loads every upcoming event and the Past tab on demand, 30 per page. A short page means "no more". The queue's four blocks (needs attention, answered in the last 48 h, coming up, earlier) come from `Core.buildQueue`. New list views must page; the events table grows every week.

Member-facing labels: `accept`/`decline`/`manual` are shown as Going / Not going / Leave to me.

## Security conventions

- **Sessions are checked against the database** on every request (`deps._get_current_user`): never trust `is_admin`/`linked_user_id` from the cookie. It uses its own short session (`deps.open_session`), never `Depends(get_db)`, because SSE streams stay open for hours and would pin a pool connection. Session cookies carry `pwv` (password fingerprint): any password change must re-issue the cookie of the person changing it (`set_session_cookie`).
- **CSP forbids inline scripts**: no `<script>` without `src`, no `onclick=` etc. (tests enforce it). Put behaviour in a `.js` file and add the file to the page. Remote scripts need `integrity=` and `crossorigin`.
- **HTML is `Cache-Control: no-cache`** (no version in its URL). Keep new page routes on `_page()`. The service worker fetches navigations with `cache: "no-cache"` for the same reason.
- User-controlled text in `innerHTML` must go through `esc()`; `tests/frontend/e2e/xss.spec.js` plants markup in every view, add new views there.
- New endpoints: decide who may call them (`CurrentUser` / `AdminDep`), check ownership for member routes, rate-limit anything that verifies a secret, and add an `audit.record()` call.
- Passwords: `hash_password`/`verify_password` cut at 72 *bytes*; do not hash `plain[:72]` yourself.
- `docs/security.md` lists protections, operator duties and known open items: update it when you change any of them.

## Audit trail

- Every action that changes something calls `audit.record("area.action", target_type=…, target_id=…, target_label=…, details=…)` **after the commit** (staged on the request, written after the response). Name actions `area.verb`; the area is the filter category. New endpoints that write need a `record()` call; without one the middleware still logs a generic `http.<method>` row, but with no meaning.
- The bot's own actions use `await audit.record_system(...)`. Answers sent/failed are `rsvp.sent`/`rsvp.failed` with member, choice, latency_ms, retries and error: the admin panel has no separate answer-log view, the Audit view (area *Answers sent*) is it. `rsvp_log` stays for stats/charts. `record()` outside a request does nothing.
- Never put secrets in `details` (the scrubber is a safety net, not a licence). Use whitelisted fields and before/after values only.
- Successful GETs are deliberately not logged. Reading the trail is not logged; exporting it is.
- Frontend labels live in `Core.auditLabel` (`frontend/core.js`): add a label when you add an action.
- Tests: `AUDIT_ENABLED=false` by default (tests/conftest.py); opt in with the `audit_on` fixture and use `tests/audit_helpers.py` (real login cookies, no auth overrides, so the middleware sees the actor).
- IPs come from uvicorn's `--forwarded-allow-ips` (`TRUSTED_PROXIES` from `.env`; default `127.0.0.1`, never `*`: tests forbid a wildcard default). Behind a proxy the operator must set it (docs/setup.md).

## PWA and Web Push

- The app is installable (`manifest.webmanifest`, `sw.js` at the root). Page loads go network-first with `offline.html` as fallback; static files are stale-while-revalidate (**bump `?v=` on changed assets**); `/api/` is never cached. A new service worker waits until the member clicks Reload in the banner.
- `/sw.js`, `/sw-core.js` and the manifest have explicit routes in `app/main.py` with `Cache-Control: no-cache`; the catch-all asset whitelist includes `.woff2`.
- Fonts are self-hosted (`frontend/fonts/`); there are no requests to Google.
- Web Push is optional: it needs `VAPID_PRIVATE_KEY` (`scripts/generate_vapid_key.py`); the public key is derived from it. The executioner's `_notify_member()` sends the SSE event and the push. Subscription endpoints are restricted to real push services (SSRF guard). Push must never block or fail an RSVP.
- Notification kinds: `answer_sent`, `answer_failed`, `reminder_8h`, `reminder_4h`, `reminder_1h` (`PREFERENCE_KEYS`), chosen per **account** (`/push/preferences`, all devices). Every send passes a `kind` to `push.send_to_spond_user`, which skips logins that switched it off (the test notification is the only unfiltered send). A new kind needs: the key in `PREFERENCE_KEYS`, a column + migration, the schema field, an entry in `Core.NOTIFICATION_KINDS`, and the sender passing `kind=`.
- Reminders mean what the dashboard inbox means by *undecided*: `manual`, not answered, registration opening ahead. The reminder is claimed in `reminder_log` before it is sent. Never send reminders without that claim.
- E2E: service worker specs (`pwa.spec.js`) run against a real local server; all other specs block service workers. Push UI specs use `installFakePush`.

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

Retries: a 401 re-logs in and retries once at once; 5xx/429/timeouts/connection errors follow a ladder (50 ms … 2 s) for up to 20 s; other 4xx get 3 quick retries (covers firing a few ms early). The recipient ID is reused across attempts. `rsvp.sent`/`rsvp.failed` audit details carry `fire_ms`, `prep_ms`, `request_ms`, `response_ms`, `attempts` (ms relative to registration opening).

Fast path: the warmup (10 s before) warms the DB pool and leaves a `_Prepared` per event in `executioner._PREPARED` (fresh-checked token, member ID, open HTTPS connection; `GET /profile` proves the token). The sniper job starts 0.25 s early, waits for the exact instant, registers the event in `_INFLIGHT` (the executioner skips those; a duplicate sniper returns) and sends the PUT **without touching the database**; `_process_event(presend=…)` then claims, logs, audits and notifies. A failed prepared send continues on the normal path (retries). Without prepared data the normal path runs (read, claim, send); it re-checks the event because a running job cannot be cancelled. `schedule_sniper` drops prepared data whose decision changed, `cancel_sniper` drops it always, a 120 s sweep closes leftovers. Audit details carry `prepared: true/false`. Never put database access before the PUT on the prepared path.

## Event Status / Choice Values

| Field | Values |
|-------|--------|
| `user_choice` | `accept` / `decline` / `manual` |
| `status` | `pending` / `processing` / `processed` / `failed` |

## Configuration (env vars)

See `docs/setup.md` for the full reference. Critical vars: `DATABASE_URL`, `FERNET_KEY`, `API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `RSVP_LEAD_TIME_MS`. Optional: `VAPID_PRIVATE_KEY` (Web Push), `TRUSTED_PROXIES`, `AUDIT_RETENTION_DAYS`.

## Further Reading

- `docs/architecture.md` — full component diagram, data model, request lifecycle
- `docs/setup.md` — Docker deployment, env vars, first-run guide
- `docs/codebase-review.md` — code quality notes, known gotchas, design rationale
- `docs/feature-ideas.md` — prioritized feature backlog with design sketches
