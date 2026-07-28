import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";

/* #295 — "OPai must not silently ignore a new instruction because an older run
 * is active."
 *
 * Enter during a run used to be dropped on the floor: the keystroke vanished
 * with no trace, which is worst exactly when it matters most — someone
 * correcting or redirecting work in flight. The text is now held and sent when
 * the run ends.
 *
 * Deliberately a queue, not a second concurrent run: single-flight is what
 * keeps cost and side effects controllable. What changes is that the user's
 * words survive.
 */

test("a message typed during a run is kept, not discarded", async ({ page }) => {
  await openApp(page);
  await sendPrompt(page, "first task");

  await page.fill("#input", "actually, use the settings page");
  await page.press("#input", "Enter");

  const queued = page.locator("#composerQueued");
  await expect(queued).toBeVisible();
  await expect(queued).toContainText("actually, use the settings page");
  // The composer is free for the next thought.
  await expect(page.locator("#input")).toHaveValue("");
});

test("queueing never starts a second concurrent request", async ({ page }) => {
  // The single-flight guarantee this feature must not break.
  await openApp(page);
  await sendPrompt(page, "first task");
  await page.fill("#input", "second thought");
  await page.press("#input", "Enter");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});

test("the queued message sends itself when the run finishes", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "first task");
  await page.fill("#input", "second thought");
  await page.press("#input", "Enter");

  await finishRequest(page, id, { status: "answered", answer: "Done." });

  await expect(page.locator("#composerQueued")).toBeHidden();
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
  expect(await page.evaluate(() => window.__mock.lastRequest.text)).toBe("second thought");
});

test("a queued message waits while OPai is waiting on the user", async ({ page }) => {
  // An awaiting-input turn is not an ending. Firing the queued message here
  // would start a fresh run over an approval card the user has not answered,
  // losing both the question and the work behind it.
  await openApp(page);
  const id = await sendPrompt(page, "fix the failing test");
  await page.fill("#input", "and then open a PR");
  await page.press("#input", "Enter");

  await finishRequest(page, id, {
    status: "needs_command_approval",
    answer: "I need your OK to run this command.",
    run_state: "awaiting_input",
    awaiting: { kind: "approval", question: "I need your OK to run this command." },
    command_approval: { command: "pytest -q" },
  });

  await expect(page.locator("#composerQueued")).toBeVisible();
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});

test("the user can remove a queued message", async ({ page }) => {
  await openApp(page);
  await sendPrompt(page, "first task");
  await page.fill("#input", "never mind this");
  await page.press("#input", "Enter");

  await page.locator('#composerQueued [data-a="drop"]').click();
  await expect(page.locator("#composerQueued")).toBeHidden();
});

test("the user can pull a queued message back for editing", async ({ page }) => {
  await openApp(page);
  await sendPrompt(page, "first task");
  await page.fill("#input", "half a thought");
  await page.press("#input", "Enter");

  await page.locator('#composerQueued [data-a="edit"]').click();
  await expect(page.locator("#composerQueued")).toBeHidden();
  await expect(page.locator("#input")).toHaveValue("half a thought");
});

test("nothing is queued when no run is active", async ({ page }) => {
  // The ordinary path must be untouched: typing and pressing Enter sends.
  await openApp(page);
  await page.fill("#input", "just ask normally");
  await page.press("#input", "Enter");
  await expect(page.locator("#composerQueued")).toBeHidden();
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});
