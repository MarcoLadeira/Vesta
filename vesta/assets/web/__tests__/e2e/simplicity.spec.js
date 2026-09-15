import { test, expect } from "@playwright/test";

import { openApp } from "./helpers/app.js";

// The production first-run contract: the sidebar is the user's own chat list
// and nothing else. Chat, Prompt Library and the seven Insights pages are all
// routable but unlisted -- a "Chat" row above your chats is a link to where you
// already are -- and New chat is a header action.
const SIMPLE_BOOT = {
  boot: { navGroups: [], prefs: { showPanel: false } },
};

test("first-run sidebar is the chat list and nothing else", async ({ page }) => {
  await openApp(page, SIMPLE_BOOT);
  await expect(page.locator(".nav-item:visible")).toHaveCount(0);
  await expect(page.locator(".nav-group-toggle")).toHaveCount(0);
  // What the rail used to carry above the user's own history.
  await expect(
    page.getByRole("button", { name: "Prompt Library", exact: true })
  ).toHaveCount(0);
  // The recents list is still the point of the rail.
  await expect(page.locator(".recents-label")).toBeVisible();
});

test("New chat is a header action, offered once", async ({ page }) => {
  await openApp(page, SIMPLE_BOOT);
  await expect(page.locator("#newChat")).toHaveCount(0);
  await expect(page.locator("#headerNewChat")).toBeVisible();
});

test("New chat still starts a chat from the header", async ({ page }) => {
  await openApp(page, SIMPLE_BOOT);
  await page.locator("#headerNewChat").click();
  await expect(page.locator("#view-chat")).toBeVisible();
});

test("inspector stays out of the way by default but is one click away", async ({ page }) => {
  await openApp(page, SIMPLE_BOOT);
  await expect(page.locator("#app")).toHaveClass(/panel-hidden/);
  await expect(page.locator("#input")).toBeVisible();
  await page.getByRole("button", { name: "Inspector" }).click();
  await expect(page.locator("#app")).not.toHaveClass(/panel-hidden/);
  await expect(page.locator("#inspector")).toContainText("Session");
  await expect(page.locator("#inspector")).toContainText("Advanced");
});

test("settings is always findable from the sidebar footer", async ({ page }) => {
  await openApp(page, SIMPLE_BOOT);
  await page.locator("#footSettings").click();
  await expect(page.locator("#view-settings")).toBeVisible();
  await expect(page.locator("#settingsPage")).toContainText("Accounts");
});

// SMOKE-UX-001: a new, empty chat opened with the inspector expanded and
// nothing in it, taking the right third of the window before the user had
// started work. The stored preference default was already "hidden"; every
// fallback around it said "shown", so first-time users got the opposite of
// the documented intent.
// The absent-preference case is pinned in Python (test_gui_web: boot_payload
// defaults showPanel to False); the harness drops undefined during
// serialization, so it cannot be expressed here. These pin the rendering
// contract the boot payload drives.
test("the inspector stays closed when the payload says so", async ({ page }) => {
  await openApp(page, { boot: { prefs: { showPanel: false } } });
  await expect(page.locator("#app")).toHaveClass(/panel-hidden/);
  await expect(page.locator("#panelToggle")).toHaveAttribute("aria-pressed", "false");
});

test("the inspector still opens when the user has asked for it", async ({ page }) => {
  await openApp(page, { boot: { prefs: { showPanel: true } } });
  await expect(page.locator("#app")).not.toHaveClass(/panel-hidden/);
  await expect(page.locator("#panelToggle")).toHaveAttribute("aria-pressed", "true");
});

test("toggling the inspector open and closed round-trips", async ({ page }) => {
  await openApp(page, { boot: { prefs: { showPanel: false } } });
  await page.locator("#panelToggle").click();
  await expect(page.locator("#app")).not.toHaveClass(/panel-hidden/);
  await page.locator("#panelToggle").click();
  await expect(page.locator("#app")).toHaveClass(/panel-hidden/);
});
