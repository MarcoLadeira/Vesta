import { test, expect } from "@playwright/test";

import { openApp, openNav, expectNoFatalErrors } from "./helpers/app.js";


test("loads the complete Vesta workspace shell", async ({ page }) => {
  const diagnostics = await openApp(page);

  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.locator("#headerNewChat")).toBeVisible();
  await expect(page.locator("#view-chat")).toBeVisible();
  await expect(page.locator("#input")).toBeVisible();
  await expect(page.getByRole("button", { name: "Inspector" })).toBeVisible();
  await expect(page.locator("#wsSwitch")).toBeVisible();
  await expect(page.locator("#acct")).toContainText("connected");
  expectNoFatalErrors(diagnostics);
});

test("all primary navigation destinations are present and activate", async ({ page }) => {
  await openApp(page);
  // The sidebar is the chat list: Chat is the header's New chat, and every other
  // destination opens from the Settings page the redesign placed it on (openNav
  // goes there like a user would). Each must open its own page.
  const destinations = [
    ["Chat", "#view-chat", null],
    ["Prompt Library", "#view-prompts", "Prompt Library"],
    ["Money Saved", "#view-dashboard", "Money Saved"],
    ["Cost Firewall", "#view-dashboard", "Cost Firewall"],
    ["Context Waste", "#view-dashboard", "Context Waste"],
    ["Benchmark", "#view-dashboard", "Benchmark Proof"],
    ["Agents", "#view-dashboard", "Agents"],
    ["Proof Bundle", "#view-dashboard", "Proof Bundle"],
    ["Workflows", "#view-dashboard", "Guarded Workflows"],
  ];
  for (const [label, view, heading] of destinations) {
    await openNav(page, label);
    await expect(page.locator(view), label).toBeVisible();
    if (heading) {
      await expect(page.locator(view), label).toContainText(heading);
    }
  }
  await page.locator("#headerSettings").click();
  await expect(page.locator("#view-settings")).toBeVisible();
});

test("workspace, model, mode, spend status, and provider state are visible", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#wsLabel")).toContainText("demo");
  await expect(page.locator("#modelSel")).toHaveValue("auto");
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
  await expect(page.locator("#statusLine")).toContainText("$0.42 today");
  await expect(page.locator("#statusLine")).toContainText("$12.34 saved");
  await expect(page.locator("#acct")).toContainText("Claude");
  await expect(page.locator("#acct")).toContainText("Codex");
  await expect(page.locator("#acct")).toContainText("Copilot");
});

test("command palette opens, filters, executes, and closes with Escape", async ({ page }) => {
  await openApp(page);
  await page.keyboard.press("Control+k");
  await expect(page.locator("#palette")).toHaveClass(/open/);
  await page.fill("#paletteInput", "prompt library");
  await expect(page.locator("#paletteList .opt")).toHaveCount(1);
  await page.keyboard.press("Enter");
  await expect(page.locator("#view-prompts")).toBeVisible();

  await page.keyboard.press("Control+k");
  await page.keyboard.press("Escape");
  await expect(page.locator("#palette")).not.toHaveClass(/open/);
});

test("Ctrl+B toggles the sidebar (#400: advertised shortcut is wired)", async ({ page }) => {
  await openApp(page);
  const app = page.locator("#app");
  await expect(app).not.toHaveClass(/sidebar-hidden/);
  await page.keyboard.press("Control+b");
  await expect(app).toHaveClass(/sidebar-hidden/);
  await page.keyboard.press("Control+b");
  await expect(app).not.toHaveClass(/sidebar-hidden/);
});

test("? shows the keyboard shortcuts (#400), but not while typing", async ({ page }) => {
  await openApp(page);
  // Typing "?" in the composer must not hijack the key.
  await page.locator("#input").click();
  await page.keyboard.type("?");
  await expect(page.locator("#toast")).not.toContainText("palette");
  await expect(page.locator("#input")).toHaveValue("?");

  // Pressing "?" outside a field surfaces the shortcuts toast. (press("?")
  // dispatches e.key === "?", matching a real browser's Shift+/; Playwright's
  // synthetic "Shift+/" would report e.key === "/".)
  await page.locator("body").click();
  await page.keyboard.press("?");
  await expect(page.locator("#toast")).toContainText("Ctrl+B sidebar");
});

test("palette exposes stop and doctor commands (#400) and they run", async ({ page }) => {
  await openApp(page);
  // Doctor must open the exact Settings page where its refreshed results live.
  await page.keyboard.press("Control+k");
  await page.fill("#paletteInput", "doctor");
  await expect(page.locator("#paletteList .opt")).toHaveCount(1);
  await page.keyboard.press("Enter");
  await expect(page.locator("#view-settings")).toBeVisible();
  // Providers are part of the Integrations page.
  await expect(page.locator('.settings-pane[data-pane="connections"]')).toHaveClass(/active/);
  await expect(page.getByRole("region", { name: "Connection Doctor" })).toBeVisible();

  // stop is offered as a command (safe no-op when idle).
  await page.keyboard.press("Control+k");
  await page.fill("#paletteInput", "stop generation");
  await expect(page.locator("#paletteList .opt")).toHaveCount(1);
  await page.keyboard.press("Escape");
});
