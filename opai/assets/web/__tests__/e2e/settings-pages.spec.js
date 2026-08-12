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

test("the rail lists every page and Overview is the default", async ({ page }) => {
  // `allInnerTexts()` does not retry — it returns whatever matches at that
  // instant. On a slower hosted runner the rail had not rendered yet, so it
  // returned [] and the assertion failed with no waiting at all. That is the
  // whole of the intermittent required-check failure on this suite: the error
  // read "expected 11 items, received Array []", which looks like the UI
  // collapsed but is only a race against first paint.
  //
  // `toHaveText` with an array asserts the same list and auto-retries until the
  // expect timeout, so a slow paint costs milliseconds instead of a red gate.
  await expect(
    page.locator(".settings-rail-item .settings-rail-label"),
  ).toHaveText([
    "Overview",
    "Providers & Connections",
    "Models & Routing",
    "Cost Firewall",
    "Model Usage",
    "Permissions & Safety",
    "Privacy & Data",
    "Appearance",
    "About",
  ]);
  await expect(railItem(page, "overview")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#settingsPage")).toContainText("OPai status", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Cost firewall", seen);
});

test("the Overview status card reflects the real payload", async ({ page }) => {
  const settings = page.locator("#settingsPage");
  // Fixtures: cloud gate on, solo-balanced profile, 3 healthy accounts.
  await expect(settings).toContainText("Cloud gate: confirm", seen);
  await expect(settings).toContainText("solo-balanced", seen);
  await expect(settings).toContainText("3 connected", seen);
  await expect(settings).toContainText("Ask before edits", seen);
  await expect(settings).not.toContainText("Safe Auto", seen);
  // All connections are healthy, so the attention list is honestly calm.
  await expect(settings).toContainText("Nothing needs your attention right now", seen);
});

test("Overview quick controls navigate to the target page", async ({ page }) => {
  await page.locator('.quick-tile[data-go-page="firewall"]').click();
  await expect(railItem(page, "firewall")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#settingsPage")).toContainText("Panic mode", seen);
  expect(page.url()).toContain("#settings/firewall");
});

test("clicking a rail page switches to that page only", async ({ page }) => {
  await railItem(page, "firewall").click();
  await expect(page.locator("#settingsPage")).toContainText("Panic mode", seen);
  await expect(page.locator("#setPanic")).toBeVisible();
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
  await expect(railItem(page, "firewall")).toHaveAttribute("aria-current", "page");
  await expect(railItem(page, "providers")).not.toHaveAttribute("aria-current", "page");
});

test("About shows the exact hosted asset build identity", async ({ page }) => {
  await railItem(page, "about").click();
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("ASSET BUILD", seen);
  await expect(settings).toContainText("7ac9f12b4e88", seen);
  await expect(settings).toContainText("source checkout", seen);
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
