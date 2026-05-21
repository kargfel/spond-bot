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
| `app/api/auth.py` | `/auth/*` — login, logout, me, frontend user CRUD |
| `app/api/events.py` | `/events/*` — list, get, set decision, manual sync trigger |
| `app/api/users.py` | `/users/*` — Spond user CRUD |
| `app/api/deps.py` | FastAPI dependency injectors: `CurrentUser`, `DbDep`, `AdminDep` |
| `app/schemas/` | Pydantic request/response models |
| `frontend/` | Vanilla JS SPA — `index.html` (login), `dashboard.html`, `admin.html`, `app.js`, `style.css` |

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
- **Path traversal guard in file serving:** `catch_all` in `main.py` checks `is_relative_to(_FRONTEND_DIR)` before serving any file.
- **Timing attack mitigation in login:** `auth.py` always runs bcrypt even when the username doesn't exist.

### Known Limitations / Tech Debt

- **No Alembic migrations:** schema changes require manual SQL or recreating the DB. As the schema evolves, proper migration tooling should be added.
- **No test suite:** there are no automated tests. Core logic (token lifecycle, sniper scheduling, upsert behavior) would benefit from unit tests.
- **Frontend is a monolith:** `app.js` is a single large file handling all three pages. As features grow, this will become hard to maintain.
- **No audit log:** once an RSVP fires, the only record is the `status` field. There's no history of what was submitted, when exactly, or what the API responded with.
- **Fernet key rotation is destructive:** changing `FERNET_KEY` invalidates all stored credentials with no migration path.
- **Single APScheduler instance:** the scheduler lives in the same process as the web server. Under high load, a slow RSVP batch could affect HTTP response times. For scale, consider a separate worker process.
- **Discovery is sequential per user:** `_sync_user()` calls are made in a loop, not concurrently. With many users, discovery can take a long time. Consider `asyncio.gather` with a semaphore.
