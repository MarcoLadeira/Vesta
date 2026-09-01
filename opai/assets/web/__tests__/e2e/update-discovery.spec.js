import { test, expect } from "@playwright/test";
import { openApp } from "./helpers/app.js";

/**
 * OPai has to look, not just report.
 *
 * Everything about updates in the shell reports state: boot reads the stored
 * operation "without touching the network", and the 2s loop calls `maintain()`,
 * which reconciles and advances persisted work but performs no discovery
 * (verified directly against the service: `maintain()` left
 * `last_successful_check_at` untouched).
 *
 * The only call that actually looked was a button in Settings. So a source
 * checkout could sit any number of commits behind origin/main and the app
 * would keep repeating its last cached answer -- and restarting could not fix
 * it, because boot is precisely the path that does not look.
 *
 * The detection itself was never broken: pointed at a checkout three commits
 * back, `check_for_update` returned `commits_behind` correctly. It was simply
 * never invoked.
 */
test("startup asks whether anything landed, instead of only reading the last answer", async ({ page }) => {
  await openApp(page);

  // Forced: a restart is implicitly asking "did anything land while I was
  // away", and an unforced check would be swallowed by the service's four-hour
  // minimum interval.
  await expect.poll(() => page.evaluate(() => window.__mock.updateChecks), { timeout: 8000 })
    .toEqual([true]);
});

test("an update found at startup reaches the banner", async ({ page }) => {
  await openApp(page, {
    updateCheckResponse: {
      schema_version: 1,
      operation: {
        state: "available",
        candidate: { version: "0.3.0" },
        safe_diagnostic: "",
      },
      policy: {},
    },
  });

  // Assert the shell becomes *visible*, not that the text is present: every
  // banner state's copy is static markup inside a hidden #updateShell, so
  // `toContainText("Update available")` passes on a fresh boot that never
  // checked anything. This test passed with the startup check deleted until
  // that was noticed.
  await expect(page.locator("#updateShell")).toBeVisible({ timeout: 8000 });
  await expect(page.locator("#updateBanner")).toContainText("Update available");
});
