import { test, expect } from "@playwright/test";

import { emitToken, finishRequest, openApp, openNav, sendPrompt } from "./helpers/app.js";


async function expectUsableViewport(page) {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.innerWidth);
  await expect(page.locator("#input")).toBeVisible();
  await expect(page.locator("#send")).toBeVisible();
  await expect(page.locator("#wsSwitch")).toBeVisible();
  const [sendBox, chatBox] = await Promise.all([
    page.locator("#send").boundingBox(),
    page.locator("#view-chat").boundingBox(),
  ]);
  expect(sendBox.x).toBeGreaterThanOrEqual(chatBox.x);
  expect(
    sendBox.x + sendBox.width,
    `Send is clipped at ${page.viewportSize().width}×${page.viewportSize().height}`,
  ).toBeLessThanOrEqual(chatBox.x + chatBox.width + 0.5);
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
  await page.setViewportSize({ width: 1280, height: 800 });
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
    { width: 1181, height: 800 },
    { width: 1180, height: 800 },
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

test("assistant responses align with the full-width work lane", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await openApp(page);
  const id = await sendPrompt(page, "Keep the response focused.");
  const conversationMetrics = await page.evaluate(() => {
    const lane = document.querySelector(".msg.bot:last-child").getBoundingClientRect();
    const userMessage = Array.from(document.querySelectorAll(".msg.user")).at(-1);
    const user = userMessage.getBoundingClientRect();
    const bubble = userMessage.querySelector(".bubble").getBoundingClientRect();
    const composer = document.querySelector("#composer").getBoundingClientRect();
    return { lane, user, bubble, composer };
  });
  expect(Math.abs(conversationMetrics.user.x - conversationMetrics.lane.x)).toBeLessThan(2);
  expect(Math.abs(conversationMetrics.user.width - conversationMetrics.lane.width)).toBeLessThan(2);
  expect(Math.abs(
    conversationMetrics.bubble.x + conversationMetrics.bubble.width
      - conversationMetrics.composer.x - conversationMetrics.composer.width,
  )).toBeLessThan(2);

  await emitToken(page, id, "A concise progress update.");
  await expect(page.locator(".msg.bot:last-child .stream-block > p")).toBeVisible();

  const liveMetrics = await page.evaluate(() => {
    const lane = document.querySelector(".msg.bot:last-child").getBoundingClientRect();
    const header = document.querySelector(".msg.bot:last-child .assistant-header").getBoundingClientRect();
    const status = document.querySelector(".msg.bot:last-child .gen-work-surface").getBoundingClientRect();
    const activity = document.querySelector(".msg.bot:last-child .gen-toggle").getBoundingClientRect();
    const prose = document.querySelector(".msg.bot:last-child .stream-block > p").getBoundingClientRect();
    return { lane, header, status, activity, prose };
  });
  expect(Math.abs(liveMetrics.header.x - liveMetrics.lane.x)).toBeLessThan(2);
  expect(Math.abs(liveMetrics.header.width - liveMetrics.lane.width)).toBeLessThan(2);
  expect(Math.abs(liveMetrics.status.x - liveMetrics.lane.x)).toBeLessThan(2);
  expect(Math.abs(liveMetrics.status.width - liveMetrics.lane.width)).toBeLessThan(2);
  expect(liveMetrics.activity.x).toBeGreaterThanOrEqual(liveMetrics.status.x);
  expect(liveMetrics.activity.x + liveMetrics.activity.width).toBeLessThanOrEqual(
    liveMetrics.status.x + liveMetrics.status.width + 1,
  );
  expect(Math.abs(liveMetrics.prose.x - liveMetrics.lane.x)).toBeLessThan(2);
  expect(Math.abs(liveMetrics.prose.width - liveMetrics.lane.width)).toBeLessThan(2);

  await finishRequest(page, id, {
    answer: "# Result\n\nA concise final answer.\n\n| File | Result |\n| --- | --- |\n| src/example.ts | Passed |",
  });
  await expect(page.locator(".msg.bot:last-child .response-prose > h1")).toBeVisible();
  const finalMetrics = await page.evaluate(() => {
    const lane = document.querySelector(".msg.bot:last-child").getBoundingClientRect();
    const header = document.querySelector(".msg.bot:last-child .assistant-header").getBoundingClientRect();
    const heading = document.querySelector(".msg.bot:last-child .response-prose > h1").getBoundingClientRect();
    const prose = document.querySelector(".msg.bot:last-child .response-prose > p").getBoundingClientRect();
    const table = document.querySelector(".msg.bot:last-child .response-table-scroll").getBoundingClientRect();
    return { lane, header, heading, prose, table };
  });
  expect(Math.abs(finalMetrics.header.x - finalMetrics.lane.x)).toBeLessThan(2);
  expect(Math.abs(finalMetrics.header.width - finalMetrics.lane.width)).toBeLessThan(2);
  expect(Math.abs(finalMetrics.heading.x - finalMetrics.lane.x)).toBeLessThan(2);
  expect(Math.abs(finalMetrics.heading.width - finalMetrics.lane.width)).toBeLessThan(2);
  expect(Math.abs(finalMetrics.prose.x - finalMetrics.lane.x)).toBeLessThan(2);
  expect(Math.abs(finalMetrics.prose.width - finalMetrics.lane.width)).toBeLessThan(2);
  expect(Math.abs(finalMetrics.table.x - finalMetrics.lane.x)).toBeLessThan(2);
  expect(Math.abs(finalMetrics.table.width - finalMetrics.lane.width)).toBeLessThan(2);
});
