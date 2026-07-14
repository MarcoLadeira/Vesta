import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


// Claude-style paned settings (#217): the rail is real page navigation — one
// cleanly labelled page at a time, deep-linkable via #settings/<id>. Hidden
// panes stay in the DOM for wiring, so on-screen claims use innerText.

test.beforeEach(async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
});

const railItem = (page, id) => page.locator(`.settings-rail-item[data-rail-target="${id}"]`);
const seen = { useInnerText: true };

test("the rail lists every page and Providers & Connections is the default", async ({ page }) => {
  const labels = await page.locator(".settings-rail-item .settings-rail-label").allInnerTexts();
  expect(labels).toEqual([
    "Providers & Connections",
    "Models & Routing",
    "Cost Firewall",
    "Permissions & Safety",
    "Privacy & Data",
    "About",
  ]);
  await expect(railItem(page, "providers")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#settingsPage")).toContainText("Connection Doctor", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Cost firewall", seen);
});

test("clicking a rail page switches to that page only", async ({ page }) => {
  await railItem(page, "firewall").click();
  await expect(page.locator("#settingsPage")).toContainText("Panic mode", seen);
  await expect(page.locator("#setPanic")).toBeVisible();
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
  await expect(railItem(page, "firewall")).toHaveAttribute("aria-current", "page");
  await expect(railItem(page, "providers")).not.toHaveAttribute("aria-current", "page");
});

test("choosing a page writes a deep link that survives leaving settings", async ({ page }) => {
  await railItem(page, "privacy").click();
  expect(page.url()).toContain("#settings/privacy");
  await openNav(page, "Chat");
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage")).toContainText("No telemetry", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
});

test("a deep link set before opening settings opens that page", async ({ page }) => {
  await page.evaluate(() => history.replaceState(null, "", "#settings/firewall"));
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage")).toContainText("Panic mode", seen);
  await expect(railItem(page, "firewall")).toHaveAttribute("aria-current", "page");
});
