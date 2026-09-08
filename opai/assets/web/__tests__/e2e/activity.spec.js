import { test, expect } from "@playwright/test";

import { openTurnDetails } from "./helpers/app.js";

const MOCK = "opai/assets/web/__tests__/e2e/mock-bridge.js";

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: MOCK });
  await page.goto("/opai/assets/web/index.html");
  await page.waitForSelector("#input");
});

async function sendPrompt(page, text) {
  await page.fill("#input", text || "do something");
  await page.click("#send");
  await page.waitForSelector(".gen-stop");
}
const reqId = (page) => page.evaluate(() => window.__mock.reqId());

test("generation shows a compact status row, model, timer and stop", async ({ page }) => {
  await sendPrompt(page, "hello");
  await expect(page.locator(".gen-stage")).toContainText("is preparing your response");
  await expect(page.locator(".gen-work-surface")).toBeVisible();
  await expect(page.locator(".gen-eyebrow")).toBeHidden();
  const surface = await page.locator(".gen-work-surface").boundingBox();
  const workLog = await page.locator(".gen-toggle").boundingBox();
  expect(surface.height).toBeLessThanOrEqual(110);
  expect(workLog.height).toBeGreaterThanOrEqual(32);
  await expect(page.locator(".thinking")).toHaveCount(0);
  await expect(page.locator(".gen-toggle")).toHaveText("Hide work log");
  await expect(page.locator(".gen-time")).toHaveText(/0\d:\d\d/);
  await expect(page.locator(".gen-stop")).toBeVisible();
  await expect(page.locator("body")).toHaveClass(/ai-working/);
  await expect(page.locator(".msg.bot .role")).toContainText("OPai");

  const id = await reqId(page);
  await page.evaluate((id) => window.__mock.emitReply(id, { status: "answered", answer: "Hi there", receipt: {} }), id);
  await expect(page.locator(".msg.bot .body")).toContainText("Hi there");
  await expect(page.locator(".gen-stop")).toHaveCount(0);
  await expect(page.locator("body")).not.toHaveClass(/ai-working/);
});

test("the live work log is open by default and grows the active panel", async ({ page }) => {
  await sendPrompt(page, "inspect the project");
  const surface = page.locator(".gen-work-surface");
  const before = await surface.boundingBox();

  await expect(page.locator(".gen-toggle")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".gen-toggle")).toHaveText("Hide work log");
  await expect(page.locator(".timeline")).toBeVisible();

  const id = await reqId(page);
  await page.evaluate((requestId) => window.__mock.emitActivity(requestId, {
    id: "read", type: "file_read", status: "success", title: "Reading project files", detail: "app.py",
  }), id);
  await expect(page.locator(".timeline .tl-t")).toContainText("Reading project files");

  const after = await surface.boundingBox();
  expect(after.height).toBeGreaterThan(before.height);
});

test("a live final response uses its structured evidence and work log", async ({ page }) => {
  await sendPrompt(page, "verify the change");
  const id = await reqId(page);
  await page.evaluate((requestId) => window.__mock.emitReply(requestId, {
    status: "answered",
    answer: "Verification is complete. 999 imaginary tests did not run.",
    receipt: {},
    presentation: {
      schema_version: 1,
      run: { state: "completed", label: "Completed", reason: "Structured checks passed." },
      evidence: { verification: { applicable: true, verdict: "verified" } },
      tests: { status: "passed", passed: 2, failed: 0, skipped: 0 },
      activity: [
        { phase: "test", status: "completed", message: '<script>alert("x")</script>' },
        { phase: "test", status: "completed", message: "Two focused checks passed" },
      ],
    },
  }), id);

  const response = page.locator(".msg.bot").last();
  // The verdict is the turn summary's own row now, and the evidence sits
  // behind it. The claim under test is unchanged: the response reports the
  // structured evidence it was given, and never the 999 it invented in prose.
  await expect(response.locator(".ts-verdict")).toContainText("Done");
  await openTurnDetails(page, response);
  await expect(response.locator(".evidence-bar")).toContainText("2 passed");
  await expect(response.locator(".evidence-bar")).not.toContainText("999");
  await expect(response.locator(".gen-toggle.done")).toContainText("Work log (2)");
  await response.locator(".gen-toggle.done").click();
  await expect(response.locator(".timeline.done")).toContainText("Two focused checks passed");
  await expect(response.locator("script")).toHaveCount(0);
});

test("activity timeline receives events", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  await page.evaluate((id) => window.__mock.emitActivity(id, {
    id: "e1", type: "file_read", status: "success", title: "Reading project files", detail: "app.py",
  }), id);
  await expect(page.locator(".gen-stage")).toHaveText("Reading project files");
  await expect(page.locator(".gen-detail")).toHaveText("app.py");
  await expect(page.locator(".gen-toggle")).toHaveText("Hide work log");
  await expect(page.locator(".timeline .tl-t")).toContainText("Reading project files");
});

test("real agent operations update distinct timeline rows without duplicates", async ({ page }) => {
  await sendPrompt(page, "ship the fix");
  const id = await reqId(page);
  const emit = (event) => page.evaluate(
    ({ id, event }) => window.__mock.emitActivity(id, event), { id, event },
  );
  await emit({ id: "tests", type: "validation", status: "running", title: "Running full tests" });
  await emit({ id: "tests", type: "validation", status: "success", title: "Full tests passed" });
  await emit({ id: "pr", type: "tool_call", status: "success", title: "Pull request opened" });
  await emit({ id: "ci", type: "ci_watch", status: "running", title: "CI checks pending" });
  await emit({ id: "ci", type: "ci_watch", status: "success", title: "CI checks passed" });
  await emit({ id: "merge", type: "command_complete", status: "success", title: "Pull request merged" });
  const rows = page.locator(".timeline .tl-row");
  await expect(rows).toHaveCount(4);
  await expect(rows).toContainText([
    "Full tests passed",
    "Pull request opened",
    "CI checks passed",
    "Pull request merged",
  ]);
});

test("inspector shows live status while generating and hides after", async ({ page }) => {
  await expect(page.locator("#inspLive")).toBeHidden();
  await sendPrompt(page);
  await expect(page.locator("#inspLive")).toBeVisible();
  const id = await reqId(page);
  await page.evaluate((id) => window.__mock.emitActivity(id, { id: "e1", type: "file_read", status: "success", title: "Read file: app.py" }), id);
  await expect(page.locator("#inspLiveStep")).toContainText("Read file: app.py");
  await expect(page.locator("#inspLiveEvents")).toContainText("step");
  await page.evaluate((id) => window.__mock.emitReply(id, { status: "answered", answer: "done", receipt: {} }), id);
  await expect(page.locator("#inspLive")).toBeHidden();
});

test("slow model shows 'taking longer' and keeps stop clickable", async ({ page }) => {
  await sendPrompt(page);
  await page.evaluate(() => { window.__opai.state.startTime = Date.now() - 16000; });
  await expect(page.locator(".gen-reassure")).toContainText("longer than usual");
  await expect(page.locator(".gen-switch")).toBeVisible();
  await expect(page.locator(".gen-stop")).toBeEnabled();
});

test("stop before first token; late reply is ignored (no stale overwrite)", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  await page.click(".gen-stop");
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  // #380: a reply arriving while the run is still stopping must not land
  // either. The old stop() got this by dropping the request id, which also
  // made teardown unobservable; the guard now lives in canApply().
  await page.evaluate((id) => window.__mock.emitReply(id, { status: "answered", answer: "LATE GHOST" }), id);
  await expect(page.locator(".msg.bot")).not.toContainText("LATE GHOST");

  await page.evaluate((id) => window.__mock.confirmCancel(id), id);
  await expect(page.locator(".stopped-card")).toBeVisible();
  await page.evaluate((id) => window.__mock.emitReply(id, { status: "answered", answer: "LATER GHOST" }), id);
  await expect(page.locator(".msg.bot")).not.toContainText("LATER GHOST");
  await expect(page.locator('.stopped-card [data-a="retry"]')).toBeVisible();
});

test("stop during streaming preserves partial and drops later tokens", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  await page.evaluate((id) => window.__mock.emitToken(id, "hello "), id);
  await expect(page.locator(".body.stream")).toContainText("hello");
  await page.click(".gen-stop");
  // Tokens are dropped from the moment Stop is accepted, before teardown is
  // even confirmed.
  await page.evaluate((id) => window.__mock.emitToken(id, "EXTRA"), id);
  await page.evaluate((id) => window.__mock.confirmCancel(id), id);
  await expect(page.locator(".stopped-card")).toBeVisible();
  await expect(page.locator(".msg.bot")).not.toContainText("EXTRA");
});

test("double stop yields exactly one cancellation and stays stable", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  await page.click(".gen-stop");
  await page.evaluate(() => { window.__opai.stop(); window.__opai.stop(); });
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  await page.evaluate((id) => window.__mock.confirmCancel(id), id);
  await expect(page.locator(".stopped-card")).toHaveCount(1);
});

test("Enter during generation does not create a duplicate request", async ({ page }) => {
  await sendPrompt(page);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
  await page.fill("#input", "again");
  await page.press("#input", "Enter");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});

test("retry after stop starts a fresh request", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  await page.click(".gen-stop");
  await page.evaluate((id) => window.__mock.confirmCancel(id), id);
  await page.click('.stopped-card [data-a="retry"]');
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
  await expect(page.locator(".gen-stop")).toBeVisible();
});

test("provider error shows a recoverable error card", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  await page.evaluate((id) => window.__mock.emitReply(id, {
    status: "failed",
    answer: "Reconnect the provider account or update its credentials in Settings.",
    error: {
      code: "AUTH_INVALID",
      title: "OPai could not authenticate this connection.",
      userMessage: "Reconnect the provider account or update its credentials in Settings.",
      recoveryActions: ["open_settings", "reconnect", "show_details"],
      technicalMessage: "401 Invalid authentication credentials",
    },
  }), id);
  await expect(page.locator(".error-card")).toBeVisible();
  await expect(page.locator(".error-card")).toContainText("OPai could not authenticate");
  await expect(page.locator('.error-card [data-a="settings"]')).toBeVisible();
  await expect(page.locator('.error-card [data-a="details"]')).toBeVisible();
  await expect(page.locator(".error-card .ec-w")).not.toContainText("401 Invalid authentication credentials");
  await expect(page.locator(".ec-details")).not.toHaveAttribute("open", "");
  await page.click('.error-card [data-a="details"]');
  await expect(page.locator(".ec-details")).toHaveAttribute("open", "");
  await expect(page.locator(".ec-details")).toContainText("401 Invalid authentication credentials");
  await page.click('.error-card [data-a="retry"]');
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
});

test("normal chat and status are OPai-first", async ({ page }) => {
  await sendPrompt(page);
  await expect(page.locator("#statusLine")).toContainText("OPai");
  await expect(page.locator("#modelSel option:checked")).toContainText("OPai");
  await expect(page.locator(".msg.bot .role")).toContainText("OPai");
  await expect(page.locator("#view-chat")).not.toContainText("account:claude");
});

test("stop button and activity region are accessible", async ({ page }) => {
  await sendPrompt(page);
  await expect(page.locator(".gen-stop")).toHaveAttribute("aria-label", "Stop generation");
  await expect(page.locator(".timeline")).toHaveAttribute("aria-label", "AI activity");
  await expect(page.locator(".gen-reassure")).toHaveAttribute("aria-live", "polite");
});

test("timeline rows carry real elapsed offsets, never invented ones", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  // Real timestamp -> a "+N.Ns" offset appears.
  await page.evaluate((id) => window.__mock.emitActivity(id, {
    id: "t1", type: "file_read", status: "success", title: "Read file: app.py", timestamp: Date.now(),
  }), id);
  // No timestamp -> no offset label for that row (honesty rule).
  await page.evaluate((id) => window.__mock.emitActivity(id, {
    id: "t2", type: "command_run", status: "success", title: "Ran command: pytest",
  }), id);
  const rows = page.locator(".timeline .tl-row");
  await expect(rows.filter({ hasText: "Read file" }).locator(".tl-ts")).toHaveText(/\+\d+(\.\d)?s/);
  await expect(rows.filter({ hasText: "Ran command" }).locator(".tl-ts")).toHaveCount(0);
});

test("the receipt strip copies a plaintext receipt on click", async ({ page }) => {
  await sendPrompt(page, "review the auth module");
  const id = await reqId(page);
  await page.evaluate((id) => window.__mock.emitReply(id, {
    status: "answered", answer: "done",
    receipt: { estimated_actual_usd: 0.0123 },
  }), id);
  // The cost receipt is a record of the run, so it lives behind the summary.
  await openTurnDetails(page);
  const strip = page.locator(".footer-note");
  await expect(strip).toBeVisible();
  await expect(strip).toHaveAttribute("aria-label", "Copy receipt");
  await strip.click();
  await expect(page.locator("#toast")).toContainText("Receipt copied");
});
