import { test, expect } from "@playwright/test";

import { openApp, openSettings } from "./helpers/app.js";


// Mandatory-update system: an honest "check for updates" everywhere it
// matters — a shell-wide banner from the cached boot check, an About page
// card with the same three states (unknown / up to date / available), and
// an "Update now" action gated by the same styled inline confirm every
// other mutating settings action uses.

const seen = { useInnerText: true };

test("no banner and an up-to-date About card when already current", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#updateBanner")).toBeHidden();
  await openSettings(page, "about");
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("latest version", seen);
  await expect(page.locator('[data-update-status="up-to-date"]')).toBeVisible();
});

test("an available update shows the shell banner and the About card, with a working Update now flow", async ({ page }) => {
  await openApp(page, {
    settings: {
      about: {
        version: "0.2.0a1",
        release_stage: "alpha.1",
        update: {
          current_version: "0.2.0a1", checked: true, up_to_date: false,
          latest_version: "0.3.0", commits_behind: 5, branch: "main", reason: null,
        },
      },
    },
    boot: {
      update: {
        current_version: "0.2.0a1", checked: true, up_to_date: false,
        latest_version: "0.3.0", commits_behind: 5, branch: "main", reason: null,
      },
    },
  });
  const banner = page.locator("#updateBanner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("0.3.0");

  // The banner's action opens Settings > About, the real home for the update.
  await banner.getByRole("button", { name: "Update now" }).click();
  await expect(page.locator("#view-settings")).toBeVisible();
  await expect(page.locator('[data-update-status="available"]')).toBeVisible();
  await expect(page.locator("#settingsPage")).toContainText("0.3.0", seen);
  await expect(page.locator("#settingsPage")).toContainText("5 changes behind", seen);

  // Applying goes through the styled inline confirm — never an instant mutation.
  await page.locator("#settingsApplyUpdate").click();
  const confirm = page.locator(".inline-confirm");
  await expect(confirm).toBeVisible();
  await expect(confirm).toContainText("uncommitted local changes");
  await confirm.locator('[data-ic="ok"]').click();

  await expect(page.locator('[data-update-status="restart"]')).toBeVisible();
  await expect(page.locator("#settingsPage")).toContainText("Updated to 0.3.0", seen);
  // The shell banner clears once the update is installed — restart, not "behind".
  await expect(page.locator("#updateBanner")).toBeHidden();

  await page.locator("#settingsRestartOpai").click();
  await expect.poll(() => page.evaluate(() => window.__mock.updateRestarts)).toBe(1);
});

test("cancelling the update confirm applies nothing", async ({ page }) => {
  await openApp(page, {
    settings: {
      about: {
        version: "0.2.0a1",
        release_stage: "alpha.1",
        update: { checked: true, up_to_date: false, latest_version: "0.3.0", commits_behind: 1, branch: "main" },
      },
    },
  });
  await openSettings(page, "about");
  await page.locator("#settingsApplyUpdate").click();
  await page.locator(".inline-confirm [data-ic=\"cancel\"]").click();
  expect(await page.evaluate(() => window.__mock.updateApplies)).toBe(0);
  await expect(page.locator('[data-update-status="available"]')).toBeVisible();
});

test("uncommitted local changes offer an Update anyway choice that stashes and restores them", async ({ page }) => {
  await openApp(page, {
    settings: {
      about: {
        version: "0.2.0a1",
        release_stage: "alpha.1",
        update: {
          current_version: "0.2.0a1", checked: true, up_to_date: false,
          latest_version: "0.3.0", commits_behind: 5, branch: "main", reason: null,
        },
      },
    },
    applyUpdateDirty: true,
    applyUpdateResponse: {
      ok: true, restart_required: true, installed_version: "0.3.0",
      local_changes_restored: true,
    },
  });
  await openSettings(page, "about");

  await page.locator("#settingsApplyUpdate").click();
  await page.locator(".inline-confirm [data-ic=\"ok\"]").click();

  // First attempt refuses (dirty) and offers "Update anyway" instead of a dead end.
  const anywayConfirm = page.locator(".inline-confirm");
  await expect(anywayConfirm).toBeVisible();
  await expect(anywayConfirm).toContainText("Update anyway");
  await anywayConfirm.getByRole("button", { name: "Update anyway" }).click();

  await expect.poll(() => page.evaluate(() => window.__mock.updateApplyForce)).toEqual([false, true]);
  await expect(page.locator('[data-update-status="restart"]')).toBeVisible();
  await expect(page.locator("#settingsPage")).toContainText("restored", seen);
});

test("Check for updates always forces a live check, never the stale cache", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "about");
  await page.locator("#settingsCheckUpdate").click();
  await expect.poll(() => page.evaluate(() => window.__mock.updateChecks)).toEqual([true]);
});

test("an unknown check state is honest, not a false up-to-date claim", async ({ page }) => {
  await openApp(page, {
    settings: {
      about: {
        version: "0.2.0a1",
        release_stage: "alpha.1",
        update: { checked: false, reason: "You may be offline." },
      },
    },
  });
  await openSettings(page, "about");
  const status = page.locator('[data-update-status="unknown"]');
  await expect(status).toBeVisible();
  await expect(status).toContainText("You may be offline.");
  await expect(page.locator('[data-update-status="up-to-date"]')).toHaveCount(0);
});
