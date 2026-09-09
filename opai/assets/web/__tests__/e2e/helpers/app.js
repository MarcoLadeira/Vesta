import { expect } from "@playwright/test";

import { fullScenario } from "./fixtures.js";


export const MOCK_PATH = "opai/assets/web/__tests__/e2e/mock-bridge.js";
export const APP_PATH = "/opai/assets/web/index.html";
// Every spec boots the app through this helper, so this one number decides
// whether a contended run reads as a functional failure. Two hosted runs
// failed here at exactly 20000ms -- model-usage.spec.js in one,
// app-shell.spec.js in the next, 487 of 488 passing both times, and both
// stack-traced to this line rather than to anything the specs assert. A
// different spec each time is the signature of scheduling luck on a busy
// runner, not of a bug in whichever spec drew the short straw.
//
// It was also two thirds of the whole per-test budget, which left almost no
// room for a test's actual assertions after a slow boot. Doubled, and the test
// budget in playwright.config.js raised alongside it so boot is no longer most
// of the test.
const APP_READY_TIMEOUT = process.env.CI ? 40_000 : 10_000;

export async function openApp(page, overrides = {}) {
  const diagnostics = { consoleErrors: [], pageErrors: [] };
  page.on("console", (message) => {
    // Static Chromium cannot resolve Qt's qrc:// script. The injected bridge
    // replaces it before app boot; every other console error remains fatal.
    const expectedQtHarnessMiss = message.text() === "Failed to load resource: net::ERR_UNKNOWN_URL_SCHEME";
    if (message.type() === "error" && !expectedQtHarnessMiss) {
      diagnostics.consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => diagnostics.pageErrors.push(error.message));

  await page.addInitScript((scenario) => {
    window.__OPAI_TEST_SCENARIO__ = scenario;
  }, fullScenario(overrides));
  await page.addInitScript({ path: MOCK_PATH });
  await page.goto(APP_PATH);
  await page.waitForSelector("#input");
  await page.waitForFunction(() => (
    Boolean(window.__opai) && document.querySelector("#modelSel")?.options.length > 0
  ), undefined, { timeout: APP_READY_TIMEOUT });
  return diagnostics;
}

export async function sendPrompt(page, text = "Explain this project") {
  await page.fill("#input", text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".gen-stop")).toBeVisible();
  return page.evaluate(() => window.__mock.reqId());
}

export async function finishRequest(page, requestId, result = {}) {
  const payload = {
    status: "answered",
    answer: "Completed safely.",
    receipt: {},
    ...result,
  };
  await page.evaluate(
    ({ id, value }) => window.__mock.emitReply(id, value),
    { id: requestId, value: payload },
  );
  await page.waitForFunction(
    (id) => window.__opai?.state?.currentRequest !== id,
    requestId,
  );
}

export async function emitActivity(page, requestId, event) {
  await page.evaluate(
    ({ id, value }) => window.__mock.emitActivity(id, value),
    { id: requestId, value: event },
  );
}

export async function emitToken(page, requestId, text, { blockStart = false } = {}) {
  await page.evaluate(
    ({ id, value, startsBlock }) => window.__mock.emitToken(id, value, startsBlock),
    { id: requestId, value: text, startsBlock: blockStart },
  );
}

/* ---- Calm Stream scenario builders (#230) ----------------------------------
   Canned event sequences shaped exactly like the real backend emits since
   #223-#225: derived stable ids ({rid}:connect / :stream / :tool:{seq}),
   status-channel mirrors, and per-type tool groups. Each builder returns the
   emitted events so specs can do accounting assertions. Emission happens in
   ONE page.evaluate (a synchronous burst), settled with two rAF frames. */

export function claudeTurnEvents(rid, { chunks = 200, reads = 3, commands = 2 } = {}) {
  const events = [];
  events.push({
    id: `${rid}:connect`, type: "provider_request", status: "success",
    title: "Connected to Claude · claude-opus", requestId: rid, channel: "status",
  });
  for (let i = 1; i <= chunks; i++) {
    events.push({
      id: `${rid}:stream`, type: "streaming", status: "running",
      title: "Streaming response", detail: `${i * 12} chars · 00:0${i % 9}`, requestId: rid,
    });
  }
  let seq = 0;
  for (let i = 0; i < reads; i++, seq++) {
    events.push({
      id: `${rid}:tool:${seq}`, type: "file_read", status: "success",
      title: `Read file: src/f${i}.py`, requestId: rid, group: `${rid}:g0`,
    });
  }
  for (let i = 0; i < commands; i++, seq++) {
    events.push({
      id: `${rid}:tool:${seq}`, type: "command_run", status: "success",
      title: "Ran command: pytest", requestId: rid, group: `${rid}:g1`,
    });
  }
  events.push({
    id: `${rid}:stream`, type: "streaming", status: "success",
    title: "Response received", detail: `${chunks * 12} chars`, requestId: rid, durationMs: 1234,
  });
  return events;
}

export function codexTurnEvents(rid, items = 2) {
  const events = [{
    id: `${rid}:connect`, type: "provider_request", status: "success",
    title: "Connected to Codex", requestId: rid, channel: "status",
  }];
  for (let i = 0; i < items; i++) {
    events.push({
      id: `${rid}:codex:item_${i}`, type: "command_run", status: "running",
      title: `Ran command: step ${i}`, requestId: rid,
    });
    events.push({
      id: `${rid}:codex:item_${i}`, type: "command_run", status: "success",
      title: `Ran command: step ${i}`, requestId: rid, durationMs: 40,
    });
  }
  return events;
}

export async function emitScenario(page, requestId, events) {
  await page.evaluate(async ({ id, list }) => {
    for (const event of list) window.__mock.emitActivity(id, event);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, { id: requestId, list: events });
  return events;
}

export async function emitScenarioBatch(page, requestId, events) {
  await page.evaluate(async ({ id, list }) => {
    window.__mock.emitActivityBatch(id, list);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, { id: requestId, list: events });
  return events;
}

const SETTINGS_TARGETS = {
  overview: "general",
  providers: "connections",
  balance: "usage",
  firewall: "usage",
  permissions: "safety",
  privacy: "safety",
  tools: "advanced",
  about: "advanced",
};

// Specs may still use historical destinations to exercise compatibility, but
// they click the canonical seven-page navigation a current user sees.
export async function openSettings(page, id) {
  await openNav(page, "Settings");
  if (id) {
    const target = SETTINGS_TARGETS[id] || id;
    await page.locator(`.settings-rail-item[data-rail-target="${target}"]`).click();
  }
}

export async function openNav(page, label) {
  // Do what a user does. The sidebar is the chat list now, so most destinations
  // are reached from the header or from Settings -> Tools & Insights rather
  // than from a nav row.
  if (label === "Settings") {
    const headerSettings = page.locator("#headerSettings");
    if (await headerSettings.isVisible().catch(() => false)) {
      await headerSettings.click();
    } else {
      await page.locator("#sidebarToggle").click();
      await page.locator("#footSettings").click();
    }
    return;
  }
  if (label === "Chat") {
    await page.locator("#headerNewChat").click();
    return;
  }
  const target = page.getByRole("button", { name: label, exact: true });
  if (await target.isVisible().catch(() => false)) {
    await target.click();
    return;
  }
  // Prompt Library and the Insights dashboards live under Advanced.
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="advanced"]').click();
  await page.locator(`[data-go-view] >> text=${label}`).first().click();
}

export function expectNoFatalErrors(diagnostics) {
  expect(diagnostics.pageErrors).toEqual([]);
  expect(diagnostics.consoleErrors).toEqual([]);
}

export async function expectNoUiSentinels(page, locator = page.locator("body")) {
  const text = await locator.innerText();
  expect(text).not.toMatch(/\bundefined\b|\bNaN\b|\[object Object\]/);
}

export async function expectNoRawProviderIds(page) {
  const thread = await page.locator("#thread").innerText();
  expect(thread).not.toMatch(/account:(claude|codex|copilot):|anthropic\.messages\.create/);
}

/**
 * Expand a turn's diagnostics.
 *
 * Everything describing a run -- evidence, verification, changes, the work
 * log, the workflow card, the cost receipt -- now sits behind the one-line
 * turn summary. Tests that assert on any of it have to open the disclosure
 * first, exactly as a reader would.
 *
 * Idempotent, and a no-op on turns that have no diagnostics to show, so it can
 * be called unconditionally before reaching into a response.
 */
export async function openTurnDetails(page, scope) {
  const root = scope || page;
  const summaries = root.locator(".turn-summary:not([open]) > .ts-row");
  const count = await summaries.count();
  for (let index = 0; index < count; index += 1) {
    // Click the verdict, never the row's centre: Retry lives in the middle of
    // the row on a failed turn, and clicking it would retry the request rather
    // than open the panel.
    await summaries.nth(index).locator(".ts-verdict").click();
  }
}
