import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openNav, sendPrompt } from "./helpers/app.js";


test("Money Saved separates estimated savings, actual spend, and avoided calls", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Money Saved");
  const dashboard = page.locator("#dashPage");
  await expect(dashboard).toContainText("Estimated savings");
  await expect(dashboard).toContainText("Actual spend");
  await expect(dashboard).toContainText("Spent today");
  await expect(dashboard).toContainText("Paid calls avoided");
  await expect(dashboard).toContainText("Confidence");
});

test("paid account receipt shows spend and never claims savings", async ({ page }) => {
  await openApp(page);
  await page.selectOption("#modelSel", "account:claude:opus");
  const id = await sendPrompt(page, "Use my paid account");
  await finishRequest(page, id, {
    answer: "Paid response",
    receipt: { estimated_actual_usd: 0.042, estimated_savings_usd: 0, paid_call_avoided: false },
  });
  const footer = page.locator(".msg.bot .footer-note");
  await expect(footer).toContainText("$0.0420");
  await expect(footer).not.toContainText("saved");
  await expect(footer).not.toContainText("paid call avoided");
});

test("local route receipt labels estimated savings and paid-call avoidance", async ({ page }) => {
  await openApp(page);
  await page.selectOption("#modelSel", "ollama:qwen2.5-coder");
  const id = await sendPrompt(page, "Use the local route");
  await finishRequest(page, id, {
    answer: "Local response",
    receipt: { estimated_actual_usd: 0, estimated_savings_usd: 0.084, paid_call_avoided: true },
  });
  const footer = page.locator(".msg.bot .footer-note");
  await expect(footer).toContainText("$0.0840 saved");
  await expect(footer).toContainText("paid call avoided");
});

test("zero-data savings state makes no unsupported money claim", async ({ page }) => {
  await openApp(page, {
    dashboards: {
      home: {
        title: "Money Saved",
        subtitle: "No routed tasks yet.",
        hero: { headline: "Ready to record first savings", caption: "Run a captured route to create evidence.", severity: "neutral" },
        kpis: [], cards: [], actions: [],
      },
    },
  });
  await openNav(page, "Money Saved");
  await expect(page.locator("#dashPage")).toContainText("No routed tasks yet");
  await expect(page.locator("#dashPage")).not.toContainText("$0.00 saved");
});

test("savings report action copies an explicit CLI command", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Money Saved");
  await page.getByRole("button", { name: "Copy savings report" }).click();
  await expect(page.locator("#toast")).toContainText("opai savings --markdown");
});
