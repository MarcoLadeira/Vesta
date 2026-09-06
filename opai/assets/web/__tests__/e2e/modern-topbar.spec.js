import { test, expect } from "@playwright/test";

import { openApp, sendPrompt, finishRequest } from "./helpers/app.js";


test("renders one OPai app header without browser toolbar controls", async ({ page }) => {
  await openApp(page);

  const header = page.locator("#appHeader");
  await expect(header).toBeVisible();
  await expect(page.locator("main > .header")).toHaveCount(0);
  await expect(page.locator("[data-browser-action], [aria-label='Back'], [aria-label='Forward'], [aria-label='Reload']")).toHaveCount(0);

  await expect(page.getByRole("button", { name: "Toggle sidebar" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Switch project folder" })).toBeVisible();
  // The sidebar copy is gone: New chat is a header action, offered once.
  await expect(page.locator("#newChat")).toHaveCount(0);
  await expect(page.locator("#headerNewChat")).toBeVisible();
  await expect(page.locator("#footSettings")).toBeVisible();
  await expect(page.locator("#headerSettings")).toBeVisible();
  await expect(page.getByRole("button", { name: "Inspector" })).toBeVisible();
  await expect(page.locator("#windowControls")).toBeVisible();
});

test("topbar New Chat, Settings, sidebar, and Inspector controls preserve behavior", async ({ page }) => {
  await openApp(page);
  const requestId = await sendPrompt(page, "Check the modern header");
  await finishRequest(page, requestId, { answer: "Header checked." });
  await expect(page.locator(".msg.bot")).toContainText("Header checked.");

  await page.locator("#headerNewChat").click();
  await expect(page.locator("#empty")).toBeVisible();
  await expect(page.locator(".message, .msg")).toHaveCount(0);

  await page.locator("#headerSettings").click();
  await expect(page.locator("#view-settings")).toBeVisible();

  await page.locator("#sidebarToggle").click();
  await expect(page.locator("#app")).toHaveClass(/sidebar-hidden/);
  await page.locator("#sidebarToggle").click();
  await expect(page.locator("#app")).not.toHaveClass(/sidebar-hidden/);

  await page.locator("#panelToggle").click();
  await expect(page.locator("#app")).toHaveClass(/panel-hidden/);
});

test("custom desktop controls invoke only the native window bridge", async ({ page }) => {
  await openApp(page);

  await page.locator("#windowMinimize").click();
  await page.locator("#windowMaximize").click();
  await page.locator("#windowClose").click();
  const calls = await page.evaluate(() => ({
    minimize: window.__mock.windowMinimizes,
    maximize: window.__mock.windowMaximizes,
    close: window.__mock.windowCloses,
  }));
  expect(calls).toEqual({ minimize: 1, maximize: 1, close: 1 });
});

test("header ON indicator reflects active AI work", async ({ page }) => {
  await openApp(page);
  const requestId = await sendPrompt(page, "Verify the working indicator");

  await expect(page.locator("body")).toHaveClass(/ai-working/);
  await expect(page.locator("#brandDot")).toHaveCSS("animation-name", "pulse");

  await finishRequest(page, requestId);
  await expect(page.locator("body")).not.toHaveClass(/ai-working/);
});

for (const viewport of [
  { width: 1040, height: 700 },
  { width: 768, height: 820 },
  { width: 375, height: 667 },
]) {
  test(`topbar stays usable at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await openApp(page);

    const metrics = await page.locator("#appHeader").evaluate((header) => {
      const rect = header.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        width: rect.width,
        viewport: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      };
    });
    expect(metrics.left).toBeGreaterThanOrEqual(0);
    expect(metrics.right).toBeLessThanOrEqual(metrics.viewport);
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.viewport);
    await expect(page.locator("#sidebarToggle")).toBeVisible();
    await expect(page.locator("#headerNewChat")).toBeVisible();
    await expect(page.locator("#input")).toBeVisible();
    if (viewport.width <= 700) {
      await expect(page.locator("#sidebarToggle")).toHaveAttribute("aria-expanded", "false");
    }
  });
}
