import { test, expect } from "@playwright/test";
import { finishRequest, openApp, openTurnDetails, sendPrompt } from "./helpers/app.js";

/**
 * One line under the answer, and the record of the run behind it.
 *
 * A finished turn used to stack up to eight blocks: an evidence bar, a
 * verification table, a changes card, a work log, a workflow card, warnings, a
 * cost receipt and a completion verdict. Several said the same thing -- the
 * verdict alone appeared three times, as the workflow card's heading, as the
 * cost receipt's prefix, and as its own card -- and the whole thing was
 * unreadable precisely because nothing in it was ranked.
 */

const base = {
  answer: "Fixed the off-by-one in the tokenizer.",
  changed_files: [" M parser.py", " M lexer.py"],
  receipt: { estimated_actual_usd: 0.0231, confidence: "actual" },
  workflow: { mode: "implement", phase: "completed", tests_status: "passed" },
};

test("a finished turn shows one line, and the diagnostics only on request", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Fix the parser");
  await finishRequest(page, id, {
    ...base,
    presentation: {
      schema_version: 1,
      run: { state: "completed", label: "Completed" },
      tests: { status: "passed", passed: 12, failed: 0, skipped: 0 },
      changes: { summary: { files: 2, additions: 9, deletions: 3 } },
      activity: [{ phase: "implementing", status: "completed", message: "Applied the fix" }],
    },
  });

  const row = page.locator(".ts-row");
  await expect(row).toContainText("Done");
  await expect(row).toContainText("2 files");
  await expect(row).toContainText("12 tests passed");
  await expect(row).toContainText("$0.02");

  // Everything else is genuinely hidden, not merely quieter.
  await expect(page.locator(".evidence-bar")).toBeHidden();
  await expect(page.locator(".workflow-card")).toBeHidden();
  await expect(page.locator(".receipt-card")).toBeHidden();

  await openTurnDetails(page);
  await expect(page.locator(".evidence-bar")).toBeVisible();
  await expect(page.locator(".workflow-card")).toBeVisible();
});

test("a read-only answer reports a verdict and a price, not empty columns", async ({ page }) => {
  // The row is adaptive on purpose: a turn that changed nothing and ran no
  // tests should not render "0 files · 0 tests" to fill the space.
  await openApp(page);
  const id = await sendPrompt(page, "How does routing work?");
  await finishRequest(page, id, {
    answer: "Auto scores each capable model.",
    receipt: { estimated_actual_usd: 0.0021, confidence: "actual" },
    presentation: { schema_version: 1, run: { state: "completed", label: "Completed" } },
  });

  const row = page.locator(".ts-row");
  await expect(row).toContainText("Done");
  await expect(row).toContainText("$0.0021");
  await expect(row).not.toContainText("file");
  await expect(row).not.toContainText("test");
});

test("a failing run says so on the row, and offers the one action worth having", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Fix it");
  await finishRequest(page, id, {
    ...base,
    changed_files: [],
    presentation: {
      schema_version: 1,
      run: {
        state: "partial",
        label: "Partial",
        reason: "No diff evidence verifies the edit.",
        next_action: "Review the tool trace.",
      },
      tests: { status: "failed", passed: 1, failed: 3, skipped: 0 },
    },
  });

  const summary = page.locator(".turn-summary");
  await expect(summary).toHaveClass(/is-partial/);
  // "Partial" is jargon for a checkable thing: the model claimed an edit and
  // no diff agrees. The row says that instead.
  await expect(summary.locator(".ts-verdict")).toHaveText("No changes made");
  // Failures lead: 3 failed matters more than 1 passed.
  await expect(page.locator(".ts-row")).toContainText("3 tests failed");
  await expect(page.locator(".ts-row")).not.toContainText("1 test passed");

  // Retry sits on the row, and must not double as the disclosure toggle.
  await expect(summary.locator(".ts-retry")).toBeVisible();
  await summary.locator(".ts-retry").click();
  await expect(summary).not.toHaveAttribute("open", "");
  await expect.poll(() => page.evaluate(() => window.__mock.sendCount)).toBe(2);
});

test("a completed turn offers no retry", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Fix the parser");
  await finishRequest(page, id, {
    ...base,
    presentation: { schema_version: 1, run: { state: "completed", label: "Completed" } },
  });
  await expect(page.locator(".ts-retry")).toHaveCount(0);
});

test("the panel states each fact once", async ({ page }) => {
  // The first version of this collapse nested the old blocks unchanged, so
  // expanding it produced the stack it replaced: the same sentence three
  // times (the workflow card's message, the cost strip's prefix, and its own
  // verdict card) and the next action twice.
  await openApp(page);
  const id = await sendPrompt(page, "Explain the budget guard");
  await finishRequest(page, id, {
    answer: "The budget guard stops a run before it spends past the daily cap.",
    receipt: { estimated_actual_usd: 0.0407, confidence: "actual" },
    completion_verdict: {
      verdict: "completed",
      reason_code: "answer_delivered",
      reason: "Provider returned a complete response; its content was not independently verified.",
      next_action: "Review the response and its cited evidence.",
      evidence: [],
    },
    agent_policy: { mode: "explain", label: "Explain" },
    workflow: { mode: "explain", phase: "completed", provider: { model: "account:claude:haiku" } },
  });

  await openTurnDetails(page);
  const panel = (await page.locator(".ts-detail").innerText()).replace(/\s+/g, " ");
  expect(panel.split("not independently verified").length - 1).toBe(1);
  expect(panel.split("Review the response and its cited evidence").length - 1).toBe(1);

  // And the row must not overclaim: a plain answer verified nothing about its
  // own content, so it reads "Response received", never "Done".
  await expect(page.locator(".ts-verdict")).toHaveText("Response received");
});

test("rows that say nothing happened are not rendered", async ({ page }) => {
  // An Explain run printed "PR: not opened" and "Merge: —" -- two rows stating
  // that things which were never going to happen did not happen -- plus a Cost
  // the row and the receipt strip were both already showing.
  await openApp(page);
  const id = await sendPrompt(page, "Explain it");
  await finishRequest(page, id, {
    answer: "Here is the explanation.",
    receipt: { estimated_actual_usd: 0.0407, confidence: "actual" },
    agent_policy: { mode: "explain", label: "Explain" },
    workflow: { mode: "explain", phase: "completed", merge_status: "not_requested", pr_url: "" },
  });

  await openTurnDetails(page);
  const card = page.locator(".workflow-card");
  await expect(card).not.toContainText("not opened");
  await expect(card).not.toContainText("Merge");
  await expect(card).not.toContainText("Cost");
});
