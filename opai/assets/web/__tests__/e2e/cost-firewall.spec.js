import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openNav, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("firewall shows budget, policy, panic state, warnings, and blocks", async ({ page }) => {
  await openNav(page, "Cost Firewall");
  const dashboard = page.locator("#dashPage");
  await expect(dashboard).toContainText("solo-balanced");
  await expect(dashboard).toContainText("$0.42 / $2.00");
  await expect(dashboard).toContainText("Expensive request warning");
  await expect(dashboard).toContainText("Confirmation required");
  await expect(dashboard).toContainText("blocked before spend");
});

test("expensive route confirmation renders as a warning, not success", async ({ page }) => {
  const id = await sendPrompt(page, "Use the most expensive frontier model");
  await finishRequest(page, id, {
    status: "needs_confirmation",
    answer: "This paid route needs confirmation before spending.",
  });
  await expect(page.locator(".error-card .ec-t")).toHaveText("Needs a paid model");
  await expect(page.locator(".msg.bot")).not.toContainText("Completed");
  await expect(page.locator(".footer-note")).toHaveCount(0);
});

test("budget exceeded is blocked without a fake receipt", async ({ page }) => {
  const id = await sendPrompt(page, "Continue after the budget cap");
  await finishRequest(page, id, { status: "blocked", answer: "Daily budget exceeded. No call was made." });
  await expect(page.locator(".error-card")).toContainText("Blocked as risky");
  await expect(page.locator(".error-card")).toContainText("No call was made");
  await expect(page.locator(".footer-note")).toHaveCount(0);
});

test("panic-mode action delegates to OPai and makes no provider request", async ({ page }) => {
  await openNav(page, "Cost Firewall");
  await page.getByRole("button", { name: "Enable panic mode" }).click();
  expect(await page.evaluate(() => window.__mock.runTools)).toEqual(["panic"]);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});

test("budget status action exposes the CLI parity command", async ({ page }) => {
  await openNav(page, "Cost Firewall");
  await page.getByRole("button", { name: "Copy budget status" }).click();
  await expect(page.locator("#toast")).toContainText("opai budget status");
});
