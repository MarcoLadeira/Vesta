import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openNav, sendPrompt } from "./helpers/app.js";


// Appearance settings (#241): density and reduced motion apply to the document
// root instantly, persist through savePref, and are re-applied at boot.

const openAppearance = async (page) => {
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="appearance"]').click();
};

test("the Appearance page renders honest defaults", async ({ page }) => {
  await openApp(page);
  await openAppearance(page);
  const density = page.locator('[data-appearance-key="density"]');
  const motion = page.locator('[data-appearance-key="reduced_motion"]');
  const responseDensity = page.locator('[data-appearance-key="response_density"]');
  await expect(density.locator("button.active")).toHaveText("Comfortable");
  await expect(motion.locator("button.active")).toHaveText("System");
  await expect(responseDensity.locator("button.active")).toHaveText("Balanced");
  // Light mode shipped as a real choice (theme.spec.js); dark is still the default.
  await expect(page.locator('[data-appearance-key="theme"] button.active')).toHaveText("Dark");
});

test("response density is independent, applies instantly, and persists", async ({ page }) => {
  await openApp(page, { settings: { prefs: { response_density: "compact" } } });
  await openAppearance(page);
  const responseDensity = page.locator('[data-appearance-key="response_density"]');
  await expect(responseDensity.locator("button.active")).toHaveText("Compact");
  await responseDensity.locator('button[data-value="detailed"]').click();
  expect(await page.evaluate(() => document.documentElement.dataset.responseDensity)).toBe("detailed");
  expect(await page.evaluate(() => document.documentElement.classList.contains("density-compact"))).toBe(false);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["response_density", "detailed"]);
});

test("response density updates existing and new response shells", async ({ page }) => {
  await openApp(page, { boot: { prefs: { responseDensity: "compact" } } });
  const requestId = await sendPrompt(page, "Show the response shell");
  await finishRequest(page, requestId, { answer: "A focused response." });
  const shell = page.locator(".msg .response-shell").last();
  await expect(shell).toHaveAttribute("data-response-density", "compact");
  await openAppearance(page);
  await page.locator('[data-appearance-key="response_density"] button[data-value="detailed"]').click();
  await expect(shell).toHaveAttribute("data-response-density", "detailed");
});

test("response density changes disclosure without discarding structured evidence", async ({ page }) => {
  await openApp(page, { boot: { prefs: { responseDensity: "compact" } } });
  const requestId = await sendPrompt(page, "Show density behavior");
  await finishRequest(page, requestId, {
    answer: "Density changes presentation only.",
    presentation: {
      schema_version: 1,
      run: { state: "completed", label: "Completed" },
      activity: [{ phase: "verify", status: "passed", message: "Checks passed" }],
    },
    verification_manifest: {
      checks: [{ check_id: "unit", kind: "unit", requirement: "Run checks", status: "passed" }],
    },
    workflow: {
      phase: "completed",
      diff_review: {
        summary: { files: 2 },
        files: [
          { path: "one.js", decision: "approved", hunks: [] },
          { path: "two.js", decision: "approved", hunks: [] },
        ],
      },
    },
  });
  const shell = page.locator(".msg .response-shell").last();
  await expect(shell.locator(".timeline.done")).toHaveAttribute("hidden", "");
  await expect(shell.locator(".verification-check[open]")).toHaveCount(0);
  await expect(shell.locator(".diff-file2[open]")).toHaveCount(0);

  await openAppearance(page);
  await page.locator('[data-appearance-key="response_density"] button[data-value="detailed"]').click();
  await expect(shell.locator(".timeline.done")).not.toHaveAttribute("hidden", "");
  await expect(shell.locator(".verification-check[open]")).toHaveCount(1);
  await expect(shell.locator(".diff-file2[open]")).toHaveCount(2);

  await page.locator('[data-appearance-key="response_density"] button[data-value="balanced"]').click();
  await expect(shell.locator(".timeline.done")).toHaveAttribute("hidden", "");
  await expect(shell.locator(".verification-check[open]")).toHaveCount(0);
  await expect(shell.locator(".diff-file2[open]")).toHaveCount(1);
  await expect(shell.locator(".verification-card")).toContainText("1 passed");
});

test("compact density applies to the root instantly and persists the pref", async ({ page }) => {
  await openApp(page);
  await openAppearance(page);
  await page.locator('[data-appearance-key="density"] button[data-value="compact"]').click();
  expect(await page.evaluate(() => document.documentElement.classList.contains("density-compact"))).toBe(true);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["density", "compact"]);
  await page.locator('[data-appearance-key="density"] button[data-value="comfortable"]').click();
  expect(await page.evaluate(() => document.documentElement.classList.contains("density-compact"))).toBe(false);
});

test("reduced-motion override works in all three states", async ({ page }) => {
  await openApp(page);
  await openAppearance(page);
  const motionButton = (value) => page.locator(`[data-appearance-key="reduced_motion"] button[data-value="${value}"]`);

  await motionButton("on").click();
  expect(await page.evaluate(() => document.documentElement.dataset.motion)).toBe("on");
  // The override force-disables animations: the active pane's entry animation
  // computes to (effectively) zero duration.
  const duration = await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector(".settings-pane.active")).animationDuration));
  expect(duration).toBeLessThan(0.001);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["reduced_motion", "on"]);

  await motionButton("off").click();
  expect(await page.evaluate(() => document.documentElement.dataset.motion)).toBe("off");

  await motionButton("system").click();
  expect(await page.evaluate(() => document.documentElement.dataset.motion)).toBeUndefined();
});

test("persisted appearance is applied at boot, before settings ever opens", async ({ page }) => {
  await openApp(page, { boot: { prefs: { density: "compact", reducedMotion: "on", responseDensity: "compact" } } });
  expect(await page.evaluate(() => document.documentElement.classList.contains("density-compact"))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.dataset.motion)).toBe("on");
  expect(await page.evaluate(() => document.documentElement.dataset.responseDensity)).toBe("compact");
});
