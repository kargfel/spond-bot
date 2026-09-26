# Feature Ideas

Prioritized backlog of improvements, grouped by theme. Each idea includes the motivation, a rough design sketch, and complexity estimate.

Items marked ✅ are shipped.

---

## Observability (admin-only)

### ✅ 1. RSVP Audit Log

Shipped. `rsvp_log` table records every attempt with `fired_at`, `submitted_at`, `outcome`, `retry_count`, `error_detail`. Viewable in the admin RSVP Log tab with filters and pagination.

---

### ✅ 2. Admin Health Dashboard

Shipped. `GET /admin/stats` surfaces active users, event counts by status, p50/p95 latency, and recent failures. Auto-refreshes every 30 s via SSE.

---

### ✅ 3. Timing Precision Metrics

Shipped. `submitted_at` column in `rsvp_log`, latency computed as `submitted_at − invite_time`. p50/p95 shown in the Stats overview and per-user in the Charts tab. Configurable `RSVP_LEAD_TIME_MS` offset already exists.

---

### ✅ 4. Realtime Admin Stream (SSE)

Shipped. `GET /admin/stream` pushes `rsvp_fired` and `scheduler_changed` events to the admin panel. Stats and scheduler panel update live without polling.

---

### ✅ 5. Charts & Scheduler Panel

Shipped. Admin Charts tab has a latency scatter plot (Chart.js), daily success/retry/failed bar chart, and per-user stats table. Scheduler panel lists armed sniper jobs with countdown; supports cancel and immediate fire.

---

### ✅ 6. API Rename & Swagger Protection (PR #17)

Shipped. Routes renamed for clarity:

| Before | After |
|--------|-------|
| `GET/POST/PATCH/DELETE /auth/users[/{id}]` | `/accounts[/{id}]` |
| `GET/POST/PATCH/DELETE /users[/{id}]` | `/spond-accounts[/{id}]` |
| `PATCH /events/{id}/decision` | `PATCH /events/{id}` |
| `POST /sync` | `POST /admin/sync` |

Swagger UI (`/docs`, `/redoc`) now requires an active admin session.

---

### ✅ 7. User Dashboard UX (PR #20)

Shipped. Dashboard sidebar shows "Managing: [Spond account name]" (or "No Spond account linked"). Change Password exposed as a direct sidebar button — no admin required.

---

### ✅ 8. Frontend Redesign (PR #22)

Shipped. Member dashboard with a decision inbox (one undecided event at a time) and a day-grouped agenda using Going / Not going / Leave to me. Admin console with a live countdown to the next answer, a queue ordered by fire time, a per-account timeline, and users, log and charts views. Frontend logic is unit tested and the pages have Playwright e2e tests.

---

## User Experience

### 9. Bulk Decision Setting

**What:** Select multiple events and set the same choice for all at once.

**Why:** Setting 10 events to "accept" one by one is tedious.

**Design sketch:**
- Checkbox per event card, "Select all" toggle, bulk action dropdown
- `PATCH /api/v1/events/bulk-decision` with `{ "event_ids": [...], "user_choice": "accept" }`
- Validates ownership before applying; reschedules/cancels sniper jobs for all affected events

**Complexity:** Low–Medium.

---

### 10. "RSVP Now" Button (partly shipped)

*Partly shipped in PR #22:* admins can "Send now" any armed answer from the queue (uses `POST /admin/scheduler/{job_id}/fire`). Members still have no equivalent, and events without an armed timer cannot be sent early.

**What:** Submit an RSVP immediately, bypassing `invite_time`.

**Why:** Useful for retrying a failed RSVP or manually firing before the window opens.

**Design sketch:**
- "RSVP Now" button on events where `choice != manual`
- `POST /api/v1/events/{id}/rsvp-now` — calls `_process_event()` directly
- Returns the updated event status

**Complexity:** Low.

---

### 11. Notifications on RSVP Completion

**What:** Notify users when their RSVP fires (success or failure).

**Why:** The bot runs silently. Users have no out-of-band confirmation without refreshing the dashboard. The SSE stream covers in-app, but doesn't help when the browser is closed.

**Options:**

**A. Telegram bot**
- Add `telegram_chat_id` to `frontend_users`
- POST to Telegram Bot API on `rsvp_fired`
- User configures chat ID in profile settings
- Works on mobile without HTTPS requirements

**B. Browser push notifications**
- Service worker + Web Push API
- User opts in from dashboard
- Requires HTTPS (already the case in production)

Telegram is simpler and more reliable on mobile.

**Complexity:** Medium (Telegram); High (browser push).

---

### ✅ 12. Per-Event Status Indicators

*Shipped in PR #22:* every agenda row shows its state ("Scheduled · answers in 3h 0m", "No answer set", "Sent …", "Could not be sent"), and the admin queue shows a live T-minus per event.

**What:** Show sniper state per event card — "Fires in 2h 15m", "Fired 3 min ago", "No decision set".

**Why:** Users can't tell if the bot is actively watching an event.

**Design sketch:**
- Compute label from `invite_time` and `status` on the frontend
- Color-coded: blue=scheduled, green=processed, red=failed, grey=manual
- No API changes needed

**Complexity:** Low. Frontend only.

---

## Resilience

### 13. Configurable Retry Budget

**What:** N retries with exponential backoff instead of a single 401 retry.

**Why:** Transient Spond 5xx errors cause permanent failure. A retry budget handles brief outages.

**Design sketch:**
- `MAX_RSVP_RETRIES` config var (default 3)
- On non-auth failure: increment `retry_count`, reset to `pending`, schedule retry in `N * 2^retry_count` seconds
- Auth failure: still force-refresh + immediate retry (current behavior)

**Complexity:** Medium.

---

### 14. Spond API Change Detection

**What:** Alert admin when Spond API starts returning unexpected responses across all users.

**Why:** Spond changes their API without notice. Silent failure for all users is the worst outcome.

**Design sketch:**
- Track `consecutive_auth_failures` per user in `users` table
- If all active users fail in the same discovery cycle → warning log + optional Telegram notification to admin
- `/health` endpoint gains `"spond_api": "degraded"` flag

**Complexity:** Low–Medium.

---

### 15. Concurrent Discovery

**What:** Sync all users in parallel instead of sequentially.

**Why:** With 10+ users, sequential discovery takes several minutes.

**Design sketch:**
```python
sem = asyncio.Semaphore(5)
async def _sync_with_sem(u):
    async with sem:
        await _sync_user(u)
await asyncio.gather(*[_sync_with_sem(u) for u in users], return_exceptions=True)
```

**Complexity:** Low. ~5 lines of change.

---

## Bonus: Rule Engine

### 16. Auto-Assignment Rules

**What:** Rules that automatically set `user_choice` for new events — "always accept group X", "decline if title contains 'training'", "accept only if notice > 3 days".

**Why:** Turns the bot into a true "set it and forget it" system. Currently every new event lands as `manual` and requires a dashboard visit.

**Design sketch:**
- New `rules` table: `(id, user_id FK, priority INT, condition_type, condition_value, action)`
  - `condition_type`: `group_id` | `title_contains` | `min_notice_hours` | `always`
  - `action`: `accept` | `decline` | `manual`
- Evaluate rules after upsert in discovery (skip if event already has a non-manual choice)
- First match wins (priority order)
- Per-user rule UI in the dashboard

**Complexity:** Medium–High. Separate feature branch.
