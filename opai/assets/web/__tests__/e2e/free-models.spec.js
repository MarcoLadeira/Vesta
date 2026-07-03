/**
 * E2E tests for free API model picker integration.
 *
 * Free models always appear in the picker under "Free models" optgroup.
 * When no API key is set (available=false), the option is visible but disabled
 * with the setup hint as its title. When a key is present (available=true),
 * the option is enabled and selectable.
 */
import { test, expect } from "@playwright/test";

import { MODELS } from "./helpers/fixtures.js";
import { openApp } from "./helpers/app.js";

// A scenario where Gemini is available but Groq/Mistral are not.
const FREE_MODELS = [
  {
    id: "free:gemini:gemini-3.1-flash-lite",
    label: "Gemini · 3.1 Flash-Lite (free tier)",
    advanced_label: "Google Gemini 3.1 Flash-Lite via Google AI API (free-tier eligible)",
    kind: "free",
    group: "free",
    provider: "gemini",
    paid: false,
    available: true,
  },
  {
    id: "free:groq:openai/gpt-oss-120b",
    label: "Groq · GPT-OSS 120B (free tier)",
    advanced_label: "OpenAI GPT-OSS 120B via Groq (free-tier eligible)",
    kind: "free",
    group: "free",
    provider: "groq",
    paid: false,
    available: false,
    disabled_reason: "Set GROQ_API_KEY to enable Groq · GPT-OSS 120B (free tier)",
  },
  {
    id: "free:mistral:mistral-small-latest",
    label: "Mistral · Small (free tier)",
    advanced_label: "Mistral Small via Mistral AI API (free-tier eligible)",
    kind: "free",
    group: "free",
    provider: "mistral",
    paid: false,
    available: false,
    disabled_reason: "Set MISTRAL_API_KEY to enable Mistral · Small (free tier)",
  },
];

// Base models without the free group entries from fixture, then add all free models
const MODELS_WITH_FREE = MODELS.filter((m) => m.group !== "free").concat(FREE_MODELS);

test("free models optgroup appears in model picker", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const groupLabels = await page.locator("#modelSel optgroup").evaluateAll(
    (els) => els.map((el) => el.getAttribute("label"))
  );
  expect(groupLabels).toContain("Free models");
});

test("all verified free-tier model options are rendered in the picker", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const freeOptions = await page.locator('#modelSel optgroup[label="Free models"] option').allTextContents();
  expect(freeOptions).toHaveLength(3);
  expect(freeOptions.some((t) => t.includes("Gemini"))).toBe(true);
  expect(freeOptions.some((t) => t.includes("Groq"))).toBe(true);
  expect(freeOptions.some((t) => t.includes("Mistral"))).toBe(true);
});

test("free model without API key is disabled with setup hint in title", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const opt = page.locator('#modelSel option[value="free:groq:openai/gpt-oss-120b"]');
  await expect(opt).toBeDisabled();
  await expect(opt).toHaveAttribute("title", /GROQ_API_KEY/);
});

test("free model with API key is enabled and selectable", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const opt = page.locator('#modelSel option[value="free:gemini:gemini-3.1-flash-lite"]');
  await expect(opt).not.toBeDisabled();
});

test("free model labels identify free-tier eligibility", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const freeOptions = await page.locator('#modelSel optgroup[label="Free models"] option').allTextContents();
  for (const label of freeOptions) {
    expect(label).toMatch(/\(free tier\)$/);
  }
});

test("picker groups order: Claude → Codex → Copilot → Free models → OPai routing → Local models", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const groupLabels = await page.locator("#modelSel optgroup").evaluateAll(
    (els) => els.map((el) => el.getAttribute("label"))
  );
  const expectedOrder = ["Claude", "Codex", "Copilot", "Free models", "OPai routing", "Local models"];
  // Filter to only our expected groups (some may not appear if no models in them)
  const presentExpected = expectedOrder.filter((l) => groupLabels.includes(l));
  // Verify they appear in the correct relative order
  const presentIndices = presentExpected.map((l) => groupLabels.indexOf(l));
  for (let i = 1; i < presentIndices.length; i++) {
    expect(presentIndices[i]).toBeGreaterThan(presentIndices[i - 1]);
  }
});

test("free model advanced_label appears as option title tooltip", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const opt = page.locator('#modelSel option[value="free:gemini:gemini-3.1-flash-lite"]');
  const title = await opt.getAttribute("title");
  expect(title).toMatch(/Gemini/);
});

test("free-tier API asks for confirmation in-chat, then sends on confirm", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  await page.selectOption("#modelSel", "free:gemini:gemini-3.1-flash-lite");
  await page.fill("#input", "Explain this project");
  // No native dialog: the first send goes out immediately with allowCloud=false.
  await page.getByRole("button", { name: "Send" }).click();
  const first = await page.evaluate(() => window.__mock.lastRequest);
  expect(first.allowCloud).toBe(false);
  // The pipeline asks for consent → an in-chat card with a "Send to Gemini" button.
  await page.evaluate(() => {
    const id = window.__mock.lastRequest.requestId;
    window.__mock.emitReply(id, {
      status: "needs_free_confirmation",
      answer: "Gemini will receive your task and compact project context. Continue?",
    });
  });
  await page.getByRole("button", { name: /Send to Gemini/i }).click();
  const second = await page.evaluate(() => window.__mock.lastRequest);
  expect(second.allowCloud).toBe(true);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
  // Consent is persisted for next time via the bridge.
  const grants = await page.evaluate(() => window.__mock.freeConsentGrants);
  expect(grants).toEqual(["free:gemini:gemini-3.1-flash-lite"]);
});

test("no free-tier cloud send happens without explicit confirmation", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  await page.selectOption("#modelSel", "free:gemini:gemini-3.1-flash-lite");
  await page.fill("#input", "Explain this project");
  await page.getByRole("button", { name: "Send" }).click();
  const first = await page.evaluate(() => window.__mock.lastRequest);
  // The provider is never contacted without consent: the only send is gated.
  expect(first.allowCloud).toBe(false);
  // Without clicking the confirm button, no second (allowCloud=true) send fires.
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});

test("free-tier confirmation is one-time — second message goes through immediately", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  await page.selectOption("#modelSel", "free:gemini:gemini-3.1-flash-lite");
  // First message: card appears, user confirms.
  await page.fill("#input", "First message");
  await page.locator("#send").click();
  await page.evaluate(() => window.__mock.emitReply(window.__mock.lastRequest.requestId, {
    status: "needs_free_confirmation", answer: "Continue?",
  }));
  await page.getByRole("button", { name: /Send to Gemini/i }).click();
  // Finish the first exchange cleanly so the composer becomes idle.
  await page.evaluate(() => window.__mock.emitReply(window.__mock.lastRequest.requestId, {
    status: "answered", answer: "hi", receipt: {},
  }));
  const firstCount = await page.evaluate(() => window.__mock.sendCount);
  // Second message: no card, allowCloud=true up front.
  await page.fill("#input", "Second message");
  await page.locator("#send").click();
  const second = await page.evaluate(() => window.__mock.lastRequest);
  expect(second.text).toBe("Second message");
  expect(second.allowCloud).toBe(true);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(firstCount + 1);
});

test("free-tier consent from previous session skips the card entirely", async ({ page }) => {
  await openApp(page, {
    boot: {
      models: MODELS_WITH_FREE,
      prefs: { freeConsent: ["free:gemini:gemini-3.1-flash-lite"] },
    },
  });
  await page.selectOption("#modelSel", "free:gemini:gemini-3.1-flash-lite");
  await page.fill("#input", "Hello");
  await page.getByRole("button", { name: "Send" }).click();
  const req = await page.evaluate(() => window.__mock.lastRequest);
  expect(req.allowCloud).toBe(true);
  // No new grant call needed — consent came from prefs.
  const grants = await page.evaluate(() => window.__mock.freeConsentGrants);
  expect(grants).toEqual([]);
});
