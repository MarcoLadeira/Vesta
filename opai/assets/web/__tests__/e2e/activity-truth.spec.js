import { test, expect } from "@playwright/test";

import { emitActivity, finishRequest, openApp, openTurnDetails, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("activity timeline preserves ordered request evidence", async ({ page }) => {
  const id = await sendPrompt(page);
  const events = [
    { id: "prepare", type: "status", status: "success", title: "Preparing request" },
    { id: "context", type: "file_read", status: "success", title: "Reading project context" },
    { id: "route", type: "model", status: "success", title: "Selected cheapest capable model" },
    { id: "send", type: "request", status: "running", title: "Sending request" },
    { id: "wait", type: "request", status: "running", title: "Waiting for response" },
  ];
  for (const event of events) await emitActivity(page, id, event);
  await page.locator(".gen-toggle").click();
  await expect(page.locator(".timeline .tl-row")).toHaveCount(events.length);
  await expect(page.locator(".timeline")).toContainText("Preparing request");
  await expect(page.locator(".timeline")).toContainText("Waiting for response");
  await expect(page.locator("#inspLiveEvents")).toContainText("5 steps");
});

test("success can show completed only after the provider answers", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, { id: "done", type: "status", status: "success", title: "Completed" });
  await finishRequest(page, id, { answer: "done" });
  await openTurnDetails(page);
  await page.locator(".gen-toggle.done").click();
  await expect(page.locator(".timeline.done")).toContainText("Completed");
});

test("provider failure never presents a completed state", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, { id: "failed", type: "status", status: "error", title: "Failed" });
  await finishRequest(page, id, { status: "account_error", answer: "Provider failed", error: "timeout" });
  await expect(page.locator(".error-card")).toBeVisible();
  await expect(page.locator(".msg.bot")).not.toContainText("Completed");
});

test("an unverified run renders a partial verdict instead of success", async ({ page }) => {
  const id = await sendPrompt(page, "Fix parser.py and run tests.");
  await finishRequest(page, id, {
    status: "answered",
    answer: "I inspected the parser.",
    completion_verdict: {
      verdict: "partial",
      reason_code: "change_not_verified",
      reason: "OPai received a response but no changed-file or diff evidence verifies the requested edit.",
      next_action: "Ask OPai to apply the change.",
      evidence: [],
    },
  });

  await expect(page.locator(".completion-verdict.partial")).toContainText("Partial");
  await expect(page.locator(".completion-verdict")).toContainText("no changed-file or diff evidence");
  await expect(page.locator("#ssConn")).toHaveText("Partial");
  await expect(page.locator(".msg.bot")).not.toContainText("✓ Completed");
  // Round 5 finding 2: an honest partial is amber, not the same red as a hard
  // failure — the dot and the verdict card must grade the run the same way.
  await expect(page.locator("#statusStrip")).toHaveClass(/ss-warning/);
  // No success claim in the prose, so no unverified-claim banner either.
  await expect(page.locator(".unverified-claim")).toHaveCount(0);
});

test("workflow summary uses the authoritative verdict instead of a stale completed phase", async ({ page }) => {
  // Round 6: a verified push could show a Partial pill while the card below it
  // still read "Implement · Completed". The workflow runtime tracks process
  // progress, but the verdict is the user-facing outcome for this turn.
  const id = await sendPrompt(page, "Push the current branch.");
  await finishRequest(page, id, {
    status: "answered",
    answer: "The branch was pushed successfully.",
    completion_verdict: {
      verdict: "partial",
      reason_code: "change_not_verified",
      reason: "OPai could not verify the requested objective.",
      next_action: "Check the remote branch, then retry verification.",
      evidence: [],
    },
    workflow: {
      mode: "implement",
      phase: "completed",
      message: "Read-only task completed",
      tests_status: "not_run",
      merge_status: "not_requested",
      history: [{ phase: "completed", message: "Read-only task completed" }],
    },
  });

  // The invariant is unchanged; the surface that carries it moved. The turn
  // summary owns the verdict now, and the workflow card stopped printing a
  // phase of its own -- which is a stronger fix than making the card echo the
  // verdict, because a card with no phase in it cannot contradict anything.
  const summary = page.locator(".turn-summary");
  await expect(summary.locator(".ts-verdict")).toHaveText("No changes made");
  await expect(summary).toHaveClass(/is-partial/);

  const workflow = page.locator(".workflow-card");
  await expect(workflow.locator(".wf-head")).not.toContainText("Completed");
  await expect(workflow.locator(".wf-message")).toHaveText("OPai could not verify the requested objective.");
  await expect(workflow.locator(".wf-actions")).toContainText("Check the remote branch, then retry verification.");
  await summary.locator(".ts-row").click();
  await workflow.locator(".wf-history summary").click();
  await expect(workflow.locator(".wf-history")).toContainText("Partial");
  await expect(workflow.locator(".wf-history")).not.toContainText("Read-only task completed");
});

test("a success claim the run could not verify is labelled where it is written", async ({ page }) => {
  // Round 5 finding 2, the live failure: a red "Failed" pill sat directly above
  // "The current branch has been successfully pushed to the origin remote." A
  // user reading only the pill and a user reading only the prose drew opposite
  // conclusions. The claim itself now carries the caveat.
  const id = await sendPrompt(page, "Push the current branch.");
  await finishRequest(page, id, {
    status: "answered",
    answer: "The current branch has been successfully pushed to the origin remote.",
    completion_verdict: {
      verdict: "failed",
      reason_code: "provider_failed",
      reason: "The provider failed before OPai could verify the objective.",
      next_action: "Retry the run, or switch to another provider.",
      evidence: [],
      answer_conflicts: true,
    },
  });

  const banner = page.locator(".unverified-claim");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("could not verify");
  // It names the same verdict the pill shows, so the two surfaces agree.
  await expect(banner).toContainText("Failed");
  await expect(page.locator("#ssConn")).toHaveText("Failed");
  // It remains adjacent to the quiet final state, after the supporting work.
  expect(await page.evaluate(() => {
    const bot = document.querySelector(".msg.bot");
    return bot.querySelector(".unverified-claim")
      .compareDocumentPosition(bot.querySelector(".completion-verdict")) & Node.DOCUMENT_POSITION_FOLLOWING;
  })).toBeTruthy();
});

test("answer delivery is not labelled independently verified", async ({ page }) => {
  const id = await sendPrompt(page, "What is 2+2?");
  await finishRequest(page, id, {
    status: "answered",
    answer: "Four",
    completion_verdict: {
      verdict: "completed",
      reason_code: "answer_delivered",
      reason: "Provider returned a complete response; its content was not independently verified.",
      next_action: "Review the response and its cited evidence.",
      evidence: [{ kind: "answer", summary: "Provider returned a non-empty response" }],
    },
  });

  await expect(page.locator(".completion-verdict")).toContainText("Response received");
  await expect(page.locator(".completion-verdict")).not.toContainText("Completed");
  await expect(page.locator(".completion-verdict")).toContainText("not independently verified");
  await expect(page.locator("#ssConn")).toHaveText("Response received");
});

test("cancelled request never becomes failed or completed", async ({ page }) => {
  const id = await sendPrompt(page);
  await page.locator(".gen-stop").click();
  // #380: the run is only reported cancelled once teardown is confirmed.
  await page.evaluate((rid) => window.__mock.confirmCancel(rid), id);
  await expect(page.locator(".stopped-card")).toContainText("stopped by you");
  await expect(page.locator(".msg.bot")).not.toContainText(/Failed|Completed/);
});

test("activity evidence remains reviewable after failure", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitActivity(page, id, { id: "failed", type: "status", status: "error", title: "Failed at provider" });
  await finishRequest(page, id, { status: "account_error", answer: "Provider failed" });
  await expect(page.getByRole("button", { name: /Activity/ })).toBeVisible();
});
