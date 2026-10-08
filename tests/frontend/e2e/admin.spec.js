// Admin panel: dispatch console (queue, countdown, counters) plus a
// per-account timeline, users, audit log and charts.
const { test, expect } = require("./fixtures");

const qrow = (page, title) => page.getByTestId("queue-row").filter({ hasText: title });

test.describe("admin panel", () => {
  test.beforeEach(async ({ api }) => {
    api.asAdmin();
  });

  test("members are sent to their dashboard", async ({ page, api }) => {
    api.asMember();
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("hero counts down to the next armed answer across all accounts", async ({ page }) => {
    await page.goto("/admin");
    const hero = page.getByTestId("next-fire");
    await expect(hero).toContainText("Sunday league");
    await expect(hero).toContainText("Mara Lind");
    await expect(hero.getByTestId("countdown")).toHaveText("02:00:00");
  });

  test("counters show queue health and timing", async ({ page }) => {
    await page.goto("/admin");
    const counters = page.getByTestId("counters");
    await expect(counters.getByTestId("count-armed")).toHaveText("3");
    await expect(counters.getByTestId("count-failed")).toHaveText("1");
    await expect(counters.getByTestId("count-p50")).toHaveText("38 ms");
    await expect(counters.getByTestId("count-p95")).toHaveText("112 ms");
    await expect(counters).toContainText("12 min ago");
  });

  test("queue lists upcoming events for every account with their answer", async ({ page }) => {
    await page.goto("/admin");
    const bbq = qrow(page, "Club barbecue");
    await expect(bbq).toContainText("Felix Karg");
    await expect(bbq).toContainText("armed");
    await expect(bbq.getByRole("button", { name: "Not going", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(qrow(page, "Sunday league")).toContainText("Mara Lind");
    await expect(qrow(page, "Old friendly match")).toHaveCount(0);
  });

  test("changing an answer from the queue saves it", async ({ page, api }) => {
    await page.goto("/admin");
    await qrow(page, "Club barbecue").getByRole("button", { name: "Going", exact: true }).click();
    await expect(qrow(page, "Club barbecue").getByRole("button", { name: "Going", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(api.callsTo("PATCH", "/events/e4")[0].body).toEqual({ user_choice: "accept" });
  });

  test("retry keeps the member's own answer instead of forcing accept", async ({ page, api }) => {
    await page.goto("/admin");
    const row = qrow(page, "Autumn tournament");
    await expect(row).toContainText("member not found in group");
    await row.getByRole("button", { name: "Retry" }).click();
    expect(api.callsTo("PATCH", "/events/e5")[0].body).toEqual({ user_choice: "decline" });
    await expect(qrow(page, "Autumn tournament")).toContainText("armed");
  });

  test("an armed answer can be sent immediately after confirming", async ({ page, api }) => {
    await page.goto("/admin");
    await qrow(page, "League match vs. TSV Nord").getByRole("button", { name: "Send now" }).click();
    const dialog = page.getByRole("dialog", { name: "Send this answer now?" });
    await expect(dialog).toContainText("League match vs. TSV Nord");
    await dialog.getByRole("button", { name: "Send now" }).click();
    await expect(dialog).toBeHidden();
    await expect.poll(() => api.callsTo("POST", "/admin/scheduler/sniper_e2/fire").length).toBe(1);
  });

  test("cancelling the confirmation does nothing", async ({ page, api }) => {
    await page.goto("/admin");
    await qrow(page, "League match vs. TSV Nord").getByRole("button", { name: "Disarm" }).click();
    const dialog = page.getByRole("dialog", { name: "Disarm this answer?" });
    await dialog.getByRole("button", { name: "Keep it" }).click();
    await expect(dialog).toBeHidden();
    expect(api.callsTo("DELETE", "/admin/scheduler/sniper_e2")).toHaveLength(0);
  });

  test("an armed answer can be disarmed", async ({ page, api }) => {
    await page.goto("/admin");
    await qrow(page, "League match vs. TSV Nord").getByRole("button", { name: "Disarm" }).click();
    await page.getByRole("dialog", { name: "Disarm this answer?" }).getByRole("button", { name: "Disarm" }).click();
    await expect.poll(() => api.callsTo("DELETE", "/admin/scheduler/sniper_e2").length).toBe(1);
  });

  test("queue can be filtered by account, state and text", async ({ page }) => {
    await page.goto("/admin");
    await page.getByRole("combobox", { name: "Account" }).selectOption({ label: "Mara Lind" });
    await expect(page.getByTestId("queue-row")).toHaveCount(2);
    await page.getByRole("combobox", { name: "State" }).selectOption({ label: "No answer" });
    await expect(page.getByTestId("queue-row")).toHaveCount(1);
    await expect(qrow(page, "Pilates")).toBeVisible();
    await page.getByRole("combobox", { name: "Account" }).selectOption({ label: "All accounts" });
    await page.getByRole("combobox", { name: "State" }).selectOption({ label: "All states" });
    await page.getByLabel("Search").fill("barbecue");
    await expect(page.getByTestId("queue-row")).toHaveCount(1);
  });

  /** Old, never answered events, one per week. */
  const history = (api, count, owner = "u1") => {
    for (let i = 1; i <= count; i++) {
      const start = Date.parse("2026-09-20T18:00:00Z") - i * 7 * 864e5;
      api.state.events.push({
        id: `h${i}`, spond_event_id: `sp-h${i}`, user_id: owner, heading: `History ${i}`, start_timestamp: new Date(start).toISOString(),
        invite_time: new Date(start - 7 * 864e5).toISOString(), rsvp_date: null, user_choice: "manual", status: "pending",
        error_message: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
      });
    }
  };

  test("the queue asks for a window, not for every event ever stored", async ({ page, api }) => {
    history(api, 300);
    await page.goto("/admin");
    await expect(page.getByTestId("queue-row").first()).toBeVisible();
    const calls = api.callsTo("GET", "/events");
    expect(calls.length).toBe(2);                                    // the window, and older failures
    const win = calls.find((c) => c.query.status === undefined);
    expect(Date.parse(win.query.start_from)).toBeGreaterThan(Date.parse("2026-09-23T00:00:00Z"));
    expect(Date.parse(win.query.start_to) - Date.parse("2026-09-26T19:00:00Z")).toBe(60 * 864e5);
    expect(await page.getByTestId("queue-row").count()).toBeLessThan(15);   // 300 old events are not even in the page
    await expect(page.getByTestId("queue-note")).toContainText("last 2 days and the next 60 days");
  });

  test("the queue is grouped: what needs attention, what was just answered, what is coming, the rest", async ({ page }) => {
    await page.goto("/admin");
    const heads = page.getByTestId("queue-group");
    await expect(heads).toHaveText([/Needs attention\s*1/, /Answered in the last 48 hours\s*1/, /Coming up\s*6/]);   // nothing earlier in the window
    const order = await page.getByTestId("queue-row").evaluateAll((rows) => rows.map((r) => r.querySelector(".c-ev .t-strong").textContent));
    expect(order.slice(0, 2)).toEqual(["Autumn tournament", "Training, Hall B"]);   // failed, then answered two days ago
    expect(order.indexOf("Sunday league")).toBeGreaterThan(1);                      // armed / coming up
  });

  test("an answer from an hour ago sits right under the failures, not at the bottom", async ({ page, api }) => {
    for (let i = 0; i < 40; i++) {            // a long list of upcoming events
      api.state.events.push({ id: `up${i}`, spond_event_id: `sp-up${i}`, user_id: "u2", heading: `Upcoming ${i}`,
        start_timestamp: new Date(Date.parse("2026-10-01T18:00:00Z") + i * 864e5).toISOString(),
        invite_time: new Date(Date.parse("2026-09-28T18:00:00Z") + i * 864e5).toISOString(), rsvp_date: null,
        user_choice: "accept", status: "pending", error_message: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" });
    }
    api.state.events.push({ id: "fresh", spond_event_id: "sp-fresh", user_id: "u2", heading: "Answered an hour ago",
      start_timestamp: "2026-10-03T18:00:00Z", invite_time: "2026-09-26T18:00:00Z", rsvp_date: null, user_choice: "accept",
      status: "processed", error_message: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-26T18:00:01Z" });
    await page.goto("/admin");
    await expect(qrow(page, "Answered an hour ago")).toBeVisible();
    const order = await page.getByTestId("queue-row").evaluateAll((rows) => rows.map((r) => r.querySelector(".c-ev .t-strong").textContent));
    expect(order.indexOf("Answered an hour ago")).toBeLessThan(3);
    expect(order.length).toBeGreaterThan(40);
  });

  test("older failures stay visible without widening the range", async ({ page, api }) => {
    api.state.events.push({ id: "oldfail", spond_event_id: "sp-oldfail", user_id: "u1", heading: "Failed last week", start_timestamp: "2026-09-20T18:00:00Z",
      invite_time: "2026-09-13T18:00:00Z", rsvp_date: null, user_choice: "accept", status: "failed", error_message: "403 member not found",
      created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-13T18:00:01Z" });
    history(api, 3);                                                 // quiet history stays out
    await page.goto("/admin");
    await expect(qrow(page, "Failed last week")).toBeVisible();
    await expect(qrow(page, "Failed last week")).toContainText("403 member not found");
    await expect(qrow(page, "History 1")).toHaveCount(0);
  });

  test("a longer range loads earlier events newest first, in pages", async ({ page, api }) => {
    history(api, 130);
    await page.goto("/admin");
    await expect(qrow(page, "Old friendly match")).toHaveCount(0);   // started Sep 20: before the window
    await page.getByLabel("Show").selectOption("30d");
    await expect(qrow(page, "Old friendly match")).toBeVisible();    // within the last 30 days
    await expect(qrow(page, "History 1")).toBeVisible();             // Sep 13
    await expect(qrow(page, "History 5")).toHaveCount(0);            // Aug 16: older than 30 days
    await expect(page.getByTestId("queue-more")).toBeHidden();       // everything in range fit one page

    await page.getByLabel("Show").selectOption("all");
    await expect(page.getByTestId("queue-note")).toContainText("100 earlier events loaded");
    await expect(page.getByTestId("queue-more")).toBeVisible();
    await expect(qrow(page, "History 130")).toHaveCount(0);
    const rowsBefore = await page.getByTestId("queue-row").count();
    await page.getByTestId("queue-more").click();
    await expect(qrow(page, "History 130")).toBeVisible();           // second page
    await expect(page.getByTestId("queue-more")).toBeHidden();
    await expect(page.getByTestId("queue-note")).toContainText("that is all of them");
    expect(await page.getByTestId("queue-row").count()).toBeGreaterThan(rowsBefore);

    const older = api.callsTo("GET", "/events").filter((c) => c.query.order === "-start" && c.query.limit === "100");
    expect(older.map((c) => c.query.offset)).toEqual(["0", "0", "100"]);   // 30d, all, then "show more"
    await page.getByLabel("Show").selectOption("2d");                      // back to the window: earlier rows disappear
    await expect(qrow(page, "History 1")).toHaveCount(0);
  });

  test("the account filter is applied to earlier events on the server too", async ({ page, api }) => {
    history(api, 5, "u1");
    history(api, 5, "u2");
    await page.goto("/admin");
    await page.getByLabel("Show").selectOption("all");
    await page.getByRole("combobox", { name: "Account" }).selectOption({ label: "Mara Lind" });
    await expect.poll(() => api.callsTo("GET", "/events").some((c) => c.query.user_id === "u2" && c.query.order === "-start")).toBe(true);
  });

  test("sync asks the discovery worker to run", async ({ page, api }) => {
    await page.goto("/admin");
    await page.getByRole("button", { name: "Sync now" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Sync started" })).toBeVisible();
    expect(api.callsTo("POST", "/admin/sync")).toHaveLength(1);
  });

  test("timeline shows one lane per account and details for a selected event", async ({ page }) => {
    await page.goto("/admin");
    await page.getByRole("link", { name: "Timeline" }).click();
    await expect(page).toHaveURL(/#timeline$/);
    const lanes = page.getByTestId("lane");
    await expect(lanes).toHaveCount(2);
    await expect(lanes.nth(0)).toContainText("Felix Karg");
    await expect(lanes.nth(1)).toContainText("Mara Lind");
    await lanes.nth(1).getByRole("button", { name: /Pilates/ }).click();
    const detail = page.getByTestId("timeline-detail");
    await expect(detail).toContainText("Pilates");
    await expect(detail).toContainText("Tue 29 Sep, 18:00");
    await detail.getByRole("button", { name: "Going", exact: true }).click();
    await expect(detail.getByRole("button", { name: "Going", exact: true })).toHaveAttribute("aria-pressed", "true");
  });

  test("timeline: a registration that opened before the window leaves no marker on the edge", async ({ page, api }) => {
    // Opened 7 days before the visible window, the event itself is inside it (like a real club event
    // whose registration opens a week ahead). Its marker used to be clamped onto the left border.
    api.state.events.push({
      id: "x1", spond_event_id: "sp-x1", user_id: "u1", heading: "Late registration", start_timestamp: "2026-10-03T20:15:00Z",
      invite_time: "2026-09-19T16:00:00Z", rsvp_date: null, user_choice: "accept", status: "processed", error_message: null,
      created_at: "2026-09-19T00:00:00Z", updated_at: "2026-09-19T16:00:01Z",
    });
    await page.goto("/admin#timeline");
    await page.reload();
    const lane = page.getByTestId("lane").nth(0);
    await expect(lane).toContainText("Felix Karg");
    await expect(lane.getByRole("button", { name: /Late registration/ })).toHaveCount(0);   // no phantom marker
    await expect(lane.locator('.tl-event[title^="Late registration"]')).toHaveCount(1);          // the event start is drawn
    const links = lane.locator(".tl-link");
    const clipped = await links.evaluateAll((els) => els.filter((e) => e.style.left === "0%").length);
    expect(clipped).toBe(1);                                                                // dotted link runs in from the edge

    // Every other marker is still where its own time says, and events outside the window stay out.
    await expect(lane.getByRole("button", { name: /League match/ })).toHaveCount(1);
    await expect(lane.getByRole("button", { name: /Old friendly/ })).toHaveCount(0);
  });

  test("timeline: an event whose start is beyond the window shows its registration and no start block", async ({ page, api }) => {
    api.state.events.push({
      id: "x2", spond_event_id: "sp-x2", user_id: "u2", heading: "Winter camp", start_timestamp: "2026-10-20T09:00:00Z",
      invite_time: "2026-09-30T09:00:00Z", rsvp_date: null, user_choice: "manual", status: "pending", error_message: null,
      created_at: "2026-09-19T00:00:00Z", updated_at: "2026-09-19T16:00:01Z",
    });
    await page.goto("/admin#timeline");
    await page.reload();
    const lane = page.getByTestId("lane").nth(1);
    await expect(lane.getByRole("button", { name: /Winter camp/ })).toHaveCount(1);
    await expect(lane.locator('.tl-event[title^="Winter camp"]')).toHaveCount(0);
  });

  test("users: lists logins and Spond accounts and creates a login", async ({ page, api }) => {
    await page.goto("/admin#users");
    const logins = page.getByRole("table", { name: "Dashboard logins" });
    await expect(logins.getByRole("row", { name: /felix/ })).toContainText("Felix Karg");
    await expect(page.getByRole("table", { name: "Spond accounts" })).toContainText("mara@example.com");

    await page.getByRole("button", { name: "Add login" }).click();
    const dialog = page.getByRole("dialog", { name: "Add login" });
    await dialog.getByLabel("Username").fill("mara");
    await dialog.getByLabel("Password").fill("short");
    await dialog.getByLabel("Spond account").selectOption({ label: "Mara Lind (mara@example.com)" });
    await dialog.getByRole("button", { name: "Create login" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("The password needs at least 8 characters.");
    await dialog.getByLabel("Password").fill("long-enough-1");
    await dialog.getByRole("button", { name: "Create login" }).click();
    await expect(dialog).toBeHidden();
    expect(api.callsTo("POST", "/accounts")[0].body).toEqual({
      username: "mara", password: "long-enough-1", linked_user_id: "u2", is_admin: false,
    });
    await expect(logins.getByRole("row", { name: /mara/ })).toContainText("Mara Lind");
  });

  test("users: deleting a Spond account needs confirmation", async ({ page, api }) => {
    await page.goto("/admin#users");
    const spond = page.getByRole("table", { name: "Spond accounts" });
    await spond.getByRole("row", { name: /Mara Lind/ }).getByRole("button", { name: "Delete" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete Mara Lind?" });
    await dialog.getByRole("button", { name: "Delete account" }).click();
    await expect(spond.getByRole("row", { name: /Mara Lind/ })).toHaveCount(0);
    expect(api.callsTo("DELETE", "/spond-accounts/u2")).toHaveLength(1);
  });

  test("users: a failed delete reports the error and keeps the row", async ({ page }) => {
    await page.route("**/api/v1/spond-accounts/u2", (route) =>
      route.request().method() === "DELETE"
        ? route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Database unavailable" }) })
        : route.fallback(),
    );
    await page.goto("/admin#users");
    const spond = page.getByRole("table", { name: "Spond accounts" });
    await spond.getByRole("row", { name: /Mara Lind/ }).getByRole("button", { name: "Delete" }).click();
    await page.getByRole("dialog", { name: "Delete Mara Lind?" }).getByRole("button", { name: "Delete account" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Database unavailable" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "deleted" })).toHaveCount(0);
    await expect(spond.getByRole("row", { name: /Mara Lind/ })).toHaveCount(1);
  });

  test("users: an account can be paused", async ({ page, api }) => {
    await page.goto("/admin#users");
    const row = page.getByRole("table", { name: "Spond accounts" }).getByRole("row", { name: /Mara Lind/ });
    await row.getByRole("switch", { name: "Active" }).click();
    expect(api.callsTo("PATCH", "/spond-accounts/u2")[0].body).toEqual({ is_active: false });
    await expect(row.getByRole("switch", { name: "Active" })).not.toBeChecked();
  });

  test("charts show the per-account breakdown", async ({ page }) => {
    await page.goto("/admin#charts");
    const table = page.getByRole("table", { name: "Per account" });
    await expect(table).toContainText("Mara Lind");
    await expect(table).toContainText("90 ms");
  });

  test("layout never scrolls sideways", async ({ page }) => {
    for (const view of ["", "#timeline", "#users", "#audit", "#charts"]) {
      // Same-page hash changes do not reload, so force a full load and let the view render.
      await page.goto("/admin" + view);
      await page.reload();
      await page.waitForLoadState("networkidle");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `view ${view || "#queue"}`).toBeLessThanOrEqual(0);
    }
  });
});
