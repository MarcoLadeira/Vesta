import { expect } from "@playwright/test";

import { fullScenario } from "./fixtures.js";


export const MOCK_PATH = "opai/assets/web/__tests__/e2e/mock-bridge.js";
export const APP_PATH = "/opai/assets/web/index.html";

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
  ), undefined, { timeout: 10_000 });
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
}

export async function emitActivity(page, requestId, event) {
  await page.evaluate(
    ({ id, value }) => window.__mock.emitActivity(id, value),
    { id: requestId, value: event },
  );
}

export async function emitToken(page, requestId, text) {
  await page.evaluate(
    ({ id, value }) => window.__mock.emitToken(id, value),
    { id: requestId, value: text },
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

export async function openNav(page, label) {
  if (label === "Settings") {
    await page.locator("#headerSettings").click();
    return;
  }
  const target = page.getByRole("button", { name: label, exact: true });
  // Simple-by-default sidebar: dashboard items may sit inside a folded group
  // ("Insights"). Do what a user does — unfold it, then click.
  if (!(await target.isVisible().catch(() => false))) {
    const toggles = page.locator(".nav-group-toggle");
    const count = await toggles.count();
    for (let i = 0; i < count; i++) {
      const toggle = toggles.nth(i);
      if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
    }
  }
  await target.click();
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
