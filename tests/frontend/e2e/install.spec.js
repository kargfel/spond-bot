// Installing the app from the account menu: the browser's own prompt where there is one,
// step-by-step instructions on iPhone and iPad, nothing once it is installed.
const { test, expect, installFakePush } = require("./fixtures");

async function openMenu(page) {
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Account" }).click();
  await expect(page.getByRole("menuitem", { name: "Profile" })).toBeVisible();
}

const offerInstall = (page) =>
  page.evaluate(() => {
    const e = new Event("beforeinstallprompt", { cancelable: true });
    e.prompt = () => { window.__prompted = (window.__prompted || 0) + 1; };
    e.userChoice = Promise.resolve({ outcome: "accepted" });
    window.dispatchEvent(e);
  });

test.beforeEach(async ({ api, page }) => {
  api.asMember();
  await installFakePush(page);
});

test("there is no install item when the browser offers nothing", async ({ page }) => {
  await openMenu(page);
  await expect(page.getByRole("menuitem", { name: "Install app" })).toBeHidden();
});

test("the browser's install prompt is offered and used", async ({ page }) => {
  await page.goto("/dashboard");
  await offerInstall(page);
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Install app" }).click();
  expect(await page.evaluate(() => window.__prompted)).toBe(1);
  // The prompt can be used once; afterwards the item is gone.
  await page.getByRole("button", { name: "Account" }).click();
  await expect(page.getByRole("menuitem", { name: "Install app" })).toBeHidden();
});

test("once installed the item disappears", async ({ page }) => {
  await page.goto("/dashboard");
  await offerInstall(page);
  await page.evaluate(() => window.dispatchEvent(new Event("appinstalled")));
  await page.getByRole("button", { name: "Account" }).click();
  await expect(page.getByRole("menuitem", { name: "Install app" })).toBeHidden();
});

test.describe("on iPhone", () => {
  test("Safari gets the Home Screen steps", async ({ page }) => {
    await installFakePush(page, { supported: false, ios: true });
    await openMenu(page);
    await page.getByRole("menuitem", { name: "Install app" }).click();
    const dialog = page.getByRole("dialog", { name: "Add SpondBot to your Home Screen" });
    await expect(dialog).toContainText("Share");
    await expect(dialog).toContainText("Add to Home Screen");
    await dialog.getByRole("button", { name: "Got it" }).click();
    await expect(dialog).toBeHidden();
  });

  test("the installed app does not offer to install itself", async ({ page }) => {
    await installFakePush(page, { ios: true, standalone: true });
    await openMenu(page);
    await expect(page.getByRole("menuitem", { name: "Install app" })).toBeHidden();
  });
});
