// Playwright fixtures: serves the real frontend/ files and a stateful, in-memory
// mock of the SpondBot API. The mock mirrors the backend rules the UI relies on
// (see app/api/*.py), and records every API call for assertions.
const fs = require("node:fs");
const path = require("node:path");
const { test: base, expect } = require("@playwright/test");

const FRONTEND = path.resolve(__dirname, "../../../frontend");
const NOW = Date.parse("2026-09-26T19:00:00Z");
const H = 3600e3;
const D = 24 * H;
const iso = (ms) => new Date(ms).toISOString();

const SPOND_REJECTED = "Spond did not accept that login and password. Check them in the Spond app and try again.";
const inviteStatus = (i) => (i.used_at ? "used" : Date.parse(i.expires_at) <= NOW ? "expired" : "pending");

const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml" };

function event(id, user_id, heading, start, invite, user_choice, status, error_message = null) {
  return {
    id, spond_event_id: `sp-${id}`, user_id, heading,
    start_timestamp: start, invite_time: invite, rsvp_date: null,
    user_choice, status, error_message,
    created_at: iso(NOW - 5 * D), updated_at: iso(NOW - D),
  };
}

function defaultState() {
  return {
    me: { sub: "a1", username: "felix", is_admin: false, linked_user_id: "u1" },
    passwords: { felix: "correct-horse", admin: "admin-pass" },
    accounts: [
      { id: "a1", username: "felix", is_admin: false, linked_user_id: "u1" },
      { id: "a2", username: "admin", is_admin: true, linked_user_id: null },
    ],
    spondUsers: [
      { id: "u1", display_name: "Felix Karg", login: "felix@example.com", profile_id: "PROFILE1234567", is_active: true, created_at: iso(NOW - 30 * D) },
      { id: "u2", display_name: "Mara Lind", login: "mara@example.com", profile_id: "PROFILE7654321", is_active: true, created_at: iso(NOW - 20 * D) },
    ],
    events: [
      event("e1", "u1", "Training, Hall B", iso(NOW + 5 * H), iso(NOW - 2 * D), "accept", "processed"),
      event("e2", "u1", "League match vs. TSV Nord", "2026-09-28T15:00:00Z", iso(NOW + 3 * H), "accept", "pending"),
      event("e3", "u1", "Evening training", "2026-09-29T19:30:00Z", "2026-09-27T19:30:00Z", "manual", "pending"),
      event("e4", "u1", "Club barbecue", "2026-10-02T18:00:00Z", "2026-09-28T12:00:00Z", "decline", "pending"),
      event("e5", "u1", "Autumn tournament", "2026-10-05T09:00:00Z", "2026-09-25T20:00:00Z", "decline", "failed",
        "Spond returned 403: member not found in group"),
      event("e6", "u1", "Recovery session", "2026-10-07T19:30:00Z", "2026-10-02T19:30:00Z", "manual", "pending"),
      event("e7", "u1", "Old friendly match", "2026-09-20T10:00:00Z", "2026-09-15T10:00:00Z", "accept", "processed"),
      event("m1", "u2", "Sunday league", "2026-09-27T11:00:00Z", "2026-09-26T21:00:00Z", "accept", "pending"),
      event("m2", "u2", "Pilates", "2026-09-30T18:00:00Z", "2026-09-29T18:00:00Z", "manual", "pending"),
    ],
    jobs: [
      { job_id: "sniper_e2", event_id: "e2", heading: "League match vs. TSV Nord", user_name: "Felix Karg", fire_at: iso(NOW + 3 * H), countdown_s: 3 * 3600 },
      { job_id: "sniper_m1", event_id: "m1", heading: "Sunday league", user_name: "Mara Lind", fire_at: "2026-09-26T21:00:00Z", countdown_s: 2 * 3600 },
      { job_id: "sniper_e4", event_id: "e4", heading: "Club barbecue", user_name: "Felix Karg", fire_at: "2026-09-28T12:00:00Z", countdown_s: 41 * 3600 },
    ],
    stats: {
      active_users: 2, total_events: 9, events_pending: 5, events_processed: 2, events_failed: 1,
      last_discovery_at: iso(NOW - 12 * 60e3),
      recent_failures: [
        { event_id: "e5", user_display_name: "Felix Karg", heading: "Autumn tournament",
          error_message: "Spond returned 403: member not found in group", updated_at: iso(NOW - 23 * H) },
      ],
      rsvp_p50_ms: 38, rsvp_p95_ms: 112, rsvp_sample_count: 41,
    },
    rsvpLog: [
      { id: "l1", event_id: "e1", user_id: "u1", spond_event_id: "sp-e1-abcdef", choice: "accept",
        fired_at: iso(NOW - 2 * D), submitted_at: iso(NOW - 2 * D + 41), outcome: "success", retry_count: 0, error_detail: null },
      { id: "l2", event_id: "e5", user_id: "u1", spond_event_id: "sp-e5-abcdef", choice: "decline",
        fired_at: iso(NOW - 23 * H), submitted_at: null, outcome: "failed", retry_count: 1, error_detail: "403 member not found" },
      { id: "l3", event_id: "m9", user_id: "u2", spond_event_id: "sp-m9-abcdef", choice: "accept",
        fired_at: iso(NOW - 3 * D), submitted_at: iso(NOW - 3 * D + 90), outcome: "retry_success", retry_count: 1, error_detail: null },
    ],
    invites: [
      { id: "i1", token: "used-token", note: "Jonas", created_at: iso(NOW - 3 * D), expires_at: iso(NOW + 4 * D), used_at: iso(NOW - D) },
      { id: "i2", token: "valid-token", note: "Mara", created_at: iso(NOW - H), expires_at: iso(NOW + 7 * D), used_at: null },
      { id: "i3", token: "old-token", note: null, created_at: iso(NOW - 10 * D), expires_at: iso(NOW - 3 * D), used_at: null },
    ],
    charts: {
      latency_scatter: [
        { fired_at: iso(NOW - 2 * D), latency_ms: 41, user_name: "Felix Karg", heading: "Training, Hall B" },
        { fired_at: iso(NOW - 3 * D), latency_ms: 90, user_name: "Mara Lind", heading: "Yoga" },
      ],
      daily_success_rate: [
        { date: "2026-09-23", success: 1, failed: 0, retry_success: 1 },
        { date: "2026-09-24", success: 1, failed: 0, retry_success: 0 },
        { date: "2026-09-25", success: 0, failed: 1, retry_success: 0 },
      ],
      per_user: [
        { user_name: "Felix Karg", total: 2, success: 1, p50_ms: 41, p95_ms: 41 },
        { user_name: "Mara Lind", total: 1, success: 1, p50_ms: 90, p95_ms: 90 },
      ],
    },
  };
}

class MockApi {
  constructor() {
    this.state = defaultState();
    this.calls = [];
  }

  asMember(overrides = {}) {
    return this.signIn({ sub: "a1", username: "felix", is_admin: false, linked_user_id: "u1", ...overrides });
  }

  asAdmin(overrides = {}) {
    return this.signIn({ sub: "a2", username: "admin", is_admin: true, linked_user_id: null, ...overrides });
  }

  /** Session claims mirror the stored login, as they do on the real server. */
  signIn(claims) {
    this.state.me = claims;
    const acct = this.state.accounts.find((a) => a.id === claims.sub);
    if (acct) Object.assign(acct, { is_admin: claims.is_admin, linked_user_id: claims.linked_user_id });
    return this;
  }

  signedOut() {
    this.state.me = null;
    return this;
  }

  addSpondUser(login, displayName) {
    const u = { id: `u${this.state.spondUsers.length + 1}`, display_name: displayName || login.split("@")[0], login,
      profile_id: "PROFILE-NEW", is_active: true, created_at: new Date(NOW).toISOString() };
    this.state.spondUsers.push(u);
    return u;
  }

  /** API calls matching method and path (path without the /api/v1 prefix). */
  callsTo(method, pathname) {
    return this.calls.filter((c) => c.method === method && c.path === pathname);
  }

  async attach(page) {
    await page.clock.setFixedTime(NOW);
    await page.route("**/*", (route) => this.handle(route));
  }

  async handle(route) {
    const req = route.request();
    const url = new URL(req.url());
    if (url.hostname !== "spondbot.test") return route.abort();
    if (url.pathname.startsWith("/api/v1/")) return this.handleApi(route, req, url);
    return this.serveFile(route, url.pathname);
  }

  serveFile(route, pathname) {
    const pages = { "/": "index.html", "/login": "index.html", "/dashboard": "dashboard.html", "/admin": "admin.html", "/join": "join.html" };
    const rel = pages[pathname] || pathname.slice(1);
    const file = path.join(FRONTEND, rel);
    if (!file.startsWith(FRONTEND) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      return route.fulfill({ status: 404, body: "not found" });
    }
    return route.fulfill({
      status: 200,
      contentType: TYPES[path.extname(file)] || "application/octet-stream",
      body: fs.readFileSync(file),
    });
  }

  async handleApi(route, req, url) {
    const method = req.method();
    const p = url.pathname.slice("/api/v1".length);
    let body = null;
    try { body = req.postDataJSON(); } catch { body = null; }
    this.calls.push({ method, path: p, query: Object.fromEntries(url.searchParams), body });

    const json = (status, data) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    const empty = (status) => route.fulfill({ status, body: "" });
    const s = this.state;
    const me = s.me;
    const m = (re) => p.match(re);
    let hit;

    if (p.endsWith("/stream")) return empty(204);

    if (method === "POST" && p === "/auth/login") {
      const acct = s.accounts.find((a) => a.username === body.username);
      if (!acct || s.passwords[acct.username] !== body.password) {
        return json(401, { detail: "Incorrect username or password." });
      }
      s.me = { sub: acct.id, username: acct.username, is_admin: acct.is_admin, linked_user_id: acct.linked_user_id };
      return empty(204);
    }
    if (method === "POST" && p === "/invites/check") {
      const inv = s.invites.find((i) => i.token === body.token);
      if (!inv) return json(200, { valid: false, reason: "unknown", note: null, expires_at: null });
      const st = inviteStatus(inv);
      if (st !== "pending") return json(200, { valid: false, reason: st, note: null, expires_at: null });
      return json(200, { valid: true, reason: null, note: inv.note, expires_at: inv.expires_at });
    }
    if (method === "POST" && p === "/invites/accept") {
      const inv = s.invites.find((i) => i.token === body.token);
      const st = inv ? inviteStatus(inv) : null;
      if (st !== "pending") return json(410, { detail: st === "used" ? "This invite has already been used." : "This invite link is not valid." });
      if (s.accounts.some((a) => a.username === body.username)) return json(409, { detail: "That username is taken. Pick another one." });
      if (body.spond_password === "wrong") return json(401, { detail: SPOND_REJECTED });
      const u = this.addSpondUser(body.spond_login, body.display_name);
      const acct = { id: `a${s.accounts.length + 1}`, username: body.username, is_admin: false, linked_user_id: u.id };
      s.accounts.push(acct);
      s.passwords[acct.username] = body.password;
      inv.used_at = new Date(NOW).toISOString();
      s.me = { sub: acct.id, username: acct.username, is_admin: false, linked_user_id: u.id };
      return json(201, acct);
    }
    if (!me) return json(401, { detail: "Not authenticated" });

    if (method === "GET" && p === "/auth/me") return json(200, me);
    if (method === "POST" && p === "/auth/logout") { s.me = null; return empty(204); }
    if (method === "PATCH" && p === "/auth/me/password") {
      if (s.passwords[me.username] !== body.current_password) return json(401, { detail: "Current password is incorrect." });
      s.passwords[me.username] = body.new_password;
      return empty(204);
    }

    // Events
    if (method === "GET" && p === "/events") {
      const q = url.searchParams;
      let list = s.events;
      if (me.is_admin && (q.get("all") === "true" || q.get("user_id"))) {
        if (q.get("user_id")) list = list.filter((e) => e.user_id === q.get("user_id"));
      } else {
        if (!me.linked_user_id) return json(200, []);
        list = list.filter((e) => e.user_id === me.linked_user_id);
      }
      if (q.get("status")) list = list.filter((e) => e.status === q.get("status"));
      if (q.get("choice")) list = list.filter((e) => e.user_choice === q.get("choice"));
      return json(200, list);
    }
    if ((hit = m(/^\/events\/([^/]+)$/))) {
      const ev = s.events.find((e) => e.id === hit[1]);
      if (!ev) return json(404, { detail: "Event not found." });
      if (!me.is_admin && ev.user_id !== me.linked_user_id) return json(403, { detail: "You do not have access to this event." });
      if (method === "GET") return json(200, ev);
      if (method === "PATCH") {
        ev.user_choice = body.user_choice;
        if (ev.status === "failed" && body.user_choice !== "manual") {
          ev.status = "pending";
          ev.error_message = null;
        }
        return json(200, ev);
      }
    }

    // Spond accounts
    if (p === "/spond-accounts") {
      if (method === "GET") return json(200, s.spondUsers);
      if (method === "POST") {
        const u = { id: `u${s.spondUsers.length + 1}`, display_name: body.display_name, login: body.login, profile_id: null, is_active: true, created_at: new Date(NOW).toISOString() };
        s.spondUsers.push(u);
        return json(201, u);
      }
    }
    if (method === "POST" && p === "/spond-accounts/me") {
      const acct = s.accounts.find((a) => a.id === me.sub);
      if (me.linked_user_id || acct?.linked_user_id) return json(409, { detail: "Your login is already linked to a Spond account." });
      if (body.password === "wrong") return json(401, { detail: SPOND_REJECTED });
      const u = this.addSpondUser(body.login, body.display_name);
      if (acct) acct.linked_user_id = u.id;
      s.me = { ...me, linked_user_id: u.id };
      return json(201, u);
    }
    if ((hit = m(/^\/spond-accounts\/([^/]+)$/))) {
      const u = s.spondUsers.find((x) => x.id === hit[1]);
      if (!u) return json(404, { detail: "User not found." });
      if (!me.is_admin && me.linked_user_id !== u.id) return json(403, { detail: "Forbidden" });
      if (method === "GET") return json(200, u);
      if (method === "PATCH") { Object.assign(u, body); return json(200, u); }
      if (method === "DELETE") {
        s.spondUsers = s.spondUsers.filter((x) => x !== u);
        s.events = s.events.filter((e) => e.user_id !== u.id);
        return empty(204);
      }
    }

    // Everything below is admin only
    if (!me.is_admin) return json(403, { detail: "Admin privileges required." });

    if (p === "/accounts") {
      if (method === "GET") return json(200, s.accounts);
      if (method === "POST") {
        const a = { id: `a${s.accounts.length + 1}`, username: body.username, is_admin: body.is_admin, linked_user_id: body.linked_user_id };
        s.accounts.push(a);
        s.passwords[a.username] = body.password;
        return json(201, a);
      }
    }
    if ((hit = m(/^\/accounts\/([^/]+)$/))) {
      const a = s.accounts.find((x) => x.id === hit[1]);
      if (!a) return json(404, { detail: "Account not found." });
      if (method === "PATCH") { const { new_password, ...rest } = body; Object.assign(a, rest); return json(200, a); }
      if (method === "DELETE") { s.accounts = s.accounts.filter((x) => x !== a); return empty(204); }
    }

    if (p === "/invites") {
      const view = (i) => ({ id: i.id, note: i.note, created_at: i.created_at, expires_at: i.expires_at, used_at: i.used_at, status: inviteStatus(i) });
      if (method === "GET") return json(200, [...s.invites].reverse().map(view));
      if (method === "POST") {
        const inv = { id: `i${s.invites.length + 1}`, token: `tok-${s.invites.length + 1}-abcdefghijklmnopqrstuvwxyz0123456789`, note: body.note ?? null,
          created_at: new Date(NOW).toISOString(), expires_at: new Date(NOW + (body.days_valid ?? 7) * D).toISOString(), used_at: null };
        s.invites.push(inv);
        return json(201, { ...view(inv), token: inv.token });
      }
    }
    if ((hit = m(/^\/invites\/([^/]+)$/)) && method === "DELETE") {
      const before = s.invites.length;
      s.invites = s.invites.filter((i) => i.id !== hit[1]);
      return before === s.invites.length ? json(404, { detail: "Invite not found." }) : empty(204);
    }
    if (method === "GET" && p === "/admin/stats") return json(200, s.stats);
    if (method === "GET" && p === "/admin/scheduler") return json(200, s.jobs);
    if (method === "GET" && p === "/admin/rsvp-log") {
      const uid = url.searchParams.get("user_id");
      return json(200, uid ? s.rsvpLog.filter((r) => r.user_id === uid) : s.rsvpLog);
    }
    if (method === "GET" && p === "/admin/charts") return json(200, s.charts);
    if (method === "POST" && p === "/admin/sync") return json(202, { detail: "Discovery sync triggered." });
    if ((hit = m(/^\/admin\/scheduler\/([^/]+)$/)) && method === "DELETE") {
      s.jobs = s.jobs.filter((j) => j.job_id !== hit[1]);
      return empty(204);
    }
    if ((hit = m(/^\/admin\/scheduler\/([^/]+)\/fire$/)) && method === "POST") {
      s.jobs = s.jobs.filter((j) => j.job_id !== hit[1]);
      return json(202, { detail: "fired" });
    }

    return json(404, { detail: `No mock for ${method} ${p}` });
  }
}

const test = base.extend({
  api: async ({ page }, use) => {
    const api = new MockApi();
    await api.attach(page);
    await use(api);
  },
});

module.exports = { test, expect, NOW };
