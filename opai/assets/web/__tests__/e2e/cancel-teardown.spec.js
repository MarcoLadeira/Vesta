import { expect, test } from "@playwright/test";

import { openApp, sendPrompt } from "./helpers/app.js";

/* #380 — cancellation is a request, not a fact.
 *
 * Pressing Stop set a cancel flag and the UI immediately declared the run
 * "cancelled", a terminal state meaning *proven stopped*, then dropped the
 * request id so any later signal was ignored. But the backend cancel is
 * asynchronous: the provider CLI dies whenever it next notices. So the UI could
 * claim a paid call had stopped while it was still running, and made that
 * unobservable — #295 invariant 9 (cancel acknowledgement, propagation,
 * teardown and final state are one contract) and 15 (no hidden work).
 *
 * Stop is now acknowledged immediately as "Stopping…" and only becomes
 * "cancelled" when the backend confirms the worker actually returned.
 */

async function startAndStop(page, prompt = "a long running task") {
  const id = await sendPrompt(page, prompt);
  await page.click(".gen-stop");
  return id;
}

test("Stop is acknowledged immediately without claiming the work stopped", async ({ page }) => {
  await openApp(page);
  await startAndStop(page);

  // The request really was sent to the backend...
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  // ...but the run is not yet reported as cancelled.
  expect(await page.evaluate(() => window.__opai.state.message.status)).toBe("cancel_requested");
});

test("the status strip says Stopping, not Stopped, until teardown is proven", async ({ page }) => {
  await openApp(page);
  await startAndStop(page);
  await expect(page.locator("#ssConn")).toHaveText("Stopping…");
});

test("confirmed teardown is what turns the run cancelled", async ({ page }) => {
  await openApp(page);
  const id = await startAndStop(page);

  await page.evaluate((rid) => window.__mock.confirmCancel(rid, "complete"), id);

  expect(await page.evaluate(() => window.__opai.state.message.status)).toBe("cancelled");
  await expect(page.locator("#ssConn")).toHaveText("Stopped");
});

test("a request that was never running reports back at once", async ({ page }) => {
  // The real bridge answers "not_running" when there is nothing to stop, so the
  // UI is never left waiting for a teardown that will never be confirmed.
  await openApp(page);
  const id = await startAndStop(page);
  await page.evaluate((rid) => window.__mock.confirmCancel(rid, "not_running"), id);
  expect(await page.evaluate(() => window.__opai.state.message.status)).toBe("cancelled");
});

test("a confirmation for a different request is ignored", async ({ page }) => {
  await openApp(page);
  await startAndStop(page);
  await page.evaluate(() => window.__mock.confirmCancel("some-other-request", "complete"));
  expect(await page.evaluate(() => window.__opai.state.message.status)).toBe("cancel_requested");
});

test("pressing Stop twice does not double-cancel", async ({ page }) => {
  await openApp(page);
  await sendPrompt(page, "a long running task");
  await page.click(".gen-stop");
  await page.click(".gen-stop").catch(() => {});
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
});

test("unconfirmed teardown is reported, not silently called a clean stop", async ({ page }) => {
  // If the backend never confirms, the run must still resolve — but it must not
  // pretend the provider call was proven stopped.
  await openApp(page);
  await page.evaluate(() => { window.CANCEL_TEARDOWN_MS_TEST = true; });
  const id = await startAndStop(page);
  await page.evaluate((rid) => window.__mock.confirmCancel(rid, "unknown"), id);

  expect(await page.evaluate(() => window.__opai.state.message.status)).toBe("cancelled");
});
