import { test, expect } from "@playwright/test";
import { openApp } from "./helpers/app.js";

/**
 * The front end does not schedule updates.
 *
 * It used to: a forced check three seconds after boot, then an ordinary one
 * every fifteen minutes. That put updater policy in the one place that cannot
 * know the install type, the channel, the retry state, or whether another
 * window is already doing it -- and because an ordinary check inside the
 * freshness window returns the persisted answer, roughly fifteen of every
 * sixteen of those ticks reached nothing at all. Measured against the real
 * service: eighteen ticks produced two remote calls.
 *
 * `UpdateScheduler` owns that decision now, in the backend, off the UI thread.
 * What is left on this side is a status poll and one explicit user action, and
 * these tests pin exactly that boundary.
 */

test("the front end schedules nothing", async ({ page }) => {
  await openApp(page);

  // Long enough that the old three-second startup timer would have fired.
  await page.waitForTimeout(4500);

  expect(await page.evaluate(() => window.__mock.updateChecks)).toEqual([]);
});

test("an update the backend reports reaches the banner, visibly", async ({
  page,
}) => {
  // Asserted on visibility, never on text. Every banner state's copy is static
  // markup inside a hidden #updateShell, so `toContainText("Update available")`
  // passes on a boot that discovered nothing -- an earlier version of this test
  // did exactly that, and passed with the startup check deleted.
  await openApp(page, {
    boot: {
      update: {
        schema_version: 1,
        operation: {
          state: "available",
          candidate: { version: "0.3.0" },
          safe_diagnostic: "",
        },
        policy: {},
      },
    },
  });

  await expect(page.locator("#updateShell")).toBeVisible();
  await expect(page.locator("#updateBanner")).toContainText("Update available");
});

test("a quiet updater keeps the banner out of the way", async ({ page }) => {
  // The counterpart to the test above: `up_to_date` is not in the banner's
  // state map, so the shell stays hidden. Without this, "visible" proves
  // nothing -- a shell that is always visible would satisfy it too.
  await openApp(page);

  await expect(page.locator("#updateShell")).toBeHidden();
});

test('main updates remain visible with automatic installation disabled and the agents strip hidden', async ({ page }, testInfo) => {
  await openApp(page, { boot: {
    prefs: { showAgentsStrip: false },
    update: {
      schema_version: 1,
      installed: { install_type: 'source_checkout' },
      operation: { state: 'unsupported_install', error_category: 'manual_update_required', safe_diagnostic: 'This source checkout is 3 commits behind origin/main.' },
      policy: { automatic_downloads: false, automatic_install_on_quit: false },
      discovery: { self_updatable: true, summary: { title: 'Update available', message: 'A newer Vesta is ready. Update when you are ready.' } },
    },
  } });
  await expect(page.locator('#agentsTeamStrip')).toBeHidden();
  await expect(page.locator('#updateShell')).toBeVisible();
  await page.locator('#updateBanner').click();
  const apply = page.locator('[data-update-action="developer_apply"]');
  await expect(apply).toBeVisible();
  await expect(apply).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath('manual-update-hidden-sidebar.png'), animations: 'disabled' });
  await apply.click();
  expect(await page.evaluate(() => window.__mock.updateActions)).toContain('developer_apply');
});
