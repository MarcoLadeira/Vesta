import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openTurnDetails, sendPrompt } from "./helpers/app.js";

test("structured evidence, changes, warnings, and final state keep one honest reading order", async ({ page }) => {
  await openApp(page, { boot: { prefs: { responseDensity: "balanced" } } });
  const id = await sendPrompt(page, "Change the parser and verify it");
  await finishRequest(page, id, {
    answer: "The parser change is ready for review.",
    changed_files: [" M parser.py"],
    presentation: {
      schema_version: 1,
      run: {
        state: "partial",
        label: "Partial",
        reason: "One verification check still needs attention.",
        next_action: "Review the failed evidence.",
        answer_conflicts: true,
      },
      evidence: { verification: { applicable: true, verdict: "partial" } },
      tests: { status: "partial", passed: 1, failed: 1, skipped: 0 },
      changes: { summary: { files: 1, additions: 2, deletions: 1, risky: 1, truncated: true } },
      activity: [
        { phase: "inspect", status: "completed", message: "Read parser.py" },
        { phase: "verify", status: "failed", message: "One check failed" },
      ],
    },
    verification_manifest: {
      checks: [{
        check_id: "unit",
        kind: "unit",
        requirement: "Run parser checks",
        status: "passed",
        attempts: [{
          index: 1,
          status: "passed",
          command: ["python", "-m", "pytest", "tests/test parser.py"],
          started_at: "2026-08-30T12:00:00Z",
          ended_at: "2026-08-30T12:00:01Z",
          exit_status: 0,
          output_summary: "Focused check passed; this is an output summary, not a parsed test count.",
          teardown_verified: true,
        }],
      }],
    },
    warnings: [
      { severity: "warning", reason: "Review permissions", term: "write" },
      { severity: "warning", reason: "Review permissions", term: "write" },
    ],
    background_work: { unfinished: [{ command: ["python", "worker.py"] }] },
    agent_policy: { mode: "implement", label: "Implement" },
    workflow: {
      mode: "implement",
      phase: "completed",
      tests_status: "passed",
      history: [{ phase: "verify", message: "A coarse workflow status" }],
      diff_review: {
        summary: { files: 1, additions: 2, deletions: 1, pending: 0, risky: 1, truncated: true },
        files: [{
          path: "parser.py",
          decision: "pending",
          additions: 2,
          deletions: 1,
          risky: true,
          risk_reasons: ["permissions"],
          sensitive: true,
          hunks: [],
        }],
      },
    },
  });

  const response = page.locator(".msg.bot").last();
  // The run's evidence lives behind the turn summary now; open it before
  // reading any of it.
  await openTurnDetails(page, response);
  await expect(response.locator(".verification-card")).toContainText("1 passed");
  await response.locator(".verification-check > summary").click();
  await expect(response.locator(".verification-command code")).toHaveText('python -m pytest "tests/test parser.py"');
  await response.locator("[data-copy-command]").click();
  await expect.poll(() => page.evaluate(() => window.__mock.copiedTexts.at(-1))).toBe('python -m pytest "tests/test parser.py"');
  await expect(response.locator(".verification-output")).toContainText("Output summary");

  const changes = response.locator(".changeset-card");
  await expect(changes).toContainText("Diff truncated");
  await expect(changes).toContainText("1 risky");
  await expect(changes).toContainText("Sensitive diff content is hidden");
  await expect(changes).not.toContainText("Tests: passed");
  await expect(response.locator(".workflow-card .changeset-card")).toHaveCount(0);
  await expect(response.locator(".workflow-card .wf-row").filter({ hasText: "Tests" })).toHaveCount(0);
  await expect(response.locator(".workflow-card .wf-history")).toHaveCount(0);

  await expect(response.locator(".timeline.done")).toBeHidden();
  await response.locator(".gen-toggle.done").click();
  await expect(response.locator(".timeline.done")).toContainText("One check failed");
  await expect(response.locator(".response-warnings")).toContainText("Review permissions · write");
  await expect(response.locator(".response-warnings .warning-message").filter({ hasText: "Review permissions" })).toHaveCount(1);
  await expect(response.locator(".response-warnings")).toContainText("background command is still unfinished");

  // The reading order still has to be one honest sequence, but the sequence
  // itself changed: the answer, then anything that contradicts it, then what
  // actually changed on disk, and only then the summary that holds the record
  // of the run. Everything from the evidence bar down is inside that summary,
  // which is why it now comes last rather than being stacked under the answer.
  const order = await response.evaluate((element) => {
    const selectors = [
      ".response-prose",
      ".changeset-card",
      ".turn-summary",
      ".evidence-bar",
      ".verification-card",
      ".gen-toggle.done",
      ".workflow-card",
      ".response-warnings",
    ];
    return selectors.map((selector) => Array.from(element.querySelectorAll("*")).indexOf(element.querySelector(selector)));
  });
  expect(order.every((position) => position >= 0)).toBe(true);
  expect(order).toEqual([...order].sort((a, b) => a - b));

  // The verdict is stated once, on the summary row, and nowhere else. Saying
  // it three times -- as a card, as the workflow card's heading and as the
  // receipt's prefix -- is what made a finished turn unreadable.
  await expect(response.locator(".ts-verdict")).toHaveText("No changes made");
  await expect(response.locator(".completion-verdict")).toHaveCount(0);
  await expect(response.locator(".workflow-card .wf-head")).not.toContainText("Partial");
});
