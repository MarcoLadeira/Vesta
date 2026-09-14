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
  publisher_identity: "CN=Vesta Software Ltd",
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

test("passive up-to-date snapshots keep Settings quiet", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "about");
  await expect(page.locator('[data-update-status="up_to_date"]')).toBeVisible();

  await page.evaluate(() => {
    window.__mock.bridge.updateReady.emit(JSON.stringify({
      operation: { state: "up_to_date", candidate: null, safe_diagnostic: null },
      policy: { discovery_enabled: true },
    }));
  });

  await expect(page.locator('[data-update-status="up_to_date"]')).toBeVisible();
  expect(await page.locator("#toast").getAttribute("class")).not.toMatch(/\bshow\b/);
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
  await expect(page.locator("#updateSheet")).toContainText("Vesta 0.3.0", seen);
  await expect(page.locator("#updateSheet")).toContainText("12.0 MB", seen);
  await expect(page.locator("#updateSheet")).toContainText("stable", seen);
  await expect(page.locator("#updateSheet")).toContainText("recommended", seen);
  await expect(page.locator("#updateSheet")).toContainText("CN=Vesta Software Ltd", seen);
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
  await expect(page.locator("#toast")).toContainText("latest version", seen);
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

test("manual-update state surfaces the backend diagnostic in the sheet", async ({ page }) => {
  const diagnostic = "This source checkout is 3 commits behind origin/main; update with the explicit developer update command.";
  await openWithUpdate(page, updateState("unsupported_install", { safe_diagnostic: diagnostic }));
  await page.locator("#updateBanner").click();
  await expect(page.locator("#updateSheetDescription")).toContainText("3 commits behind origin/main", seen);
});

/* Developer source checkouts update by fast-forwarding origin/main, so the
   sheet offers the deliberate apply action instead of a fake download. */
function sourceCheckout(state, operation = {}) {
  const update = updateState(state, operation);
  update.installed.install_type = "source_checkout";
  return update;
}

test("Settings offers the same restart, and only when it is possible", async ({ page }) => {
  const done = sourceCheckout("completed", {
    safe_diagnostic: "Updated automatically: fast-forwarded 2 commits from origin/main. Restart Vesta to use it.",
  });
  done.restart_available = true;
  await openApp(page, { boot: { update: done }, settings: { about: { update: done } } });
  await openSettings(page, "about");
  await expect(page.locator("#settingsRestartUpdate")).toBeVisible();

  const stuck = sourceCheckout("completed", { safe_diagnostic: done.operation.safe_diagnostic });
  stuck.restart_available = false;
  await page.evaluate((detail) => {
    window.dispatchEvent(new CustomEvent("opai-update-state", { detail }));
  }, stuck);
  await expect(page.locator("#settingsRestartUpdate")).toHaveCount(0);
});

test("the automatic-downloads switch says what it does to a git checkout", async ({ page }) => {
  const update = sourceCheckout("up_to_date");
  await openApp(page, { boot: { update }, settings: { about: { update } } });
  await openSettings(page, "about");
  const row = page.locator('[data-update-policy="automatic_downloads"]');
  await expect(row).toContainText("fast-forwards this checkout to origin/main", seen);
  await expect(row).not.toContainText("signed packaged updates", seen);
});

test("the automatic-downloads switch still speaks of packages on a packaged build", async ({ page }) => {
  await openWithUpdate(page, updateState("up_to_date"));
  await openSettings(page, "about");
  await expect(page.locator('[data-update-policy="automatic_downloads"]')).toContainText(
    "signed packaged updates download and verify",
    seen,
  );
});

test("a source update in progress shows the stage it is on, not a frozen window", async ({ page }) => {
  // The reinstall is seconds long with nothing to show for it. A check that
  // is mid-apply publishes a stage label; that is the signal to stop hiding.
  const update = sourceCheckout("checking", {
    progress_label: "Reinstalling Vesta",
    downloaded_bytes: 3,
    total_bytes: 4,
  });
  await openWithUpdate(page, update);
  await expect(page.locator("#updateShell")).toBeVisible();
  await expect(page.locator("#updateBannerText")).toHaveText("Updating Vesta");
  // The bar lives in the sheet, so it is only on screen once the sheet is.
  await page.locator("#updateBanner").click();
  await expect(page.locator("#updateSheetDescription")).toHaveText("Reinstalling Vesta");
  await expect(page.locator("#updateProgress")).toBeVisible();
  await expect(page.locator("#updateProgress")).toHaveAttribute("aria-valuenow", "75");
});

test("an ordinary check stays out of the way", async ({ page }) => {
  await openWithUpdate(page, sourceCheckout("checking"));
  await expect(page.locator("#updateShell")).toBeHidden();
});

test("an applied source update offers the restart that finishes it", async ({ page }) => {
  const update = sourceCheckout("completed", {
    safe_diagnostic: "Updated automatically: fast-forwarded 2 commits from origin/main to 0.9.1. Restart Vesta to use it.",
  });
  update.restart_available = true;
  await openWithUpdate(page, update);
  await page.locator("#updateBanner").click();
  const restart = page.getByRole("button", { name: "Restart now" });
  await expect(restart).toBeVisible();
  await restart.click();
  await expect.poll(() => page.evaluate(() => window.__mock.updateActions)).toEqual(["restart_now"]);
});

test("no restart is offered when the app cannot start itself again", async ({ page }) => {
  // A button that closes the window and does not bring it back is worse than
  // no button, so availability is established before it is ever shown.
  const update = sourceCheckout("completed", {
    safe_diagnostic: "Updated automatically: fast-forwarded 2 commits from origin/main. Restart Vesta to use it.",
  });
  update.restart_available = false;
  await openWithUpdate(page, update);
  await page.locator("#updateBanner").click();
  await expect(page.getByRole("button", { name: "Restart now" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Check again" })).toBeVisible();
});

test("a restart that could not be arranged says so instead of going quiet", async ({ page }) => {
  const start = sourceCheckout("completed", {
    safe_diagnostic: "Updated automatically: fast-forwarded 2 commits from origin/main. Restart Vesta to use it.",
  });
  start.restart_available = true;
  const refused = sourceCheckout("completed", { safe_diagnostic: start.operation.safe_diagnostic });
  refused.restart_available = true;
  refused.restart = { ok: false, message: "Vesta could not arrange its own restart. Quit and open it again to finish the update." };
  await openApp(page, {
    boot: { update: start },
    settings: { about: { update: start } },
    updateActionResponses: { restart_now: refused },
  });
  await page.locator("#updateBanner").click();
  await page.getByRole("button", { name: "Restart now" }).click();
  await expect(page.locator("#toast")).toContainText("could not arrange its own restart", seen);
});

test("an automatic source fast-forward says so and asks for the restart", async ({ page }) => {
  // A silent auto-update is the bug, not the feature: the process keeps the
  // old code in memory, so the banner has to say a restart is what's left.
  const done = sourceCheckout("completed", {
    safe_diagnostic: "Updated automatically: fast-forwarded 3 commits from origin/main to 0.2.1a2. Restart Vesta to use it.",
  });
  await openWithUpdate(page, done);
  await expect(page.locator("#updateShell")).toBeVisible();
  await expect(page.locator("#updateBannerText")).toHaveText("Update installed");
  await page.locator("#updateBanner").click();
  await expect(page.locator("#updateSheetDescription")).toContainText("fast-forwarded 3 commits");
  await expect(page.locator("#updateSheetDescription")).toContainText("Restart Vesta to use it.");
  await openSettings(page, "about");
  await expect(page.locator('[data-update-status="completed"]')).toBeVisible();
});

test("source checkouts get Update now, and a successful apply reports the restart", async ({ page }) => {
  const start = sourceCheckout("unsupported_install", { safe_diagnostic: "This source checkout is 3 commits behind origin/main; update with the explicit developer update command." });
  const done = sourceCheckout("up_to_date");
  done.developer_apply = { ok: true, restart_required: true, message: "Updated to 0.2.1a2 — restart Vesta to use it." };
  await openApp(page, {
    boot: { update: start },
    settings: { about: { update: start } },
    updateActionResponses: { developer_apply: done },
  });
  await page.locator("#updateBanner").click();
  await page.getByRole("button", { name: "Update now" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.updateActions)).toEqual(["developer_apply"]);
  await expect(page.locator("#toast")).toContainText("restart Vesta", seen);
  // the refreshed state is up_to_date, so the persistent control hides
  await expect(page.locator("#updateShell")).toBeHidden();
});

test("a dirty checkout escalates to the explicit stash-and-restore step", async ({ page }) => {
  const start = sourceCheckout("unsupported_install", { safe_diagnostic: "This source checkout is 3 commits behind origin/main; update with the explicit developer update command." });
  const dirty = sourceCheckout("unsupported_install", { safe_diagnostic: start.operation.safe_diagnostic });
  dirty.developer_apply = { ok: false, dirty: true, message: "There are uncommitted local changes — commit, stash, or discard them before updating." };
  await openApp(page, {
    boot: { update: start },
    settings: { about: { update: start } },
    updateActionResponses: { developer_apply: dirty },
  });
  await page.locator("#updateBanner").click();
  await page.getByRole("button", { name: "Update now" }).click();
  await expect(page.locator("#toast")).toContainText("uncommitted local changes", seen);
  await expect(page.getByRole("button", { name: "Update anyway (stash & restore)" })).toBeVisible();
});

test("non-source manual installs never get a developer apply button", async ({ page }) => {
  await openWithUpdate(page, updateState("unsupported_install", { safe_diagnostic: "Manual update required." }));
  await page.locator("#updateBanner").click();
  await expect(page.getByRole("button", { name: "Update now" })).toHaveCount(0);
});

test("hidden states still notify settings listeners so cards refresh", async ({ page }) => {
  const start = sourceCheckout("unsupported_install", { safe_diagnostic: "This source checkout is 3 commits behind origin/main; update with the explicit developer update command." });
  const done = sourceCheckout("up_to_date");
  done.developer_apply = { ok: true, restart_required: true, message: "Updated — restart Vesta to use it." };
  await openApp(page, {
    boot: { update: start },
    settings: { about: { update: start } },
    updateActionResponses: { developer_apply: done },
  });
  await openSettings(page, "about");
  await expect(page.locator('[data-update-status="unsupported_install"]')).toBeVisible();
  await page.locator("#settingsApplyUpdate").click();
  // up_to_date is not a visible banner state; the card must still refresh
  await expect(page.locator('[data-update-status="up_to_date"]')).toBeVisible();
  await expect(page.locator("#settingsCheckUpdate")).toBeEnabled();
});

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
