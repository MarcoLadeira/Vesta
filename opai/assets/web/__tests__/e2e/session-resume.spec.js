import { test, expect } from "@playwright/test";

import { expectNoFatalErrors, openApp } from "./helpers/app.js";


const resume = {
  available: true,
  requires_choice: true,
  thread: {
    task_id: "task-313",
    messages: [
      { role: "user", text: "Continue the index work", status: "complete", timestamp: "2026-07-13T08:00:00Z" },
      { role: "assistant", text: "Focused tests are green. <img src=x onerror=alert(1)>", status: "complete", timestamp: "2026-07-13T08:01:00Z" },
    ],
    plan: [{ step: "Run the full suite", status: "in_progress" }],
    changed_files: ["opai/gui_web.py"],
  },
  workflow: { phase: "testing", message: "Focused tests passed", plan_steps: ["Run the full suite"] },
  checkpoint: { id: "cp-313", completion_state: "interrupted", recovery_actions: ["Review the working tree"] },
};


test("startup requires an explicit resume choice before restoring safe messages", async ({ page }) => {
  const diagnostics = await openApp(page, { boot: { resume } });

  await expect(page.getByRole("group", { name: "Resume previous work" })).toBeVisible();
  await expect(page.locator("#input")).toBeDisabled();
  await expect(page.locator("#thread")).not.toContainText("Continue the index work");

  await page.getByRole("button", { name: "Resume work" }).click();

  await expect(page.locator("#input")).toBeEnabled();
  await expect(page.locator(".msg.user")).toContainText("Continue the index work");
  await expect(page.locator(".msg.bot .body")).toContainText("Focused tests are green");
  await expect(page.locator("#thread img")).toHaveCount(0);
  await expect(page.locator("#thread")).toContainText("Checkpoint cp-313");
  await expect(page.locator("#thread")).toContainText("Run the full suite");
  expect(await page.evaluate(() => window.__mock.resumedSessions)).toBe(1);
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(0);
  expectNoFatalErrors(diagnostics);
});


test("start fresh clears only through the session bridge and restores the empty composer", async ({ page }) => {
  await openApp(page, { boot: { resume } });

  await page.getByRole("button", { name: "Start fresh" }).click();

  await expect(page.locator("#empty")).toBeVisible();
  await expect(page.locator("#input")).toBeEnabled();
  await expect(page.locator("#thread")).not.toContainText("Continue the index work");
  expect(await page.evaluate(() => window.__mock.resumedSessions)).toBe(0);
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(1);
});


test("failed start fresh keeps resume visible and reports a recoverable error", async ({ page }) => {
  const failure = {
    ok: false,
    error: {
      code: "SESSION_CLEAR_FAILED",
      userMessage: "OPai could not clear the saved session.",
      recoveryActions: ["Close other OPai windows and try again."],
    },
    resume,
  };
  await openApp(page, { boot: { resume }, clearSessionResult: failure });

  await page.getByRole("button", { name: "Start fresh" }).click();

  await expect(page.getByRole("group", { name: "Resume previous work" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("could not clear");
  await expect(page.locator("#input")).toBeDisabled();
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(1);
});


test("failed clear history does not hide resumable work", async ({ page }) => {
  const failure = {
    ok: false,
    error: {
      code: "SESSION_CLEAR_FAILED",
      userMessage: "OPai could not clear the saved history.",
      recoveryActions: ["Try again."],
    },
    resume,
    recents: ["summarize my changes"],
  };
  await openApp(page, { boot: { resume }, clearRecentsResult: failure });

  await page.click("#clearRecents");

  await expect(page.getByRole("group", { name: "Resume previous work" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("could not clear");
  await expect(page.locator("#clearRecents")).toBeVisible();
  expect(await page.evaluate(() => window.__mock.clearedRecents)).toBe(1);
});
