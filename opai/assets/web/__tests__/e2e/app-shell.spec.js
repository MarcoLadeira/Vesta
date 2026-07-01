import { test, expect } from "@playwright/test";

import { openApp, expectNoFatalErrors } from "./helpers/app.js";


test("loads the complete OPai workspace shell", async ({ page }) => {
  const diagnostics = await openApp(page);

  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.getByRole("button", { name: "New chat" })).toBeVisible();
  await expect(page.locator("#view-chat")).toBeVisible();
  await expect(page.locator("#input")).toBeVisible();
  await expect(page.getByRole("button", { name: "Inspector" })).toBeVisible();
  await expect(page.locator("#wsSwitch")).toBeVisible();
  await expect(page.locator("#acct")).toContainText("connected");
  expectNoFatalErrors(diagnostics);
});

test("all primary navigation destinations are present and activate", async ({ page }) => {
  await openApp(page);
  const labels = [
    "Chat", "Prompt Library", "Money Saved", "Cost Firewall", "Context Waste",
    "Benchmark", "Agents", "Proof Bundle", "Workflows", "Settings",
  ];
  for (const label of labels) {
    const button = page.getByRole("button", { name: label, exact: true });
    await expect(button).toBeVisible();
    await button.click();
    await expect(button).toHaveClass(/active/);
  }
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
