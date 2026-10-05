/**
 * SpondBot service worker — pure decision logic.
 *
 * No access to caches, fetch or the registration: everything takes plain values and
 * returns plain values, so it can be unit tested in Node (tests/frontend/unit/).
 * In the service worker it is loaded with importScripts() and exposed as `SwCore`.
 */
(function (root, factory) {
  const SwCore = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = SwCore;
  else root.SwCore = SwCore;
})(typeof self !== "undefined" ? self : this, function () {
  const OFFLINE_URL = "/offline.html";
  const FONTS_CSS = "/fonts/fonts.css?v=1";
  const ICON = "/icons/icon-192.png";
  const BADGE = "/icons/badge-96.png";

  /** Files stored at install time so the offline page renders without the network. */
  const OFFLINE_JS = "/offline.js?v=1";
  const PRECACHE_URLS = [OFFLINE_URL, OFFLINE_JS, "/icons/logo.png", ICON, BADGE, "/icons/favicon-32.png", FONTS_CSS];

  /** Static files worth keeping for the next start. Everything else goes to the network. */
  const ASSET_PATH = /\.(?:css|js|png|svg|ico|woff2|webmanifest)$/i;

  /**
   * How the service worker handles a request:
   *   "navigate"    page loads: network first, the offline page when the server cannot be reached
   *   "asset"       same-origin static file: answered from cache, refreshed in the background
   *   "passthrough" untouched — the API (never cached: RSVP data must be current), other methods,
   *                 other origins and the service worker script itself
   */
  function classifyRequest(request, origin) {
    if (request.method !== "GET") return "passthrough";
    let url;
    try { url = new URL(request.url); } catch { return "passthrough"; }
    if (url.origin !== origin) return "passthrough";
    if (url.pathname.startsWith("/api/")) return "passthrough";
    if (request.mode === "navigate") return "navigate";
    if (url.pathname === "/sw.js" || url.pathname === "/sw-core.js") return "passthrough";
    return ASSET_PATH.test(url.pathname) ? "asset" : "passthrough";
  }

  /** A reverse proxy answers 502/503/504 when the app behind it is down: same as offline for the user. */
  function isServerDown(status) {
    return status === 502 || status === 503 || status === 504;
  }

  /** Only complete, same-origin answers are worth keeping (not errors or opaque responses). */
  function isCacheable(response) {
    return Boolean(response) && response.status === 200 && response.type === "basic";
  }

  /** Font file URLs referenced by a stylesheet, for precaching the offline page's fonts. */
  function extractFontUrls(css) {
    const urls = [];
    for (const m of String(css).matchAll(/url\(\s*['"]?([^'")\s]+\.woff2)['"]?\s*\)/g)) {
      if (!urls.includes(m[1])) urls.push(m[1]);
    }
    return urls;
  }

  /** Cache names to delete on activate: ours from older versions, never anyone else's. */
  function staleCacheNames(names, current) {
    return names.filter((n) => n.startsWith("spondbot-") && n !== current);
  }

  /** Same-origin path only; anything else (other sites, //host, javascript:) falls back to "/". */
  function safeTargetUrl(raw, origin) {
    if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//")) return "/";
    try {
      const url = new URL(raw, origin);
      return url.origin === origin ? url.pathname + url.search + url.hash : "/";
    } catch {
      return "/";
    }
  }

  const clip = (text, max) => {
    const s = String(text ?? "").trim();
    return s.length > max ? s.slice(0, max - 1) + "…" : s;
  };

  /** Turns the server's push payload (parsed JSON, plain text or nothing) into showNotification() arguments. */
  function notificationFromPush(payload, origin) {
    const p = payload && typeof payload === "object" ? payload : { body: payload };
    const failed = p.outcome === "failed";
    const options = {
      body: clip(p.body, 200),
      icon: ICON,
      badge: BADGE,
      tag: typeof p.tag === "string" && p.tag ? p.tag.slice(0, 100) : "spondbot",
      data: { url: safeTargetUrl(p.url, origin) },
    };
    // A failed answer needs the member's attention, so it stays until they react.
    if (failed) options.requireInteraction = true;
    return { title: clip(p.title, 80) || "SpondBot", options };
  }

  /**
   * Which open window a notification click should reuse: a focused or visible one first,
   * then any. Returns the client, or null when a new window has to be opened.
   */
  function pickClient(clients, origin) {
    const ours = clients.filter((c) => {
      try { return new URL(c.url).origin === origin; } catch { return false; }
    });
    return ours.find((c) => c.focused) || ours.find((c) => c.visibilityState === "visible") || ours[0] || null;
  }

  /** Whether a window already showing `currentUrl` has to be navigated to reach `target`. */
  function needsNavigation(currentUrl, target, origin) {
    try {
      const a = new URL(currentUrl);
      const b = new URL(target, origin);
      return a.pathname !== b.pathname || a.search !== b.search;
    } catch {
      return true;
    }
  }

  return {
    OFFLINE_URL,
    PRECACHE_URLS,
    classifyRequest,
    isServerDown,
    isCacheable,
    extractFontUrls,
    staleCacheNames,
    safeTargetUrl,
    notificationFromPush,
    pickClient,
    needsNavigation,
  };
});
