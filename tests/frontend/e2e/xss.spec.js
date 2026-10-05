// User-controlled text (event titles from Spond, names, usernames, invite notes, error texts)
// must never become markup, in any view. A page script could act as the signed-in member or admin.
const { test, expect } = require("./fixtures");

const PAYLOADS = {
  heading: '<img src=x onerror="window.__pwned=\'heading\'">Evil event',
  name: '"><svg onload="window.__pwned=\'name\'">Mallory',
  username: "<script>window.__pwned='username'</script>mallory",
  note: "<b onmouseover=\"window.__pwned='note'\">Jonas</b>",
  error: "<iframe srcdoc=\"<script>parent.__pwned='error'</script>\"></iframe> 403",
};

async function plant(api) {
  const s = api.state;
  const now = Date.parse("2026-09-26T19:00:00Z");
  const iso = (ms) => new Date(ms).toISOString();
  s.spondUsers[0].display_name = PAYLOADS.name;
  s.accounts.push({ id: "a9", username: PAYLOADS.username, is_admin: false, linked_user_id: "u2" });
  s.invites[1].note = PAYLOADS.note;
  for (const e of s.events) if (e.user_id === "u1") e.heading = PAYLOADS.heading;
  const failed = s.events.find((e) => e.status === "failed");
  failed.error_message = PAYLOADS.error;
  s.events.push({
    id: "evil", spond_event_id: "sp-evil", user_id: "u1", heading: PAYLOADS.heading, start_timestamp: iso(now + 30 * 3600e3),
    invite_time: iso(now - 3600e3), rsvp_date: null, user_choice: "manual", status: "pending", error_message: null,
    created_at: iso(now), updated_at: iso(now),
  });
}

async function assertInert(page) {
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  await expect(page.locator('img[src="x"], svg[onload], iframe[srcdoc], b[onmouseover]')).toHaveCount(0);
  // the script tag typed as a username must not have been inserted as an element either
  expect(await page.evaluate(() => [...document.scripts].some((s) => s.textContent.includes("__pwned")))).toBe(false);
}

test("member dashboard shows event titles and names as plain text", async ({ page, api }) => {
  api.asMember();
  await plant(api);
  await page.goto("/dashboard");
  await expect(page.getByText(PAYLOADS.heading).first()).toBeVisible();
  await assertInert(page);
});

test("member dashboard: failed answers, controls and the account menu stay inert", async ({ page, api }) => {
  api.asMember();
  await plant(api);
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Account" }).click();
  await expect(page.getByText(PAYLOADS.name).first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText(PAYLOADS.heading).first()).toBeVisible();
  await assertInert(page);
});

test.describe("admin panel", () => {
  test.beforeEach(async ({ api }) => {
    api.asAdmin();
    await plant(api);
  });

  for (const view of ["queue", "timeline", "users", "audit"]) {
    test(`#${view} renders planted markup as text`, async ({ page }) => {
      await page.goto(`/admin#${view}`);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(150);
      await assertInert(page);
    });
  }

  test("queue shows the planted title literally and a failed answer's error literally", async ({ page }) => {
    await page.goto("/admin#queue");
    await expect(page.getByText(PAYLOADS.heading).first()).toBeVisible();
    await page.getByRole("combobox", { name: /State/i }).selectOption({ label: "Failed" }).catch(() => {});
    await assertInert(page);
  });

  test("users view shows planted names, usernames and invite notes literally", async ({ page }) => {
    await page.goto("/admin#users");
    await expect(page.getByRole("table", { name: "Dashboard logins" })).toContainText(PAYLOADS.username);
    await expect(page.getByRole("table", { name: "Spond accounts" })).toContainText(PAYLOADS.name);
    await assertInert(page);
  });

  test("the timeline's labels and detail card stay inert", async ({ page }) => {
    await page.goto("/admin#timeline");
    const marks = page.locator("[data-mark]");
    await expect(marks.first()).toBeVisible();
    await marks.first().click();
    await assertInert(page);
  });

  test("confirmation dialogs show names as text", async ({ page }) => {
    await page.goto("/admin#users");
    await page.getByRole("button", { name: /Revoke/ }).first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await assertInert(page);
  });
});

test("the join page shows an invite's name as text", async ({ page, api }) => {
  api.signedOut();
  api.state.invites.push({ id: "i9", token: "evil-note-token", note: PAYLOADS.note, created_at: new Date().toISOString(),
    expires_at: "2026-10-30T00:00:00Z", used_at: null });
  await page.goto("/join#evil-note-token");
  await expect(page.locator("#join-intro")).toContainText(PAYLOADS.note); // shown literally, as text
  await expect(page.locator("#join-display-name")).toHaveValue(PAYLOADS.note);
  await assertInert(page);
});
