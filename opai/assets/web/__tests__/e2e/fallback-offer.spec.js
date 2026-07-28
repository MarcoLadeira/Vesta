import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";

/* Never a dead end.
 *
 * The consistency complaint OPai is fixing here is not that one provider is
 * flaky — it is that when a provider refuses, the user is left to diagnose a
 * routing problem OPai already solved. When the engine can name a model that
 * still works, the failure card offers it as one click.
 */

const MODELS = [
  { id: "account:codex", label: "Codex · GPT-5.5", kind: "account", group: "codex", provider: "codex", available: true, healthy: true },
  { id: "free:gemini:3.1-flash-lite", label: "Gemini · 3.1 Flash-Lite", kind: "free", group: "free", provider: "gemini", available: true, healthy: true },
  { id: "auto", label: "OPai · Auto mode", kind: "auto", group: "routing", available: true, healthy: true },
];

async function openWithCodexSelected(page) {
  await openApp(page, {
    boot: {
      models: MODELS,
      selectedModel: "account:codex",
      prefs: { model: "account:codex" },
    },
  });
}

const CLI_OUTDATED = {
  status: "failed",
  answer: "The installed CLI is too old for the model it was asked to run.",
  error: {
    code: "PROVIDER_CLI_OUTDATED",
    title: "This provider's CLI is out of date.",
    userMessage: "The installed CLI is too old for the model it was asked to run.",
    provider: "codex",
    recoveryActions: ["change_mode", "open_settings", "show_details"],
    retryable: false,
  },
  fallback_offer: {
    id: "free:gemini:3.1-flash-lite",
    label: "Gemini · 3.1 Flash-Lite",
    kind: "free",
    provider: "gemini",
    paid: false,
  },
};

test("a provider that cannot answer offers the model that can", async ({ page }) => {
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "fix the failing test");
  await finishRequest(page, id, CLI_OUTDATED);

  const offer = page.locator('.error-card [data-a="continue-with"]');
  await expect(offer).toBeVisible();
  await expect(offer).toContainText("Continue with Gemini · 3.1 Flash-Lite");
  // The exact remedy stays visible — the offer is a way forward, not a way to
  // hide what went wrong.
  await expect(page.locator(".error-card")).toContainText("CLI is out of date");
});

test("the offer re-sends the same task on the named model", async ({ page }) => {
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "fix the failing test");
  await finishRequest(page, id, CLI_OUTDATED);

  await page.locator('.error-card [data-a="continue-with"]').click();

  const request = await page.evaluate(() => window.__mock.lastRequest);
  expect(request.model).toBe("free:gemini:3.1-flash-lite");
  expect(request.text).toBe("fix the failing test");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
});

test("the offer never carries the previous route's cloud consent", async ({ page }) => {
  // PR #511 boundary: switching model must not inherit an approval the user
  // gave for a different provider. The new model passes its own gates.
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "fix the failing test");
  await finishRequest(page, id, CLI_OUTDATED);

  await page.locator('.error-card [data-a="continue-with"]').click();

  const request = await page.evaluate(() => window.__mock.lastRequest);
  expect(request.allowCloud).toBeFalsy();
  expect(request.allowLimit).toBeFalsy();
});

test("the composer shows the model that is actually running", async ({ page }) => {
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "fix the failing test");
  await finishRequest(page, id, CLI_OUTDATED);

  await page.locator('.error-card [data-a="continue-with"]').click();

  await expect(page.locator("#modelSel")).toHaveValue("free:gemini:3.1-flash-lite");
});

test("awaiting-input cards never offer a different model", async ({ page }) => {
  // A confirmation card already carries the exact action that unblocks it.
  // Offering another model there would read as a way around a safety gate.
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "fix the failing test");
  await finishRequest(page, id, {
    status: "needs_auto_confirmation",
    answer: "OPai can continue with Gemini · 3.1 Flash-Lite, a free-tier cloud model.",
    fallbackModelId: "free:gemini:3.1-flash-lite",
    fallbackModelLabel: "Gemini · 3.1 Flash-Lite",
    fallback_offer: {
      id: "free:gemini:3.1-flash-lite",
      label: "Gemini · 3.1 Flash-Lite",
      kind: "free",
      provider: "gemini",
      paid: false,
    },
  });

  await expect(page.locator('.error-card [data-a="continue-with"]')).toHaveCount(0);
  await expect(page.locator('.error-card [data-a="fallback"]')).toBeVisible();
});

test("a failure with no usable alternative still ends honestly", async ({ page }) => {
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "fix the failing test");
  await finishRequest(page, id, {
    status: "failed",
    answer: "OPai could not complete this request.",
    error: { code: "UNKNOWN", title: "OPai could not complete this request.", provider: "codex" },
  });

  await expect(page.locator('.error-card [data-a="continue-with"]')).toHaveCount(0);
  await expect(page.locator('.error-card [data-a="switch"]')).toBeVisible();
});
