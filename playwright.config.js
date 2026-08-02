import { defineConfig } from "@playwright/test";

// E2E for the web UI front-end. A static server serves the repo; each spec
// injects the mock bridge (no Qt) and drives streaming/cancellation.
export default defineConfig({
  testDir: "opai/assets/web/__tests__/e2e",
  timeout: 15000,
  expect: { timeout: 5000 },
  // Token screenshots use bundled fonts and a fixed viewport, so a single
  // baseline is intentional across the Windows desktop build and Linux CI.
  snapshotPathTemplate: "{testDir}/{testFilePath}-snapshots/{arg}{ext}",
  fullyParallel: true,
  workers: 4,
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
    // Four browser workers fetch the application bundle at once.  The
    // standard `http.server` CLI is single-threaded, which makes those first
    // loads serialize on slower hosted Windows runners and causes false test
    // timeouts.  Keep the zero-dependency server, but handle each request in
    // its own thread just as the production web server would.
    command: "python -c \"from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler; ThreadingHTTPServer(('localhost', 8099), SimpleHTTPRequestHandler).serve_forever()\"",
    port: 8099,
    reuseExistingServer: true,
    timeout: 20000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
