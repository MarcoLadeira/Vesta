import { test, expect } from "@playwright/test";

import { openApp } from "./helpers/app.js";

// Bypass Permissions is a *switch*, not a fifth mode. As a mode row it
// replaced whichever mode you were working in, and turning it off could not
// give that mode back -- so the menu could not express "Accept edits, without
// the asking". These pin the composed shape.

const withBypass = (on) => ({
  boot: { prefs: { bypassPermissions: on, mode: "auto-edits" } },
});

async function openModeMenu(page) {
  await page.locator("#modeBtn").click();
  await expect(page.locator("#modePop")).toBeVisible();
}

test("the mode stays selected while bypass is on", async ({ page }) => {
  await openApp(page, withBypass(true));
  await openModeMenu(page);
  // Both are checked at once -- that is the whole point of a switch.
  const mode = page.locator('#modePop [data-id="auto-edits"]');
  const bypass = page.locator("#modePop [data-bypass]");
  await expect(mode).toHaveAttribute("aria-checked", "true");
  await expect(bypass).toHaveAttribute("aria-checked", "true");
});

test("bypass is unchecked while the mode still is", async ({ page }) => {
  await openApp(page, withBypass(false));
  await openModeMenu(page);
  await expect(page.locator('#modePop [data-id="auto-edits"]')).toHaveAttribute(
    "aria-checked",
    "true"
  );
  await expect(page.locator("#modePop [data-bypass]")).toHaveAttribute(
    "aria-checked",
    "false"
  );
});

test("bypass is a checkbox, not another radio in the mode group", async ({ page }) => {
  await openApp(page, withBypass(false));
  await openModeMenu(page);
  await expect(page.locator("#modePop [data-bypass]")).toHaveAttribute(
    "role",
    "menuitemcheckbox"
  );
  await expect(page.locator('#modePop [data-id="auto-edits"]')).toHaveAttribute(
    "role",
    "menuitemradio"
  );
});

test("toggling bypass persists it and leaves the mode alone", async ({ page }) => {
  await openApp(page, withBypass(false));
  await openModeMenu(page);
  await page.locator("#modePop [data-bypass]").click();

  await expect(page.locator("#modePop [data-bypass]")).toHaveAttribute(
    "aria-checked",
    "true"
  );
  // The mode selector -- the thing that decides the run -- is untouched.
  await expect(page.locator("#modeSel")).toHaveValue("auto-edits");
  // savedPrefs is a list of [key, value] pairs.
  const saved = await page.evaluate(() => window.__mock.savedPrefs || []);
  expect(saved).toContainEqual(["bypass_permissions", "true"]);
  expect(saved.map((pair) => pair[0])).not.toContain("default_mode");
});

test("the composer pill says bypass is on without opening the menu", async ({ page }) => {
  await openApp(page, withBypass(true));
  await expect(page.locator("#modeBtnLabel")).toHaveText("Accept edits · Bypass");
  await expect(page.locator("#modeBtn")).toHaveAttribute(
    "aria-label",
    /permissions bypassed/
  );
});

test("the pill shows only the mode when bypass is off", async ({ page }) => {
  await openApp(page, withBypass(false));
  await expect(page.locator("#modeBtnLabel")).toHaveText("Accept edits");
});
