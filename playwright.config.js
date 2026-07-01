import { defineConfig } from "@playwright/test";

// E2E for the web UI front-end. A static server serves the repo; each spec
// injects the mock bridge (no Qt) and drives streaming/cancellation.
export default defineConfig({
  testDir: "opai/assets/web/__tests__/e2e",
  timeout: 15000,
  expect: { timeout: 5000 },
  fullyParallel: true,
  reporter: process.env.CI
    ? [["line"], ["html", { open: "never" }]]
    : [["list"]],
  use: {
    baseURL: "http://localhost:8099",
    actionTimeout: 5000,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "python -m http.server 8099",
    port: 8099,
    reuseExistingServer: true,
    timeout: 20000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
