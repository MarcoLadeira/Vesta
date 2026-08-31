import { test, expect } from "@playwright/test";

import { expectNoFatalErrors, openApp } from "./helpers/app.js";


const presentation = {
  schema_version: 1,
  run: {
    state: "completed",
    label: "Completed",
    reason: "Focused verification passed.",
    next_action: "Run the full suite",
  },
  evidence: {
    verification: { applicable: true, verdict: "verified" },
    delivery: { applicable: true, verdict: "not_applicable" },
  },
  tests: { status: "passed", passed: 4, failed: 0, skipped: 1 },
  changes: { summary: { files: 1, additions: 8, deletions: 2 } },
  activity: [
    { phase: "inspect", status: "completed", message: '<img src=x onerror="alert(1)">' },
    { phase: "test", status: "completed", message: "Focused tests passed", next_action: "Run the full suite" },
  ],
};


const resume = {
  available: true,
  requires_choice: true,
  thread: {
    task_id: "task-313",
    messages: [
      { role: "user", text: "Continue the index work", status: "complete", timestamp: "2026-07-13T08:00:00Z" },
      {
        role: "assistant",
        text: "Focused tests are green. <img src=x onerror=alert(1)>",
        status: "complete",
        timestamp: "2026-07-13T08:01:00Z",
        presentation,
      },
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
  // The checkpoint names the session -- that is the point of it -- so the
  // prompt is on screen before the choice is made. What must not have happened
  // is the restore: no saved message is in the thread as a message, and the
  // session bridge has not been called.
  await expect(page.locator("#thread .msg.user")).toHaveCount(0);
  await expect(page.locator("#thread .msg.bot:not(.resume-choice)")).toHaveCount(0);
  expect(await page.evaluate(() => window.__mock.resumedSessions)).toBe(0);

  await page.locator('[data-resume="resume"]').click();

  await expect(page.locator("#input")).toBeEnabled();
  await expect(page.locator(".msg.user")).toContainText("Continue the index work");
  await expect(page.locator(".msg.bot .body")).toContainText("Focused tests are green");
  await expect(page.locator(".msg.bot .completion-verdict")).toContainText("Completed");
  await expect(page.locator(".msg.bot .evidence-bar")).toContainText("4 passed");
  await expect(page.locator(".msg.bot .gen-toggle.done")).toContainText("Work log (2)");
  await page.locator(".msg.bot .gen-toggle.done").click();
  await expect(page.locator(".msg.bot .timeline.done")).toContainText("Focused tests passed");
  // The restored message must not mint an <img> from its text (XSS guard). Scope
  // to message bodies so the legitimate empty-state brand mascot doesn't count.
  await expect(page.locator(".msg .body img")).toHaveCount(0);
  await expect(page.locator("#thread")).toContainText("Checkpoint cp-313");
  await expect(page.locator("#thread")).toContainText("Run the full suite");
  expect(await page.evaluate(() => window.__mock.resumedSessions)).toBe(1);
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(0);
  expectNoFatalErrors(diagnostics);
});

test("a legacy v1 assistant remains a prose-only inert fallback", async ({ page }) => {
  const legacyResume = {
    ...resume,
    thread: {
      ...resume.thread,
      schema_version: 1,
      messages: [
        { role: "user", text: "Old question", status: "complete", timestamp: "2026-07-13T08:00:00Z" },
        { role: "assistant", text: "999 tests passed and 42 files changed", status: "complete", timestamp: "2026-07-13T08:01:00Z" },
      ],
    },
  };
  await openApp(page, { boot: { resume: legacyResume } });

  await page.locator('[data-resume="resume"]').click();

  const restored = page.locator(".msg.bot").first();
  await expect(restored.locator(".body")).toContainText("999 tests passed");
  await expect(restored.locator(".evidence-bar")).toHaveCount(0);
  await expect(restored.locator(".completion-verdict")).toHaveCount(0);
  await expect(restored.locator(".timeline.done")).toHaveCount(0);
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

  await page.locator('[data-resume="resume"]').click();

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

test("resuming a cloud-blocked Build confirms back into Build, never Chat", async ({ page }) => {
  const buildResume = {
    ...resume,
    thread: {
      ...resume.thread,
      mode: "build",
      messages: [
        { role: "user", text: "Add search", status: "complete", timestamp: "2026-07-13T08:00:00Z" },
        { role: "assistant", text: "Confirm the named cloud model.", status: "blocked", timestamp: "2026-07-13T08:01:00Z" },
      ],
    },
    workflow: {
      phase: "blocked",
      message: "OPai needs a safe resolution before continuing",
      next_actions: ["Confirm the named cloud model, or pick a different model."],
      provider: { model: "free:gemini:gemini-3.1-flash-lite-preview", run_mode: "ask" },
      safety_gates: {
        pending_action: {
          kind: "auto_cloud_confirmation",
          model_id: "free:gemini:gemini-3.1-flash-lite-preview",
          model_label: "Gemini · 3.1 Flash-Lite (free tier)",
        },
      },
    },
    checkpoint: { id: "cp-build-cloud", completion_state: "blocked", recovery_actions: [] },
  };
  await openApp(page, {
    boot: { workspace: { build_app: true, build_app_name: "demo" }, selectedModel: "auto", resume: buildResume },
    buildCloudGate: true,
  });

  await page.locator('[data-resume="resume"]').click();
  const confirm = page.getByRole("button", {
    name: "Confirm Gemini · 3.1 Flash-Lite (free tier)",
  });
  await expect(confirm).toBeVisible();
  await expect(page.locator(".resumed-approval .role")).toContainText("OPai Build");
  expect(await page.evaluate(() => window.__mock.buildCount)).toBe(0);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);

  await confirm.click();
  await expect(page.getByRole("group", { name: "Build result" })).toBeVisible();
  const [buildCount, sendCount, request] = await page.evaluate(() => [
    window.__mock.buildCount, window.__mock.sendCount, window.__mock.lastBuild,
  ]);
  expect(buildCount).toBe(1);
  expect(sendCount).toBe(0);
  expect(request.allowCloud).toBe(true);
  expect(request.model).toBe("free:gemini:gemini-3.1-flash-lite-preview");
});


test("start fresh clears only through the session bridge and restores the empty composer", async ({ page }) => {
  await openApp(page, { boot: { resume } });

  await page.locator('[data-resume="fresh"]').click();

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
  await page.locator('[data-resume="fresh"]').click();

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

  await page.locator('[data-resume="resume"]').click();

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

  await page.locator('[data-resume="fresh"]').click();

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

test("a run that did not finish is marked, and one that is mid-flight is not", async ({ page }) => {
  // The phase used to be dropped into the middle of a grey sentence --
  // "2 saved messages · failed. Nothing is restored until you choose." --
  // where the one word that changes what you would decide read like filler.
  await openApp(page, {
    boot: { resume: { ...resume, workflow: { ...resume.workflow, phase: "failed" } } },
  });
  // A run that ended in failure names its last node for what happened, and
  // "you are here" sits on it.
  await expect(page.locator(".resume-card .rc-node").last()).toContainText("Failed");
  await expect(page.locator(".resume-card .rc-node.is-here")).toContainText("Failed");
});

test("a phase that is merely where the work got to is shown plainly", async ({ page }) => {
  await openApp(page, { boot: { resume } });
  // Mid-flight: the marker sits on the stage the run actually reached, and the
  // stages after it stay unvisited rather than being claimed as done.
  await expect(page.locator(".resume-card .rc-node.is-here")).toContainText("Tests");
  await expect(page.locator(".resume-card .rc-node.is-todo")).toHaveCount(1);
  await expect(page.locator(".resume-card .rc-node.is-done")).toHaveCount(3);
});

test("the centring comes off with the gate, so a restored thread starts at the top", async ({ page }) => {
  // The gate centres the thread because the card is the only thing in it. That
  // is exactly wrong for a real transcript, so the class has to come off when
  // the gate does -- otherwise resuming leaves the conversation floating in
  // the middle of the room.
  await openApp(page, { boot: { resume } });
  await expect(page.locator("#chatScroll")).toHaveClass(/gated/);

  await page.locator('[data-resume="resume"]').click();

  await expect(page.locator("#chatScroll")).not.toHaveClass(/gated/);
  await expect(page.locator("#input")).toBeEnabled();
});

test("the checkpoint answers what was I doing and what changed", async ({ page }) => {
  // The old card showed a message count and a raw phase word: everything the
  // app knew, expressed as almost nothing the user could use. All of this was
  // already in the boot payload and none of it was on screen.
  await openApp(page, {
    boot: {
      resume: {
        ...resume,
        workflow: {
          ...resume.workflow,
          tests_status: "passed",
          last_test: { passed: 3, failed: 0 },
          next_actions: ["Verify expired sessions"],
        },
        checkpoint: { ...resume.checkpoint, changed_files: ["opai/auth_service.py", "opai/tokens.py"] },
      },
    },
  });

  await expect(page.locator(".resume-card .rc-title")).toHaveText("Continue the index work");
  await expect(page.locator(".resume-card .rc-facts")).toContainText("auth_service.py");
  await expect(page.locator(".resume-card .rc-facts")).toContainText("3 tests passing");
  await expect(page.locator(".resume-card .rc-facts")).toContainText("Verify expired sessions");
});

test("a payload with nothing in it renders nothing, never a blank line", async ({ page }) => {
  // Every field here is optional in practice. A sparse session must degrade to
  // less card, not to a card full of empty rows or invented facts.
  await openApp(page, {
    boot: {
      resume: {
        available: true, requires_choice: true,
        thread: { messages: [{ role: "user", text: "hello", status: "complete" }] },
        workflow: {}, checkpoint: {},
      },
    },
  });

  await expect(page.locator(".resume-card")).toBeVisible();
  await expect(page.locator(".resume-card .rc-facts")).toHaveCount(0);
  await expect(page.locator(".resume-card .rc-title")).toHaveText("hello");
});

test("a phase OPai does not recognise is placed at the start, not guessed forward", async ({ page }) => {
  // RuntimePhase gains members over time, and a checkpoint written by a newer
  // build can name one this front-end has never heard of. Claiming that work
  // reached "Tests" when OPai does not know is worse than claiming nothing.
  //
  // Worth stating why this is its own test: the sparse case above defaults to
  // phase "idle", which *is* a known phase, so it exercised the ordinary path
  // and would have passed with this rule inverted.
  await openApp(page, {
    boot: {
      resume: {
        available: true, requires_choice: true,
        thread: { messages: [{ role: "user", text: "hello", status: "complete" }] },
        workflow: { phase: "some_future_phase" }, checkpoint: {},
      },
    },
  });

  await expect(page.locator(".resume-card .rc-node.is-here")).toContainText("Prompt");
  await expect(page.locator(".resume-card .rc-node.is-done")).toHaveCount(0);
});

test("the session title is escaped, not rendered", async ({ page }) => {
  // New surface: the checkpoint puts saved user text on screen before any
  // choice is made, so that text is now an injection site it never was before.
  await openApp(page, {
    boot: {
      resume: {
        ...resume,
        thread: {
          ...resume.thread,
          messages: [{ role: "user", text: '<img src=x onerror="alert(1)">', status: "complete" }],
        },
      },
    },
  });

  await expect(page.locator(".resume-card img")).toHaveCount(0);
  await expect(page.locator(".resume-card .rc-title")).toContainText("<img");
});

test("review shows the session without activating it", async ({ page }) => {
  // A third button that looks like a choice and does nothing is worse than two
  // buttons. Review expands the transcript and the file list in place, gate
  // still up, nothing restored.
  await openApp(page, { boot: { resume } });

  await page.locator('[data-resume="review"]').click();

  await expect(page.locator(".resume-card .rc-detail")).toBeVisible();
  await expect(page.locator(".resume-card .rc-turns")).toContainText("Continue the index work");
  await expect(page.locator(".resume-card .rc-files")).toContainText("opai/gui_web.py");
  // Still gated, still nothing activated.
  await expect(page.locator("#input")).toBeDisabled();
  expect(await page.evaluate(() => window.__mock.resumedSessions)).toBe(0);
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(0);

  await page.locator('[data-resume="review"]').click();
  await expect(page.locator(".resume-card .rc-detail")).toBeHidden();
});
