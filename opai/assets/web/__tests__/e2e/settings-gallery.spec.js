import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";

test.describe.configure({ timeout: 60_000 });

const DESTINATIONS = [
  ["general", "general"],
  ["appearance", "appearance"],
  ["models", "models-routing"],
  ["agents", "agents"],
  ["plugins", "plugins"],
  ["usage", "usage-budgets"],
  ["workspace", "workspace"],
  ["connections", "connections"],
  ["safety", "safety-privacy"],
  ["advanced", "advanced"],
];

async function bootSettings(page, viewport, overrides = {}) {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openApp(page, overrides);
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage .settings-layout")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function choose(page, id) {
  const back = page.locator("#settingsMobileBack");
  if (await back.isVisible()) {
    await back.click();
  }
  await page.locator(`.settings-rail-item[data-rail-target="${id}"]`).click();
  await expect(page.locator(`#set-sec-${id}`)).toBeVisible();
}

async function capture(page, name) {
  await expect(page.locator("#view-settings")).toHaveScreenshot(name, {
    animations: "disabled",
    caret: "hide",
    maxDiffPixels: 200,
  });
}

test("desktop Settings destination gallery", async ({ page }) => {
  await bootSettings(page, { width: 1440, height: 900 });
  for (const [id, name] of DESTINATIONS) {
    await choose(page, id);
    await capture(page, `settings-desktop-${name}.png`);
  }
});

test("tablet Settings adaptive gallery", async ({ page }) => {
  await bootSettings(page, { width: 768, height: 1024 });
  for (const [id, name] of DESTINATIONS.filter(([id]) =>
    ["general", "models", "workspace", "connections", "usage"].includes(id)
  )) {
    await choose(page, id);
    await capture(page, `settings-tablet-${name}.png`);
  }
});

test("phone Settings index, search, and destination gallery", async ({ page }) => {
  await bootSettings(page, { width: 390, height: 844 });
  await capture(page, "settings-phone-index.png");

  const search = page.locator("#settingsSearch");
  await search.fill("cloud gate");
  await expect(page.locator(".settings-result")).toHaveCount(1);
  await capture(page, "settings-phone-search-results.png");
  await page.locator("#settingsSearchClear").click();

  for (const [id, name] of DESTINATIONS.filter(([id]) =>
    ["models", "workspace", "connections", "usage", "safety", "appearance"].includes(id)
  )) {
    await choose(page, id);
    await capture(page, `settings-phone-${name}.png`);
  }

  await page.locator("#settingsMobileBack").click();
  await search.fill("setting-that-does-not-exist");
  await expect(page.locator("#settingsNoResults")).toBeVisible();
  await capture(page, "settings-phone-search-no-results.png");
});

test("Settings high-information state gallery", async ({ page }) => {
  await bootSettings(page, { width: 1440, height: 900 }, {
    settings: {
      connectionDoctor: [
        {
          providerId: "codex",
          displayName: "Codex",
          kind: "account",
          health: "failed",
          authStatus: "failed",
          credentialSourceLabel: "Subscription sign-in",
          cliInstalled: true,
          cliVersion: "codex-cli test",
          safeDiagnostic: "The test account session expired. Sign in again to restore this route.",
          lastErrorCode: "account_expired",
          lastError: "Authentication expired.",
          detected: true,
          loginSupported: true,
          recoveryActions: ["sign_in", "test_connection", "disconnect"],
        },
      ],
      usage: [
        {
          modelId: "account:codex:gpt-5.5",
          used: 950,
          limit: 1000,
          percent: 95,
          metric: "tokens",
          source: "local",
          window: "month",
          confidence: "measured",
          modelCalls: 19,
          taskCount: 4,
        },
      ],
      models: [
        { id: "account:codex:gpt-5.5", label: "Codex · GPT-5.5" },
      ],
      about: {
        update: {
          operation: {
            state: "available",
            candidate: { version: "0.3.0-test" },
            safe_diagnostic: "A verified test update is ready to download.",
          },
          discovery: {
            summary: {
              title: "Update available",
              message: "OPai 0.3.0-test is available for this deterministic review build.",
            },
          },
        },
      },
    },
  });

  await choose(page, "connections");
  await capture(page, "settings-state-failed-connection-recovery.png");

  await choose(page, "usage");
  await page.locator('[data-model-id="account:codex:gpt-5.5"]').scrollIntoViewIfNeeded();
  await capture(page, "settings-state-near-budget-limit.png");

  await choose(page, "safety");
  await page.locator("#set-sec-safety .perm .s.block").scrollIntoViewIfNeeded();
  await capture(page, "settings-state-permission-restricted.png");

  await choose(page, "advanced");
  await page.locator("#settingsUpdateCard").scrollIntoViewIfNeeded();
  await capture(page, "settings-state-update-available.png");
});
