// Notifications from the member's account menu: turn on/off, test, and every blocked or
// unsupported situation. The browser's push machinery is faked (installFakePush); the API is mocked.
const { test, expect, installFakePush } = require("./fixtures");

const IOS_STEPS = /Add to Home Screen/;

async function openNotifications(page) {
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Notifications" }).click();
  return page.getByRole("dialog", { name: "Notifications" });
}

const pushState = (page) => page.evaluate(() => ({ ...window.__push, subscription: Boolean(window.__push.subscription) }));

test.describe("turning notifications on and off", () => {
  test.beforeEach(async ({ api }) => {
    api.asMember();
  });

  test("a member turns notifications on for this device", async ({ page, api }) => {
    await installFakePush(page);
    const dialog = await openNotifications(page);
    await expect(dialog.getByRole("status")).toContainText("Get a notification on this device");
    await expect(dialog.getByRole("button", { name: "Send test" })).toBeHidden();

    await dialog.getByRole("button", { name: "Turn on" }).click();

    await expect(dialog.getByRole("status")).toContainText("Notifications are on for this device");
    await expect(dialog.getByRole("button", { name: "Turn off" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Turn on" })).toBeHidden();
    await expect(page.getByRole("status").filter({ hasText: "Notifications are on for this device." }).last()).toBeVisible();

    const [sent] = api.callsTo("POST", "/push/subscribe");
    expect(sent.body).toEqual({
      endpoint: "https://fcm.googleapis.com/fcm/send/new-device",
      expirationTime: null,
      keys: { p256dh: "p256dh-key", auth: "auth-key" },
    });
    expect((await pushState(page)).prompts).toBe(1);
  });

  test("the device subscribes with the server's key", async ({ page }) => {
    await installFakePush(page);
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await expect(dialog.getByRole("button", { name: "Turn off" })).toBeVisible();
    const keyBytes = await page.evaluate(() => [...new Uint8Array(window.__push.subscription.options.applicationServerKey)]);
    expect(keyBytes).toHaveLength(65);
    expect(keyBytes[0]).toBe(4);
    expect(keyBytes[1]).toBe(9);
  });

  test("a device that is already subscribed shows as on", async ({ page }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await expect(dialog.getByRole("status")).toContainText("Notifications are on for this device");
    await expect(dialog.getByRole("button", { name: "Turn off" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Send test" })).toBeVisible();
  });

  test("turning them off forgets the device on the server and in the browser", async ({ page, api }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn off" }).click();

    await expect(dialog.getByRole("status")).toContainText("Get a notification on this device");
    await expect(dialog.getByRole("button", { name: "Turn on" })).toBeVisible();
    expect(api.callsTo("POST", "/push/unsubscribe")[0].body).toEqual({ endpoint: "https://fcm.googleapis.com/fcm/send/existing" });
    expect((await pushState(page)).subscription).toBe(false);
  });

  test("a test notification is sent to the member's devices", async ({ page, api }) => {
    await installFakePush(page);
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await dialog.getByRole("button", { name: "Send test" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Test sent." })).toBeVisible();
    expect(api.callsTo("POST", "/push/test")).toHaveLength(1);
  });

  test("a test that reached no device says so", async ({ page, api }) => {
    api.state.push.delivered = 0;
    await installFakePush(page);
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await dialog.getByRole("button", { name: "Send test" }).click();
    await expect(page.getByRole("status").filter({ hasText: "could not be delivered" })).toBeVisible();
  });

  test("too many tests are reported in the dialog", async ({ page, api }) => {
    await installFakePush(page);
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    api.state.push.testStatus = 429;
    await dialog.getByRole("button", { name: "Send test" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Too many tests");
    await expect(dialog.getByRole("button", { name: "Send test" })).toBeEnabled();
  });
});

test.describe("when notifications cannot be turned on", () => {
  test.beforeEach(async ({ api }) => {
    api.asMember();
  });

  test("declining the browser prompt leaves everything off and explains it", async ({ page, api }) => {
    await installFakePush(page, { grant: false });
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await expect(dialog.getByRole("alert")).toContainText("blocked");
    await expect(dialog.getByRole("status")).toContainText("blocked");
    await expect(dialog.getByRole("button", { name: "Turn on" })).toBeHidden();
    expect(api.callsTo("POST", "/push/subscribe")).toHaveLength(0);
  });

  test("notifications blocked in the browser are explained, with no button to fight it", async ({ page }) => {
    await installFakePush(page, { permission: "denied" });
    const dialog = await openNotifications(page);
    await expect(dialog.getByRole("status")).toContainText("blocked for SpondBot");
    await expect(dialog.getByRole("button", { name: /Turn o/ })).toHaveCount(0);
  });

  test("a server that refuses the subscription leaves no half-subscribed device", async ({ page, api }) => {
    api.state.push.subscribeStatus = 500;
    await installFakePush(page);
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Subscribing failed");
    await expect(dialog.getByRole("button", { name: "Turn on" })).toBeEnabled();
    expect((await pushState(page)).subscription).toBe(false);
    expect((await pushState(page)).log).toEqual(["subscribe", "unsubscribe"]);
  });

  test("a browser that fails to subscribe shows the problem and stays off", async ({ page, api }) => {
    await installFakePush(page, { subscribeError: "Registration failed - push service error" });
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await expect(dialog.getByRole("alert")).toContainText("push service error");
    await expect(dialog.getByRole("button", { name: "Turn on" })).toBeEnabled();
    expect(api.callsTo("POST", "/push/subscribe")).toHaveLength(0);
  });

  test("a device subscribed with an outdated server key is subscribed again", async ({ page, api }) => {
    await installFakePush(page, { subscribed: true, keyDiffers: true, permission: "default" });
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await expect(dialog.getByRole("button", { name: "Turn off" })).toBeVisible();
    expect((await pushState(page)).log).toEqual(["unsubscribe", "subscribe"]);
    expect(api.callsTo("POST", "/push/subscribe")[0].body.endpoint).toContain("new-device");
  });

  test("without a server key the option is not offered", async ({ page, api }) => {
    api.state.push.enabled = false;
    await installFakePush(page);
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await expect(page.getByRole("menuitem", { name: "Profile" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Notifications" })).toBeHidden();
  });

  test("an iPhone in the browser is told to add the app to the Home Screen first", async ({ page }) => {
    await installFakePush(page, { supported: false, ios: true });
    const dialog = await openNotifications(page);
    await expect(dialog.getByRole("status")).toContainText(IOS_STEPS);
    await expect(dialog.getByRole("button", { name: /Turn o/ })).toHaveCount(0);
  });

  test("a browser without push support says so", async ({ page }) => {
    await installFakePush(page, { supported: false });
    const dialog = await openNotifications(page);
    await expect(dialog.getByRole("status")).toContainText("can't show notifications");
  });
});

test.describe("signing out", () => {
  test("removes this device so the next login on it starts clean", async ({ page, api }) => {
    api.asMember();
    await installFakePush(page, { subscribed: true, permission: "granted" });
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/$/);
    expect(api.callsTo("POST", "/push/unsubscribe")[0].body.endpoint).toContain("existing");
    expect(await page.evaluate(() => sessionStorage.getItem("__push_unsubscribed"))).toBe("1");
  });

  test("works the same for a device that never subscribed", async ({ page, api }) => {
    api.asMember();
    await installFakePush(page);
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/$/);
    expect(api.callsTo("POST", "/push/unsubscribe")).toHaveLength(0);
  });
});
