import { test, expect } from "@playwright/test";

import {
  emitActivity,
  finishRequest,
  openApp,
  sendPrompt,
} from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("REGRESSION: 401 authentication failure never shows Completed", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "account_error", answer: "Authentication failed." });
  await expect(page.locator(".msg.bot")).not.toContainText("Completed");
  await expect(page.locator(".error-card")).toHaveCount(1);
});

test("REGRESSION: one provider failure produces one error card", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "account_error", answer: "Provider unavailable.", error: "diagnostic" });
  await expect(page.locator(".error-card")).toHaveCount(1);
  await expect(page.locator(".ec-t")).toHaveCount(1);
});

test("REGRESSION: Stop never leaves the interface busy", async ({ page }) => {
  await sendPrompt(page);
  await page.locator(".gen-stop").click();
  await expect(page.locator("body")).not.toHaveClass(/ai-working/);
  await expect(page.locator("#send")).toHaveText("Send");
});

test("REGRESSION: paid call spend is never labelled as saved", async ({ page }) => {
  await page.selectOption("#modelSel", "account:claude:opus");
  const id = await sendPrompt(page);
  await finishRequest(page, id, { receipt: { estimated_actual_usd: 0.12, estimated_savings_usd: 0, paid_call_avoided: false } });
  await expect(page.locator(".footer-note")).toContainText("$0.1200");
  await expect(page.locator(".footer-note")).not.toContainText("saved");
});

test("REGRESSION: raw route IDs stay hidden from normal chat", async ({ page }) => {
  await page.selectOption("#modelSel", "account:codex:gpt-5.5");
  const id = await sendPrompt(page);
  await finishRequest(page, id, { answer: "Finished." });
  await expect(page.locator("#thread")).not.toContainText("account:codex:gpt-5.5");
});

test("REGRESSION: empty answer becomes a visible error, never blank success", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "empty", answer: "" });
  await expect(page.locator(".error-card")).toBeVisible();
  await expect(page.locator(".msg.bot .body")).toHaveCount(0);
});

test("REGRESSION: late aborted response cannot overwrite current chat", async ({ page }) => {
  const oldId = await sendPrompt(page, "old request");
  await page.locator(".gen-stop").click();
  await page.evaluate((id) => window.__mock.emitReply(id, { status: "answered", answer: "LATE ABORTED" }), oldId);
  await expect(page.locator("#thread")).not.toContainText("LATE ABORTED");
});

test("REGRESSION: Inspector live fields follow activity", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, { id: "route", type: "model", status: "success", title: "Selected model" });
  await expect(page.locator("#inspLiveStep")).toContainText("Selected model");
  await expect(page.locator("#inspLiveEvents")).toContainText("1 step");
});

test("REGRESSION: user-facing UI never renders JavaScript coercion sentinels", async ({ page }) => {
  const body = await page.locator("body").innerText();
  expect(body).not.toMatch(/\bundefined\b|\bNaN\b|\[object Object\]/);
});
