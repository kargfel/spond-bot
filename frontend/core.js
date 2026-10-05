/**
 * SpondBot Frontend — pure view logic shared by the dashboard and admin pages.
 *
 * No DOM access and no fetch calls: everything here takes plain API objects and
 * returns plain values, so it can be unit tested in Node (tests/frontend/unit/).
 * In the browser it is exposed as the global `Core`.
 */
(function (root, factory) {
  const Core = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = Core;
  else root.Core = Core;
})(typeof self !== "undefined" ? self : this, function () {
  const MIN = 60e3;
  const HR = 60 * MIN;
  const DAY = 24 * HR;
  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const CHOICE_LABELS = { accept: "Going", decline: "Not going", manual: "Leave to me" };

  const STATE_LABELS = {
    open: "No answer set",
    self: "Left to you",
    armed: "Scheduled",
    sending: "Sending",
    sent: "Answered",
    failed: "Failed",
  };

  /* ── Time helpers ──────────────────────────────────────────────── */
  const ms = (v) => (v == null ? null : typeof v === "number" ? v : Date.parse(v));
  const two = (n) => String(n).padStart(2, "0");
  const byInvite = (a, b) => (ms(a.invite_time) ?? Infinity) - (ms(b.invite_time) ?? Infinity);
  const byStart = (a, b) => (ms(a.start_timestamp) ?? Infinity) - (ms(b.start_timestamp) ?? Infinity);

  /* ── Event state ───────────────────────────────────────────────── */
  /**
   * Collapses (status, user_choice, invite_time) into what the member cares about:
   *  open    – no automatic answer set yet, registration has not opened
   *  self    – left to the member and registration is already open
   *  armed   – SpondBot will answer when registration opens
   *  sending – the bot is submitting right now
   *  sent    – answered
   *  failed  – the bot tried and Spond rejected it
   */
  function eventState(ev, now = Date.now()) {
    if (ev.status === "failed") return "failed";
    if (ev.status === "processed") return "sent";
    if (ev.status === "processing") return "sending";
    if (ev.user_choice === "manual") {
      const invite = ms(ev.invite_time);
      return invite != null && invite <= now ? "self" : "open";
    }
    return "armed";
  }

  function isUpcoming(ev, now) {
    const start = ms(ev.start_timestamp);
    return start == null || start >= now;
  }

  function needsAnswer(events, now = Date.now()) {
    return events
      .filter((e) => eventState(e, now) === "open" && isUpcoming(e, now))
      .sort(byInvite);
  }

  function nextAnswer(events, now = Date.now()) {
    return (
      events
        .filter((e) => eventState(e, now) === "armed" && ms(e.invite_time) > now)
        .sort(byInvite)[0] || null
    );
  }

  function splitByTime(events, now = Date.now()) {
    const upcoming = events.filter((e) => isUpcoming(e, now)).sort(byStart);
    const past = events.filter((e) => !isUpcoming(e, now)).sort((a, b) => byStart(b, a));
    return { upcoming, past };
  }

  function dayKey(v) {
    const t = ms(v);
    if (t == null) return "none";
    const d = new Date(t);
    return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
  }

  function groupByDay(events) {
    const groups = [];
    for (const e of events) {
      const key = dayKey(e.start_timestamp);
      let g = groups[groups.length - 1];
      if (!g || g.key !== key) {
        g = { key, date: key === "none" ? null : e.start_timestamp, events: [] };
        groups.push(g);
      }
      g.events.push(e);
    }
    return groups;
  }

  function countByState(events, now = Date.now()) {
    const counts = { open: 0, self: 0, armed: 0, sending: 0, sent: 0, failed: 0 };
    for (const e of events) counts[eventState(e, now)]++;
    return counts;
  }

  function retryPayload(ev) {
    return ev.user_choice === "manual" ? null : { user_choice: ev.user_choice };
  }

  /* ── Formatting ────────────────────────────────────────────────── */
  function formatDuration(delta) {
    const a = Math.abs(delta);
    if (a < MIN) return `${Math.max(1, Math.round(a / 1000))} s`;
    if (a < HR) return `${Math.floor(a / MIN)} min`;
    if (a < DAY) return `${Math.floor(a / HR)}h ${Math.floor((a % HR) / MIN)}m`;
    return `${Math.floor(a / DAY)}d ${Math.floor((a % DAY) / HR)}h`;
  }

  function formatRelative(v, now = Date.now()) {
    const t = ms(v);
    if (t == null) return "";
    const delta = t - now;
    return delta >= 0 ? `in ${formatDuration(delta)}` : `${formatDuration(delta)} ago`;
  }

  function formatClock(delta) {
    const a = Math.max(0, delta);
    const days = Math.floor(a / DAY);
    const clock = `${two(Math.floor((a % DAY) / HR))}:${two(Math.floor((a % HR) / MIN))}:${two(Math.floor((a % MIN) / 1000))}`;
    return days ? `${days}d ${clock}` : clock;
  }

  function formatDay(v) {
    const t = ms(v);
    if (t == null) return "";
    const d = new Date(t);
    return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  }

  function formatTime(v) {
    const t = ms(v);
    if (t == null) return "";
    const d = new Date(t);
    return `${two(d.getHours())}:${two(d.getMinutes())}`;
  }

  function formatStamp(v) {
    const t = ms(v);
    if (t == null) return "";
    const d = new Date(t);
    return `${two(d.getDate())}.${two(d.getMonth() + 1)} ${formatTime(t)}:${two(d.getSeconds())}`;
  }

  function dayParts(v) {
    const d = new Date(ms(v));
    return { weekday: WEEKDAYS[d.getDay()], day: d.getDate(), month: MONTHS[d.getMonth()] };
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  /* ── Admin helpers ─────────────────────────────────────────────── */
  function timelineScale(start, end) {
    const span = end - start;
    return (v) => Math.max(0, Math.min(100, ((ms(v) - start) / span) * 100));
  }

  function userNameLookup(users) {
    const byId = new Map(users.map((u) => [u.id, u.display_name]));
    return (id) => byId.get(id) || String(id || "").slice(0, 8);
  }

  /** Queue rows for the admin console: failed first, then by invite time, sent last. */
  function buildQueue(events, jobs, users, now = Date.now()) {
    const jobsByEvent = new Map(jobs.map((j) => [String(j.event_id), j]));
    const nameOf = userNameLookup(users);
    const rank = { failed: 0, sent: 2 };
    return events
      .map((event) => ({
        event,
        state: eventState(event, now),
        job: jobsByEvent.get(String(event.id)) || null,
        userName: nameOf(event.user_id),
      }))
      .sort((a, b) => {
        const ra = rank[a.state] ?? 1;
        const rb = rank[b.state] ?? 1;
        if (ra !== rb) return ra - rb;
        return ra === 2 ? byInvite(b.event, a.event) : byInvite(a.event, b.event);
      });
  }

  /** Timeline lanes for the admin: one per Spond account, sorted by name. */
  function groupByUser(events, users) {
    return users
      .map((u) => ({
        id: u.id,
        name: u.display_name,
        events: events.filter((e) => e.user_id === u.id).sort(byInvite),
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  /* ── Audit trail ───────────────────────────────────────────────── */

  const AUDIT_LABELS = {
    "auth.login.success": "Signed in",
    "auth.login.failed": "Failed sign-in",
    "auth.logout": "Signed out",
    "auth.password_changed": "Changed own password",
    "account.created": "Created login",
    "account.updated": "Updated login",
    "account.deleted": "Deleted login",
    "spond_account.created": "Added Spond account",
    "spond_account.connected": "Connected Spond account",
    "spond_account.updated": "Updated Spond account",
    "spond_account.password_updated": "Replaced Spond password",
    "spond_account.deleted": "Deleted Spond account",
    "invite.created": "Created invite",
    "invite.revoked": "Revoked invite",
    "invite.accepted": "Accepted invite",
    "invite.accept_failed": "Failed to accept invite",
    "event.choice_set": "Set answer",
    "scheduler.job_cancelled": "Disarmed answer",
    "scheduler.job_fired": "Sent answer now",
    "discovery.triggered": "Started sync",
    "discovery.completed": "Sync finished",
    "push.subscribed": "Turned on notifications",
    "push.unsubscribed": "Turned off notifications",
    "push.test_sent": "Sent test notification",
    "rsvp.sent": "Answer sent",
    "rsvp.failed": "Answer failed",
    "audit.exported": "Exported audit log",
    "audit.purged": "Removed old audit entries",
  };

  const AUDIT_REASONS = {
    unknown_user: "unknown user",
    wrong_password: "wrong password",
    wrong_current_password: "wrong current password",
    spond_rejected_credentials: "Spond rejected the login",
    spond_rejected: "Spond rejected the login",
    spond_account_unavailable: "Spond account unavailable",
    different_profile: "belongs to a different Spond profile",
    username_taken: "username taken",
    invite_unknown: "invite not valid",
    invite_used: "invite already used",
    invite_expired: "invite expired",
  };

  /** "auth.login.failed" → "Failed sign-in"; unlisted writes ("http.post") → "POST request". */
  function auditLabel(action) {
    if (AUDIT_LABELS[action]) return AUDIT_LABELS[action];
    if (typeof action === "string" && action.startsWith("http.")) return `${action.slice(5).toUpperCase()} request`;
    return action || "Unknown";
  }

  /** Who did it: the login, the bot, or nobody (not signed in). */
  function auditWho(entry) {
    if (entry.actor_type === "system") return "SpondBot";
    if (entry.actor_type === "anonymous" || !entry.actor_username) return "Not signed in";
    return entry.actor_username;
  }

  const humanValue = (v) => {
    if (v === true) return "yes";
    if (v === false) return "no";
    if (v === null || v === undefined || v === "") return "none";
    return CHOICE_LABELS[v] || String(v);
  };

  /** One-line summary of an entry's details, e.g. "Leave to me → Going". Empty when there is nothing to add. */
  function auditDetails(entry) {
    const d = entry.details;
    if (!d || typeof d !== "object") return "";
    if (entry.action === "event.choice_set") return `${humanValue(d.from)} → ${humanValue(d.to)}`;
    const parts = [];
    for (const [key, value] of Object.entries(d)) {
      if (key === "reason") parts.push(AUDIT_REASONS[value] || String(value).replace(/_/g, " "));
      else if (value && typeof value === "object" && "from" in value && "to" in value) {
        parts.push(`${key.replace(/_/g, " ")}: ${humanValue(value.from)} → ${humanValue(value.to)}`);
      } else if (value === true) parts.push(key.replace(/_/g, " "));
      else if (value !== null && value !== undefined && value !== false && value !== "") parts.push(`${key.replace(/_/g, " ")}: ${humanValue(value)}`);
    }
    return parts.join(" · ");
  }

  /** What was acted on: "Training, Hall B", "login felix", or empty. */
  function auditTarget(entry) {
    if (entry.target_label) return entry.target_type && entry.target_type !== "event" ? `${entry.target_type.replace(/_/g, " ")} ${entry.target_label}` : entry.target_label;
    return entry.target_id ? String(entry.target_id).slice(0, 8) : "";
  }

  /** Query string for /admin/audit from the filter form values (empty values are left out). */
  function auditQuery({ q, category, outcome, range, cursor, limit }, now = Date.now()) {
    const params = new URLSearchParams();
    if (q && q.trim()) params.set("q", q.trim());
    if (category) params.set("category", category);
    if (outcome) params.set("outcome", outcome);
    const spans = { "1h": 3600e3, "24h": 24 * 3600e3, "7d": 7 * 24 * 3600e3, "30d": 30 * 24 * 3600e3 };
    if (spans[range]) params.set("since", new Date(now - spans[range]).toISOString());
    if (cursor) params.set("cursor", cursor);
    if (limit) params.set("limit", String(limit));
    return params.toString();
  }

  /* ── App install & notifications ───────────────────────────────── */

  /** What the member can do to install the app: "installed" | "prompt" | "ios-steps" | "none". */
  function installMode({ standalone, ios, hasPrompt }) {
    if (standalone) return "installed";
    if (hasPrompt) return "prompt";
    if (ios) return "ios-steps";
    return "none";
  }

  /**
   * State of the notification setting for this device, with the text to show.
   * kind: unavailable | needs-install | unsupported | blocked | on | off
   */
  function pushState({ serverEnabled, supported, ios, standalone, permission, subscribed }) {
    const state = (kind, message, extra = {}) => ({ kind, message, canEnable: false, canDisable: false, canTest: false, ...extra });
    if (!serverEnabled) return state("unavailable", "Notifications are not set up on this SpondBot server.");
    if (!supported && ios && !standalone) {
      return state("needs-install",
        "On iPhone and iPad, add SpondBot to your Home Screen first: tap Share, then Add to Home Screen. Open it from there to turn notifications on.");
    }
    if (!supported) return state("unsupported", "This browser can't show notifications from SpondBot.");
    if (permission === "denied") {
      return state("blocked",
        "Notifications are blocked for SpondBot. Allow them in your browser or system settings, then come back here.");
    }
    if (subscribed && permission === "granted") {
      return state("on",
        "Notifications are on for this device. You get one when SpondBot sends an answer for you, or fails to.",
        { canDisable: true, canTest: true });
    }
    return state("off",
      "Get a notification on this device whenever SpondBot answers an event for you, or fails to.",
      { canEnable: true });
  }

  /** The server's VAPID public key (base64url) as the bytes PushManager.subscribe() wants. */
  function urlBase64ToBytes(base64url) {
    const padded = base64url + "=".repeat((4 - (base64url.length % 4)) % 4);
    const binary = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(binary, (c) => c.charCodeAt(0));
  }

  return {
    CHOICE_LABELS,
    STATE_LABELS,
    eventState,
    needsAnswer,
    nextAnswer,
    splitByTime,
    groupByDay,
    countByState,
    retryPayload,
    formatDuration,
    formatRelative,
    formatClock,
    formatDay,
    formatTime,
    formatStamp,
    dayParts,
    escapeHtml,
    timelineScale,
    buildQueue,
    groupByUser,
    auditLabel,
    auditWho,
    auditDetails,
    auditTarget,
    auditQuery,
    installMode,
    pushState,
    urlBase64ToBytes,
  };
});
