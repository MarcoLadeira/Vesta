import { test, expect } from "@playwright/test";

import { openApp, openNav, openSettings } from "./helpers/app.js";


const MUTATING_TOOL = {
  title: "Panic mode",
  text: "Switch to local-only routing?",
  needs_confirm: true,
  apply: "panic",
};

test("mutating tool renders an approval card — approve applies, exactly once", async ({ page }) => {
  await openApp(page, {
    toolResponses: { panic: MUTATING_TOOL },
    applyToolResponses: { panic: { text: "Panic mode enabled." } },
  });
  let dialogOpened = false;
  page.on("dialog", async (dialog) => { dialogOpened = true; await dialog.dismiss(); });
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="firewall"]').click();
  await page.getByRole("button", { name: "Enable panic" }).click();

  // The gate is a real in-chat card, not a native browser dialog.
  const card = page.getByRole("group", { name: "Approval required" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("Panic mode");
  await expect(card).toContainText("Switch to local-only routing?");
  await expect(card).toContainText("Config change");
  await expect(card).toContainText("Affects");
  expect(await page.evaluate(() => window.__mock.appliedTools)).toEqual([]);

  await card.getByRole("button", { name: "Approve once" }).click();
  await expect(page.locator(".tool-card")).toContainText("Panic mode enabled");
  expect(await page.evaluate(() => window.__mock.appliedTools)).toEqual(["panic"]);
  // Buttons lock after the decision: no double-apply.
  await expect(card.getByRole("button", { name: "Approve once" })).toBeDisabled();
  await expect(card).toContainText("Approved");
  expect(dialogOpened).toBe(false);
});

test("denied approval applies nothing and says so calmly", async ({ page }) => {
  await openApp(page, { toolResponses: { panic: MUTATING_TOOL } });
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="firewall"]').click();
  await page.getByRole("button", { name: "Enable panic" }).click();
  const card = page.getByRole("group", { name: "Approval required" });
  await card.getByRole("button", { name: "Deny" }).click();
  await expect(card).toContainText("Denied — nothing was changed.");
  await expect(page.locator(".tool-card")).toContainText("Cancelled");
  expect(await page.evaluate(() => window.__mock.appliedTools)).toEqual([]);
  await expect(card.getByRole("button", { name: "Deny" })).toBeDisabled();
});

test("read-only tool result renders without any approval gate", async ({ page }) => {
  await openApp(page, {
    toolResponses: { connect: { title: "Connections", text: "Claude connected.", needs_confirm: false } },
  });
  let dialogOpened = false;
  page.on("dialog", async (dialog) => { dialogOpened = true; await dialog.dismiss(); });
  await openSettings(page, "providers");
  // "Connect CLI accounts…" lives on the Providers & Connections page.
  await page.locator("#setConnect").click();
  await expect(page.locator(".tool-card")).toContainText("Claude connected");
  await expect(page.locator(".approval-card")).toHaveCount(0);
  expect(dialogOpened).toBe(false);
});

test("slash commands run local tools and never reach a paid model", async ({ page }) => {
  await openApp(page, { toolResponses: { panic: MUTATING_TOOL } });
  await page.fill("#input", "/panic");
  await page.getByRole("button", { name: "Send" }).click();
  // The tool ran locally and raised its approval gate…
  await expect(page.getByRole("group", { name: "Approval required" })).toBeVisible();
  expect(await page.evaluate(() => window.__mock.runTools)).toEqual(["panic"]);
  // …and no model request was ever created from the slash command.
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});

test("approval card is keyboard-operable", async ({ page }) => {
  await openApp(page, {
    toolResponses: { panic: MUTATING_TOOL },
    applyToolResponses: { panic: { text: "Panic mode enabled." } },
  });
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="firewall"]').click();
  await page.getByRole("button", { name: "Enable panic" }).click();
  const approve = page.getByRole("button", { name: "Approve once" });
  await approve.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".tool-card")).toContainText("Panic mode enabled");
});
