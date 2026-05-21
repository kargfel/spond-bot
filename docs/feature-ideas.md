# Feature Ideas

Prioritized backlog of improvements, grouped by theme. Each idea includes the motivation, a rough design sketch, and complexity estimate.

---

## Observability (admin-only)

### 1. RSVP Audit Log

**What:** A persistent log of every RSVP attempt — event, user, choice, timestamp fired, outcome, Spond response status.

**Why:** Currently the only record is `events.status`. Once an RSVP fires, you can't tell *when* it fired, whether the first attempt failed, or what Spond responded. An audit log makes debugging much easier and gives admins confidence the bot is working correctly.

**Design sketch:**
- Add a new `rsvp_log` table: `(id, event_id FK, user_id FK, choice, fired_at, outcome, http_status, error_detail, retry_count)`
- `_process_event()` writes a log row on every attempt (including retries)
- Admin API endpoint: `GET /api/v1/admin/rsvp-log?user_id=&event_id=&since=`
- Admin panel page: table view with filters for user, date range, outcome

**Complexity:** Medium. Schema change + one write per RSVP + simple API endpoint.

---

### 2. Admin Health Dashboard

**What:** A dashboard page showing system health at a glance — active users, recent RSVP outcomes, upcoming snipers, last discovery run, any failed events.

**Why:** Currently you have to read logs to know if anything went wrong. An admin dashboard surface makes problems visible without log diving.

**Design sketch:**
- New API endpoint: `GET /api/v1/admin/stats`
  - Active user count, events by status (counts), last discovery timestamp, next scheduled sniper, recent failures (last 10)
- Admin panel widget/section rendered from this endpoint
- Auto-refreshes every 30 seconds

**Complexity:** Low–Medium. Mostly DB aggregation queries + a small UI widget.

---

### 3. Timing Precision Metrics

**What:** Record the delta between `invite_time` and actual RSVP submission time for each event. Surface the p50/p95 in the admin dashboard.

**Why:** The sniper is designed for millisecond precision, but real-world network latency, scheduler jitter, and token refresh overhead add up. Knowing your actual precision helps tune the system (e.g., fire the RSVP request N ms *before* `invite_time` to account for round-trip time).

**Design sketch:**
- Add `submitted_at TIMESTAMPTZ` to `rsvp_log` (above)
- Compute `delta_ms = submitted_at - invite_time` when writing the log row
- Show p50/p95 delta in the admin dashboard
- Optional: configurable `RSVP_LEAD_TIME_MS` offset — sniper fires N ms before `invite_time`

**Complexity:** Low (once audit log exists). Medium if implementing lead-time offset.

---

## User Experience

### 4. Bulk Decision Setting

**What:** Select multiple events in the dashboard and set the same choice (accept/decline/manual) for all of them at once.

**Why:** If you have 10 upcoming events and want to accept all of them, you currently have to click each one individually. Bulk actions are a significant quality-of-life improvement.

**Design sketch:**
- Dashboard: checkbox per event row, "Select all" toggle, bulk action dropdown
- New API endpoint: `PATCH /api/v1/events/bulk-decision` with body `{ "event_ids": [...], "user_choice": "accept" }`
- Endpoint validates access to all event IDs before applying any changes (all-or-nothing)
- Reschedules/cancels sniper jobs for all affected events

**Complexity:** Low–Medium. Mostly frontend work + one new API endpoint.

---

### 5. "RSVP Now" Button

**What:** A button on any pending event that immediately submits the RSVP, bypassing `invite_time`.

**Why:** Sometimes you want to RSVP before the window officially opens (e.g., you already know you can attend and the invite window hasn't opened yet but the event is already in the DB). Or you want to manually retry a failed RSVP without waiting for the next executioner cycle.

**Design sketch:**
- Dashboard: "RSVP Now" button on events with `choice != manual`
- New API endpoint: `POST /api/v1/events/{id}/rsvp-now` (admin or event owner)
- Calls `_process_event()` directly, bypassing the `invite_time <= now` gate
- Returns the updated event (with `status=processed` or `failed`)

**Complexity:** Low. Reuses existing `_process_event()` logic.

---

### 6. Notifications on RSVP Completion

**What:** Notify users when their RSVP fires — success or failure.

**Why:** The bot runs silently in the background. Users have no way to know their RSVP was submitted without polling the dashboard. A notification closes the feedback loop, especially for time-sensitive events.

**Options (pick one or both):**

**A. Browser push notifications**
- Service worker + Web Push API
- User opts in from the dashboard
- Sends a notification when `status` changes to `processed` or `failed`
- Requires HTTPS

**B. Telegram bot notifications**
- Add `telegram_chat_id` field to `frontend_users`
- On RSVP completion, POST to Telegram Bot API
- Users configure their chat ID in profile settings
- Works without HTTPS; works on mobile

Telegram is simpler to implement and more reliable on mobile. Browser push requires HTTPS and service worker setup.

**Complexity:** Medium. Telegram is lower complexity; browser push is higher.

---

### 7. Per-Event Status Indicators

**What:** Visual status indicators in the dashboard showing the sniper state for each event — "scheduled for HH:MM:SS", "fired N minutes ago", "no decision set".

**Why:** Users can't currently tell whether the bot is actively watching an event. A live countdown or status badge builds confidence.

**Design sketch:**
- `GET /events` response already includes `invite_time` and `status`
- Frontend computes a human-readable label: "Fires in 2h 15m", "Fired 3 minutes ago", "Waiting for your decision"
- Color-coded badge: blue=scheduled, green=processed, red=failed, grey=manual
- Auto-refreshes on a 30-second interval

**Complexity:** Low. Purely frontend changes; no API changes needed.

---

## Resilience

### 8. Configurable Retry Budget

**What:** Instead of a single automatic retry on 401, allow configuring N retries with exponential backoff before marking an event as failed.

**Why:** Transient Spond API errors (rate limits, 5xx) currently result in permanent failure. A retry budget handles brief outages gracefully without spamming the API.

**Design sketch:**
- Add `retry_count INT DEFAULT 0` to the `events` table (or to `rsvp_log`)
- Add `MAX_RSVP_RETRIES` config var (default: 3)
- On non-auth failure, if `retry_count < MAX_RSVP_RETRIES`: increment counter, reset to `pending`, schedule a retry job in N*2^retry_count seconds
- On auth failure: still force-refresh and retry once immediately (current behavior)
- After `MAX_RSVP_RETRIES`: mark as `failed`

**Complexity:** Medium. Requires schema change + retry scheduling logic.

---

### 9. Spond API Change Detection

**What:** Alert the admin when the Spond API starts returning unexpected responses — e.g., login fails for all users simultaneously, or a new response shape is detected.

**Why:** Spond changes their API without notice (as seen on 2026-05-21). When this happens, every user's bot silently stops working. Early detection gives the admin time to fix it before users notice.

**Design sketch:**
- Track a `consecutive_auth_failures` counter per user in the `users` table
- If all active users have auth failures in the same discovery cycle → emit a warning log line and optionally a Telegram notification to the admin
- Add a `/health` field: `"spond_api": "degraded"` if >50% of users have recent auth failures
- The admin dashboard health widget surfaces this immediately

**Complexity:** Low–Medium. Mostly logic around existing error tracking.

---

### 10. Concurrent Discovery

**What:** Run `_sync_user()` for all users concurrently instead of sequentially.

**Why:** With 10+ users, sequential discovery can take several minutes. With 60-minute intervals that's fine, but shorter intervals risk the next run starting before the first finishes.

**Design sketch:**
- Replace the `for user in users` loop in `run_discovery()` with `asyncio.gather(*[_sync_user(u) for u in users], return_exceptions=True)`
- Add a semaphore (`asyncio.Semaphore(5)`) to cap concurrent Spond API sessions and avoid rate limiting

**Complexity:** Low. A few lines of change with meaningful scalability benefit.

---

## Bonus: Rule Engine (Auto-Assignment)

### 11. Auto-Assignment Rules

**What:** Let users define rules that automatically set `user_choice` for new events — e.g., "always accept events from group X", "decline if title contains 'training'", "accept only if RSVP deadline is more than 3 days away".

**Why:** Currently, every new event lands with `choice=manual`. Users have to log in and manually set each choice. Rules turn the bot into a true "set it and forget it" system.

**Design sketch:**
- New `rules` table: `(id, user_id FK, priority INT, condition_type, condition_value, action)`
  - `condition_type`: `group_id` | `title_contains` | `min_notice_hours` | `always`
  - `action`: `accept` | `decline` | `manual`
- After upsert in discovery, evaluate rules for newly inserted events (skip if event already has a non-manual choice)
- Rules evaluated in priority order; first match wins
- Admin/user UI to add/edit/delete rules
- Rules are per-user (each Spond user gets their own rule set)

**Complexity:** Medium–High. Schema + rule evaluation engine + UI. Worth implementing as a separate feature branch.
