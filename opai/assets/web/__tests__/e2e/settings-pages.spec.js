import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test.beforeEach(async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
});

const railItem = (page, id) => page.locator(`.settings-rail-item[data-rail-target="${id}"]`);
const seen = { useInnerText: true };

test("the rail presents seven task-oriented destinations with General first", async ({ page }) => {
  await expect(page.locator(".settings-rail-item .settings-rail-label")).toHaveText([
    "General",
    "Models & Routing",
    "Connections",
    "Usage & Budgets",
    "Safety & Privacy",
    "Appearance",
    "Advanced",
  ]);
  await expect(railItem(page, "general")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#settingsPage")).toContainText("Defaults for new tasks", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Daily cap", seen);
});

test("choosing a destination replaces the detail pane and writes a canonical link", async ({ page }) => {
  await railItem(page, "usage").click();
  await expect(page.locator("#settingsPage")).toContainText("Daily cap", seen);
  await expect(page.locator("#setPanic")).not.toBeVisible();
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
  await expect(railItem(page, "usage")).toHaveAttribute("aria-current", "page");
  await expect(railItem(page, "connections")).not.toHaveAttribute("aria-current", "page");
  expect(page.url()).toContain("#settings/usage");
});

test("Advanced shows the exact hosted asset build identity", async ({ page }) => {
  await railItem(page, "advanced").click();
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("ASSET BUILD", seen);
  await expect(settings).toContainText("7ac9f12b4e88", seen);
  await expect(settings).toContainText("source checkout", seen);
});

test("a canonical destination survives leaving Settings", async ({ page }) => {
  await railItem(page, "safety").click();
  expect(page.url()).toContain("#settings/safety");
  await openNav(page, "Chat");
  await openNav(page, "Settings");
  await expect(railItem(page, "safety")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#settingsPage")).toContainText("No telemetry", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
});

test("a historical deep link opens the owning destination", async ({ page }) => {
  await page.evaluate(() => history.replaceState(null, "", "#settings/firewall"));
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage")).toContainText("Daily cap", seen);
  await expect(railItem(page, "usage")).toHaveAttribute("aria-current", "page");
});
