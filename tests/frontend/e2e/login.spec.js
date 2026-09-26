const { test, expect } = require("./fixtures");

test.describe("sign in", () => {
  test.beforeEach(async ({ api }) => {
    api.signedOut();
  });

  test("members land on their dashboard", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill("felix");
    await page.getByLabel("Password", { exact: true }).fill("correct-horse");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("admins land on the admin panel", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill("admin");
    await page.getByLabel("Password", { exact: true }).fill("admin-pass");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/admin$/);
  });

  test("a wrong password shows the reason and keeps the username", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill("felix");
    await page.getByLabel("Password", { exact: true }).fill("nope");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toHaveText("Incorrect username or password.");
    await expect(page.getByLabel("Username")).toHaveValue("felix");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  test("the password can be shown while typing", async ({ page }) => {
    await page.goto("/");
    const pw = page.getByLabel("Password", { exact: true });
    await expect(pw).toHaveAttribute("type", "password");
    await page.getByRole("button", { name: "Show password" }).click();
    await expect(pw).toHaveAttribute("type", "text");
  });

  test("an existing session skips the form", async ({ page, api }) => {
    api.asMember();
    await page.goto("/");
    await expect(page).toHaveURL(/\/dashboard$/);
  });
});
