// Unit tests for frontend/core.js — pure logic shared by the dashboard and admin pages.
// Run with: npm run test:unit   (TZ is pinned to UTC by the npm script)
const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const Core = require("../../../frontend/core.js");

const NOW = Date.parse("2026-09-26T21:00:00Z");
const H = 3600e3;
const D = 24 * H;
const iso = (ms) => new Date(ms).toISOString();

function ev(overrides = {}) {
  return {
    id: overrides.id || "e1",
    user_id: "u1",
    heading: "Training",
    start_timestamp: iso(NOW + 2 * D),
    invite_time: iso(NOW + 3 * H),
    rsvp_date: null,
    user_choice: "accept",
    status: "pending",
    error_message: null,
    ...overrides,
  };
}

describe("eventState", () => {
  test("pending with an automatic answer is armed", () => {
    assert.equal(Core.eventState(ev({ user_choice: "accept" }), NOW), "armed");
    assert.equal(Core.eventState(ev({ user_choice: "decline" }), NOW), "armed");
  });

  test("pending manual event before registration opens is open (needs an answer)", () => {
    assert.equal(Core.eventState(ev({ user_choice: "manual" }), NOW), "open");
  });

  test("pending manual event without invite time is open", () => {
    assert.equal(Core.eventState(ev({ user_choice: "manual", invite_time: null }), NOW), "open");
  });

  test("manual event after registration opened is left to the member", () => {
    const e = ev({ user_choice: "manual", invite_time: iso(NOW - H) });
    assert.equal(Core.eventState(e, NOW), "self");
  });

  test("processing, processed and failed map to sending, sent and failed", () => {
    assert.equal(Core.eventState(ev({ status: "processing" }), NOW), "sending");
    assert.equal(Core.eventState(ev({ status: "processed" }), NOW), "sent");
    assert.equal(Core.eventState(ev({ status: "failed" }), NOW), "failed");
  });
});

describe("labels", () => {
  test("choices use member language", () => {
    assert.deepEqual(Core.CHOICE_LABELS, {
      accept: "Going",
      decline: "Not going",
      manual: "Leave to me",
    });
  });

  test("every state has a label", () => {
    for (const s of ["open", "self", "armed", "sending", "sent", "failed"]) {
      assert.equal(typeof Core.STATE_LABELS[s], "string", s);
    }
  });
});

describe("needsAnswer", () => {
  test("returns open upcoming events ordered by invite time", () => {
    const events = [
      ev({ id: "late", user_choice: "manual", invite_time: iso(NOW + 5 * D) }),
      ev({ id: "armed", user_choice: "accept" }),
      ev({ id: "soon", user_choice: "manual", invite_time: iso(NOW + D) }),
      ev({ id: "noinvite", user_choice: "manual", invite_time: null }),
    ];
    assert.deepEqual(
      Core.needsAnswer(events, NOW).map((e) => e.id),
      ["soon", "late", "noinvite"],
    );
  });

  test("skips events that already started", () => {
    const events = [
      ev({ id: "past", user_choice: "manual", start_timestamp: iso(NOW - H), invite_time: null }),
    ];
    assert.deepEqual(Core.needsAnswer(events, NOW), []);
  });
});

describe("nextAnswer", () => {
  test("returns the armed event whose invite time comes first", () => {
    const events = [
      ev({ id: "b", invite_time: iso(NOW + 2 * D) }),
      ev({ id: "a", invite_time: iso(NOW + 3 * H) }),
      ev({ id: "manual", user_choice: "manual", invite_time: iso(NOW + H) }),
      ev({ id: "sent", status: "processed", invite_time: iso(NOW + H) }),
    ];
    assert.equal(Core.nextAnswer(events, NOW).id, "a");
  });

  test("ignores armed events whose invite time has passed", () => {
    const events = [ev({ id: "due", invite_time: iso(NOW - H) })];
    assert.equal(Core.nextAnswer(events, NOW), null);
  });

  test("returns null when nothing is armed", () => {
    assert.equal(Core.nextAnswer([], NOW), null);
  });
});

describe("splitByTime", () => {
  test("upcoming ascending by start, past descending, undated counts as upcoming", () => {
    const events = [
      ev({ id: "u2", start_timestamp: iso(NOW + 3 * D) }),
      ev({ id: "p1", start_timestamp: iso(NOW - D) }),
      ev({ id: "u1", start_timestamp: iso(NOW + D) }),
      ev({ id: "p2", start_timestamp: iso(NOW - 3 * D) }),
      ev({ id: "nodate", start_timestamp: null }),
    ];
    const { upcoming, past } = Core.splitByTime(events, NOW);
    assert.deepEqual(upcoming.map((e) => e.id), ["u1", "u2", "nodate"]);
    assert.deepEqual(past.map((e) => e.id), ["p1", "p2"]);
  });
});

describe("groupByDay", () => {
  test("groups consecutive events by calendar day and keeps order", () => {
    const events = [
      ev({ id: "a", start_timestamp: "2026-09-28T09:00:00Z" }),
      ev({ id: "b", start_timestamp: "2026-09-28T18:00:00Z" }),
      ev({ id: "c", start_timestamp: "2026-09-30T10:00:00Z" }),
      ev({ id: "d", start_timestamp: null }),
    ];
    const groups = Core.groupByDay(events);
    assert.deepEqual(groups.map((g) => g.key), ["2026-09-28", "2026-09-30", "none"]);
    assert.deepEqual(groups[0].events.map((e) => e.id), ["a", "b"]);
    assert.equal(groups[2].date, null);
  });
});

describe("countByState", () => {
  test("counts every state, including zeros", () => {
    const counts = Core.countByState(
      [ev(), ev({ status: "failed" }), ev({ user_choice: "manual" }), ev()],
      NOW,
    );
    assert.deepEqual(counts, { open: 1, self: 0, armed: 2, sending: 0, sent: 0, failed: 1 });
  });
});

describe("retryPayload", () => {
  test("retries with the member's existing choice", () => {
    assert.deepEqual(Core.retryPayload(ev({ status: "failed", user_choice: "decline" })), {
      user_choice: "decline",
    });
  });

  test("cannot retry an event left to the member", () => {
    assert.equal(Core.retryPayload(ev({ status: "failed", user_choice: "manual" })), null);
  });
});

describe("formatting", () => {
  test("formatDuration picks one readable unit pair", () => {
    assert.equal(Core.formatDuration(30e3), "30 s");
    assert.equal(Core.formatDuration(400), "1 s");
    assert.equal(Core.formatDuration(4 * 60e3 + 10e3), "4 min");
    assert.equal(Core.formatDuration(2 * H + 59 * 60e3), "2h 59m");
    assert.equal(Core.formatDuration(D + 4 * H + 5 * 60e3), "1d 4h");
    assert.equal(Core.formatDuration(-2 * H), "2h 0m");
  });

  test("formatRelative says in / ago", () => {
    assert.equal(Core.formatRelative(iso(NOW + 3 * H), NOW), "in 3h 0m");
    assert.equal(Core.formatRelative(iso(NOW - 5 * 60e3), NOW), "5 min ago");
    assert.equal(Core.formatRelative(null, NOW), "");
  });

  test("formatClock is a zero-padded countdown that never goes negative", () => {
    assert.equal(Core.formatClock(2 * H + 59 * 60e3 + 14e3), "02:59:14");
    assert.equal(Core.formatClock(D + 4 * H + 12 * 60e3 + 9e3), "1d 04:12:09");
    assert.equal(Core.formatClock(-5000), "00:00:00");
  });

  test("dates are short, without the year", () => {
    assert.equal(Core.formatDay("2026-09-28T15:00:00Z"), "Mon 28 Sep");
    assert.equal(Core.formatTime("2026-09-28T15:07:00Z"), "15:07");
    assert.equal(Core.formatStamp("2026-09-28T15:07:03Z"), "28.09 15:07:03");
    assert.equal(Core.formatDay(null), "");
  });

  test("dayParts gives weekday, day number and month for date badges", () => {
    assert.deepEqual(Core.dayParts("2026-10-02T18:00:00Z"), { weekday: "Fri", day: 2, month: "Oct" });
  });

  test("escapeHtml neutralises markup and quotes", () => {
    assert.equal(Core.escapeHtml(`<b a="x">'&`), "&lt;b a=&quot;x&quot;&gt;&#39;&amp;");
    assert.equal(Core.escapeHtml(null), "");
  });
});

describe("timelineScale", () => {
  test("maps a time to a clamped percentage of the range", () => {
    const pct = Core.timelineScale(NOW, NOW + 10 * D);
    assert.equal(pct(NOW), 0);
    assert.equal(pct(NOW + 5 * D), 50);
    assert.equal(pct(NOW + 20 * D), 100);
    assert.equal(pct(NOW - D), 0);
    assert.equal(pct(iso(NOW + 2.5 * D)), 25);
  });
});

describe("buildQueue (admin)", () => {
  const users = [
    { id: "u1", display_name: "Felix" },
    { id: "u2", display_name: "Mara" },
  ];

  test("joins users and scheduler jobs, failed first, sent last", () => {
    const events = [
      ev({ id: "sent", status: "processed", invite_time: iso(NOW - D) }),
      ev({ id: "late", user_id: "u2", invite_time: iso(NOW + 2 * D) }),
      ev({ id: "soon", invite_time: iso(NOW + H) }),
      ev({ id: "bad", status: "failed", invite_time: iso(NOW - 2 * H) }),
    ];
    const jobs = [{ job_id: "sniper_soon", event_id: "soon", fire_at: iso(NOW + H), countdown_s: 3600 }];
    const rows = Core.buildQueue(events, jobs, users, NOW);
    assert.deepEqual(rows.map((r) => r.event.id), ["bad", "soon", "late", "sent"]);
    assert.equal(rows[1].job.job_id, "sniper_soon");
    assert.equal(rows[2].job, null);
    assert.equal(rows[2].userName, "Mara");
    assert.equal(rows[0].state, "failed");
  });

  test("falls back to a short id for unknown users", () => {
    const rows = Core.buildQueue([ev({ user_id: "abcdef123456" })], [], [], NOW);
    assert.equal(rows[0].userName, "abcdef12");
  });
});

describe("groupByUser (admin timeline)", () => {
  test("one lane per user, sorted by name, events sorted by invite time", () => {
    const users = [
      { id: "u2", display_name: "Mara" },
      { id: "u1", display_name: "Felix" },
      { id: "u3", display_name: "Idle" },
    ];
    const events = [
      ev({ id: "m", user_id: "u2" }),
      ev({ id: "f2", user_id: "u1", invite_time: iso(NOW + 2 * D) }),
      ev({ id: "f1", user_id: "u1", invite_time: iso(NOW + D) }),
    ];
    const lanes = Core.groupByUser(events, users);
    assert.deepEqual(lanes.map((l) => l.name), ["Felix", "Idle", "Mara"]);
    assert.deepEqual(lanes[0].events.map((e) => e.id), ["f1", "f2"]);
    assert.deepEqual(lanes[1].events, []);
  });
});

describe("installMode", () => {
  test("an installed app has nothing to install", () => {
    assert.equal(Core.installMode({ standalone: true, ios: true, hasPrompt: true }), "installed");
  });
  test("a captured browser prompt is used first", () => {
    assert.equal(Core.installMode({ standalone: false, ios: false, hasPrompt: true }), "prompt");
  });
  test("iPhone and iPad get the manual steps", () => {
    assert.equal(Core.installMode({ standalone: false, ios: true, hasPrompt: false }), "ios-steps");
  });
  test("otherwise there is no install button", () => {
    assert.equal(Core.installMode({ standalone: false, ios: false, hasPrompt: false }), "none");
  });
});

describe("pushState", () => {
  const base = { serverEnabled: true, supported: true, ios: false, standalone: false, permission: "default", subscribed: false };
  const kind = (o) => Core.pushState({ ...base, ...o }).kind;

  test("without server keys the option is unavailable", () => {
    assert.equal(kind({ serverEnabled: false }), "unavailable");
    assert.equal(kind({ serverEnabled: false, supported: false }), "unavailable");
  });

  test("iPhone in the browser must install first", () => {
    const s = Core.pushState({ ...base, supported: false, ios: true, standalone: false });
    assert.equal(s.kind, "needs-install");
    assert.match(s.message, /Add to Home Screen/);
    assert.equal(s.canEnable, false);
  });

  test("an installed iPhone app that still lacks support is just unsupported", () => {
    assert.equal(kind({ supported: false, ios: true, standalone: true }), "unsupported");
  });

  test("unsupported browsers say so", () => {
    assert.equal(kind({ supported: false }), "unsupported");
  });

  test("a denied permission is explained and cannot be changed from the page", () => {
    const s = Core.pushState({ ...base, permission: "denied" });
    assert.equal(s.kind, "blocked");
    assert.equal(s.canEnable, false);
    assert.equal(s.canDisable, false);
  });

  test("blocked wins even if a stale subscription exists", () => {
    assert.equal(kind({ permission: "denied", subscribed: true }), "blocked");
  });

  test("granted and subscribed is on: can turn off and test", () => {
    const s = Core.pushState({ ...base, permission: "granted", subscribed: true });
    assert.equal(s.kind, "on");
    assert.deepEqual([s.canEnable, s.canDisable, s.canTest], [false, true, true]);
  });

  test("not subscribed is off, even with permission already granted", () => {
    for (const permission of ["default", "granted"]) {
      const s = Core.pushState({ ...base, permission });
      assert.equal(s.kind, "off", permission);
      assert.deepEqual([s.canEnable, s.canDisable, s.canTest], [true, false, false]);
    }
  });

  test("a subscription without permission does not count as on", () => {
    assert.equal(kind({ permission: "default", subscribed: true }), "off");
  });
});

describe("urlBase64ToBytes", () => {
  test("decodes unpadded base64url, including - and _", () => {
    assert.deepEqual([...Core.urlBase64ToBytes("AQID-_8")], [1, 2, 3, 251, 255]);
  });
  test("accepts padded input and the empty string", () => {
    assert.deepEqual([...Core.urlBase64ToBytes("AQID")], [1, 2, 3]);
    assert.deepEqual([...Core.urlBase64ToBytes("")], []);
  });
  test("converts a real 65-byte VAPID public key", () => {
    const key = Buffer.concat([Buffer.from([4]), Buffer.alloc(64, 7)]).toString("base64url");
    const bytes = Core.urlBase64ToBytes(key);
    assert.equal(bytes.length, 65);
    assert.equal(bytes[0], 4);
  });
});

describe("audit helpers", () => {
  const entry = (o) => ({ actor_type: "user", actor_username: "felix", action: "event.choice_set", details: null, target_label: null, target_type: null, target_id: null, ...o });

  test("known actions read like sentences, unlisted writes name the method", () => {
    assert.equal(Core.auditLabel("auth.login.failed"), "Failed sign-in");
    assert.equal(Core.auditLabel("rsvp.sent"), "Answer sent");
    assert.equal(Core.auditLabel("http.delete"), "DELETE request");
    assert.equal(Core.auditLabel("something.new"), "something.new");
    assert.equal(Core.auditLabel(undefined), "Unknown");
  });

  test("who: the login, the bot, or nobody", () => {
    assert.equal(Core.auditWho(entry({})), "felix");
    assert.equal(Core.auditWho(entry({ actor_type: "system", actor_username: null })), "SpondBot");
    assert.equal(Core.auditWho(entry({ actor_type: "anonymous", actor_username: null })), "Not signed in");
    assert.equal(Core.auditWho(entry({ actor_type: "user", actor_username: null })), "Not signed in");
  });

  test("answer changes read as before → after in the member's own words", () => {
    assert.equal(Core.auditDetails(entry({ details: { from: "manual", to: "accept", owner_user_id: "u1" } })), "Leave to me → Going");
    assert.equal(Core.auditDetails(entry({ details: { from: "accept", to: "decline" } })), "Going → Not going");
  });

  test("answers the bot sent read like the old answer log", () => {
    const sent = (d) => entry({ action: "rsvp.sent", actor_type: "system", details: d });
    assert.equal(Core.auditDetails(sent({ member: "Mara Lind", choice: "accept", latency_ms: 41 })), "Mara Lind · Going · 41 ms after opening");
    assert.equal(Core.auditDetails(sent({ member: "Mara Lind", choice: "decline", latency_ms: 112, retries: 1 })), "Mara Lind · Not going · 112 ms after opening · after 1 retry");
    assert.equal(Core.auditDetails(sent({ choice: "accept", latency_ms: 90, retries: 3 })), "Going · 90 ms after opening · after 3 retries");
    assert.equal(Core.auditDetails(sent({ choice: "accept", latency_ms: -12 })), "Going · 12 ms before opening");
    assert.equal(Core.auditDetails(sent({ choice: "accept", latency_ms: 0 })), "Going · 0 ms after opening");
    assert.equal(
      Core.auditDetails(entry({ action: "rsvp.failed", details: { member: "Mara", choice: "accept", error: "403 member not found", latency_ms: null } })),
      "Mara · Going · 403 member not found",
    );
  });

  test("reasons and changes are summarised", () => {
    assert.equal(Core.auditDetails(entry({ action: "auth.login.failed", details: { reason: "unknown_user" } })), "unknown user");
    assert.equal(Core.auditDetails(entry({ action: "auth.login.failed", details: { reason: "something_new" } })), "something new");
    assert.equal(
      Core.auditDetails(entry({ action: "account.updated", details: { is_admin: { from: false, to: true }, password_reset: true } })),
      "is admin: no → yes · password reset",
    );
    assert.equal(Core.auditDetails(entry({ action: "push.test_sent", details: { devices: 2, delivered: 1 } })), "devices: 2 · delivered: 1");
  });

  test("no details, odd details and empty values say nothing", () => {
    assert.equal(Core.auditDetails(entry({ details: null })), "");
    assert.equal(Core.auditDetails(entry({ details: "text" })), "");
    assert.equal(Core.auditDetails(entry({ action: "x.y", details: { a: null, b: false, c: "" } })), "");
  });

  test("targets show what was touched", () => {
    assert.equal(Core.auditTarget(entry({ target_type: "event", target_label: "Training, Hall B" })), "Training, Hall B");
    assert.equal(Core.auditTarget(entry({ target_type: "spond_account", target_label: "Felix Karg" })), "spond account Felix Karg");
    assert.equal(Core.auditTarget(entry({ target_type: "login", target_label: "mara" })), "login mara");
    assert.equal(Core.auditTarget(entry({ target_type: "event", target_id: "123e4567-e89b-12d3" })), "123e4567");
    assert.equal(Core.auditTarget(entry({})), "");
  });

  test("filters become a query string, leaving out what is empty", () => {
    const now = Date.parse("2026-10-05T12:00:00Z");
    assert.equal(Core.auditQuery({}, now), "");
    assert.equal(Core.auditQuery({ q: "  felix ", category: "auth", outcome: "denied", range: "", cursor: "", limit: 50 }, now),
      "q=felix&category=auth&outcome=denied&limit=50");
    assert.equal(Core.auditQuery({ range: "24h" }, now), "since=2026-10-04T12%3A00%3A00.000Z");
    assert.equal(Core.auditQuery({ range: "7d" }, now), "since=2026-09-28T12%3A00%3A00.000Z");
    assert.equal(Core.auditQuery({ range: "all" }, now), "");
    assert.equal(Core.auditQuery({ cursor: "2026-10-05T11:00:00|abc" }, now), "cursor=2026-10-05T11%3A00%3A00%7Cabc");
  });
});
