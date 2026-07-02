import { test, expect } from "@playwright/test";

import {
  emitToken,
  expectNoRawProviderIds,
  expectNoUiSentinels,
  finishRequest,
  openApp,
  sendPrompt,
} from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("sends a prompt, streams one response, and accepts a follow-up", async ({ page }) => {
  const first = await sendPrompt(page, "Explain <main> & test safety");
  await emitToken(page, first, "First answer");
  await finishRequest(page, first, { answer: "First answer" });
  await expect(page.locator(".msg.user")).toHaveCount(1);
  await expect(page.locator(".msg.bot")).toHaveCount(1);
  await expect(page.locator(".msg.bot .body")).toContainText("First answer");

  const second = await sendPrompt(page, "Now add the edge cases");
  await finishRequest(page, second, { answer: "Second answer" });
  await expect(page.locator(".msg.user")).toHaveCount(2);
  await expect(page.locator(".msg.bot")).toHaveCount(2);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
});

test("empty and whitespace-only prompts never create requests", async ({ page }) => {
  await page.fill("#input", "   ");
  await page.getByRole("button", { name: "Send" }).click();
  await page.press("#input", "Enter");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
  await expect(page.locator(".msg")).toHaveCount(0);
});

test("long and hostile-looking text stays inert and readable", async ({ page }) => {
  const prompt = `<script>window.pwned=true</script> & "quotes" ` + "x".repeat(3000);
  const id = await sendPrompt(page, prompt);
  await finishRequest(page, id, { answer: "Handled safely." });
  await expect(page.locator(".msg.user script")).toHaveCount(0);
  await expect(page.locator(".msg.user")).toContainText("<script>");
  expect(await page.evaluate(() => window.pwned)).toBeUndefined();
});

test("new chat clears messages and restores the branded empty state", async ({ page }) => {
  const id = await sendPrompt(page, "temporary conversation");
  await finishRequest(page, id);
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page.locator(".msg")).toHaveCount(0);
  await expect(page.locator("#empty h1")).toHaveText("Build more. Burn less.");
});

test("starter chip submits exactly one useful prompt", async ({ page }) => {
  await page.getByRole("button", { name: "Explain this repo" }).click();
  await expect(page.locator(".msg.user")).toContainText("high-level tour");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});

test("selected model and mode reach the native bridge request", async ({ page }) => {
  await page.selectOption("#modelSel", "account:codex:gpt-5.5");
  await page.selectOption("#modeSel", "plan");
  await sendPrompt(page, "Plan a safe migration");
  const request = await page.evaluate(() => window.__mock.lastRequest);
  expect(request.model).toBe("account:codex:gpt-5.5");
  expect(request.mode).toBe("plan");
  expect(request.text).toBe("Plan a safe migration");
});

test("normal chat hides route IDs and broken-value sentinels", async ({ page }) => {
  const id = await sendPrompt(page, "keep details human");
  await finishRequest(page, id, { answer: "OPai chose the safest capable route." });
  await expectNoRawProviderIds(page);
  await expectNoUiSentinels(page);
});

test("Auto fallback names the model and only starts cloud after confirmation", async ({ page }) => {
  await openApp(page, { boot: { selectedModel: "auto" } });
  await page.fill("#input", "Explain the project");
  await page.getByRole("button", { name: "Send" }).click();
  const first = await page.evaluate(() => window.__mock.lastRequest);
  await page.evaluate((id) => window.__mock.emitReply(id, {
    status: "needs_auto_confirmation",
    answer: "No local model is running. Continue with Groq · GPT-OSS 120B?",
    fallbackModelId: "free:groq:openai/gpt-oss-120b",
    fallbackModelLabel: "Groq · GPT-OSS 120B",
    cloudStarted: false,
  }), first.requestId);
  await expect(page.getByText("Continue with Groq · GPT-OSS 120B")).toBeVisible();
  await page.getByRole("button", { name: "Confirm Groq · GPT-OSS 120B" }).click();
  const second = await page.evaluate(() => window.__mock.lastRequest);
  expect(second.allowCloud).toBe(true);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
});

test("usage limit warning resends only after explicit confirmation", async ({ page }) => {
  await openApp(page);
  await page.fill("#input", "Continue working");
  await page.getByRole("button", { name: "Send" }).click();
  const first = await page.evaluate(() => window.__mock.lastRequest);
  await page.evaluate((id) => window.__mock.emitReply(id, {
    status: "needs_limit_confirmation",
    answer: "Claude Haiku reached your 5,000 token soft limit.",
    usage: { percent: 100, used: 5000, limit: 5000 },
  }), first.requestId);
  await page.getByRole("button", { name: "Continue past limit" }).click();
  const second = await page.evaluate(() => window.__mock.lastRequest);
  expect(second.allowLimit).toBe(true);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
});
