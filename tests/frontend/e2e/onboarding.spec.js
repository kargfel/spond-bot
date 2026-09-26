// Member self-onboarding: invite links (/join#<token>), connecting a Spond account
// from the dashboard, and the admin side of invites.
const { test, expect } = require("./fixtures");

async function fillJoin(page, overrides = {}) {
  const v = {
    username: "mara",
    password: "long-password-1",
    repeat: "long-password-1",
    spondLogin: "mara@example.com",
    spondPassword: "spond-secret",
    ...overrides,
  };
  await page.getByLabel("Username").fill(v.username);
  await page.getByLabel("Password", { exact: true }).fill(v.password);
  await page.getByLabel("Repeat password").fill(v.repeat);
  await page.getByLabel("Spond email or phone").fill(v.spondLogin);
  await page.getByLabel("Spond password").fill(v.spondPassword);
}

test.describe("joining with an invite link", () => {
  test.beforeEach(async ({ api }) => {
    api.signedOut();
  });

  test("a valid invite creates the login, connects Spond and opens the dashboard", async ({ page, api }) => {
    await page.goto("/join#valid-token");
    await expect(page.getByRole("heading", { level: 2 })).toHaveText("Create your login");
    await expect(page.getByLabel("Name shown in SpondBot")).toHaveValue("Mara");
    await fillJoin(page);
    await page.getByRole("button", { name: "Create login" }).click();

    await expect(page).toHaveURL(/\/dashboard$/);
    expect(api.callsTo("POST", "/invites/accept")[0].body).toEqual({
      token: "valid-token",
      username: "mara",
      password: "long-password-1",
      spond_login: "mara@example.com",
      spond_password: "spond-secret",
      display_name: "Mara",
    });
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("No upcoming events");
  });

  test("the token is removed from the address bar once read", async ({ page }) => {
    await page.goto("/join#valid-token");
    await expect(page.getByLabel("Username")).toBeVisible();
    expect(new URL(page.url()).hash).toBe("");
  });

  test("mismatched passwords are caught before anything is sent", async ({ page, api }) => {
    await page.goto("/join#valid-token");
    await fillJoin(page, { repeat: "something-else" });
    await page.getByRole("button", { name: "Create login" }).click();
    await expect(page.getByRole("alert")).toHaveText("The passwords do not match.");
    expect(api.callsTo("POST", "/invites/accept")).toHaveLength(0);
  });

  test("a rejected Spond password shows Spond's reason and keeps the form", async ({ page }) => {
    await page.goto("/join#valid-token");
    await fillJoin(page, { spondPassword: "wrong" });
    await page.getByRole("button", { name: "Create login" }).click();
    await expect(page.getByRole("alert")).toContainText("Spond did not accept that login and password");
    await expect(page).toHaveURL(/\/join/);
    await expect(page.getByLabel("Username")).toHaveValue("mara");
    await expect(page.getByRole("button", { name: "Create login" })).toBeEnabled();
  });

  for (const [token, text] of [
    ["used-token", "This invite has already been used"],
    ["old-token", "This invite has expired"],
    ["no-such-token", "This invite link is not valid"],
  ]) {
    test(`an unusable invite explains why (${token})`, async ({ page }) => {
      await page.goto(`/join#${token}`);
      await expect(page.getByText(text)).toBeVisible();
      await expect(page.getByLabel("Username")).toBeHidden();
      await expect(page.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/");
    });
  }

  test("a link without a token explains that it is incomplete", async ({ page }) => {
    await page.goto("/join");
    await expect(page.getByText("This invite link is not valid")).toBeVisible();
  });
});

test.describe("connecting a Spond account from the dashboard", () => {
  test("a login without an account connects one and sees its events", async ({ page, api }) => {
    api.asMember({ linked_user_id: null });
    await page.goto("/dashboard");
    const form = page.getByRole("form", { name: "Connect your Spond account" });
    await form.getByLabel("Spond email or phone").fill("felix2@example.com");
    await form.getByLabel("Spond password").fill("spond-secret");
    await form.getByLabel("Name shown in SpondBot").fill("Felix");
    await form.getByRole("button", { name: "Connect" }).click();

    await expect(page.getByRole("heading", { level: 1 })).toHaveText("No upcoming events");
    await expect(form).toHaveCount(0);
    expect(api.callsTo("POST", "/spond-accounts/me")[0].body).toEqual({
      login: "felix2@example.com",
      password: "spond-secret",
      display_name: "Felix",
    });
  });

  test("a rejected Spond password is shown in the form", async ({ page, api }) => {
    api.asMember({ linked_user_id: null });
    await page.goto("/dashboard");
    const form = page.getByRole("form", { name: "Connect your Spond account" });
    await form.getByLabel("Spond email or phone").fill("felix2@example.com");
    await form.getByLabel("Spond password").fill("wrong");
    await form.getByRole("button", { name: "Connect" }).click();
    await expect(form.getByRole("alert")).toContainText("Spond did not accept");
    await expect(form.getByRole("button", { name: "Connect" })).toBeEnabled();
  });
});

test.describe("admin invites", () => {
  test.beforeEach(async ({ api }) => {
    api.asAdmin();
  });

  test("creating an invite shows the link once, ready to copy", async ({ page, api }) => {
    await page.goto("/admin#users");
    await page.getByRole("button", { name: "Invite member" }).click();
    const dialog = page.getByRole("dialog", { name: "Invite a member" });
    await dialog.getByLabel("Name").fill("Lena");
    await dialog.getByLabel("Valid for").selectOption({ label: "3 days" });
    await dialog.getByRole("button", { name: "Create link" }).click();

    expect(api.callsTo("POST", "/invites")[0].body).toEqual({ note: "Lena", days_valid: 3 });
    const link = dialog.getByLabel("Invite link");
    await expect(link).toHaveValue(/^http:\/\/spondbot\.test\/join#tok-4-/);
    await expect(dialog.getByRole("button", { name: "Copy link" })).toBeVisible();
    await expect(dialog).toContainText("only shown once");

    await dialog.getByRole("button", { name: "Done" }).click();
    const table = page.getByRole("table", { name: "Invites" });
    await expect(table.getByRole("row", { name: /Lena/ })).toContainText("pending");
  });

  test("invites list shows each status and pending ones can be revoked", async ({ page, api }) => {
    await page.goto("/admin#users");
    const table = page.getByRole("table", { name: "Invites" });
    await expect(table.getByRole("row", { name: /Jonas/ })).toContainText("used");
    await expect(table.getByRole("row", { name: /Mara/ })).toContainText("pending");
    await expect(table.getByRole("row", { name: /expired/ })).toHaveCount(1);
    await expect(table.getByRole("row", { name: /Jonas/ }).getByRole("button", { name: "Revoke" })).toHaveCount(0);

    await table.getByRole("row", { name: /Mara/ }).getByRole("button", { name: "Revoke" }).click();
    await page.getByRole("dialog", { name: "Revoke the invite for Mara?" }).getByRole("button", { name: "Revoke" }).click();
    await expect(table.getByRole("row", { name: /Mara/ })).toHaveCount(0);
    expect(api.callsTo("DELETE", "/invites/i2")).toHaveLength(1);
  });
});
