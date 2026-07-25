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
  // The restored message must not mint an <img> from its text (XSS guard). Scope
  // to message bodies so the legitimate empty-state brand mascot doesn't count.
  await expect(page.locator(".msg .body img")).toHaveCount(0);
  await expect(page.locator("#thread")).toContainText("Checkpoint cp-313");
  await expect(page.locator("#thread")).toContainText("Run the full suite");
  expect(await page.evaluate(() => window.__mock.resumedSessions)).toBe(1);
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(0);
  expectNoFatalErrors(diagnostics);
});

test("resuming a cloud-blocked turn restores the exact approval without granting it", async ({ page }) => {
  const cloudResume = {
    ...resume,
    thread: {
      ...resume.thread,
      mode: "ask",
      messages: [
        { role: "user", text: "Explain this repository", status: "complete", timestamp: "2026-07-13T08:00:00Z" },
        { role: "assistant", text: "Gemini will receive compact project context.", status: "blocked", timestamp: "2026-07-13T08:01:00Z" },
      ],
    },
    workflow: {
      phase: "blocked",
      message: "OPai needs a safe resolution before continuing",
      next_actions: ["Confirm the named cloud model, or pick a different model."],
      provider: { model: "free:gemini:gemini-3.1-flash-lite", run_mode: "ask" },
      safety_gates: {
        pending_action: {
          kind: "auto_cloud_confirmation",
          model_id: "free:gemini:gemini-3.1-flash-lite",
          model_label: "Gemini · 3.1 Flash-Lite",
        },
      },
    },
    checkpoint: { id: "cp-cloud", completion_state: "blocked", recovery_actions: ["Resolve the requested approval or input, then retry."] },
  };
  await openApp(page, { boot: { selectedModel: "auto", resume: cloudResume } });

  await page.getByRole("button", { name: "Resume work" }).click();

  await expect(page.getByRole("button", { name: "Confirm Gemini · 3.1 Flash-Lite" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(page.locator(".resume-summary")).toContainText("Confirm the named cloud model");
  await expect(page.locator(".resume-summary")).not.toContainText("then retry");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);

  await page.getByRole("button", { name: "Confirm Gemini · 3.1 Flash-Lite" }).click();
  const request = await page.evaluate(() => window.__mock.lastRequest);
  expect(request.text).toBe("Explain this repository");
  expect(request.model).toBe("free:gemini:gemini-3.1-flash-lite");
  expect(request.allowCloud).toBe(true);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
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


test("start fresh dismisses the resume card immediately, before the bridge confirms (#416)", async ({ page }) => {
  // Hold the clearSession callback to simulate a slow/contended real bridge; the
  // card must still disappear on click, not wait for the reply (a lingering,
  // still-interactive card is what made an earlier click seem to need a second).
  await openApp(page, { boot: { resume }, deferClearSession: true });

  await expect(page.getByRole("group", { name: "Resume previous work" })).toBeVisible();
  await page.getByRole("button", { name: "Start fresh" }).click();

  // Gone right away, while the bridge callback is still pending.
  await expect(page.getByRole("group", { name: "Resume previous work" })).toHaveCount(0);
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(1);
  expect(await page.evaluate(() => typeof window.__mock.flushClearSession)).toBe("function");

  // Let the bridge finally answer; the composer settles into the empty state.
  await page.evaluate(() => window.__mock.flushClearSession());
  await expect(page.locator("#empty")).toBeVisible();
  await expect(page.locator("#input")).toBeEnabled();
});


test("resume work dismisses the card immediately, before activation confirms (#416)", async ({ page }) => {
  await openApp(page, { boot: { resume } });

  await page.getByRole("button", { name: "Resume work" }).click();

  // The choice card is removed synchronously; the restored thread renders in its place.
  await expect(page.getByRole("group", { name: "Resume previous work" })).toHaveCount(0);
  await expect(page.locator(".msg.user")).toContainText("Continue the index work");
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
  await page.locator("#recents .inline-confirm [data-ic='ok']").click();

  await expect(page.getByRole("group", { name: "Resume previous work" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("could not clear");
  await expect(page.locator("#clearRecents")).toBeVisible();
  expect(await page.evaluate(() => window.__mock.clearedRecents)).toBe(1);
});
