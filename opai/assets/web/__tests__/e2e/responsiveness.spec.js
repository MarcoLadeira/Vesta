import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openNav, sendPrompt } from "./helpers/app.js";


async function expectUsableViewport(page) {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.innerWidth);
  await expect(page.locator("#input")).toBeVisible();
  await expect(page.locator("#send")).toBeVisible();
  await expect(page.locator("#wsSwitch")).toBeVisible();
}

for (const viewport of [
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1280, height: 820 },
  { name: "large desktop", width: 1600, height: 1000 },
]) {
  test(`${viewport.name} keeps chat and navigation usable`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await openApp(page);
    await expectUsableViewport(page);
    await openNav(page, "Money Saved");
    await expect(page.locator("#dashPage .kpis")).toBeVisible();
  });
}

for (const viewport of [
  { name: "mobile large", width: 430, height: 932 },
  { name: "mobile small", width: 375, height: 667 },
]) {
  test(`${viewport.name} has no overflow and keeps composer usable`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await openApp(page);
    await expectUsableViewport(page);
  });
}

test("desktop Inspector remains usable when toggled", async ({ page }) => {
  await page.setViewportSize({ width: 1040, height: 700 });
  await openApp(page);
  await expect(page.locator("#inspector")).toBeVisible();
  await page.getByRole("button", { name: "Inspector" }).click();
  await expect(page.locator("#input")).toBeVisible();
  await expectUsableViewport(page);
});

test("agent response remains usable at target widths and layout boundaries", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    answer: "The focused change is ready.\n\n| File | Result |\n| --- | --- |\n| src/very-long-component-name.ts | Passed |",
    changed_files: [" M src/very-long-component-name.ts"],
    presentation: {
      schema_version: 1,
      tests: { status: "passed", passed: 12 },
      changes: { summary: { files: 1, additions: 8, deletions: 2 } },
    },
  });
  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1440, height: 900 },
    { width: 1280, height: 800 },
    { width: 1024, height: 768 },
    { width: 981, height: 800 },
    { width: 980, height: 800 },
    { width: 701, height: 800 },
    { width: 700, height: 800 },
    { width: 431, height: 800 },
    { width: 430, height: 800 },
    { width: 375, height: 667 },
  ]) {
    await page.setViewportSize(viewport);
    await expectUsableViewport(page);
    await expect(page.locator(".msg.bot").last()).toBeVisible();
  }
});

test("200 percent zoom equivalent keeps the response and composer usable", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 450 });
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    answer: "The response stays readable at a constrained effective viewport.",
    presentation: { schema_version: 1, tests: { status: "passed", passed: 1 } },
  });
  await expectUsableViewport(page);
  await expect(page.locator(".response-prose")).toBeVisible();
});
