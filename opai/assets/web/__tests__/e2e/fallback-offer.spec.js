import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";

/* Never a dead end.
 *
 * The consistency complaint Vesta is fixing here is not that one provider is
 * flaky — it is that when a provider refuses, the user is left to diagnose a
 * routing problem Vesta already solved. When the engine can name a model that
 * still works, the failure card offers it as one click.
 */

const MODELS = [
  { id: "account:codex", label: "Codex · GPT-5.5", kind: "account", group: "codex", provider: "codex", available: true, healthy: true },
  { id: "free:gemini:3.1-flash-lite", label: "Gemini · 3.1 Flash-Lite", kind: "free", group: "free", provider: "gemini", available: true, healthy: true },
  { id: "auto", label: "Vesta · Auto mode", kind: "auto", group: "routing", available: true, healthy: true },
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
    answer: "Vesta can continue with Gemini · 3.1 Flash-Lite, a free-tier cloud model.",
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

test("a governed request explains why Vesta will not reroute it", async ({ page }) => {
  // Re-running an irreversible action on a different provider is a second
  // attempt at something the user approved once, for one route. Vesta declines
  // — and says so, so the missing button reads as a decision, not a dead end.
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "publish the release to production");
  await finishRequest(page, id, {
    status: "failed",
    answer: "Vesta could not complete this request.",
    error: { code: "UNKNOWN", title: "Vesta could not complete this request.", provider: "codex" },
    message_contract: {
      lane: "governed",
      laneLabel: "Governed",
      reason: "This request publishes, releases, or touches credentials.",
      allowProviderFallback: false,
      maxTransientRetries: 0,
    },
  });

  const note = page.locator(".error-card [data-lane-note]");
  await expect(note).toBeVisible();
  await expect(note).toContainText("will not move this request to another model");
  await expect(note).toContainText("publishes, releases, or touches credentials");
  await expect(page.locator('.error-card [data-a="continue-with"]')).toHaveCount(0);
  // The user can still choose a model deliberately — this restricts Vesta, not them.
  await expect(page.locator('.error-card [data-a="switch"]')).toBeVisible();
});

test("an ordinary failure carries no governed-lane note", async ({ page }) => {
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "fix the failing test");
  await finishRequest(page, id, {
    ...CLI_OUTDATED,
    message_contract: {
      lane: "stable",
      laneLabel: "Stable",
      reason: "Routine request on the predictable route.",
      allowProviderFallback: true,
      maxTransientRetries: 1,
    },
  });
  await expect(page.locator(".error-card [data-lane-note]")).toHaveCount(0);
  await expect(page.locator('.error-card [data-a="continue-with"]')).toBeVisible();
});

test("a write-incapable model says so before it is picked", async ({ page }) => {
  // Copilot's CLI cannot expose a bounded edit-tool set, so Vesta refuses to
  // launch it with write access. The picker must say that up front rather than
  // let the user choose it for an editing task and hit the refusal mid-run.
  await openApp(page, {
    boot: {
      models: [
        { id: "account:copilot:gpt-5.4", label: "Copilot · GPT-5.4", kind: "account", group: "copilot", provider: "copilot", available: true, healthy: true, repo_editing: false },
        { id: "auto", label: "Vesta · Auto mode", kind: "auto", group: "routing", available: true, healthy: true },
      ],
      selectedModel: "auto",
    },
  });
  await page.locator("#modelBtn").click();
  const row = page.locator('#modelPop [data-id="account:copilot:gpt-5.4"]');
  await expect(row).toBeVisible();
  await expect(row).toContainText("Ask & Plan only");
  // Still selectable: read-only work through it is perfectly valid.
  await expect(row).toBeEnabled();
});

test("a fully capable model carries no read-only caption", async ({ page }) => {
  await openApp(page, {
    boot: {
      models: [
        { id: "account:claude:sonnet", label: "Claude · Sonnet 4.6", kind: "account", group: "claude", provider: "claude", available: true, healthy: true, repo_editing: true },
        { id: "auto", label: "Vesta · Auto mode", kind: "auto", group: "routing", available: true, healthy: true },
      ],
      selectedModel: "auto",
    },
  });
  await page.locator("#modelBtn").click();
  const row = page.locator('#modelPop [data-id="account:claude:sonnet"]');
  await expect(row).toBeVisible();
  await expect(row.locator("[data-read-only]")).toHaveCount(0);
});

test("a failure with no usable alternative still ends honestly", async ({ page }) => {
  await openWithCodexSelected(page);
  const id = await sendPrompt(page, "fix the failing test");
  await finishRequest(page, id, {
    status: "failed",
    answer: "Vesta could not complete this request.",
    error: { code: "UNKNOWN", title: "Vesta could not complete this request.", provider: "codex" },
  });

  await expect(page.locator('.error-card [data-a="continue-with"]')).toHaveCount(0);
  await expect(page.locator('.error-card [data-a="switch"]')).toBeVisible();
});
