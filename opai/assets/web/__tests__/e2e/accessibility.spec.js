import { test, expect } from "@playwright/test";

import { openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("core controls expose useful accessible names", async ({ page }) => {
  await expect(page.getByRole("button", { name: "New chat" })).toBeVisible();
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
  await expect(page.getByRole("log", { name: "AI activity" })).toBeAttached();
  await expect(page.locator(".gen-reassure")).toHaveAttribute("aria-live", "polite");
  await expect(page.locator("#inspLive")).toHaveAttribute("aria-live", "polite");
});

test("command palette is exposed as a labelled dialog", async ({ page }) => {
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: /command/i })).toBeVisible();
});

test("model and mode selectors have accessible names", async ({ page }) => {
  // Anchored regexes: "Model" also contains the substring "mode", so the
  // unanchored pair could never both resolve uniquely. The intent stands —
  // each composer select must expose a stable accessible name.
  await expect(page.getByRole("combobox", { name: /^model$/i })).toBeVisible();
  await expect(page.getByRole("combobox", { name: /^mode$/i })).toBeVisible();
});
