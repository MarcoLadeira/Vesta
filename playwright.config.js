import { defineConfig } from "@playwright/test";

// Hosted Windows Chromium is materially slower than a local browser while
// still exercising the same UI. Keep local feedback strict, but give the
// required hosted qualification enough time to distinguish a slow cold boot
// from a genuine functional failure.
// Raised alongside helpers/app.js's APP_READY_TIMEOUT: at 30s, the 20s app-boot
// wait every spec performs was two thirds of the budget, so a slow boot left a
// test barely any time to assert anything. Boot now gets 40s and the test keeps
// 20s beyond it, which is headroom rather than a race.
const testTimeout = process.env.CI ? 60_000 : 15_000;
// expect()/action waits are a *sub*-budget of one test, not the whole test —
// scaling only `testTimeout` left these fixed at 5000ms, so an individual
// assertion (a locator appearing, a click landing) could still time out on a
// slower hosted runner well before the overall test budget was exhausted.
// Three PR-630 CI runs each failed a *different* handful of specs at exactly
// this 5000ms boundary (async-data, calm-scenarios, inspector, chat-history,
// status-strip, ...) — no functional overlap between runs, the signature of
// environment timing, not a logic bug.
const assertTimeout = process.env.CI ? 10_000 : 5_000;

// E2E for the web UI front-end. A static server serves the repo; each spec
// injects the mock bridge (no Qt) and drives streaming/cancellation.
export default defineConfig({
  testDir: "vesta/assets/web/__tests__/e2e",
  timeout: testTimeout,
  expect: { timeout: assertTimeout },
  // A mandatory attempt is evidence, not a flake vote. Keep one attempt so an
  // intermittent failure remains visible instead of being overwritten by a
  // later retry. A maintainer can rerun the exact SHA as a separate run/attempt.
  retries: 0,
  // Token screenshots use bundled fonts and a fixed viewport, so a single
  // baseline is intentional across the Windows desktop build and Linux CI.
  snapshotPathTemplate: "{testDir}/{testFilePath}-snapshots/{arg}{ext}",
  fullyParallel: true,
  workers: 4,
  // CI uploads only ci_local's bounded, canonical-redactor JSON manifest. Raw
  // HTML reports, traces and screenshots may contain repository/user content.
  // #618/#621: `line` alone is human text, so a failing hosted run left no
  // machine-readable record of *what* failed. Diagnosis degenerated into
  // blacklisting log lines out of a fixed character budget -- webserver access
  // logs, then per-test progress lines -- each fix correct and each
  // insufficient, because a chatty reporter will always find a new way to fill
  // the buffer. The JSON reporter ends that: ci_local reads the structured
  // result and prints the failing spec, its error and the worker's exit
  // condition, regardless of how much noise the run produced.
  //
  // Still no HTML report, trace or screenshot in CI: those may carry
  // repository or user content, and the JSON file records spec identity,
  // status, duration and error message only.
  reporter: process.env.CI
    ? [["line"], ["json", { outputFile: "playwright-results.json" }]]
    : [["list"]],
  use: {
    baseURL: "http://localhost:8099",
    actionTimeout: assertTimeout,
    screenshot: process.env.CI ? "off" : "only-on-failure",
    trace: process.env.CI ? "off" : "retain-on-failure",
  },
  webServer: {
    // Four browser workers fetch the application bundle at once.  The
    // standard `http.server` CLI is single-threaded, which makes those first
    // loads serialize on slower hosted Windows runners and causes false test
    // timeouts.  Keep the zero-dependency server, but handle each request in
    // its own thread just as the production web server would.
    //
    // `directory` is explicit rather than inherited from the working
    // directory, so what is served cannot depend on where the process happened
    // to be started.
    command:
      "python -c \"import functools;from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler;" +
      "h=functools.partial(SimpleHTTPRequestHandler, directory='.');" +
      "ThreadingHTTPServer(('localhost', 8099), h).serve_forever()\"",
    port: 8099,
    // Never inherit a server this config did not start. Playwright cannot tell
    // whether something already on the port serves the right tree, and a stale
    // one silently turns every spec into a test of old assets: a suite that
    // passes while proving nothing, or fails on code that is already fixed.
    // Starting one costs about a second; that is the cheaper mistake.
    reuseExistingServer: false,
    timeout: 20000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
