import { test, expect } from "@playwright/test";

import { openApp, openSettings } from "./helpers/app.js";


// Settings → Model Usage: per-provider account-usage windows. Live-limit
// providers show a real bar + reset countdown; account providers show an
// honest "unavailable" with the official link; prepaid credit shows the
// remaining amount without a fabricated percentage. One unavailable provider
// never breaks the rest of the page.

const seen = { useInnerText: true };

test("the rail exposes Model Usage as its own page under Spend & safety", async ({ page }) => {
  await openApp(page);
  await openSettings(page);
  await expect(
    page.locator('.settings-rail-item[data-rail-target="usage"] .settings-rail-label')
  ).toHaveText("Model Usage");
});

test("a live-limit provider renders a real progress bar and a ticking reset countdown", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "usage");
  const card = page.locator('.usage2-card[data-usage-provider="gemini"]');
  await expect(card).toBeVisible();
  await expect(card.locator('[role="progressbar"]')).toHaveAttribute("aria-valuenow", "18");
  await expect(card).toContainText("270 / 1,500 requests used", seen);
  await expect(card.locator("[data-usage-pill]")).toHaveText("Live");
  // The countdown is present and formatted as a duration.
  await expect(card.locator("[data-usage-countdown]")).toContainText(/\d+ (hr|min|sec)/);
});

test("account providers show an honest unavailable state with the official-usage link, never a fake bar", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "usage");
  const card = page.locator('.usage2-card[data-usage-provider="claude"]');
  await expect(card).toContainText("5-hour session window", seen);
  await expect(card.locator('[role="progressbar"]')).toHaveCount(0);
  await expect(card.locator("a.usage2-link")).toHaveAttribute("href", "https://claude.ai/settings/usage");
  // OPai-tracked activity is shown but clearly separated from official usage.
  // (The tag is CSS-uppercased, so match case-insensitively.)
  await expect(card).toContainText(/opai tracked/i, seen);
  await expect(card).toContainText("12 calls", seen);
});

test("prepaid credit shows the remaining amount without inventing a percentage", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "usage");
  const card = page.locator('.usage2-card[data-usage-provider="kimi"]');
  await expect(card).toContainText("8.42 USD left", seen);
  await expect(card.locator('[role="progressbar"]')).toHaveCount(0);
});

test("a not-connected provider is shown as such and does not break the page", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "usage");
  const card = page.locator('.usage2-card[data-usage-provider="groq"]');
  await expect(card.locator("[data-usage-pill]")).toHaveText("Not connected");
  // The other cards still render — one unavailable provider is isolated.
  await expect(page.locator(".usage2-card")).toHaveCount(4);
});

test("Refresh live usage calls the bridge and re-renders", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "usage");
  await page.locator("#usageRefresh").click();
  await expect.poll(() => page.evaluate(() => window.__mock.usageRefreshes)).toBeGreaterThan(0);
});

test("the page shows a discoverable empty state when no providers are connected", async ({ page }) => {
  await openApp(page, { settings: { providerUsage: [] } });
  await openSettings(page, "usage");
  await expect(page.locator("#settingsPage")).toContainText("No providers connected yet", seen);
});

test("search finds the Model Usage page from another page", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "overview");
  await page.locator("#settingsSearch").fill("session window");
  await expect(page.locator("#settingsPage")).toContainText("5-hour session window", seen);
});
