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
    installMode,
    pushState,
    urlBase64ToBytes,
  };
});
