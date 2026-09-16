import { test, expect } from "@playwright/test";

import { emitActivity, emitToken, finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

const strip = (page) => page.locator("#statusStrip");

test("the strip is hidden until a request starts", async ({ page }) => {
  await expect(strip(page)).toBeHidden();
});

test("sending shows the strip in a connecting state with the model", async ({ page }) => {
  await sendPrompt(page);
  await expect(strip(page)).toBeVisible();
  await expect(strip(page)).toHaveClass(/ss-connecting/);
  await expect(page.locator("#ssConn")).toHaveText("Connecting…");
  await expect(page.locator("#ssModel")).not.toHaveText(""); // shows the selected model
});

test("a status-channel connect event turns the dot connected and never a timeline row", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, {
    id: `${id}:connect`, type: "provider_request", status: "success",
    title: "Connected · claude", channel: "status",
  });
  await expect(strip(page)).toHaveClass(/ss-connected/);
  await expect(page.locator("#ssConn")).toHaveText("Connected · claude");
  // Ambient state is NOT evidence: it must not appear as a timeline row.
  await page.locator(".gen-toggle").click();
  await expect(page.locator(".timeline", { hasText: "Connected · claude" })).toHaveCount(0);
});

test("a status model event refines the model chip", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, {
    id: `${id}:model`, type: "model_selected", status: "success",
    title: "Model: account:claude:sonnet", channel: "status",
    metadata: { model: "account:claude:sonnet" },
  });
  await expect(page.locator("#ssModel")).toHaveText("claude:sonnet");
});

test("streaming tokens make the dot active", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "Hello");
  await expect(strip(page)).toHaveClass(/ss-active/);
});

test("the elapsed clock advances while working", async ({ page }) => {
  await sendPrompt(page);
  const initialElapsed = await page.locator("#ssTime").innerText();
  expect(initialElapsed).toMatch(/^\d{2}:\d{2}$/);
  await expect(page.locator("#ssTime")).not.toHaveText(initialElapsed, { timeout: 5_000 });
});

test("an answered reply shows Done and the real spent cost", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    status: "answered", answer: "done",
    receipt: { estimated_actual_usd: 0.0123 },
  });
  await expect(strip(page)).toHaveClass(/ss-connected/);
  await expect(page.locator("#ssConn")).toHaveText("Done");
  await expect(page.locator("#ssCost")).toHaveText("$0.0123 spent");
});

test("a local answer labels savings, never a fake $0 spend", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    status: "answered_locally", answer: "local",
    receipt: { estimated_actual_usd: 0, estimated_savings_usd: 0.02 },
  });
  await expect(page.locator("#ssConn")).toHaveText("Done");
  await expect(page.locator("#ssCost")).toHaveText("$0.0200 saved");
});

test("a failed reply shows Failed and no invented cost", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "account_error", answer: "boom", error: "timeout" });
  await expect(strip(page)).toHaveClass(/ss-error/);
  await expect(page.locator("#ssConn")).toHaveText("Failed");
  await expect(page.locator("#ssCost")).toHaveText("");
});

test("stopping shows a cancelled state", async ({ page }) => {
  const id = await sendPrompt(page);
  await page.locator(".gen-stop").click();
  // #380: Stop is acknowledged at once, but "Stopped" is only claimed after
  // the backend confirms the work actually stopped.
  await expect(strip(page)).toHaveClass(/ss-cancelled/);
  await expect(page.locator("#ssConn")).toHaveText("Stopping…");
  await page.evaluate((rid) => window.__mock.confirmCancel(rid), id);
  await expect(page.locator("#ssConn")).toHaveText("Stopped");
});

test("a new request resets the strip", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "account_error", answer: "boom" });
  await expect(strip(page)).toHaveClass(/ss-error/);
  await sendPrompt(page, "second task");
  await expect(strip(page)).toHaveClass(/ss-connecting/);
  await expect(page.locator("#ssConn")).toHaveText("Connecting…");
  await expect(page.locator("#ssCost")).toHaveText("");
});

test("status events from a superseded request never touch the strip", async ({ page }) => {
  const firstId = await sendPrompt(page);
  await page.locator(".gen-stop").click();
  await page.evaluate((rid) => window.__mock.confirmCancel(rid), firstId);
  await expect(strip(page)).toHaveClass(/ss-cancelled/);
  const secondId = await sendPrompt(page, "second");
  // A late connect from the cancelled request must be ignored (stale guard).
  await emitActivity(page, firstId, {
    id: `${firstId}:connect`, type: "provider_request", status: "success",
    title: "Connected · stale", channel: "status",
  });
  await expect(page.locator("#ssConn")).not.toHaveText("Connected · stale");
  await expect(strip(page)).toHaveClass(/ss-connecting/);
  await finishRequest(page, secondId, { status: "answered", answer: "ok", receipt: {} });
  await expect(page.locator("#ssConn")).toHaveText("Done");
});

test("New chat hides the strip", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "answered", answer: "ok", receipt: {} });
  await expect(strip(page)).toBeVisible();
  await page.locator("#headerNewChat").click();
  await expect(strip(page)).toBeHidden();
});
