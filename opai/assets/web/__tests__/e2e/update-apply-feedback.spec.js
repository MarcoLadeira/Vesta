import { test, expect } from "@playwright/test";
import { openApp } from "./helpers/app.js";

const sourceBehind = {
  schema_version: 1,
  operation: {
    state: "unsupported_install",
    candidate: {},
    error_category: "manual_update_required",
    safe_diagnostic: "This source checkout is 3 commits behind origin/main.",
  },
  policy: {},
  installed: { version: "0.2.1a1", install_type: "source_checkout" },
  restart_available: true,
};

test("the action that is running stays disabled while it runs", async ({ page }) => {
  // The bug: renderUpdateActions rebuilds every button (`replaceChildren`) on
  // each banner re-render, and the banner re-renders whenever the operation
  // changes -- repeatedly, during an apply. So the disabled button was
  // replaced by a fresh enabled one within a couple of seconds, and a
  // fast-forward taking a fetch, a merge and a reinstall looked like a button
  // that did nothing. Reported as roughly twenty clicks.
  await openApp(page, { boot: { update: sourceBehind } });

  await page.locator("#updateBanner").click();
  const apply = page.locator('#updateSheetActions [data-update-action="developer_apply"]');
  await expect(apply).toBeVisible();
  await apply.click();

  await expect(apply).toBeDisabled();
  // Survive several re-render cycles of the 2s status poller.
  await page.evaluate(() => {
    const u = JSON.parse(JSON.stringify(window.__opaiTestUpdate || {}));
    return u;
  });
  for (let i = 0; i < 3; i += 1) {
    await page.waitForTimeout(700);
    await expect(page.locator('#updateSheetActions [data-update-action="developer_apply"]')).toBeDisabled();
  }
});

test("a finished update stays on screen and offers the restart", async ({ page }) => {
  // A successful apply used to re-check, conclude "up to date", and land in a
  // state the banner does not render -- so the update UI vanished and the only
  // word about the pending restart was a transient toast with no button.
  await openApp(page, {
    boot: {
      update: {
        ...sourceBehind,
        operation: {
          state: "completed",
          candidate: {},
          safe_diagnostic: "Updated to 0.2.2 — restart Vesta to use it.",
        },
      },
    },
  });

  await expect(page.locator("#updateShell")).toBeVisible();
  await page.locator("#updateBanner").click();
  await expect(page.locator("#updateSheetDescription")).toContainText("restart Vesta to use it");
  await expect(page.locator('#updateSheetActions [data-update-action="restart_now"]')).toBeVisible();
});
