import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openTurnDetails, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

// #248: a very long run must stay bounded and say so honestly. A tiny cap is
// injected so the test trips it without emitting tens of thousands of events.
test("a run past the cap shows an honest truncation marker, ledger stays whole", async ({ page }) => {
  await page.evaluate(() => { window.__OPAI_EVENT_CAP__ = 20; });
  const id = await sendPrompt(page);
  const total = 400; // >> cap + slack (20 + 256 = 276)
  await page.evaluate(async ({ id, total }) => {
    for (let i = 0; i < total; i++) {
      window.__mock.emitActivity(id, {
        id: "ev" + i, type: "tool_call", status: "success", title: "Step " + i, timestamp: Date.now(),
      });
    }
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  }, { id, total });
  await page.locator(".gen-toggle").click();

  // The marker is present, pinned first, and its count is accurate + honest.
  const marker = page.locator(".timeline .tl-truncation");
  await expect(marker).toHaveCount(1);
  await expect(marker).toContainText("earlier steps hidden");
  // #390: the marker no longer claims an itemized "ledger" view (none exists);
  // it honestly says the rows were dropped for performance.
  await expect(marker).toContainText("dropped to stay fast");

  const { kept, truncated } = await page.evaluate(() => ({
    kept: window.__opai.state.store.list().length,
    truncated: window.__opai.state.store.truncatedCount(),
  }));
  expect(kept + truncated).toBe(total); // nothing lost, everything accounted for
  expect(truncated).toBeGreaterThan(0);
  // The marker's number equals what was actually dropped.
  await expect(marker).toContainText(String(truncated.toLocaleString()));
});

test("a normal-length turn shows no truncation marker", async ({ page }) => {
  const id = await sendPrompt(page);
  await page.evaluate((id) => {
    for (let i = 0; i < 6; i++) {
      window.__mock.emitActivity(id, { id: "e" + i, type: "tool_call", status: "success", title: "Step " + i });
    }
  }, id);
  await page.locator(".gen-toggle").click();
  await expect(page.locator(".timeline .tl-truncation")).toHaveCount(0);
});

test("a completed capped turn keeps its honest truncation marker", async ({ page }) => {
  await page.evaluate(() => { window.__OPAI_EVENT_CAP__ = 20; });
  const id = await sendPrompt(page);
  const total = 400;
  await page.evaluate(async ({ id, total }) => {
    for (let i = 0; i < total; i++) {
      window.__mock.emitActivity(id, {
        id: "archived-" + i, type: "tool_call", status: "success", title: "Step " + i,
      });
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, { id, total });
  const truncated = await page.evaluate(() => window.__opai.state.store.truncatedCount());

  await finishRequest(page, id);
  const completed = page.locator(".msg.bot").last();
  await openTurnDetails(page, completed);
  await completed.locator(".gen-toggle.done").click();

  const marker = completed.locator(".timeline.done .tl-truncation");
  await expect(marker).toHaveCount(1);
  await expect(marker).toContainText(truncated.toLocaleString());
  await expect(marker).toContainText("earlier steps hidden");
  await expect(marker).toContainText("dropped to stay fast");
});
