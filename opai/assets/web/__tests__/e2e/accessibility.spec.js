import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("core controls expose useful accessible names", async ({ page }) => {
  await expect(page.locator("#headerNewChat")).toBeVisible();
  await expect(page.getByRole("button", { name: "Inspector" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await expect(page.getByPlaceholder(/Tell OPai what to build/)).toBeVisible();
});

test("keyboard shortcuts focus the composer and toggle Inspector", async ({ page }) => {
  await page.keyboard.press("Control+l");
  await expect(page.locator("#input")).toBeFocused();
  await page.keyboard.press("Control+i");
  await expect(page.locator("#app")).toHaveClass(/panel-hidden/);
  await page.keyboard.press("Control+i");
  await expect(page.locator("#app")).not.toHaveClass(/panel-hidden/);
});

test("workspace menu exposes expanded state and keyboard-close behavior", async ({ page }) => {
  const button = page.locator("#wsSwitch");
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await button.click();
  await expect(button).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Escape");
  await expect(button).toHaveAttribute("aria-expanded", "false");
});

test("activity status uses log and polite live-region semantics", async ({ page }) => {
  await sendPrompt(page);
  const toggle = page.locator(".gen-toggle");
  const controlledId = await toggle.getAttribute("aria-controls");
  expect(controlledId).toBeTruthy();
  await expect(page.locator(`#${controlledId}`)).toHaveAttribute("role", "log");
  await expect(page.locator(`#${controlledId}`)).toHaveAttribute("aria-label", "AI activity");
  await expect(page.locator(".gen-reassure")).toHaveAttribute("aria-live", "polite");
  await expect(page.locator("#inspLive")).toHaveAttribute("aria-live", "polite");
});

test("completed work log disclosure stays keyboard operable and correctly controlled", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    presentation: {
      schema_version: 1,
      activity: [{ phase: "test", status: "completed", message: "Focused checks passed" }],
    },
  });
  const toggle = page.locator(".gen-toggle.done");
  const controlledId = await toggle.getAttribute("aria-controls");
  const log = page.locator(`#${controlledId}`);
  await expect(log).toHaveAttribute("role", "log");
  await expect(log).toBeHidden();
  await toggle.focus();
  await page.keyboard.press("Space");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(log).toBeVisible();
  await expect(toggle).toBeFocused();
});

test("command palette is exposed as a labelled dialog", async ({ page }) => {
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: /command/i })).toBeVisible();
});

test("model and mode controls have accessible names", async ({ page }) => {
  // The redesigned composer surfaces mode and model as menu buttons (the native
  // <select>s are kept for the pipeline but hidden from assistive tech). Each
  // button announces its purpose and current value, e.g. "Mode: Ask before edits".
  await expect(page.getByRole("button", { name: /^Mode:/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Model:/i })).toBeVisible();
  await expect(page.locator("#modeBtn")).toHaveAttribute("aria-haspopup", "true");
  await expect(page.locator("#modelBtn")).toHaveAttribute("aria-haspopup", "true");
});
