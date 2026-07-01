import { test, expect } from "@playwright/test";

import { emitActivity, finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("Inspector opens and closes from the top bar", async ({ page }) => {
  await expect(page.locator("#inspector")).toBeVisible();
  await page.getByRole("button", { name: "Inspector" }).click();
  await expect(page.locator("#app")).toHaveClass(/panel-hidden/);
  await page.getByRole("button", { name: "Inspector" }).click();
  await expect(page.locator("#app")).not.toHaveClass(/panel-hidden/);
});

test("Inspector shows model, mode, workspace, budget, permissions, and privacy", async ({ page }) => {
  const inspector = page.locator("#inspector");
  await expect(inspector).toContainText("Model");
  await expect(inspector).toContainText("Safe Auto");
  await expect(inspector).toContainText("3 files indexed");
  await expect(inspector).toContainText("$0.42 / $2.00 today");
  await expect(inspector).toContainText("Read files");
  await expect(inspector).toContainText("No telemetry");
});

test("advanced model details appear in Inspector CLI mirror, not chat", async ({ page }) => {
  await page.selectOption("#modelSel", "account:codex:gpt-5.5");
  await expect(page.locator("#cliMirrorCmd")).toContainText("codex:gpt-5.5");
  const id = await sendPrompt(page, "human-facing answer");
  await finishRequest(page, id, { answer: "Done without route IDs." });
  await expect(page.locator("#thread")).not.toContainText("account:codex:gpt-5.5");
});

test("Inspector live state tracks current step, count, and elapsed time", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, { id: "read", type: "file_read", status: "success", title: "Reading app.py" });
  await expect(page.locator("#inspLive")).toBeVisible();
  await expect(page.locator("#inspLiveStep")).toContainText("Reading app.py");
  await expect(page.locator("#inspLiveEvents")).toContainText("1 step");
  await expect(page.locator("#inspLiveElapsed")).toHaveText(/\d{2}:\d{2}/);
});

test("Inspector returns to idle after completion", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id);
  await expect(page.locator("#inspLive")).toBeHidden();
  await expect(page.locator("#inspector")).toContainText("CLI mirror");
});

test("CLI mirror tracks focus and output selections safely", async ({ page }) => {
  await page.selectOption("#focusSel", "general");
  await page.selectOption("#fmtSel", "normal");
  await expect(page.locator("#cliMirrorCmd")).toContainText("opai ask");
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toEqual(expect.arrayContaining([
    ["default_task_mode", "general"],
    ["default_output_format", "normal"],
  ]));
});
