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

// A scenario where DEEPSEEK is available but GOOGLE is not (mirrors real env)
const FREE_MODELS = [
  {
    id: "free:deepseek:deepseek-chat",
    label: "DeepSeek · V3 Chat (free)",
    advanced_label: "DeepSeek V3 Chat via api.deepseek.com — set DEEPSEEK_API_KEY",
    kind: "free",
    group: "free",
    provider: "deepseek",
    paid: false,
    available: true,
  },
  {
    id: "free:deepseek:deepseek-reasoner",
    label: "DeepSeek · R1 Reasoner (free)",
    advanced_label: "DeepSeek R1 via api.deepseek.com — set DEEPSEEK_API_KEY",
    kind: "free",
    group: "free",
    provider: "deepseek",
    paid: false,
    available: true,
  },
  {
    id: "free:google:gemini-2.0-flash",
    label: "Gemini · 2.0 Flash (free)",
    advanced_label: "Google Gemini 2.0 Flash via generativelanguage.googleapis.com — set GOOGLE_API_KEY",
    kind: "free",
    group: "free",
    provider: "google",
    paid: false,
    available: false,
    disabled_reason: "Set GOOGLE_API_KEY to enable Gemini · 2.0 Flash (free)",
  },
  {
    id: "free:groq:llama-3.3-70b-versatile",
    label: "Groq · Llama 3.3 (free)",
    advanced_label: "Groq Llama 3.3 70B via api.groq.com — set GROQ_API_KEY",
    kind: "free",
    group: "free",
    provider: "groq",
    paid: false,
    available: false,
    disabled_reason: "Set GROQ_API_KEY to enable Groq · Llama 3.3 (free)",
  },
  {
    id: "free:mistral:mistral-small-latest",
    label: "Mistral · Small (free)",
    advanced_label: "Mistral Small via api.mistral.ai — set MISTRAL_API_KEY",
    kind: "free",
    group: "free",
    provider: "mistral",
    paid: false,
    available: false,
    disabled_reason: "Set MISTRAL_API_KEY to enable Mistral · Small (free)",
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

test("all 5 free model options are rendered in the picker", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const freeOptions = await page.locator('#modelSel optgroup[label="Free models"] option').allTextContents();
  expect(freeOptions).toHaveLength(5);
  expect(freeOptions.some((t) => t.includes("DeepSeek"))).toBe(true);
  expect(freeOptions.some((t) => t.includes("Gemini"))).toBe(true);
  expect(freeOptions.some((t) => t.includes("Groq"))).toBe(true);
  expect(freeOptions.some((t) => t.includes("Mistral"))).toBe(true);
});

test("free model without API key is disabled with setup hint in title", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const opt = page.locator('#modelSel option[value="free:google:gemini-2.0-flash"]');
  await expect(opt).toBeDisabled();
  await expect(opt).toHaveAttribute("title", /GOOGLE_API_KEY/);
});

test("free model with API key is enabled and selectable", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const opt = page.locator('#modelSel option[value="free:deepseek:deepseek-chat"]');
  await expect(opt).not.toBeDisabled();
});

test("free model labels include (free) suffix", async ({ page }) => {
  await openApp(page, { boot: { models: MODELS_WITH_FREE } });
  const freeOptions = await page.locator('#modelSel optgroup[label="Free models"] option').allTextContents();
  for (const label of freeOptions) {
    expect(label).toMatch(/\(free\)$/);
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
  const opt = page.locator('#modelSel option[value="free:deepseek:deepseek-chat"]');
  const title = await opt.getAttribute("title");
  expect(title).toMatch(/DeepSeek/);
});
