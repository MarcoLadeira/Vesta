import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


const MUTATING_TOOL = {
  title: "Panic mode",
  text: "Switch to local-only routing?",
  needs_confirm: true,
  apply: "panic",
};

test("confirmed mutating tool applies only after the dialog is accepted", async ({ page }) => {
  await openApp(page, {
    toolResponses: { panic: MUTATING_TOOL },
    applyToolResponses: { panic: { text: "Panic mode enabled." } },
  });
  page.once("dialog", (dialog) => dialog.accept());
  await openNav(page, "Settings");
  await page.getByRole("button", { name: "Enable panic" }).click();
  await expect(page.locator(".tool-card")).toContainText("Panic mode enabled");
  expect(await page.evaluate(() => window.__mock.appliedTools)).toEqual(["panic"]);
});

test("dismissed mutating tool remains unapplied and records cancellation", async ({ page }) => {
  await openApp(page, { toolResponses: { panic: MUTATING_TOOL } });
  page.once("dialog", (dialog) => dialog.dismiss());
  await openNav(page, "Settings");
  await page.getByRole("button", { name: "Enable panic" }).click();
  await expect(page.locator(".tool-card")).toContainText("Cancelled");
  expect(await page.evaluate(() => window.__mock.appliedTools)).toEqual([]);
});

test("read-only tool result renders without a confirmation dialog", async ({ page }) => {
  await openApp(page, {
    toolResponses: { connect: { title: "Connections", text: "Claude connected.", needs_confirm: false } },
  });
  let dialogOpened = false;
  page.on("dialog", async (dialog) => { dialogOpened = true; await dialog.dismiss(); });
  await openNav(page, "Settings");
  await page.getByRole("button", { name: "Connect accounts" }).click();
  await expect(page.locator(".tool-card")).toContainText("Claude connected");
  expect(dialogOpened).toBe(false);
});
