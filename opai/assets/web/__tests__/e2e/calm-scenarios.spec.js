import { test, expect } from "@playwright/test";

import {
  claudeTurnEvents,
  codexTurnEvents,
  emitScenario,
  emitScenarioBatch,
  finishRequest,
  openApp,
  sendPrompt,
} from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

// #230: realistic scenario coverage. The multi-chunk Claude scenario is the
// regression net for the original feed spam — before #223, 200 stream chunks
// meant 200 identical "Streaming response" rows; with derived ids they must
// coalesce into ONE row that finishes with a measured duration.
test("a 200-chunk Claude turn coalesces into a handful of stable rows", async ({ page }) => {
  const id = await sendPrompt(page);
  const events = await emitScenario(page, id, claudeTurnEvents(id, { chunks: 200 }));
  await page.locator(".gen-toggle").click();
  const rows = page.locator(".timeline .tl-row");
  // 208 events -> 7 distinct ids (connect, stream, 5 tools). One row each
  // until the grouped renderer (#231) folds tool runs further.
  const distinctIds = new Set(events.map((e) => e.id)).size;
  await expect(rows).toHaveCount(distinctIds);
  const streamRow = rows.filter({ hasText: "Response received" });
  await expect(streamRow).toHaveCount(1);
  await expect(streamRow).toContainText("2400 chars"); // final detail, not a mid-chunk one
  await expect(page.locator(".timeline .tl-row", { hasText: "Streaming response" })).toHaveCount(0);
});

test("Codex started/completed pairs land as one finished row per item", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitScenario(page, id, codexTurnEvents(id, 3));
  await page.locator(".gen-toggle").click();
  const commandRows = page.locator(".timeline .tl-row", { hasText: "Ran command" });
  await expect(commandRows).toHaveCount(3);
  for (let i = 0; i < 3; i++) {
    await expect(commandRows.nth(i)).toHaveClass(/success/);
  }
});

// #226: the whole turn arrives as ONE activityBatch signal. The batch path
// must coalesce identically to the per-event path and lose nothing.
test("a batched turn ingests every event and coalesces the same as per-event", async ({ page }) => {
  const id = await sendPrompt(page);
  const events = await emitScenarioBatch(page, id, claudeTurnEvents(id, { chunks: 200 }));
  await page.locator(".gen-toggle").click();
  const distinctIds = new Set(events.map((e) => e.id)).size;
  await expect(page.locator(".timeline .tl-row")).toHaveCount(distinctIds);
  await expect(page.locator(".timeline .tl-row", { hasText: "Response received" })).toHaveCount(1);
  // Every raw event is accounted for in the store (nothing dropped by batching).
  const stored = await page.evaluate(() => window.__opai.state.store.list().length);
  expect(stored).toBe(distinctIds);
});

test("a stale batch from a superseded request is dropped whole", async ({ page }) => {
  const firstId = await sendPrompt(page);
  await page.locator(".gen-stop").click();
  await expect(page.locator(".stopped-card")).toBeVisible();
  await sendPrompt(page, "second task");
  await emitScenarioBatch(page, firstId, claudeTurnEvents(firstId, { chunks: 20 }));
  const count = await page.evaluate(() => window.__opai.state.store.list().length);
  expect(count).toBe(0); // stale batch guarded out wholesale
});

test("cancel mid-stream flips running evidence to cancelled, never completed", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitScenario(page, id, claudeTurnEvents(id, { chunks: 5 }).slice(0, 4)); // still streaming
  await page.locator(".gen-stop").click();
  await expect(page.locator(".stopped-card")).toContainText("stopped by you");
  const statuses = await page.evaluate(() =>
    window.__opai.state.store.list().map((e) => e.status),
  );
  expect(statuses).not.toContain("running");
  await expect(page.locator(".msg.bot")).not.toContainText(/Completed/);
});

test("late events from a superseded request are dropped by the stale guard", async ({ page }) => {
  const firstId = await sendPrompt(page);
  await page.locator(".gen-stop").click();
  await expect(page.locator(".stopped-card")).toBeVisible();
  const secondId = await sendPrompt(page, "second task");
  // Stale burst from the cancelled request arrives late.
  await emitScenario(page, firstId, claudeTurnEvents(firstId, { chunks: 10 }));
  const count = await page.evaluate(() => window.__opai.state.store.list().length);
  expect(count).toBe(0); // nothing from the stale request landed
  await finishRequest(page, secondId, { answer: "second answer" });
  await expect(page.locator(".msg.bot").last()).toContainText("second answer");
});

test("a local no-stream turn shows the honest spinner, never fake progress", async ({ page }) => {
  const id = await sendPrompt(page);
  // Only preamble activity arrives — no stream events, no tokens.
  await emitScenario(page, id, [{
    id: `${id}:phase`, type: "request_sending", status: "running",
    title: "Running OPai locally", requestId: id,
  }]);
  await expect(page.locator(".gen-stage")).toContainText(/Waiting for/);
  await expect(page.locator(".thinking")).toBeVisible();
  await expect(page.locator(".gen-stage")).not.toContainText("Streaming");
  await finishRequest(page, id, { answer: "local answer" });
  await expect(page.locator(".msg.bot").last()).toContainText("local answer");
});
