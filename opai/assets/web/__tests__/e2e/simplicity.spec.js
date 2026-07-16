import { test, expect } from "@playwright/test";

import { openApp } from "./helpers/app.js";

// The production first-run contract: a brand-new user sees a ChatGPT-simple
// shell — Chat, Prompt Library, Recents, Settings in the footer — with the
// seven Insights pages folded behind one toggle and no inspector panel.
const SIMPLE_BOOT = {
  boot: {
    navGroups: [
      { group: "", items: [{ id: "chat", label: "Chat" }, { id: "prompts", label: "Prompt Library" }], collapsed: false },
      {
        group: "Insights",
        collapsed: true,
        items: [
          { id: "home", label: "Money Saved" },
          { id: "firewall", label: "Cost Firewall" },
          { id: "context", label: "Context Waste" },
          { id: "benchmark", label: "Benchmark" },
          { id: "agents", label: "Agents" },
          { id: "proof", label: "Proof Bundle" },
          { id: "workflows", label: "Workflows" },
        ],
      },
    ],
    prefs: { showPanel: false },
  },
};

test("first-run sidebar is ChatGPT-simple: two items + one folded group", async ({ page }) => {
  await openApp(page, SIMPLE_BOOT);
  const visibleNav = page.locator(".nav-item:visible");
  await expect(visibleNav).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Chat", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prompt Library", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Money Saved", exact: true })).toBeHidden();
  // The Insights group toggle is a branded image button; it is identified by
  // its accessible name (aria-label), not visible text.
  await expect(page.locator(".nav-group-toggle")).toHaveAttribute("aria-label", "Insights");
});

test("Insights unfolds on demand and folds back", async ({ page }) => {
  await openApp(page, SIMPLE_BOOT);
  await page.locator(".nav-group-toggle").click();
  await expect(page.getByRole("button", { name: "Money Saved", exact: true })).toBeVisible();
  await expect(page.locator(".nav-group-toggle")).toHaveAttribute("aria-expanded", "true");
  await page.locator(".nav-group-toggle").click();
  await expect(page.getByRole("button", { name: "Money Saved", exact: true })).toBeHidden();
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
