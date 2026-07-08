import { test, expect } from "@playwright/test";

import { openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

// #228: the timeline renderer is keyed and rAF-batched. A synchronous burst of
// events must cost ~one render pass, not one full rebuild per event, and every
// event must still land as exactly one row (the one-row-per-event contract).
test("a 500-event burst renders in one batched pass with one row per event", async ({ page }) => {
  const id = await sendPrompt(page);
  const renders = await page.evaluate(async (id) => {
    const before = window.__opai.state.timelineRenders;
    for (let i = 0; i < 500; i++) {
      window.__mock.emitActivity(id, {
        id: "ev" + i, type: "tool_call", status: "success", title: "Step " + i, timestamp: Date.now(),
      });
    }
    // Two frames cover the scheduled rAF flush.
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return window.__opai.state.timelineRenders - before;
  }, id);
  expect(renders).toBeGreaterThan(0);
  expect(renders).toBeLessThanOrEqual(3);
  await page.locator(".gen-toggle").click();
  await expect(page.locator(".timeline .tl-row")).toHaveCount(500);
});

test("repeated updates to one event id stay a single patched row", async ({ page }) => {
  const id = await sendPrompt(page);
  await page.evaluate(async (id) => {
    for (let i = 0; i < 250; i++) {
      window.__mock.emitActivity(id, {
        id: "same", type: "streaming", status: i < 249 ? "running" : "success",
        title: "Streaming response", detail: "chunk " + i,
      });
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, id);
  await page.locator(".gen-toggle").click();
  const rows = page.locator(".timeline .tl-row");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("chunk 249");
  await expect(rows).toHaveClass(/success/);
});
