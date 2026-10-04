// Unit tests for frontend/sw-core.js — the service worker's pure decision logic.
// Run with: npm run test:unit
const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Sw = require("../../../frontend/sw-core.js");

const ORIGIN = "https://bot.example.com";
const req = (url, method = "GET", mode = "no-cors") => ({ url: url.startsWith("http") ? url : ORIGIN + url, method, mode });

describe("classifyRequest", () => {
  test("page loads are handled with the offline fallback", () => {
    for (const p of ["/", "/dashboard", "/admin", "/join", "/join#abc", "/docs"]) {
      assert.equal(Sw.classifyRequest(req(p, "GET", "navigate"), ORIGIN), "navigate", p);
    }
  });

  test("the API is never touched, whatever the request mode", () => {
    for (const mode of ["cors", "no-cors", "same-origin", "navigate"]) {
      assert.equal(Sw.classifyRequest(req("/api/v1/events", "GET", mode), ORIGIN), "passthrough", mode);
    }
    assert.equal(Sw.classifyRequest(req("/api/v1/user/stream", "GET", "cors"), ORIGIN), "passthrough");
  });

  test("same-origin static files use the asset cache", () => {
    for (const p of ["/app.js?v=9", "/member.css?v=2", "/icons/icon-192.png", "/fonts/x.woff2", "/manifest.webmanifest", "/core.js"]) {
      assert.equal(Sw.classifyRequest(req(p), ORIGIN), "asset", p);
    }
  });

  test("anything that is not a GET goes straight to the network", () => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      assert.equal(Sw.classifyRequest(req("/app.js", method), ORIGIN), "passthrough", method);
      assert.equal(Sw.classifyRequest(req("/dashboard", method, "navigate"), ORIGIN), "passthrough", method);
    }
  });

  test("other origins (CDN scripts, push services) are left alone", () => {
    assert.equal(Sw.classifyRequest(req("https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"), ORIGIN), "passthrough");
    assert.equal(Sw.classifyRequest(req("https://evil.example/app.js", "GET", "navigate"), ORIGIN), "passthrough");
  });

  test("the worker's own scripts are never served from its cache", () => {
    assert.equal(Sw.classifyRequest(req("/sw.js"), ORIGIN), "passthrough");
    assert.equal(Sw.classifyRequest(req("/sw-core.js?v=1"), ORIGIN), "passthrough");
  });

  test("unknown file types and malformed URLs pass through", () => {
    assert.equal(Sw.classifyRequest(req("/openapi.json"), ORIGIN), "passthrough");
    assert.equal(Sw.classifyRequest({ url: "not a url", method: "GET", mode: "navigate" }, ORIGIN), "passthrough");
  });
});

describe("response rules", () => {
  test("gateway errors mean the app is down", () => {
    for (const s of [502, 503, 504]) assert.equal(Sw.isServerDown(s), true, String(s));
    for (const s of [200, 301, 401, 404, 500]) assert.equal(Sw.isServerDown(s), false, String(s));
  });

  test("only complete same-origin answers are cached", () => {
    assert.equal(Sw.isCacheable({ status: 200, type: "basic" }), true);
    assert.equal(Sw.isCacheable({ status: 404, type: "basic" }), false);
    assert.equal(Sw.isCacheable({ status: 200, type: "opaque" }), false);
    assert.equal(Sw.isCacheable({ status: 206, type: "basic" }), false);
    assert.equal(Sw.isCacheable(null), false);
  });
});

describe("cache housekeeping", () => {
  test("old SpondBot caches go, the current one and foreign caches stay", () => {
    const names = ["spondbot-v1", "spondbot-v2", "other-app", "spondbot-v0"];
    assert.deepEqual(Sw.staleCacheNames(names, "spondbot-v2"), ["spondbot-v1", "spondbot-v0"]);
  });

  test("font files are found in a stylesheet once each", () => {
    const css = "src: url(/fonts/a.woff2) format('woff2'); src: url('/fonts/b.woff2'); src: url(/fonts/a.woff2); src: url(x.png)";
    assert.deepEqual(Sw.extractFontUrls(css), ["/fonts/a.woff2", "/fonts/b.woff2"]);
  });

  test("every precached file exists in frontend/ (a missing one would break the install)", () => {
    for (const url of Sw.PRECACHE_URLS) {
      const file = path.join(__dirname, "../../../frontend", new URL(url, "http://x").pathname);
      assert.ok(fs.existsSync(file), `${url} is precached but missing`);
    }
    assert.ok(Sw.PRECACHE_URLS.includes(Sw.OFFLINE_URL));
  });

  test("the precached font stylesheet references fonts that exist", () => {
    const fontsCss = fs.readFileSync(path.join(__dirname, "../../../frontend/fonts/fonts.css"), "utf8");
    const urls = Sw.extractFontUrls(fontsCss);
    assert.ok(urls.length >= 9);
    for (const u of urls) assert.ok(fs.existsSync(path.join(__dirname, "../../../frontend", u)), u);
  });
});

describe("notificationFromPush", () => {
  test("a success payload becomes a quiet notification that opens the dashboard", () => {
    const { title, options } = Sw.notificationFromPush(
      { title: "Answer sent: Going", body: "Training, Hall B", tag: "rsvp-e1", url: "/dashboard", outcome: "success" },
      ORIGIN,
    );
    assert.equal(title, "Answer sent: Going");
    assert.equal(options.body, "Training, Hall B");
    assert.equal(options.tag, "rsvp-e1");
    assert.deepEqual(options.data, { url: "/dashboard" });
    assert.equal(options.icon, "/icons/icon-192.png");
    assert.equal(options.badge, "/icons/badge-96.png");
    assert.equal(options.requireInteraction, undefined);
  });

  test("a failure stays on screen until the member reacts", () => {
    const { options } = Sw.notificationFromPush({ title: "x", outcome: "failed" }, ORIGIN);
    assert.equal(options.requireInteraction, true);
  });

  test("missing or odd payloads still produce a usable notification", () => {
    assert.equal(Sw.notificationFromPush(null, ORIGIN).title, "SpondBot");
    assert.equal(Sw.notificationFromPush(undefined, ORIGIN).options.tag, "spondbot");
    const plain = Sw.notificationFromPush("Plain text push", ORIGIN);
    assert.equal(plain.options.body, "Plain text push");
    assert.equal(plain.title, "SpondBot");
  });

  test("long texts are clipped to what notifications can show", () => {
    const { title, options } = Sw.notificationFromPush({ title: "t".repeat(300), body: "b".repeat(900) }, ORIGIN);
    assert.ok(title.length <= 80 && title.endsWith("…"));
    assert.ok(options.body.length <= 200 && options.body.endsWith("…"));
  });

  test("a payload can never send the click to another site", () => {
    for (const url of ["https://evil.example/", "//evil.example/x", "javascript:alert(1)", "dashboard", 42, null]) {
      assert.equal(Sw.notificationFromPush({ url }, ORIGIN).options.data.url, "/", String(url));
    }
    assert.equal(Sw.notificationFromPush({ url: "/dashboard?x=1#top" }, ORIGIN).options.data.url, "/dashboard?x=1#top");
  });
});

describe("notification clicks", () => {
  const win = (url, extra = {}) => ({ url, focused: false, visibilityState: "hidden", ...extra });

  test("a focused window wins, then a visible one, then any of ours", () => {
    const a = win(ORIGIN + "/admin");
    const b = win(ORIGIN + "/dashboard", { visibilityState: "visible" });
    const c = win(ORIGIN + "/", { focused: true });
    assert.equal(Sw.pickClient([a, b, c], ORIGIN), c);
    assert.equal(Sw.pickClient([a, b], ORIGIN), b);
    assert.equal(Sw.pickClient([a], ORIGIN), a);
  });

  test("windows on other origins are ignored; none means open a new one", () => {
    assert.equal(Sw.pickClient([win("https://other.example/")], ORIGIN), null);
    assert.equal(Sw.pickClient([], ORIGIN), null);
  });

  test("an open window is only navigated when it shows a different page", () => {
    assert.equal(Sw.needsNavigation(ORIGIN + "/dashboard", "/dashboard", ORIGIN), false);
    assert.equal(Sw.needsNavigation(ORIGIN + "/dashboard#x", "/dashboard", ORIGIN), false);
    assert.equal(Sw.needsNavigation(ORIGIN + "/admin", "/dashboard", ORIGIN), true);
    assert.equal(Sw.needsNavigation("garbage", "/dashboard", ORIGIN), true);
  });
});

describe("safeTargetUrl", () => {
  test("keeps same-origin paths with query and hash", () => {
    assert.equal(Sw.safeTargetUrl("/dashboard", ORIGIN), "/dashboard");
    assert.equal(Sw.safeTargetUrl("/join#tok", ORIGIN), "/join#tok");
  });
  test("falls back to the start page for everything else", () => {
    for (const bad of ["", "http://x", "//x", "\\\\x", undefined]) assert.equal(Sw.safeTargetUrl(bad, ORIGIN), "/", String(bad));
  });
});
