import { test, expect } from "@playwright/test";

import { expectNoUiSentinels, openApp, openNav } from "./helpers/app.js";


test("proof bundle shows signature, artifacts, changes, cost, and privacy", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Proof Bundle");
  const dashboard = page.locator("#dashPage");
  await expect(dashboard).toContainText("verified");
  await expect(dashboard).toContainText("What changed");
  await expect(dashboard).toContainText("What it cost");
  await expect(dashboard).toContainText("$0.0420 actual spend");
  await expect(dashboard).toContainText("No raw prompt or secret");
});

test("proof bundle empty state is safe and explicit", async ({ page }) => {
  await openApp(page, {
    dashboards: { proof: { title: "Proof Bundle", subtitle: "No proof bundle generated yet.", kpis: [], cards: [], actions: [] } },
  });
  await openNav(page, "Proof Bundle");
  await expect(page.locator("#dashPage")).toContainText("No proof bundle generated yet");
  await expectNoUiSentinels(page, page.locator("#dashPage"));
});

test("proof action copies the private local command", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Proof Bundle");
  await page.getByRole("button", { name: "Copy proof command" }).click();
  await expect(page.locator("#toast")).toContainText("opai proof bundle");
});

test("proof surface does not render injected secret fixture text", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Proof Bundle");
  await expect(page.locator("#dashPage")).not.toContainText("sk-supersecret");
  await expect(page.locator("#dashPage")).not.toContainText("token=private");
});
