// End-to-end tests for the installable app: service worker, offline page and update banner.
// Unlike the other specs these run against a real local HTTP server serving frontend/, because
// a service worker only exists for real network requests. "Offline" means the server is stopped.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

test.use({ serviceWorkers: "allow" });

const FRONTEND = path.resolve(__dirname, "../../../frontend");
const TYPES = {
  ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png",
  ".woff2": "font/woff2", ".webmanifest": "application/manifest+json",
};
const PAGES = { "/": "index.html", "/dashboard": "dashboard.html", "/admin": "admin.html", "/join": "join.html" };

/** A small stand-in for the SpondBot server: static files, 401 for the API, and a health probe. */
function createSite() {
  const state = { swNote: "", down: false };
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (status, type, body, headers = {}) => {
      res.writeHead(status, { "content-type": type, ...headers });
      res.end(body);
    };
    if (state.down && url.pathname !== "/sw.js" && url.pathname !== "/sw-core.js") {
      return send(502, "text/plain", "Bad gateway");
    }
    if (url.pathname === "/api/v1/health") return send(200, "application/json", '{"status":"ok"}');
    if (url.pathname.startsWith("/api/")) return send(401, "application/json", '{"detail":"Not authenticated"}');
    const rel = PAGES[url.pathname] || url.pathname.slice(1);
    const file = path.join(FRONTEND, rel);
    if (!file.startsWith(FRONTEND) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      return send(404, "text/plain", "not found");
    }
    let body = fs.readFileSync(file);
    if (rel === "sw.js") body = body + `\n// ${state.swNote}`;
    // Like production: the worker, its helper and the manifest must be revalidated, while other files
    // carry only an old Last-Modified, which lets browsers cache them heuristically for hours or days.
    const revalidate = ["sw.js", "sw-core.js", "manifest.webmanifest"].includes(rel);
    return send(200, TYPES[path.extname(file)] || "application/octet-stream", body,
      revalidate ? { "cache-control": "no-cache" } : { "last-modified": "Mon, 01 Sep 2025 08:00:00 GMT" });
  });
  let port = 0;
  return {
    state,
    get url() { return `http://localhost:${port}`; },
    start: () => new Promise((resolve) => server.listen(port, "localhost", () => { port = server.address().port; resolve(); })),
    stop: () => new Promise((resolve) => { server.close(resolve); server.closeAllConnections(); }),
  };
}

test.describe("service worker", () => {
  let site;
  test.beforeEach(async () => {
    site = createSite();
    await site.start();
  });
  test.afterEach(async () => {
    await site.stop().catch(() => {});
  });

  /** Opens the sign-in page and waits until the worker controls it. */
  async function controlled(page) {
    await page.goto(site.url + "/");
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload();
    await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
    await page.waitForLoadState("networkidle"); // no request of the sign-in page may still be in flight
  }

  test("registers at the root scope and takes control of the page", async ({ page }) => {
    await controlled(page);
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.getRegistration()).scope);
    expect(scope).toBe(site.url + "/");
  });

  test("keeps the offline page, its icons and every font in the cache", async ({ page }) => {
    await controlled(page);
    const urls = await page.evaluate(async () => {
      const cache = await caches.open("spondbot-v3");
      return (await cache.keys()).map((r) => new URL(r.url).pathname);
    });
    expect(urls).toEqual(expect.arrayContaining(["/offline.html", "/offline.js", "/icons/logo.png", "/icons/icon-192.png", "/fonts/fonts.css"]));
    expect(urls.filter((u) => u.endsWith(".woff2")).length).toBeGreaterThanOrEqual(9);
  });

  test("stores static files for the next start but never an API answer", async ({ page }) => {
    await controlled(page);
    await page.evaluate(() => fetch("/api/v1/auth/me"));
    const urls = await page.evaluate(async () => {
      const out = [];
      for (const name of await caches.keys()) for (const r of await (await caches.open(name)).keys()) out.push(new URL(r.url).pathname);
      return out;
    });
    expect(urls.some((u) => u.startsWith("/api/"))).toBe(false);
    expect(urls).toEqual(expect.arrayContaining(["/app.js", "/member.css", "/core.js"]));
  });

  test("shows the offline page, in place, when the network is gone", async ({ page }) => {
    await controlled(page);
    await site.stop();
    await page.goto(site.url + "/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("pulled");
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole("status")).toContainText("Waiting for a connection");
    await expect(page.getByText("Answers you already set are safe.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeEnabled();
    await expect(page.getByRole("img", { name: "SpondBot" })).toBeVisible();
  });

  test("a page the browser still holds in its HTTP cache does not hide that the server is gone", async ({ page }) => {
    await controlled(page);
    await page.goto(site.url + "/dashboard"); // visited while online: now sits in the HTTP cache with an old Last-Modified
    await page.waitForLoadState("networkidle");
    await site.stop();
    await page.goto(site.url + "/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("pulled");
    await expect(page.getByRole("status")).toContainText("Waiting for a connection");
  });

  test("the offline page loads nothing from the network", async ({ page }) => {
    await controlled(page);
    await site.stop();
    const failed = [];
    page.on("requestfailed", (r) => failed.push(new URL(r.url()).pathname));
    await page.goto(site.url + "/admin");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("pulled");
    await page.waitForTimeout(300);
    // The only requests allowed to fail are the page's own connection probe.
    expect(failed.filter((p) => p !== "/api/v1/health")).toEqual([]);
    const fontsReady = await page.evaluate(async () => {
      await document.fonts.ready;
      return [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family);
    });
    expect(fontsReady.join()).toContain("Barlow Condensed");
  });

  test("API calls fail when offline instead of returning old data", async ({ page }) => {
    await controlled(page);
    await page.evaluate(() => fetch("/api/v1/events"));
    await site.stop();
    const result = await page.evaluate(() => fetch("/api/v1/events").then(() => "answered", () => "failed"));
    expect(result).toBe("failed");
  });

  test("comes back by itself once the server is reachable again", async ({ page }) => {
    await controlled(page);
    await site.stop();
    await page.goto(site.url + "/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("pulled");
    await site.start();
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("a gateway error is told apart from being offline", async ({ page }) => {
    await controlled(page);
    site.state.down = true;
    await page.goto(site.url + "/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("moment");
    await expect(page.getByRole("status")).toContainText("Server not responding");
    await expect(page.getByText("Answers you already set are safe.")).toBeHidden();
    site.state.down = false;
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("opened directly while online, the offline page sends you to the app", async ({ page }) => {
    await page.goto(site.url + "/offline.html");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page).toHaveURL(site.url + "/");
  });

  test("the first install does not announce an update", async ({ page }) => {
    await controlled(page);
    await expect(page.locator("#update-banner")).toHaveCount(0);
  });

  test("a new worker waits and is offered as a reload banner", async ({ page }) => {
    await controlled(page);
    site.state.swNote = "version 2";
    await page.evaluate(async () => { await (await navigator.serviceWorker.getRegistration()).update(); });
    const banner = page.locator("#update-banner");
    await expect(banner).toContainText("A new version of SpondBot is ready.");
    expect(await page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration()).waiting))).toBe(true);

    await banner.getByRole("button", { name: "Reload" }).click();
    await expect(page.locator("#update-banner")).toHaveCount(0);
    await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration()).waiting))).toBe(false);
    await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
  });
});
