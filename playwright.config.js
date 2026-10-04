// End-to-end tests for the static frontend. The API is mocked per test
// (tests/frontend/e2e/fixtures.js), so no backend or database is needed.
const { defineConfig } = require("@playwright/test");

const launchOptions = process.env.PW_CHROMIUM_PATH
  ? { executablePath: process.env.PW_CHROMIUM_PATH }
  : {};

module.exports = defineConfig({
  testDir: "tests/frontend/e2e",
  fullyParallel: true,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://spondbot.test",
    timezoneId: "UTC",
    locale: "en-GB",
    trace: "retain-on-failure",
    // The mocked-API specs intercept requests per page, which a service worker would bypass.
    // pwa.spec.js turns workers back on and runs against a real local server instead.
    serviceWorkers: "block",
    launchOptions,
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    {
      name: "phone",
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
    },
  ],
});
