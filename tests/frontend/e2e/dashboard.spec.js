// Member dashboard: decision inbox (from the "Inbox" direction) on top of a
// day-grouped agenda (from the "Agenda" direction).
const { test, expect } = require("./fixtures");

const row = (page, title) => page.getByTestId("agenda-row").filter({ hasText: title });

test.describe("member dashboard", () => {
  test.beforeEach(async ({ api }) => {
    api.asMember();
  });

  test("summary shows how many events need an answer and the next automatic answer", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("2 events need an answer");
    const summary = page.getByTestId("summary");
    await expect(summary).toContainText("League match vs. TSV Nord");
    await expect(summary).toContainText("in 3h 0m");
  });

  test("decision card answers the first undecided event and moves on to the next", async ({ page, api }) => {
    await page.goto("/dashboard");
    const inbox = page.getByRole("region", { name: "Needs an answer" });
    await expect(inbox).toContainText("Evening training");
    await expect(inbox).toContainText("1 of 2");

    await inbox.getByRole("button", { name: "Going", exact: true }).click();

    await expect(inbox).toContainText("Recovery session");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("1 event needs an answer");
    expect(api.callsTo("PATCH", "/events/e3")[0].body).toEqual({ user_choice: "accept" });
    await expect(row(page, "Evening training").getByRole("button", { name: "Going", exact: true })).toHaveAttribute("aria-pressed", "true");
  });

  test("decide later shows the next undecided event without saving anything", async ({ page, api }) => {
    await page.goto("/dashboard");
    const inbox = page.getByRole("region", { name: "Needs an answer" });
    await inbox.getByRole("button", { name: "Decide later" }).click();
    await expect(inbox).toContainText("Recovery session");
    await expect(inbox).toContainText("2 of 2");
    expect(api.calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  });

  test("inbox says so when every upcoming event has an answer", async ({ page, api }) => {
    for (const e of api.state.events) if (e.user_choice === "manual") e.user_choice = "accept";
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Every upcoming event has an answer");
    await expect(page.getByRole("region", { name: "Needs an answer" })).toHaveCount(0);
  });

  test("agenda groups upcoming events by day with the current answer selected", async ({ page }) => {
    await page.goto("/dashboard");
    const day = page.getByTestId("agenda-day").filter({ hasText: "League match vs. TSV Nord" });
    await expect(day.getByTestId("day-label")).toContainText("28");
    await expect(day.getByTestId("day-label")).toContainText("Mon");
    const league = row(page, "League match vs. TSV Nord");
    await expect(league.getByRole("button", { name: "Going", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(league.getByRole("button", { name: "Not going" })).toHaveAttribute("aria-pressed", "false");
    await expect(league).toContainText("Scheduled");
    await expect(league).toContainText("in 3h 0m");
  });

  test("changing an answer in the agenda saves it", async ({ page, api }) => {
    await page.goto("/dashboard");
    const bbq = row(page, "Club barbecue");
    await bbq.getByRole("button", { name: "Going", exact: true }).click();
    await expect(bbq.getByRole("button", { name: "Going", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(api.callsTo("PATCH", "/events/e4")[0].body).toEqual({ user_choice: "accept" });
    await expect(page.getByRole("status").filter({ hasText: "Going" })).toBeVisible();
  });

  test("a failed save shows the server's reason and keeps the old answer", async ({ page, api }) => {
    await page.route("**/api/v1/events/e4", (route) =>
      route.request().method() === "PATCH"
        ? route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Database unavailable" }) })
        : route.fallback(),
    );
    await page.goto("/dashboard");
    const bbq = row(page, "Club barbecue");
    await bbq.getByRole("button", { name: "Going", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Database unavailable" })).toBeVisible();
    await expect(bbq.getByRole("button", { name: "Not going" })).toHaveAttribute("aria-pressed", "true");
  });

  test("answered events show the answer instead of controls", async ({ page }) => {
    await page.goto("/dashboard");
    const done = row(page, "Training, Hall B");
    await expect(done).toContainText("Answered: Going");
    await expect(done.getByRole("button")).toHaveCount(0);
  });

  test("a failed answer shows the Spond error and retries with the member's own choice", async ({ page, api }) => {
    await page.goto("/dashboard");
    const failure = page.getByTestId("failure").filter({ hasText: "Autumn tournament" });
    await expect(failure).toContainText("member not found in group");
    await failure.getByRole("button", { name: "Retry" }).click();
    expect(api.callsTo("PATCH", "/events/e5")[0].body).toEqual({ user_choice: "decline" });
    await expect(failure).toHaveCount(0);
    await expect(row(page, "Autumn tournament")).toContainText("Scheduled");
  });

  test("past tab lists events that already happened", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("tab", { name: "Past" }).click();
    await expect(row(page, "Old friendly match")).toBeVisible();
    await expect(row(page, "League match vs. TSV Nord")).toHaveCount(0);
    await page.getByRole("tab", { name: "Upcoming" }).click();
    await expect(row(page, "League match vs. TSV Nord")).toBeVisible();
  });

  test("profile: update the display name", async ({ page, api }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Profile" }).click();
    const dialog = page.getByRole("dialog", { name: "Profile" });
    await expect(dialog.getByLabel("Display name")).toHaveValue("Felix Karg");
    await expect(dialog.getByLabel("Spond login")).toHaveValue("felix@example.com");
    await dialog.getByLabel("Display name").fill("Felix K.");
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toBeHidden();
    expect(api.callsTo("PATCH", "/spond-accounts/u1")[0].body).toEqual({ display_name: "Felix K." });
  });

  test("change password: validates locally, then reports a wrong current password without signing out", async ({ page, api }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Change password" }).click();
    const dialog = page.getByRole("dialog", { name: "Change password" });

    await dialog.getByLabel("Current password").fill("wrong-pass");
    await dialog.getByLabel("New password", { exact: true }).fill("new-password-1");
    await dialog.getByLabel("Repeat new password").fill("different-1");
    await dialog.getByRole("button", { name: "Update password" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("The new passwords do not match.");
    expect(api.callsTo("PATCH", "/auth/me/password")).toHaveLength(0);

    await dialog.getByLabel("Repeat new password").fill("new-password-1");
    await dialog.getByRole("button", { name: "Update password" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("Current password is incorrect.");
    await expect(page).toHaveURL(/\/dashboard$/);

    await dialog.getByLabel("Current password").fill("correct-horse");
    await dialog.getByRole("button", { name: "Update password" }).click();
    await expect(dialog).toBeHidden();
    expect(api.state.passwords.felix).toBe("new-password-1");
  });

  test("members see no admin-only controls", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: /sync/i })).toHaveCount(0);
    await page.getByRole("button", { name: "Account" }).click();
    await expect(page.getByRole("menuitem", { name: "Admin panel" })).toHaveCount(0);
  });

  test("admins with a linked account get a link to the admin panel", async ({ page, api }) => {
    api.asAdmin({ linked_user_id: "u1" });
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await expect(page.getByRole("menuitem", { name: "Admin panel" })).toHaveAttribute("href", "/admin");
  });

  test("an account without a linked Spond account gets a clear explanation", async ({ page, api }) => {
    api.asMember({ linked_user_id: null });
    await page.goto("/dashboard");
    await expect(page.getByText("No Spond account is linked to your login")).toBeVisible();
  });

  test("signing out ends the session and returns to the sign-in page", async ({ page, api }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/$/);
    expect(api.callsTo("POST", "/auth/logout")).toHaveLength(1);
  });

  test("visitors without a session are sent to sign in", async ({ page, api }) => {
    api.signedOut();
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/$/);
  });

  test("layout never scrolls sideways and nothing covers the last event", async ({ page }) => {
    await page.goto("/dashboard");
    const last = row(page, "Recovery session");
    await last.scrollIntoViewIfNeeded();
    await page.mouse.wheel(0, 5000);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const box = await last.boundingBox();
    const covered = await page.evaluate(({ x, y }) => {
      const el = document.elementFromPoint(x, y);
      return !el || !el.closest('[data-testid="agenda-row"]');
    }, { x: box.x + box.width / 2, y: box.y + box.height / 2 });
    expect(covered).toBe(false);
  });
});
