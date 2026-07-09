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

// #230/#231: realistic scenario coverage. The multi-chunk Claude turn is the
// regression net for the original feed spam — before #223, 200 stream chunks
// meant 200 identical rows. Now they coalesce to ONE finished row, the
// status-channel connect never becomes a row, and consecutive same-type tool
// calls fold into collapsible groups.
test("a 200-chunk Claude turn folds into calm grouped rows", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitScenario(page, id, claudeTurnEvents(id, { chunks: 200 }));
  await page.locator(".gen-toggle").click();
  // Two tool groups (3 reads + 2 commands) and one finished stream single.
  await expect(page.locator(".timeline > .tl-group")).toHaveCount(2);
  await expect(page.locator(".tl-group-toggle", { hasText: "Read file ×3" })).toHaveCount(1);
  await expect(page.locator(".tl-group-toggle", { hasText: "Ran command ×2" })).toHaveCount(1);
  const streamRow = page.locator(".timeline > .tl-row:not(.tl-group)").filter({ hasText: "Response received" });
  await expect(streamRow).toHaveCount(1);
  await expect(streamRow).toContainText("2400 chars"); // final detail, not a mid-chunk one
  await expect(page.locator(".tl-group-toggle", { hasText: "Streaming response" })).toHaveCount(0);
  await expect(page.locator(".timeline", { hasText: "Connected to Claude" })).toHaveCount(0); // status channel
});

test("a tool group is collapsed by default and discloses its real children", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitScenario(page, id, claudeTurnEvents(id, { chunks: 3 }));
  await page.locator(".gen-toggle").click();
  const reads = page.locator(".tl-group", { has: page.locator(".tl-group-toggle", { hasText: "Read file ×3" }) });
  const toggle = reads.locator(".tl-group-toggle");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(reads.locator(".tl-children")).toBeHidden();
  await toggle.click(); // keyboard-operable <button>
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const kids = reads.locator(".tl-children .tl-row");
  await expect(kids).toHaveCount(3);
  await expect(kids.nth(0)).toContainText("src/f0.py");
  await expect(kids.nth(2)).toContainText("src/f2.py");
});

test("accounting contract: singles + expanded group children == feed events", async ({ page }) => {
  const id = await sendPrompt(page);
  const events = await emitScenario(page, id, claudeTurnEvents(id, { chunks: 20 }));
  await page.locator(".gen-toggle").click();
  const toggles = page.locator(".tl-group-toggle");
  const n = await toggles.count();
  for (let i = 0; i < n; i++) await toggles.nth(i).click(); // expand every group
  const singles = await page.locator(".timeline > .tl-row:not(.tl-group)").count();
  const children = await page.locator(".timeline .tl-children .tl-row").count();
  const distinctFeedIds = new Set(
    events.filter((e) => (e.channel || "feed") === "feed").map((e) => e.id),
  ).size;
  expect(singles + children).toBe(distinctFeedIds); // every feed event reachable, none invisible
});

test("a group with a failing child auto-expands to surface the error", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitScenario(page, id, [
    { id: `${id}:tool:0`, type: "file_read", status: "success", title: "Read file: a.py", requestId: id, group: `${id}:g0` },
    { id: `${id}:tool:1`, type: "file_read", status: "error", title: "Read file: b.py", requestId: id, group: `${id}:g0` },
    { id: `${id}:tool:2`, type: "file_read", status: "success", title: "Read file: c.py", requestId: id, group: `${id}:g0` },
  ]);
  await page.locator(".gen-toggle").click();
  const group = page.locator(".tl-group");
  await expect(group).toHaveClass(/error/); // worst-of status
  await expect(group.locator(".tl-group-toggle")).toHaveAttribute("aria-expanded", "true");
  await expect(group.locator(".tl-children")).toBeVisible();
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
  // Same grouped shape as the per-event path: 2 tool groups + 1 stream single.
  await expect(page.locator(".timeline > .tl-group")).toHaveCount(2);
  await expect(page.locator(".timeline > .tl-row:not(.tl-group)").filter({ hasText: "Response received" })).toHaveCount(1);
  // Every distinct raw event is accounted for in the store (nothing dropped).
  const distinctIds = new Set(events.map((e) => e.id)).size;
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
