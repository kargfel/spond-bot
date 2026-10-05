// The admin's audit trail: who did what, filters, details, paging and CSV export.
const { test, expect, auditEntry } = require("./fixtures");

test.beforeEach(async ({ api }) => {
  api.asAdmin();
});

const table = (page) => page.getByRole("table", { name: "Audit trail" });
const rowsOf = (page) => table(page).locator("tbody tr.audit-row");

async function openAudit(page) {
  await page.goto("/admin#audit");
  await expect(page.getByRole("heading", { name: "Audit", level: 1 })).toBeVisible();
}

test("opens from the navigation and from a bookmarked link", async ({ page }) => {
  await page.goto("/admin");
  await page.getByRole("link", { name: "Audit" }).click();
  await expect(page).toHaveURL(/#audit$/);
  await expect(page.getByRole("link", { name: "Audit" })).toHaveAttribute("aria-current", "page");
  await expect(rowsOf(page).first()).toBeVisible();
});

test("lists the last week newest first, in plain words", async ({ page }) => {
  await openAudit(page);
  await expect(rowsOf(page)).toHaveCount(6); // the 20-day-old entry is outside the default period
  const rows = rowsOf(page);
  await expect(rows.nth(0)).toContainText("Signed in");
  await expect(rows.nth(0)).toContainText("felix");
  await expect(rows.nth(1)).toContainText("Set answer");
  await expect(rows.nth(1)).toContainText("Leave to me → Going");
  await expect(rows.nth(1)).toContainText("League match vs. TSV Nord");
  await expect(rows.nth(2)).toContainText("Failed sign-in");
  await expect(rows.nth(2)).toContainText("Not signed in");
  await expect(rows.nth(2)).toContainText("wrong password");
  await expect(rows.nth(2)).toContainText("refused");
  await expect(rows.nth(2)).toContainText("192.0.2.99");
  await expect(rows.nth(3)).toContainText("SpondBot");
  await expect(rows.nth(3)).toContainText("Answer sent");
  await expect(rows.nth(4)).toContainText("Created login");
  await expect(rows.nth(4).getByText("admin", { exact: true })).toBeVisible();
  await expect(rows.nth(5)).toContainText("GET request");
  await expect(rows.nth(5)).toContainText("refused");
});

test("the period filter reaches back as far as asked", async ({ page, api }) => {
  await openAudit(page);
  const first = api.callsTo("GET", "/admin/audit")[0];
  expect(Date.parse(first.query.since)).toBeLessThanOrEqual(Date.parse("2026-09-19T19:00:00Z") + 1000);
  expect(first.query.limit).toBe("100");

  await page.getByLabel("Period").selectOption("1h");
  await expect(rowsOf(page)).toHaveCount(4);
  await page.getByLabel("Period").selectOption("all");
  await expect(rowsOf(page)).toHaveCount(7);
  const last = api.callsTo("GET", "/admin/audit").at(-1);
  expect(last.query.since).toBeUndefined();
});

test("area and result filters narrow the list on the server", async ({ page, api }) => {
  await openAudit(page);
  await page.getByLabel("Area").selectOption("auth");
  await expect(rowsOf(page)).toHaveCount(2);
  expect(api.callsTo("GET", "/admin/audit").at(-1).query.category).toBe("auth");

  await page.getByLabel("Result").selectOption("denied");
  await expect(rowsOf(page)).toHaveCount(1);
  await expect(rowsOf(page).first()).toContainText("Failed sign-in");
  expect(api.callsTo("GET", "/admin/audit").at(-1).query).toMatchObject({ category: "auth", outcome: "denied" });
});

test("searching waits for a pause in typing and then asks once", async ({ page, api }) => {
  await openAudit(page);
  const before = api.callsTo("GET", "/admin/audit").length;
  await page.getByRole("searchbox", { name: "Find" }).pressSequentially("192.0.2", { delay: 20 });
  await expect(rowsOf(page)).toHaveCount(1);
  await expect(rowsOf(page).first()).toContainText("Failed sign-in");
  const asked = api.callsTo("GET", "/admin/audit").slice(before);
  expect(asked).toHaveLength(1);
  expect(asked[0].query.q).toBe("192.0.2");
});

test("a search with no hits says so", async ({ page }) => {
  await openAudit(page);
  await page.getByRole("searchbox", { name: "Find" }).fill("nobody-ever-did-this");
  await expect(table(page)).toContainText("Nothing recorded for these filters.");
});

test("a row opens to show the request behind it, and closes again", async ({ page }) => {
  await openAudit(page);
  const row = rowsOf(page).nth(1);
  const toggle = row.getByRole("button");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const detail = table(page).locator("tr.audit-detail").nth(1);
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("PATCH /api/v1/events/e2 → 200");
  await expect(detail).toContainText("198.51.100.1");
  await expect(detail).toContainText("Mozilla/5.0 (iPhone");
  await expect(detail).toContainText("req-l2");
  await expect(detail.locator("pre")).toContainText('"from": "manual"');
  await toggle.click();
  await expect(detail).toBeHidden();
});

test("an opened row stays open when the list is filtered again", async ({ page }) => {
  await openAudit(page);
  await rowsOf(page).first().getByRole("button").click();
  await page.getByLabel("Result").selectOption("success");
  await expect(rowsOf(page).first().getByRole("button")).toHaveAttribute("aria-expanded", "true");
});

test("the bot's own actions are labelled as such", async ({ page }) => {
  await openAudit(page);
  const detail = table(page).locator("tr.audit-detail").nth(3);
  await rowsOf(page).nth(3).getByRole("button").click();
  await expect(detail).toContainText("by SpondBot itself");
  await expect(detail).not.toContainText("IP address");
});

test("more pages load on request until the end", async ({ page, api }) => {
  api.state.audit = Array.from({ length: 230 }, (_, i) => auditEntry(`bulk${i}`, i + 1, "http.post"));
  await openAudit(page);
  await expect(rowsOf(page)).toHaveCount(100);
  await page.getByRole("button", { name: "Load more" }).click();
  await expect(rowsOf(page)).toHaveCount(200);
  await page.getByRole("button", { name: "Load more" }).click();
  await expect(rowsOf(page)).toHaveCount(230);
  await expect(page.getByRole("button", { name: "Load more" })).toBeHidden();
  const cursors = api.callsTo("GET", "/admin/audit").map((c) => c.query.cursor);
  expect(cursors).toEqual([undefined, "100", "200"]);
});

test("changing a filter starts again from the newest entry", async ({ page, api }) => {
  api.state.audit = Array.from({ length: 150 }, (_, i) => auditEntry(`bulk${i}`, i + 1, i % 2 ? "auth.logout" : "http.post"));
  await openAudit(page);
  await page.getByRole("button", { name: "Load more" }).click();
  await expect(rowsOf(page)).toHaveCount(150);
  await page.getByLabel("Area").selectOption("auth");
  await expect(rowsOf(page)).toHaveCount(75);
  expect(api.callsTo("GET", "/admin/audit").at(-1).query.cursor).toBeUndefined();
});

test("exports what the filters show as a CSV download", async ({ page, api }) => {
  await openAudit(page);
  await page.getByLabel("Area").selectOption("auth");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV" }).click()]);
  expect(download.suggestedFilename()).toBe("spondbot-audit.csv");
  const call = api.callsTo("GET", "/admin/audit/export.csv")[0];
  expect(call.query.category).toBe("auth");
  expect(call.query.cursor).toBeUndefined();
  expect(call.query.limit).toBeUndefined();
});

test("text typed by users can never become markup", async ({ page, api }) => {
  api.state.audit.unshift(auditEntry("xss", 1, "auth.login.failed", {
    actor_type: "anonymous", actor_username: null, actor_id: null, outcome: "denied",
    target_label: '<img src=x onerror="window.__pwned=1">', user_agent: '<script>window.__pwned=1</script>',
    ip: "<b>1.2.3.4</b>", details: { note: "<svg onload=window.__pwned=1>" },
  }));
  await openAudit(page);
  await rowsOf(page).first().getByRole("button").click();
  await expect(table(page)).toContainText('<img src=x onerror="window.__pwned=1">');
  await expect(table(page)).toContainText("<script>window.__pwned=1</script>");
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  await expect(table(page).locator("img, script, svg, b")).toHaveCount(0);
});

test("the table scrolls on its own instead of widening the page", async ({ page }) => {
  await openAudit(page);
  await expect(rowsOf(page).first()).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("members never reach the audit trail", async ({ page, api }) => {
  api.asMember();
  await page.goto("/admin#audit");
  await expect(page).toHaveURL(/\/dashboard$/);
  expect(api.callsTo("GET", "/admin/audit")).toHaveLength(0);
});
