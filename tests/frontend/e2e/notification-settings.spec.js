// Choosing which notifications to get: answers sent/failed and the 8/4/1 hour reminders.
// The settings belong to the account, are saved as soon as a switch is flipped, and the server
// has the last word (a refused save puts the switch back).
const { test, expect, installFakePush } = require("./fixtures");

const KINDS = [
  { key: "answer_sent", label: "Answer sent" },
  { key: "answer_failed", label: "Answer failed" },
  { key: "reminder_8h", label: "Registration opens in 8 hours" },
  { key: "reminder_4h", label: "Registration opens in 4 hours" },
  { key: "reminder_1h", label: "Registration opens in 1 hour" },
];

async function openNotifications(page) {
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Notifications" }).click();
  return page.getByRole("dialog", { name: "Notifications" });
}

const box = (dialog, label) => dialog.getByRole("checkbox", { name: new RegExp(`^${label}`) });

test.beforeEach(async ({ api }) => {
  api.asMember();
});

test.describe("the settings form", () => {
  test("shows every kind, grouped, all on for someone who never changed anything", async ({ page }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await expect(dialog.getByRole("heading", { name: "What to notify me about" })).toBeVisible();
    await expect(dialog.getByRole("group", { name: "Answers" })).toBeVisible();
    await expect(dialog.getByRole("group", { name: "Reminders" })).toBeVisible();
    for (const k of KINDS) await expect(box(dialog, k.label)).toBeChecked();
    await expect(dialog.getByRole("group", { name: "Reminders" })).toContainText("Only for events you haven't chosen an answer for.");
    await expect(dialog).toContainText("apply to all your devices");
  });

  test("shows what the account saved earlier", async ({ page, api }) => {
    api.state.push.preferences.a1 = { answer_sent: false, reminder_4h: false };
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await expect(box(dialog, "Answer sent")).not.toBeChecked();
    await expect(box(dialog, "Registration opens in 4 hours")).not.toBeChecked();
    await expect(box(dialog, "Answer failed")).toBeChecked();
    await expect(box(dialog, "Registration opens in 8 hours")).toBeChecked();
    await expect(box(dialog, "Registration opens in 1 hour")).toBeChecked();
  });

  test("is there before notifications are turned on for this device, because it is an account setting", async ({ page }) => {
    await installFakePush(page);
    const dialog = await openNotifications(page);
    await expect(dialog.getByRole("button", { name: "Turn on" })).toBeVisible();
    await expect(box(dialog, "Answer sent")).toBeChecked();
  });

  test("appears once the device is turned on, without reopening the dialog", async ({ page }) => {
    await installFakePush(page);
    const dialog = await openNotifications(page);
    await dialog.getByRole("button", { name: "Turn on" }).click();
    await expect(dialog.getByRole("button", { name: "Turn off" })).toBeVisible();
    await expect(box(dialog, "Registration opens in 1 hour")).toBeChecked();
  });

  for (const [name, fake] of [
    ["notifications are blocked in the browser", { permission: "denied" }],
    ["an iPhone has not installed the app yet", { supported: false, ios: true }],
    ["the browser can't show notifications", { supported: false }],
  ]) {
    test(`is hidden when ${name}`, async ({ page }) => {
      await installFakePush(page, fake);
      const dialog = await openNotifications(page);
      await expect(dialog.getByRole("status").first()).toBeVisible();
      await expect(dialog.getByRole("heading", { name: "What to notify me about" })).toBeHidden();
      await expect(dialog.getByRole("checkbox")).toHaveCount(0);
    });
  }

  test("is hidden when the server has no push set up (the menu item itself is gone)", async ({ page, api }) => {
    api.state.push.enabled = false;
    await installFakePush(page);
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account" }).click();
    await expect(page.getByRole("menuitem", { name: "Notifications" })).toBeHidden();
  });

  test("a checkbox is reachable by its label and by keyboard", async ({ page }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await dialog.getByText("Answer failed", { exact: true }).click();
    await expect(box(dialog, "Answer failed")).not.toBeChecked();
    await box(dialog, "Registration opens in 8 hours").focus();
    await page.keyboard.press("Space");
    await expect(box(dialog, "Registration opens in 8 hours")).not.toBeChecked();
  });
});

test.describe("saving", () => {
  test("flipping a switch saves all settings at once and says so", async ({ page, api }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await box(dialog, "Registration opens in 4 hours").uncheck();
    await expect(dialog.locator("#push-prefs-saved").filter({ hasText: "Saved." })).toBeVisible();
    const [put] = api.callsTo("PUT", "/push/preferences");
    expect(put.body).toEqual({ answer_sent: true, answer_failed: true, reminder_8h: true, reminder_4h: false, reminder_1h: true });
    expect(api.state.push.preferences.a1.reminder_4h).toBe(false);
  });

  test("each change keeps the earlier ones", async ({ page, api }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await box(dialog, "Answer sent").uncheck();
    await expect(dialog.locator("#push-prefs-saved").filter({ hasText: "Saved." })).toBeVisible();
    await box(dialog, "Registration opens in 1 hour").uncheck();
    await expect.poll(() => api.callsTo("PUT", "/push/preferences").length).toBe(2);
    expect(api.callsTo("PUT", "/push/preferences")[1].body).toEqual({
      answer_sent: false, answer_failed: true, reminder_8h: true, reminder_4h: true, reminder_1h: false,
    });
  });

  test("a saved choice is still there after reloading the page", async ({ page }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    let dialog = await openNotifications(page);
    await box(dialog, "Answer failed").uncheck();
    await expect(dialog.locator("#push-prefs-saved").filter({ hasText: "Saved." })).toBeVisible();
    await page.reload();
    dialog = await openNotifications(page);
    await expect(box(dialog, "Answer failed")).not.toBeChecked();
    await expect(box(dialog, "Answer sent")).toBeChecked();
  });

  test("switching everything off is allowed: the device stays turned on", async ({ page, api }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    for (const k of KINDS) await box(dialog, k.label).uncheck();
    await expect.poll(() => api.callsTo("PUT", "/push/preferences").length).toBe(5);
    expect(Object.values(api.state.push.preferences.a1).every((v) => v === false)).toBe(true);
    await expect(dialog.getByRole("button", { name: "Turn off" })).toBeVisible();
  });

  test("a refused save puts the switch back and explains", async ({ page, api }) => {
    api.state.push.preferencesStatus = 500;
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await box(dialog, "Registration opens in 8 hours").uncheck();
    await expect(dialog.getByRole("alert")).toContainText("Could not save: The server could not save your settings.");
    await expect(box(dialog, "Registration opens in 8 hours")).toBeChecked();
    await expect(dialog.locator("#push-prefs-saved").filter({ hasText: "Saved." })).toHaveCount(0);
    // and it works again once the server does
    api.state.push.preferencesStatus = 200;
    await box(dialog, "Registration opens in 8 hours").uncheck();
    await expect(dialog.locator("#push-prefs-saved").filter({ hasText: "Saved." })).toBeVisible();
    await expect(dialog.getByRole("alert")).toBeHidden();
  });

  test("the switches are locked while a save is running, so two saves cannot cross", async ({ page, api }) => {
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    let release;
    const gate = new Promise((r) => { release = r; });
    await page.route("**/api/v1/push/preferences", async (route) => {
      if (route.request().method() === "PUT") await gate;
      await route.fallback();
    });
    await box(dialog, "Answer sent").uncheck();
    await expect(box(dialog, "Answer failed")).toBeDisabled();
    release();
    await expect(box(dialog, "Answer failed")).toBeEnabled();
    expect(api.callsTo("PUT", "/push/preferences")).toHaveLength(1);
  });

  test("another account's settings are not mixed in", async ({ page, api }) => {
    api.state.push.preferences.someone_else = { answer_sent: false, reminder_1h: false };
    await installFakePush(page, { subscribed: true, permission: "granted" });
    const dialog = await openNotifications(page);
    await expect(box(dialog, "Answer sent")).toBeChecked();
    await expect(box(dialog, "Registration opens in 1 hour")).toBeChecked();
  });
});

test("the dialog fits a phone: the settings scroll instead of running off the screen", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await installFakePush(page, { subscribed: true, permission: "granted" });
  const dialog = await openNotifications(page);
  await expect(box(dialog, "Registration opens in 1 hour")).toBeAttached();
  const { top, bottom } = await dialog.evaluate((el) => ({ top: el.getBoundingClientRect().top, bottom: el.getBoundingClientRect().bottom }));
  expect(top).toBeGreaterThanOrEqual(0);
  expect(bottom).toBeLessThanOrEqual(640);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("markup in a label can never be injected (labels are fixed text)", async ({ page }) => {
  await installFakePush(page, { subscribed: true, permission: "granted" });
  const dialog = await openNotifications(page);
  expect(await dialog.locator("#push-prefs-list").evaluate((el) => el.querySelectorAll("img, script, svg, iframe").length)).toBe(0);
});

test("the audit area filter offers reminders", async ({ page, api }) => {
  api.asAdmin();
  await page.goto("/admin#audit");
  await page.getByLabel("Area").selectOption("reminder");
  await expect.poll(() => api.callsTo("GET", "/admin/audit").at(-1)?.query.category).toBe("reminder");
});
