import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

// #228: the timeline renderer is keyed and rAF-batched. A synchronous burst of
// events must cost ~one render pass, not one full rebuild per event, and every
// event must still land as exactly one row (the one-row-per-event contract).
test("a collapsed 2000-event burst defers all row rendering until activity opens", async ({ page }) => {
  const id = await sendPrompt(page);
  const renders = await page.evaluate(async (id) => {
    const before = window.__opai.state.timelineRenders;
    for (let i = 0; i < 2000; i++) {
      window.__mock.emitActivity(id, {
        id: "ev" + i, type: "tool_call", status: "success", title: "Step " + i, timestamp: Date.now(),
      });
    }
    // Two frames cover the scheduled rAF flush.
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return window.__opai.state.timelineRenders - before;
  }, id);
  expect(renders).toBe(0);
  await expect(page.locator(".gen-toggle")).toContainText("2000");
  await page.locator(".gen-toggle").click();
  await expect(page.locator(".timeline .tl-row")).toHaveCount(2000);
});

test("a completed collapsed activity log stays unmounted until opened", async ({ page }) => {
  const id = await sendPrompt(page);
  await page.evaluate((requestId) => {
    for (let i = 0; i < 2000; i++) {
      window.__mock.emitActivity(requestId, {
        id: `done-${i}`, type: "tool_call", status: "success", title: `Step ${i}`,
      });
    }
  }, id);
  await finishRequest(page, id);
  await expect(page.locator(".timeline.done .tl-row")).toHaveCount(0);
  await page.locator(".gen-toggle.done").click();
  await expect(page.locator(".timeline.done .tl-row")).toHaveCount(2000);
});

// #247: an explicit wall-clock budget so the O(n^2) rewrite can never creep
// back in. Budget = 1500 ms to emit AND render a 500-event turn — deliberately
// generous (real hardware does this in tens of ms) so it catches only
// order-of-magnitude regressions, never CI-hardware noise. The pre-#228
// innerHTML-per-event renderer blew past this by 10-100x.
test("a 500-event turn emits and renders within the wall-clock budget", async ({ page }) => {
  const id = await sendPrompt(page);
  await page.locator(".gen-toggle").click();
  const elapsedMs = await page.evaluate(async (id) => {
    const t0 = performance.now();
    for (let i = 0; i < 500; i++) {
      window.__mock.emitActivity(id, {
        id: "ev" + i, type: "tool_call", status: "success", title: "Step " + i, timestamp: Date.now(),
      });
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return performance.now() - t0;
  }, id);
  expect(elapsedMs).toBeLessThan(1500);
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

test("nonconsecutive groups with the same key remain separate runs", async ({ page }) => {
  const id = await sendPrompt(page);
  await page.evaluate(async (requestId) => {
    const groups = ["a", "a", "b", "b", "a", "a"];
    groups.forEach((group, index) => window.__mock.emitActivity(requestId, {
      id: `event-${index}`,
      type: "tool_call",
      status: "success",
      title: `${group.toUpperCase()} step ${index}`,
      group,
    }));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, id);
  await page.locator(".gen-toggle").click();
  await expect(page.locator(".timeline > .tl-group")).toHaveCount(3);
  await expect(page.locator(".timeline .tl-children .tl-row")).toHaveCount(0);
  await page.locator(".timeline > .tl-group").first().getByRole("button").click();
  await expect(page.locator(".timeline > .tl-group").first().locator(".tl-children .tl-row")).toHaveCount(2);
});
