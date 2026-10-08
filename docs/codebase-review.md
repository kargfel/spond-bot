# Codebase Review

## Module Map

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app, lifespan (scheduler start + admin seed), static file serving with path-traversal guard |
| `app/config.py` | All env vars via pydantic-settings; single `settings` import target |
| `app/database.py` | SQLAlchemy async engine, `Base`, `AsyncSessionLocal`, `get_db()` dependency |
| `app/core/spond_client.py` | Stateless Spond API client — login, profile, events, RSVP |
| `app/core/security.py` | Fernet `encrypt()` / `decrypt()` helpers |
| `app/core/jwt.py` | joserfc JWT `create_access_token()` / `decode_access_token()` |
| `app/models/user.py` | `User` — Spond account with encrypted credentials |
| `app/models/frontend_user.py` | `FrontendUser` — dashboard login account |
| `app/models/event.py` | `Event` — one row per (spond_event_id, user_id); choice + status |
| `app/services/auth.py` | `ensure_fresh_token()` — token lifecycle (23h cache + force-refresh) |
| `app/workers/discovery.py` | Worker A: periodic Spond event sync for all active users |
| `app/workers/executioner.py` | Worker B: RSVP execution + sniper DateTrigger scheduling |
| `app/workers/scheduler.py` | APScheduler setup; startup sniper recovery |
| `app/api/auth.py` | `/auth/*` — login, logout, me, change password |
| `app/api/accounts.py` | `/accounts/*` — dashboard (frontend) user CRUD (admin only) |
| `app/api/events.py` | `/events/*` — list, get, set RSVP decision |
| `app/api/users.py` | `/spond-accounts/*` — Spond credential account CRUD (admin only) |
| `app/api/admin.py` | `/admin/*` — rsvp-log, stats, sync, charts, scheduler |
| `app/api/stream.py` | `/admin/stream`, `/user/stream` — SSE admin and user streams |
| `app/api/deps.py` | FastAPI dependency injectors: `CurrentUser`, `DbDep`, `AdminDep` |
| `app/schemas/` | Pydantic request/response models |
| `frontend/` | Vanilla JS SPA — `index.html` (login), `dashboard.html`, `admin.html`, `app.js`, `core.js`, `dashboard.js`, `admin.js`, `member.css`, `admin.css` |

---

## Recent Changes

- **2026-05-21**: Login endpoint migrated to `/core/v1/auth2/login`; token format changed
- **2026-05-21**: RSVP audit log added (`rsvp_log` table, `/admin/rsvp-log` endpoint)
- **2026-05-21**: Admin health dashboard added (`/admin/stats`)
- **2026-05-21**: Timing precision metrics added (p50/p95 in admin stats)
- **2026-05-21**: Sniper race condition fixed (atomic claim via `UPDATE ... WHERE status=pending`)
- **2026-05-21**: Warmup pre-fetch added (fires 10s before sniper, caches `resolved_recipient_id`)
- **2026-10-08**: Retry ladder for transient RSVP failures, per-phase timings in the audit details (`fire_ms`, `prep_ms`, `request_ms`, `response_ms`, `attempts`)
- **2026-10-08**: Warmup prepares token, member ID and an open HTTPS connection; the sniper sends on it when the decision is unchanged
- **2026-09-24**: API routes renamed (`/auth/users` → `/accounts`, `/users` → `/spond-accounts`, `PATCH /events/{id}/decision` → `PATCH /events/{id}`, `POST /sync` → `POST /admin/sync`)
- **2026-09-24**: Swagger UI protected behind admin session auth
- **2026-09-24**: SSE streams added (`/admin/stream`, `/user/stream`)
- **2026-09-24**: Admin dashboard charts + scheduler panel added
- **2026-09-24**: User dashboard improved (Spond account indicator, Change Password button)
- **2026-09-26**: Frontend redesign (PR #22): member decision inbox + agenda, admin console with queue, timeline, users, log and charts; frontend unit and e2e tests
- **2026-09-26**: CI (GitHub Actions), pinned dependency lock, Dependabot
- **2026-09-27**: Invite links and self-connect for members, `/health` returns 503 on failure + Docker HEALTHCHECK + optional autoheal, nightly `pg_dump` backups; CI runs migrations on Postgres 16
- **2026-09-27**: Static file serving switched to a startup allowlist; unused `/static` mount removed (CodeQL alerts #1–#3)
- **2026-09-27**: Stored Spond passwords can be replaced (`PUT /spond-accounts/{id}/password`, member or admin), verified with Spond first

---

## Spond API — Known Quirks and History

### Authentication endpoint migration (2026-05-21)

Spond changed their login endpoint without notice:

| Version | Endpoint | Response field |
|---------|----------|---------------|
| Old | `POST /core/v1/login` | `{ "loginToken": "..." }` |
| New | `POST /core/v1/auth2/login` | `{ "accessToken": { "token": "..." } }` |

The current `login()` function tries the old `loginToken` field first for backwards compatibility, then falls back to `accessToken.token`. The raw Base64 string in `accessToken.token` must be passed **as-is** in `Authorization: Bearer` — decoding it causes a 401.

### User-agent header

Spond's API requires a mobile client user-agent. Current value in `_headers()`:

```
Spond-iOS/2.7.10 (2233; iPhone; iOS 26.2.1; Scale/3.00)
```

If requests start failing with unexpected 4xx errors, try updating this to a more recent version string.

### Member ID vs Profile ID for RSVPs

Spond's RSVP endpoint (`PUT /sponds/{id}/responses/{recipientId}`) requires the **per-group member ID**, not the global profile ID. These are different UUIDs.

`resolve_recipient_id()` handles this by:
1. Getting the group ID from the event's `recipients.group.id`
2. Fetching `GET /groups` (the all-groups response includes `profile.id` and contact fields)
3. Matching the current user in the event's group by `profile.id` first, then email/phone as fallback
4. Falling back to the global `profile_id` for direct invites or if the group lookup fails

The primary match is `profile.id` because Spond doesn't always expose email/phone in the groups response.

### RSVP endpoint

```
PUT /core/v1/sponds/{spondEventId}/responses/{recipientId}
Body: {"accepted": true}   (or false for decline)
Expected: 200 or 204
```

`recipientId` is the per-group member ID, **not** the global profile ID (see *Member ID vs Profile ID* above). On 401, the executioner forces a token refresh and retries once; transient errors (5xx, 429, timeouts, connection errors) are retried on a short ladder, see `docs/architecture.md`.

### getBulk chunking

`GET /sponds/getBulk?ids=...` has an undocumented limit. The client chunks requests at 50 IDs to stay safe.

---

## Design Decisions

### Why a stateless Spond client?

Making `spond_client.py` a pure-function module (no class, no state) means:
- Easy to test: pass a mock session, get deterministic output
- No hidden shared state between discovery and executioner workers
- Clear separation: the DB owns the token; the client just uses it

### Why two auth systems?

The frontend users (dashboard login) are completely separate from Spond accounts because:
- An admin may not have a Spond account at all
- One dashboard user could theoretically manage multiple Spond accounts (not currently implemented, but the schema supports it via `linked_user_id`)
- Frontend passwords use bcrypt (slow, one-way); Spond passwords must be retrievable for re-login, so they use reversible Fernet encryption

### Why Fernet over a KMS or hashed approach?

Fernet is simple, well-audited, and requires no external service. The tradeoff is that the key must be kept safe — if an attacker gets both the DB dump and the `FERNET_KEY`, all passwords are exposed. For a self-hosted tool with a single operator, this is an acceptable tradeoff. A KMS would be better for a multi-tenant SaaS deployment.

### Why APScheduler DateTrigger (sniper) + interval executioner?

The interval executioner alone has ±60 second precision — too coarse for competitive RSVP slots. The sniper adds millisecond precision by scheduling one job per event at exactly `invite_time`. The executioner remains as a safety net for:
- Events whose `invite_time` passed before the sniper was scheduled
- Sniper jobs that misfired (app was down at `invite_time`)
- Edge cases where the sniper didn't get created (e.g., discovery ran before the user set a choice)

### Why not store the decoded JWT from Spond?

The initial implementation tried to base64-decode the `accessToken.token` value, but Spond's API rejected the decoded string with 401. The token must be passed as the raw Base64 string received from the login response. This is counter-intuitive but confirmed by trial.

---

## Code Quality Notes

### Strengths

- **Error isolation in workers:** both `run_discovery()` and `run_executioner()` catch all exceptions at the top level and log them — APScheduler never sees an unhandled exception, so a crash in one user's sync doesn't affect others.
- **`PROCESSING` status as a mutex:** setting `status = processing` before the RSVP call prevents the executioner and sniper from double-firing the same event if their windows overlap.
- **Upsert preserves user decisions:** the `ON CONFLICT DO UPDATE` in discovery never touches `user_choice` — a user's pre-set decision survives a metadata refresh.
- **Allowlisted file serving:** `catch_all` in `main.py` serves only web assets (`.html`, `.css`, `.js`, images) found in `frontend/` at startup; the request path is only a lookup key, never joined into a filesystem path. Everything else gets the sign-in page. (Replaced a resolve-and-check guard that CodeQL flagged as alerts #1–#3.)
- **Timing attack mitigation in login:** `auth.py` always runs bcrypt even when the username doesn't exist.

### Known Limitations / Tech Debt

- **Migrations are hand-written:** Alembic runs on container start (`alembic upgrade head`), with migrations in `migrations/versions/`. New schema changes need a matching migration.
- **Test coverage gaps:** API routes, the executioner and discovery tracking have pytest coverage, and the frontend has unit and e2e tests. The Spond client itself (`spond_client.py`) is only exercised through mocks; there is no contract test against Spond's real responses.
- **Audit log is append-only:** the `rsvp_log` table records every submission attempt (outcome, timing, error detail). The `/admin/rsvp-log` endpoint accepts a `limit` param (default 100, max 500) and supports `user_id`/`since` filters.
- **Fernet key rotation is destructive:** changing `FERNET_KEY` invalidates all stored credentials with no migration path.
- **Single APScheduler instance:** the scheduler lives in the same process as the web server. Under high load, a slow RSVP batch could affect HTTP response times. For scale, consider a separate worker process.
- **Discovery is sequential per user:** `_sync_user()` calls are made in a loop, not concurrently. With many users, discovery can take a long time. Consider `asyncio.gather` with a semaphore.
