// Replacing a stored Spond password after it was changed in Spond:
// members from their account menu, admins from the Spond accounts table.
const { test, expect } = require("./fixtures");

test.describe("member updates their Spond password", () => {
  test.beforeEach(async ({ api }) => {
    api.asMember();
  });

  test("from the account menu", async ({ page, api }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Update Spond password" }).click();
    const dialog = page.getByRole("dialog", { name: "Update Spond password" });
    await expect(dialog).toContainText("felix@example.com");
    await dialog.getByLabel("New Spond password").fill("new-spond-pw");
    await dialog.getByRole("button", { name: "Update" }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByRole("status").filter({ hasText: "Spond password updated" })).toBeVisible();
    expect(api.callsTo("PUT", "/spond-accounts/u1/password")[0].body).toEqual({ password: "new-spond-pw" });
  });

  test("a password Spond rejects is reported and nothing closes", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Update Spond password" }).click();
    const dialog = page.getByRole("dialog", { name: "Update Spond password" });
    await dialog.getByLabel("New Spond password").fill("wrong");
    await dialog.getByRole("button", { name: "Update" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Spond did not accept");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Update" })).toBeEnabled();
  });

  test("an empty password is caught before anything is sent", async ({ page, api }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Update Spond password" }).click();
    const dialog = page.getByRole("dialog", { name: "Update Spond password" });
    await dialog.getByRole("button", { name: "Update" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("Enter your new Spond password.");
    expect(api.callsTo("PUT", "/spond-accounts/u1/password")).toHaveLength(0);
  });

  test("logins without a Spond account do not see the option", async ({ page, api }) => {
    api.asMember({ linked_user_id: null });
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await expect(page.getByRole("menuitem", { name: "Update Spond password" })).toHaveCount(0);
  });
});

test("admin updates a member's Spond password from the accounts table", async ({ page, api }) => {
  api.asAdmin();
  await page.goto("/admin#users");
  const row = page.getByRole("table", { name: "Spond accounts" }).getByRole("row", { name: /Mara Lind/ });
  await row.getByRole("button", { name: "Update password" }).click();
  const dialog = page.getByRole("dialog", { name: "Update Spond password for Mara Lind" });
  await expect(dialog).toContainText("mara@example.com");
  await dialog.getByLabel("New Spond password").fill("new-spond-pw");
  await dialog.getByRole("button", { name: "Update" }).click();
  await expect(dialog).toBeHidden();
  expect(api.callsTo("PUT", "/spond-accounts/u2/password")[0].body).toEqual({ password: "new-spond-pw" });
});
