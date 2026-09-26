# SpondBot Frontend

Static HTML/CSS/JS served by the FastAPI app from the same origin. No build step.

## Files

```
frontend/
├── index.html      Sign-in page
├── dashboard.html  Member dashboard (markup)
├── dashboard.js    Member dashboard: decision inbox + day-grouped agenda
├── admin.html      Admin panel (markup)
├── admin.js        Admin panel: queue, timeline, users, log, charts
├── core.js         Pure view logic shared by both pages (unit tested)
├── app.js          Shared browser layer: API calls, auth guards, dialogs, menus, toasts
├── member.css      Member theme (light, with a dark palette from the OS setting)
└── admin.css       Admin theme (dark console)
```

Script order on every page: `core.js`, then `app.js`, then the page script.

## Member dashboard

- **Summary line**: how many events need an answer and when the next automatic answer goes out.
- **Failed answers** appear at the top with the Spond error and a Retry button. Retry re-sends the
  member's own choice; an event left to the member cannot be retried.
- **Needs an answer**: undecided events, one at a time, with large Going / Not going buttons.
  "Decide later" moves to the next one without saving.
- **Agenda**: upcoming (or past) events grouped by day, each with a Going / Not going /
  Leave to me control. Answered events show the answer instead of controls.
- **Account menu**: profile (display name), change password, admin panel (admins only), sign out.

`accept` / `decline` / `manual` are labelled Going / Not going / Leave to me throughout.

## Admin panel

Hash-routed views, so each one can be bookmarked:

| View | Contents |
|---|---|
| `#queue` | Countdown to the next armed answer, health counters (armed, no answer, failed, latency p50/p95, last sync), and every account's events ordered by fire time. Change answers inline, send an armed answer now, disarm it, or retry a failure. |
| `#timeline` | One lane per Spond account over two weeks: registration opening (marker) to event start (bar). Select a marker for details and to change the answer. |
| `#users` | Dashboard logins and Spond accounts: add, edit, pause, delete. |
| `#log` | RSVP audit log with latency from registration opening. |
| `#charts` | Latency scatter and daily outcomes (Chart.js from jsDelivr, with SRI), plus a per-account table that works without the chart library. |

Destructive actions use an in-page confirmation dialog.

## Auth

The session is an HttpOnly cookie set by `POST /api/v1/auth/login`; JavaScript never sees the
token. `requireAuth()` sends visitors without a session to `/`; `requireAdmin()` sends members to
`/dashboard`. All permissions are enforced server-side.

## Tests

From the repository root (`npm install` once):

```bash
npm run test:unit   # node:test, frontend/core.js
npm run test:e2e    # Playwright, desktop + phone, mocked API
```

The e2e fixture (`tests/frontend/e2e/fixtures.js`) serves these files and a stateful in-memory
API that follows the backend's rules, with the clock fixed at 2026-09-26 19:00 UTC. To use an
existing Chromium instead of a Playwright download, set `PW_CHROMIUM_PATH`.
