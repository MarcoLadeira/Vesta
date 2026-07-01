import { test, expect } from "@playwright/test";

import {
  expectNoRawProviderIds,
  expectNoUiSentinels,
  finishRequest,
  openApp,
  sendPrompt,
} from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("primary workspace consistently presents the OPai identity", async ({ page }) => {
  await expect(page.locator(".brand")).toContainText("OPai");
  await expect(page.locator("#empty h1")).toHaveText("Build more. Burn less.");
  await expect(page.locator("#emptySub")).toContainText("Tell OPai the goal");
});

test("known bug: workspace tooltip retains the OPai tagline", async ({ page }) => {
  test.fail(true, "BUG-QA-007: renderWorkspace overwrites the tagline applied during brand boot");
  await expect(page.locator("#wsSwitch")).toHaveAttribute("title", /Every step visible\. Every dollar accounted\./);
});

test("error cards use human OPai language instead of route internals", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "runner_error", answer: "The local model could not finish." });
  await expect(page.locator(".error-card .ec-t")).toHaveText("Local model couldn't answer");
  await expectNoRawProviderIds(page);
});

test("provider route IDs stay out of ordinary user and assistant bubbles", async ({ page }) => {
  await page.selectOption("#modelSel", "account:claude:opus");
  const id = await sendPrompt(page, "Keep this human-readable");
  await finishRequest(page, id, { answer: "OPai completed the request." });
  await expectNoRawProviderIds(page);
  await expect(page.locator(".msg.bot .role")).toContainText("Claude Opus 4.8");
});

test("advanced provider detail remains available in the Inspector", async ({ page }) => {
  await page.selectOption("#modelSel", "account:claude:opus");
  await expect(page.locator("#cliMirrorCmd")).toContainText("claude:opus");
  await expect(page.locator("#thread")).not.toContainText("account:claude:opus");
});

test("all initial user-facing copy is free of broken-value sentinels", async ({ page }) => {
  await expectNoUiSentinels(page);
  await expect(page.locator("body")).not.toContainText("anthropic.messages.create");
});

test("primary controls use the same bundled soft UI font", async ({ page }) => {
  test.fail(true, "BUG-QA-010: form controls fall back to Arial instead of inheriting bundled Inter");
  const families = await page.evaluate(() => [
    getComputedStyle(document.body).fontFamily,
    getComputedStyle(document.querySelector("#input")).fontFamily,
    getComputedStyle(document.querySelector("#send")).fontFamily,
  ]);
  for (const family of families) expect(family).toContain("Inter");
});
