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

// A well-formed VAPID public key: 65 bytes (uncompressed P-256 point), base64url.
const VAPID_PUBLIC_KEY = Buffer.concat([Buffer.from([4]), Buffer.alloc(64, 9)]).toString("base64url");

const TYPES = {
  ".html": "text/html", ".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml",
  ".png": "image/png", ".woff2": "font/woff2", ".webmanifest": "application/manifest+json",
};

function event(id, user_id, heading, start, invite, user_choice, status, error_message = null) {
  return {
    id, spond_event_id: `sp-${id}`, user_id, heading,
    start_timestamp: start, invite_time: invite, rsvp_date: null,
    user_choice, status, error_message,
    created_at: iso(NOW - 5 * D), updated_at: iso(NOW - D),
  };
}

function auditEntry(id, minutesAgo, action, o = {}) {
  return {
    id, occurred_at: iso(NOW - minutesAgo * 60e3), actor_type: "user", actor_id: "a1", actor_username: "felix",
    actor_is_admin: false, action, category: action.split(".")[0], outcome: "success", target_type: null,
    target_id: null, target_label: null, details: null, ip: "198.51.100.1", user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4)",
    method: "POST", path: "/api/v1/x", status_code: 200, request_id: `req-${id}`, ...o,
  };
}

/** Newest first, like the server returns them. */
function defaultAudit() {
  return [
    auditEntry("l1", 2, "auth.login.success", { target_type: "login", target_label: "felix", path: "/api/v1/auth/login", status_code: 204 }),
    auditEntry("l2", 5, "event.choice_set", {
      method: "PATCH", path: "/api/v1/events/e2", target_type: "event", target_id: "e2", target_label: "League match vs. TSV Nord",
      details: { from: "manual", to: "accept", owner_user_id: "u1", status: "pending" },
    }),
    auditEntry("l3", 9, "auth.login.failed", {
      actor_type: "anonymous", actor_id: null, actor_username: null, actor_is_admin: null, outcome: "denied", status_code: 401,
      ip: "192.0.2.99", target_type: "login", target_label: "admin", path: "/api/v1/auth/login", details: { reason: "wrong_password" },
    }),
    auditEntry("l4", 30, "rsvp.sent", {
      actor_type: "system", actor_id: null, actor_username: null, actor_is_admin: null, ip: null, user_agent: null, method: null,
      path: null, status_code: null, request_id: null, target_type: "event", target_label: "Training, Hall B",
      details: { choice: "accept", member: "Felix Karg", spond_user_id: "u1", spond_event_id: "sp-e1", latency_ms: 41 },
    }),
    auditEntry("l8", 45, "rsvp.failed", {
      actor_type: "system", actor_id: null, actor_username: null, actor_is_admin: null, ip: null, user_agent: null, method: null,
      path: null, status_code: null, request_id: null, outcome: "failed", target_type: "event", target_label: "Autumn tournament",
      details: { choice: "decline", member: "Mara Lind", spond_user_id: "u2", retries: 1, error: "Retry failed: 403 member not found" },
    }),
    auditEntry("l5", 180, "account.created", {
      actor_id: "a2", actor_username: "admin", actor_is_admin: true, target_type: "login", target_label: "mara", status_code: 201,
      details: { is_admin: false, linked_user_id: null },
    }),
    auditEntry("l6", 60 * 24 * 2, "http.get", { outcome: "denied", status_code: 403, method: "GET", path: "/api/v1/admin/stats" }),
    auditEntry("l7", 60 * 24 * 20, "invite.created", {
      actor_id: "a2", actor_username: "admin", actor_is_admin: true, target_type: "invite", target_label: "Jonas", details: { days_valid: 7 },
    }),
  ];
}

function filterAudit(all, q) {
  const text = (q.get("q") || "").toLowerCase();
  return all.filter((e) =>
    (!text || [e.action, e.actor_username, e.target_label, e.ip, e.path, e.details && JSON.stringify(e.details)].some((v) => (v || "").toLowerCase().includes(text))) &&
    (!q.get("category") || e.category === q.get("category")) &&
    (!q.get("outcome") || e.outcome === q.get("outcome")) &&
    (!q.get("since") || Date.parse(e.occurred_at) >= Date.parse(q.get("since"))));
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
    audit: defaultAudit(),
    push: {
      enabled: true, subscriptions: [], delivered: null, testStatus: 200, subscribeStatus: 204,
      // per login, shared by all devices; a login that never saved anything has everything on
      preferences: {}, preferencesStatus: 200,
    },
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

    // Web Push
    if (method === "GET" && p === "/push/config") {
      return json(200, { enabled: s.push.enabled, public_key: s.push.enabled ? VAPID_PUBLIC_KEY : null });
    }
    if (p === "/push/preferences") {
      const KEYS = ["answer_sent", "answer_failed", "reminder_8h", "reminder_4h", "reminder_1h"];
      if (method === "GET") return json(200, { ...Object.fromEntries(KEYS.map((k) => [k, true])), ...(s.push.preferences[me.sub] || {}) });
      if (method === "PUT") {
        if (s.push.preferencesStatus !== 200) return json(s.push.preferencesStatus, { detail: "The server could not save your settings." });
        const valid = body && KEYS.every((k) => typeof body[k] === "boolean") && Object.keys(body).every((k) => KEYS.includes(k));
        if (!valid) return json(422, { detail: "Invalid settings." });
        s.push.preferences[me.sub] = { ...body };
        return json(200, body);
      }
    }
    if (method === "POST" && p === "/push/subscribe") {
      if (s.push.subscribeStatus !== 204) return json(s.push.subscribeStatus, { detail: "Subscribing failed on the server." });
      s.push.subscriptions = s.push.subscriptions.filter((x) => x.endpoint !== body.endpoint);
      s.push.subscriptions.push({ ...body, owner: me.sub });
      return empty(204);
    }
    if (method === "POST" && p === "/push/unsubscribe") {
      s.push.subscriptions = s.push.subscriptions.filter((x) => !(x.endpoint === body.endpoint && x.owner === me.sub));
      return empty(204);
    }
    if (method === "POST" && p === "/push/test") {
      if (s.push.testStatus !== 200) return json(s.push.testStatus, { detail: "Too many requests." });
      const mine = s.push.subscriptions.filter((x) => x.owner === me.sub);
      if (!mine.length) return json(404, { detail: "No device is subscribed. Turn notifications on first." });
      return json(200, { devices: mine.length, delivered: s.push.delivered ?? mine.length });
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
    if ((hit = m(/^\/spond-accounts\/([^/]+)\/password$/)) && method === "PUT") {
      const u = s.spondUsers.find((x) => x.id === hit[1]);
      if (!me.is_admin && me.linked_user_id !== hit[1]) return json(403, { detail: "You can only access your own profile." });
      if (!u) return json(404, { detail: "User not found." });
      if (body.password === "wrong") return json(401, { detail: SPOND_REJECTED });
      return json(200, u);
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

    if (method === "GET" && p === "/admin/audit") {
      const q = url.searchParams;
      const list = filterAudit(s.audit, q);
      const limit = Number(q.get("limit") || 100);
      const start = Number(q.get("cursor") || 0); // the mock's cursor is simply an offset
      return json(200, { items: list.slice(start, start + limit), next_cursor: start + limit < list.length ? String(start + limit) : null });
    }
    if (method === "GET" && p === "/admin/audit/export.csv") {
      const rows = filterAudit(s.audit, url.searchParams);
      const body = ["occurred_at,actor_username,action,outcome", ...rows.map((e) => `${e.occurred_at},${e.actor_username || ""},${e.action},${e.outcome}`)].join("\n");
      return route.fulfill({ status: 200, body, headers: { "content-type": "text/csv", "content-disposition": 'attachment; filename="spondbot-audit.csv"' } });
    }

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

/**
 * Replaces the browser's push machinery with a controllable fake (the real one needs Google's
 * or Apple's servers). Options: supported, permission, grant (answer to the prompt), subscribed
 * (a subscription exists), keyDiffers (the existing one was made with another server key),
 * subscribeError, ios, standalone. Read back with page.evaluate(() => window.__push).
 */
async function installFakePush(page, opts = {}) {
  const o = { supported: true, permission: "default", grant: true, subscribed: false, ...opts };
  await page.addInitScript((o) => {
    const log = [];
    const state = { permission: o.permission, subscription: null, log, prompts: 0 };
    const makeSub = (id, key) => ({
      endpoint: `https://fcm.googleapis.com/fcm/send/${id}`,
      options: { applicationServerKey: key },
      toJSON() { return { endpoint: this.endpoint, expirationTime: null, keys: { p256dh: "p256dh-key", auth: "auth-key" } }; },
      async unsubscribe() {
        state.subscription = null;
        log.push("unsubscribe");
        sessionStorage.setItem("__push_unsubscribed", "1"); // survives the page navigation after sign-out
        return true;
      },
    });
    if (o.subscribed) state.subscription = makeSub("existing", new Uint8Array(o.keyDiffers ? 65 : 0).fill(1).buffer);
    const reg = {
      pushManager: {
        async getSubscription() { return state.subscription; },
        async subscribe(opts) {
          log.push("subscribe");
          if (o.subscribeError) throw new Error(o.subscribeError);
          state.subscription = makeSub("new-device", opts.applicationServerKey.buffer.slice(0));
          return state.subscription;
        },
      },
    };
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        ready: Promise.resolve(reg),
        getRegistration: async () => reg,
        register: () => Promise.reject(new Error("service workers are faked in this test")),
        addEventListener() {},
        controller: null,
      },
    });
    if (o.supported) {
      window.PushManager = function PushManager() {};
      window.Notification = {
        permission: state.permission,
        requestPermission: async () => {
          state.prompts += 1;
          state.permission = o.grant ? "granted" : "denied";
          window.Notification.permission = state.permission;
          return state.permission;
        },
      };
    } else {
      delete window.PushManager;
      delete window.Notification;
    }
    if (o.ios) {
      Object.defineProperty(navigator, "userAgent", {
        configurable: true,
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
      });
    }
    if (o.standalone) {
      const real = window.matchMedia.bind(window);
      window.matchMedia = (q) => (q.includes("display-mode: standalone") ? { matches: true, media: q, addEventListener() {}, removeEventListener() {} } : real(q));
    }
    window.__push = state;
  }, o);
}

const test = base.extend({
  api: async ({ page }, use) => {
    const api = new MockApi();
    await api.attach(page);
    await use(api);
  },
});

module.exports = { test, expect, NOW, installFakePush, auditEntry };
