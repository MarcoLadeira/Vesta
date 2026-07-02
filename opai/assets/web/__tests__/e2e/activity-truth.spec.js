import { test, expect } from "@playwright/test";

import { emitActivity, finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("activity timeline preserves ordered request evidence", async ({ page }) => {
  const id = await sendPrompt(page);
  const events = [
    { id: "prepare", type: "status", status: "success", title: "Preparing request" },
    { id: "context", type: "file_read", status: "success", title: "Reading project context" },
    { id: "route", type: "model", status: "success", title: "Selected cheapest capable model" },
    { id: "send", type: "request", status: "running", title: "Sending request" },
    { id: "wait", type: "request", status: "running", title: "Waiting for response" },
  ];
  for (const event of events) await emitActivity(page, id, event);
  await page.locator(".gen-toggle").click();
  await expect(page.locator(".timeline .tl-row")).toHaveCount(events.length);
  await expect(page.locator(".timeline")).toContainText("Preparing request");
  await expect(page.locator(".timeline")).toContainText("Waiting for response");
  await expect(page.locator("#inspLiveEvents")).toContainText("5 steps");
});

test("success can show completed only after the provider answers", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, { id: "done", type: "status", status: "success", title: "Completed" });
  await finishRequest(page, id, { answer: "done" });
  await page.locator(".gen-toggle.done").click();
  await expect(page.locator(".timeline.done")).toContainText("Completed");
});

test("provider failure never presents a completed state", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, { id: "failed", type: "status", status: "error", title: "Failed" });
  await finishRequest(page, id, { status: "account_error", answer: "Provider failed", error: "timeout" });
  await expect(page.locator(".error-card")).toBeVisible();
  await expect(page.locator(".msg.bot")).not.toContainText("Completed");
});

test("cancelled request never becomes failed or completed", async ({ page }) => {
  await sendPrompt(page);
  await page.locator(".gen-stop").click();
  await expect(page.locator(".stopped-card")).toContainText("stopped by you");
  await expect(page.locator(".msg.bot")).not.toContainText(/Failed|Completed/);
});

test("activity evidence remains reviewable after failure", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, { id: "failed", type: "status", status: "error", title: "Failed at provider" });
  await finishRequest(page, id, { status: "account_error", answer: "Provider failed" });
  await expect(page.getByRole("button", { name: /Activity/ })).toBeVisible();
});
