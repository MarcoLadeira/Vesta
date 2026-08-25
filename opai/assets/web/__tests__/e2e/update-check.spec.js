import { test, expect } from "@playwright/test";

import { openApp, openSettings } from "./helpers/app.js";


const seen = { useInnerText: true };

function updateState(state, operation = {}, policy = {}) {
  return {
    operation: {
      state,
      candidate: null,
      safe_diagnostic: null,
      downloaded_bytes: 0,
      total_bytes: 0,
      ...operation,
    },
    policy: {
      discovery_enabled: true,
      automatic_downloads: false,
      automatic_install_on_quit: false,
      channel: "stable",
      owner: "opai",
      ...policy,
    },
    installed: { version: "0.2.0a1", build_id: "old-build", install_type: "portable" },
  };
}

const candidate = {
  version: "0.3.0",
  build_id: "build-030",
  channel: "stable",
  artifact_size: 12 * 1024 * 1024,
  criticality: "recommended",
  publisher_identity: "CN=OPai Software Ltd",
  release_notes: "Safer updates and a calmer restart flow.",
  verification: { native_mechanism: "Windows Package Manager + Authenticode" },
};

async function openWithUpdate(page, update) {
  return openApp(page, {
    boot: { update },
    settings: { about: { update } },
  });
}

test("up-to-date builds keep the persistent control hidden and report current in Settings", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#updateShell")).toBeHidden();
  await openSettings(page, "about");
  await expect(page.locator('[data-update-status="up_to_date"]')).toBeVisible();
  await expect(page.locator("#settingsPage")).toContainText("latest version", seen);
});

test("the UI marks the first interactive frame so discovery can start after launch", async ({ page }) => {
  await openApp(page);
  await expect.poll(() => page.evaluate(() => window.__mock.interactiveMarks)).toBe(1);
});

test("available update opens details without navigating away from active work", async ({ page }) => {
  const available = updateState("available", { candidate });
  await openWithUpdate(page, available);
  await expect(page.locator("#view-chat")).toBeVisible();
  await page.locator("#updateBanner").click();
  await expect(page.locator("#view-chat")).toBeVisible();
  await expect(page.locator("#updateSheet")).toBeVisible();
  await expect(page.locator("#updateSheet")).toContainText("OPai 0.3.0", seen);
  await expect(page.locator("#updateSheet")).toContainText("12.0 MB", seen);
  await expect(page.locator("#updateSheet")).toContainText("stable", seen);
  await expect(page.locator("#updateSheet")).toContainText("recommended", seen);
  await expect(page.locator("#updateSheet")).toContainText("CN=OPai Software Ltd", seen);
  await expect(page.locator("#updateSheet")).toContainText("Windows Package Manager + Authenticode", seen);
  await expect(page.locator("#updateSheet")).toContainText(candidate.release_notes, seen);
});

test("download is a named backend action and advances to verified ready state", async ({ page }) => {
  await openWithUpdate(page, updateState("available", { candidate }));
  await page.locator("#updateBanner").click();
  await page.getByRole("button", { name: "Download update" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.updateActions)).toEqual(["download"]);
  await expect(page.locator("#updateBannerText")).toHaveText("Ready to restart");
  await expect(page.getByRole("button", { name: "Restart now" })).toBeVisible();
});

test("restart timing choices stay explicit", async ({ page }) => {
  await openWithUpdate(page, updateState("ready_to_install", { candidate, artifact_staged: true }));
  await page.locator("#updateBanner").click();
  await expect(page.getByRole("button", { name: "Restart now" })).toBeVisible();
  await expect(page.getByRole("button", { name: "When finished" })).toBeVisible();
  await expect(page.getByRole("button", { name: "On quit" })).toBeVisible();
  await page.getByRole("button", { name: "When finished" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.updateActions)).toEqual(["when_idle"]);
  await expect(page.locator("#updateBannerText")).toHaveText("Restart when finished");
});

test("Escape closes update details and restores focus to the control", async ({ page }) => {
  await openWithUpdate(page, updateState("available", { candidate }));
  const control = page.locator("#updateBanner");
  await control.click();
  await expect(page.locator("#updateSheetClose")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#updateSheet")).toBeHidden();
  await expect(control).toBeFocused();
});

test("idle state does not show a false update notice", async ({ page }) => {
  await openWithUpdate(page, updateState("idle"));
  await expect(page.locator("#updateShell")).toBeHidden();
});

test("completed state clears the persistent update control", async ({ page }) => {
  await openWithUpdate(page, updateState("completed"));
  await expect(page.locator("#updateShell")).toBeHidden();
});

test("release notes are rendered as text, never trusted markup", async ({ page }) => {
  const hostile = '<img src=x onerror="window.__updateXss=true">Security notes';
  await openWithUpdate(page, updateState("available", { candidate: { ...candidate, release_notes: hostile } }));
  await page.locator("#updateBanner").click();
  await expect(page.locator("#updateReleaseNotes img")).toHaveCount(0);
  await expect(page.locator("#updateReleaseNotes")).toContainText(hostile, seen);
  expect(await page.evaluate(() => window.__updateXss)).toBeUndefined();
});

test("manual check always forces live discovery", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "about");
  await page.locator("#settingsCheckUpdate").click();
  await expect.poll(() => page.evaluate(() => window.__mock.updateChecks)).toEqual([true]);
});

test("automatic downloads use app-wide updater policy instead of workspace preferences", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "about");
  const row = page.locator('[data-update-policy="automatic_downloads"]');
  await row.getByRole("button", { name: "On" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.updatePolicies)).toContainEqual(["automatic_downloads", true]);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).not.toContainEqual(["auto_update", "true"]);
});

test("install-on-quit requires separate explicit app-wide consent", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "about");
  const row = page.locator('[data-update-policy="automatic_install_on_quit"]');
  await expect(row.getByRole("button", { name: "Off" })).toHaveAttribute("aria-pressed", "true");
  await expect(row.getByRole("button", { name: "On" })).toBeDisabled();
  await page.locator('[data-update-policy="automatic_downloads"]').getByRole("button", { name: "On" }).click();
  await expect(row.getByRole("button", { name: "On" })).toBeEnabled();
  await row.getByRole("button", { name: "On" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.updatePolicies)).toContainEqual(["automatic_install_on_quit", true]);
});

test("update consent survives a workspace switch because it is application-wide", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "about");
  await page.locator('[data-update-policy="automatic_downloads"]').getByRole("button", { name: "On" }).click();
  await page.evaluate(() => window.__mock.switchWorkspace("/other/workspace"));
  await openSettings(page, "about");
  await expect(page.locator('[data-update-policy="automatic_downloads"]').getByRole("button", { name: "On" })).toHaveAttribute("aria-pressed", "true");
});

test("download progress exposes bounded assistive progress", async ({ page }) => {
  await openWithUpdate(page, updateState("downloading", {
    candidate, downloaded_bytes: 3 * 1024 * 1024, total_bytes: 12 * 1024 * 1024,
  }));
  await page.locator("#updateBanner").click();
  const progress = page.locator("#updateProgress");
  await expect(progress).toBeVisible();
  await expect(progress).toHaveAttribute("aria-valuenow", "25");
  await expect(progress).toHaveAttribute("aria-valuemin", "0");
  await expect(progress).toHaveAttribute("aria-valuemax", "100");
});

test("reduced-motion preference disables updater transitions", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openWithUpdate(page, updateState("downloading", { candidate }));
  const duration = await page.locator("#updateProgress span").evaluate((node) => getComputedStyle(node).transitionDuration);
  expect(Number.parseFloat(duration)).toBeLessThan(0.001);
});

const visibleStates = [
  ["available", "Update available"],
  ["downloading", "Downloading update"],
  ["verifying", "Verifying update"],
  ["ready_to_install", "Ready to restart"],
  ["waiting_for_idle", "Restart when finished"],
  ["install_on_quit", "Installs on quit"],
  ["deferred", "Update deferred"],
  ["failed_retriable", "Update paused"],
  ["failed_terminal", "Update blocked"],
  ["policy_blocked", "Managed by administrator"],
  ["unsupported_install", "Manual update required"],
  ["rollback_pending", "Recovery required"],
  ["needs_attention", "Update needs attention"],
  ["rolled_back", "Update rolled back"],
  ["unavailable", "Couldn’t check for updates"],
];

for (const [state, label] of visibleStates) {
  test(`persistent control renders canonical ${state} state`, async ({ page }) => {
    const policy = state === "policy_blocked" ? { owner: "intune" } : {};
    await openWithUpdate(page, updateState(state, { candidate, safe_diagnostic: "Safe diagnostic." }, policy));
    await expect(page.locator("#updateShell")).toBeVisible();
    await expect(page.locator("#updateBannerText")).toHaveText(label);
  });
}

/* Regression: the sheet is position:fixed precisely so the sidebar's overflow
   clip cannot crop it; focusing the close control must never scroll the
   sidebar sideways (scrollLeft used to jump to 131 and carry the whole rail
   off-screen). */
async function expectSheetOnScreen(page) {
  const sheet = page.locator("#updateSheet");
  await expect(sheet).toBeVisible();
  const geometry = await page.evaluate(() => {
    const rect = document.getElementById("updateSheet").getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      vw: window.innerWidth,
      sidebarScrollLeft: document.querySelector(".sidebar").scrollLeft,
    };
  });
  expect(geometry.left).toBeGreaterThanOrEqual(0);
  expect(geometry.right).toBeLessThanOrEqual(geometry.vw);
  expect(geometry.sidebarScrollLeft).toBe(0);
}

test("update details stay fully on screen without shifting the sidebar", async ({ page }) => {
  await openWithUpdate(page, updateState("available", { candidate }));
  await page.locator("#updateBanner").click();
  await expectSheetOnScreen(page);
  await expect(page.locator("#updateSheetClose")).toBeVisible();
  await expect(page.getByRole("button", { name: "Download update" })).toBeVisible();
});

test("update details stay on screen in the narrow drawer layout", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await openWithUpdate(page, updateState("unavailable", { safe_diagnostic: "The update feed could not be read." }));
  await page.locator("#sidebarToggle").click();
  await page.locator("#updateBanner").click();
  await expectSheetOnScreen(page);
  // the rail content itself must not be pushed off the left edge either
  const rail = await page.evaluate(() => {
    const rect = document.querySelector(".side-foot").getBoundingClientRect();
    return { left: rect.left, right: rect.right };
  });
  expect(rail.left).toBeGreaterThanOrEqual(0);
  expect(rail.right).toBeLessThanOrEqual(320);
});
