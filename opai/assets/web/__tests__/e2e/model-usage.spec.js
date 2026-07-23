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
  await expect(card.locator("[data-usage-primary]")).toHaveText("18% used");
  await expect(card).toContainText("270 / 1,500 requests", seen);
  await expect(card.locator("[data-usage-pill]")).toHaveText("Live");
  // The countdown is present and formatted as a duration.
  await expect(card.locator("[data-usage-countdown]")).toContainText(/\d+ (hr|min|sec)/);
});

test("account providers show a calm no-usage-API state with the official-usage link, never a fake bar", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "usage");
  const card = page.locator('.usage2-card[data-usage-provider="claude"]');
  await expect(card).toContainText("5-hour session window", seen);
  await expect(card.locator("[data-usage-pill]")).toHaveText("No usage API");
  await expect(card.locator('[role="progressbar"]')).toHaveCount(0);
  // The official link uses the app's data-ext convention (routed through the
  // native bridge), never a bare target=_blank — direct navigation is blocked
  // by the page's CSP and would silently no-op.
  const link = card.locator("a.usage2-link");
  await expect(link).toHaveAttribute("href", "https://claude.ai/settings/usage");
  await expect(link).toHaveAttribute("data-ext", "1");
  await expect(link).not.toHaveAttribute("target", "_blank");
  // With no official figure, OPai's own tracked count is the headline stat —
  // same size/weight class as a real percentage or credit figure — clearly
  // labelled, never presented as the provider's number.
  await expect(card.locator("[data-usage-primary]")).toHaveText("12 calls tracked");
  await expect(card.locator(".usage2-headline")).toHaveClass(/tracked/);
  // All-time, not window-bound — Claude's rolling 5-hour window almost never
  // has OPai-routed activity in it, since most usage goes through the bare
  // CLI directly (which OPai's ledger never sees). A "last used" freshness
  // readout and an explicit clarification prevent the count from being
  // mistaken for real Claude usage.
  await expect(card).toContainText("All time via OPai", seen);
  await expect(card).toContainText(/last used \d+ d ago/, seen);
  await expect(card).toContainText("not the claude CLI used directly", seen);
});

test("Check official usage opens through the native bridge, not a direct (CSP-blocked) navigation", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "usage");
  await page.locator('.usage2-card[data-usage-provider="claude"] a.usage2-link').click();
  await expect
    .poll(() => page.evaluate(() => window.__mock.externalUrls))
    .toEqual(["https://claude.ai/settings/usage"]);
  // Clicking never actually navigates the settings page away.
  await expect(page.locator("#view-settings")).toBeVisible();
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
