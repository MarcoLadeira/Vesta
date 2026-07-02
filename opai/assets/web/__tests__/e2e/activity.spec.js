import { test, expect } from "@playwright/test";

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

test("generation shows status bar, model, timer and stop", async ({ page }) => {
  await sendPrompt(page, "hello");
  await expect(page.locator(".gen-stage")).toBeVisible();
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

test("activity timeline receives events", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  await page.evaluate((id) => window.__mock.emitActivity(id, { id: "e1", type: "file_read", status: "success", title: "Read file: app.py" }), id);
  await page.click(".gen-toggle");
  await expect(page.locator(".timeline .tl-t")).toContainText("Read file: app.py");
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
  await expect(page.locator(".stopped-card")).toBeVisible();
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  // A late reply from the cancelled request must NOT overwrite the UI.
  await page.evaluate((id) => window.__mock.emitReply(id, { status: "answered", answer: "LATE GHOST" }), id);
  await expect(page.locator(".msg.bot")).not.toContainText("LATE GHOST");
  await expect(page.locator('.stopped-card [data-a="retry"]')).toBeVisible();
});

test("stop during streaming preserves partial and drops later tokens", async ({ page }) => {
  await sendPrompt(page);
  const id = await reqId(page);
  await page.evaluate((id) => window.__mock.emitToken(id, "hello "), id);
  await expect(page.locator(".body.stream")).toContainText("hello");
  await page.click(".gen-stop");
  await expect(page.locator(".stopped-card")).toBeVisible();
  await page.evaluate((id) => window.__mock.emitToken(id, "EXTRA"), id);
  await expect(page.locator(".msg.bot")).not.toContainText("EXTRA");
});

test("double stop yields exactly one cancellation and stays stable", async ({ page }) => {
  await sendPrompt(page);
  await page.click(".gen-stop");
  await page.evaluate(() => { window.__opai.stop(); window.__opai.stop(); });
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
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
  await page.click(".gen-stop");
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
