import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("agents show readiness, wrapper, capture, and account state", async ({ page }) => {
  await openNav(page, "Agents");
  const dashboard = page.locator("#dashPage");
  await expect(dashboard).toContainText("Claude");
  await expect(dashboard).toContainText("Codex");
  await expect(dashboard).toContainText("Copilot");
  await expect(dashboard).toContainText("selective proxy");
  await expect(dashboard).toContainText("installed");
  await expect(dashboard).toContainText("connected");
});

test("repair action delegates to the safe repair tool only", async ({ page }) => {
  await openNav(page, "Agents");
  await page.getByRole("button", { name: "Repair clients" }).click();
  expect(await page.evaluate(() => window.__mock.runTools)).toEqual(["repair"]);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});

test("workflows display risk and approval posture", async ({ page }) => {
  await openNav(page, "Workflows");
  const dashboard = page.locator("#dashPage");
  await expect(dashboard).toContainText("PR review");
  await expect(dashboard).toContainText("Security audit");
  await expect(dashboard).toContainText("Release preflight");
  await expect(dashboard).toContainText("READ-ONLY");
  await expect(dashboard).toContainText("CONFIRM");
  await expect(dashboard).toContainText("Never pushes, deploys, or publishes automatically");
});

test("workflow action copies a plan command without running it", async ({ page }) => {
  await openNav(page, "Workflows");
  await page.getByRole("button", { name: "Copy workflow command" }).click();
  await expect(page.locator("#toast")).toContainText("vesta workflow plan release_preflight");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
  expect(await page.evaluate(() => window.__mock.runTools)).toEqual([]);
});
