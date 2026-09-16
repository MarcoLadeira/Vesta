import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test.beforeEach(async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
});

const railItem = (page, id) => page.locator(`.settings-rail-item[data-rail-target="${id}"]`);
const seen = { useInnerText: true };

test("the rail groups related destinations like the desktop settings reference", async ({ page }) => {
  await expect(page.locator(".settings-rail-group")).toHaveText([
    "Vesta",
    "AI",
    "Development",
    "Trust",
    "System",
  ]);
  await expect(page.locator(".settings-rail-item .settings-rail-label")).toHaveText([
    "General",
    "Appearance",
    "Models & Routing",
    "Agents",
    "Usage & Budgets",
    "Workspace",
    "Integrations",
    "Safety & Privacy",
    "Advanced",
  ]);
  await expect(railItem(page, "general")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#settingsPage")).toContainText("Defaults for new tasks", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Daily cap", seen);
});

test("Settings owns the workspace until the back arrow returns to Chat", async ({ page }) => {
  const app = page.locator("#app");

  await expect(app).toHaveClass(/settings-active/);
  await expect(page.locator(".app > .sidebar")).toBeHidden();
  await expect(page.locator(".app > .inspector")).toBeHidden();
  await expect(page.locator("#recents")).toBeHidden();

  const back = page.locator("#settingsBack");
  await expect(back).toBeVisible();
  await expect(back).toHaveAttribute("aria-label", "Back to Chat");
  await back.click();

  await expect(page.locator("#view-chat")).toBeVisible();
  await expect(app).not.toHaveClass(/settings-active/);
  await expect(page.locator(".app > .sidebar")).toBeVisible();
});

test("every overflowing Settings page uses the custom right-hand scroll rail", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 640 });
  const scroller = page.locator("#view-settings > .scroll");
  const rail = page.locator("#settingsPageScrollbar");
  const thumb = page.locator("#settingsPageScrollbarThumb");
  const down = page.getByRole("button", { name: "Scroll settings down" });

  for (const id of ["models", "workspace", "connections", "usage", "advanced"]) {
    await railItem(page, id).click();
    await expect(rail).toBeVisible();
    expect(await scroller.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);
  }

  const visuals = await rail.evaluate((node) => {
    const railStyle = getComputedStyle(node);
    const thumbStyle = getComputedStyle(node.querySelector("#settingsPageScrollbarThumb"));
    return {
      width: railStyle.width,
      track: railStyle.backgroundColor,
      thumb: thumbStyle.backgroundColor,
    };
  });
  expect(parseFloat(visuals.width)).toBeGreaterThanOrEqual(18);
  expect(visuals.track).not.toBe("rgba(0, 0, 0, 0)");
  expect(visuals.thumb).not.toBe("rgba(0, 0, 0, 0)");

  await scroller.evaluate((node) => { node.scrollTop = 0; });
  await down.click();
  await expect.poll(() => scroller.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await expect(thumb).toHaveAttribute("aria-valuenow", /[1-9]\d*/);

  await page.setViewportSize({ width: 1280, height: 900 });
  await railItem(page, "general").click();
  await expect(rail).toBeHidden();
});

test("Workspace mirrors the development-focused reference layout", async ({ page }) => {
  await railItem(page, "workspace").click();

  await expect(page.locator("#set-sec-workspace .pane-title")).toHaveText("Workspace");
  await expect(page.locator("#workspaceTabs [data-workspace-tab]")).toHaveText([
    "Projects",
    "Terminal",
    "Git & GitHub",
    "Rules",
    "Environment",
  ]);
  await expect(page.locator("#set-sec-workspace .set-head")).toContainText([
    "Projects",
    "Terminal",
    "Git & GitHub",
    "Rules",
    "Environment",
  ]);

  await page.locator("#settingsOpenWorkspace").click();
  expect(await page.evaluate(() => window.__mock.openWorkspaceCount)).toBe(1);
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
  // Build identity is folded under "Build & runtime details".
  await page.locator("[data-settings-build-details] > summary").click();
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
