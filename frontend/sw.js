/**
 * SpondBot service worker.
 *
 *  - Page loads go to the network; when the server cannot be reached the offline page is shown.
 *  - Static files (CSS, JS, icons, fonts) are answered from cache and refreshed in the background.
 *  - /api/ is never touched: answers and tokens must always come from the server.
 *  - Web Push: shows a notification when an RSVP was sent (or failed) and opens the dashboard on click.
 *
 * Bump VERSION when this file's caching rules or the precache list change, and the ?v= on the
 * importScripts() line when sw-core.js changes (browsers only re-check the URL they know). A new worker waits
 * until the page asks it to take over (see registerServiceWorker() in app.js), so open pages never
 * run old scripts against new caches. Pure logic lives in sw-core.js (unit tested).
 */
importScripts("/sw-core.js?v=1");

const VERSION = "1";
const CACHE = `spondbot-v${VERSION}`;
const MAX_RUNTIME_ENTRIES = 120;

self.addEventListener("install", (event) => {
  event.waitUntil(precache());
});

async function precache() {
  const cache = await caches.open(CACHE);
  // "reload" skips the HTTP cache so a new worker never precaches stale copies.
  const fresh = (urls) => urls.map((url) => new Request(url, { cache: "reload" }));
  await cache.addAll(fresh(SwCore.PRECACHE_URLS));
  // The offline page uses the site fonts, so keep the font files too.
  const css = await (await cache.match(SwCore.PRECACHE_URLS.find((u) => u.includes("fonts.css")))).text();
  await cache.addAll(fresh(SwCore.extractFontUrls(css)));
}

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const stale = SwCore.staleCacheNames(await caches.keys(), CACHE);
    await Promise.all(stale.map((name) => caches.delete(name)));
    if (self.registration.navigationPreload) await self.registration.navigationPreload.enable();
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

/* ── Fetch ─────────────────────────────────────────────────────────── */
self.addEventListener("fetch", (event) => {
  const kind = SwCore.classifyRequest(event.request, self.location.origin);
  if (kind === "navigate") event.respondWith(navigate(event));
  else if (kind === "asset") event.respondWith(staleWhileRevalidate(event));
});

async function offlinePage(fallback) {
  return (await caches.match(SwCore.OFFLINE_URL)) || fallback || Response.error();
}

async function navigate(event) {
  try {
    const response = (await event.preloadResponse) || (await fetch(event.request));
    return SwCore.isServerDown(response.status) ? offlinePage(response) : response;
  } catch {
    return offlinePage();
  }
}

async function staleWhileRevalidate(event) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(event.request);
  const refresh = fetch(event.request).then((response) => {
    if (SwCore.isCacheable(response)) {
      cache.put(event.request, response.clone()).then(() => trim(cache));
    }
    return response;
  });
  if (cached) {
    event.waitUntil(refresh.catch(() => {}));
    return cached;
  }
  return refresh;
}

/** Keeps the runtime cache bounded: oldest entries (insertion order) go first. */
async function trim(cache) {
  const keys = await cache.keys();
  const precached = SwCore.PRECACHE_URLS.map((u) => new URL(u, self.location.origin).href);
  const evictable = keys.filter((r) => !precached.includes(r.url) && !r.url.endsWith(".woff2"));
  for (const request of evictable.slice(0, Math.max(0, keys.length - MAX_RUNTIME_ENTRIES))) {
    await cache.delete(request);
  }
}

/* ── Push ──────────────────────────────────────────────────────────── */
self.addEventListener("push", (event) => {
  let payload = null;
  if (event.data) {
    try { payload = event.data.json(); } catch { payload = event.data.text(); }
  }
  const { title, options } = SwCore.notificationFromPush(payload, self.location.origin);
  // Always shown, even with the app open: browsers (iOS above all) require a visible notification per push.
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const origin = self.location.origin;
  const target = SwCore.safeTargetUrl(event.notification.data && event.notification.data.url, origin);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const client = SwCore.pickClient(windows, origin);
    if (!client) return self.clients.openWindow(target);
    const focused = await client.focus();
    if (SwCore.needsNavigation(client.url, target, origin) && focused && "navigate" in focused) {
      return focused.navigate(target);
    }
    return focused;
  })());
});

/** The browser rotated the push subscription: store the new one so notifications keep arriving. */
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil((async () => {
    const old = event.oldSubscription;
    const key = old && old.options && old.options.applicationServerKey;
    const subscription = event.newSubscription ||
      (key ? await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }) : null);
    if (!subscription) return;
    await fetch("/api/v1/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(subscription.toJSON()),
    });
  })());
});
