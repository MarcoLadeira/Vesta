import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";

// opaihub/diff_review.py never mutates source files — a diff shown here is
// already on disk. "reviewing_diff" (Ask before edits) means a human sign-off
// is still needed before it can ship; any other phase means it's already
// applied and the card is read-only. Both reuse the same evidence and markup
// (changesetCardHtml / diffFileCardHtml in app.js).

function proposedScenario() {
  return {
    answer: "Here's the proposed change.",
    agent_policy: { mode: "implement", label: "Implement" },
    changed_files: [" M app.py", " M configs/permissions.yaml"],
    workflow: {
      mode: "implement", phase: "reviewing_diff", tests_status: "passed",
      diff_review: {
        summary: { files: 2, pending: 2, risky: 1, additions: 2, deletions: 1 },
        files: [
          {
            path: "app.py", decision: "pending", additions: 1, deletions: 1,
            hunks: [{
              old_start: 4, old_count: 1, new_start: 4, new_count: 1,
              heading: "route", lines: ["-old", "+new"],
            }],
          },
          {
            path: "configs/permissions.yaml", decision: "pending", additions: 1, deletions: 0,
            risky: true, risk_reasons: ["permissions"],
            hunks: [{
              old_start: 3, old_count: 1, new_start: 3, new_count: 2,
              heading: "", lines: [" existing", "+  new_perm: true"],
            }],
          },
        ],
      },
    },
  };
}

test("a proposed changeset lists every file with approve/reject and honest cleared-for-merge copy", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Add a permission");
  await finishRequest(page, id, proposedScenario());

  const card = page.locator(".changeset-card");
  await expect(card).toBeVisible();
  await expect(card.locator(".cs-badge")).toHaveText("Proposed");
  await expect(card).toContainText("2 files changed");
  await expect(card).not.toContainText("Tests: passed");
  await expect(card.locator("[data-cs-review-note]")).toHaveText("2 of 2 pending review");

  const files = card.locator(".diff-file2");
  await expect(files).toHaveCount(2);
  await expect(files.nth(0).locator(".df-type")).toHaveText("M");
  await expect(files.nth(1)).toContainText("permissions");
  await expect(files.nth(1).locator("[data-df-decision-label]")).toContainText("review carefully");

  // Only the first file is expanded by default; a collapsed file's Approve
  // button isn't reachable until its row is opened, same as a real user.
  await files.nth(1).locator(".diff-file-summary").click();
  await files.nth(1).getByRole("button", { name: "Approve" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.diffDecisions)).toEqual([
    ["configs/permissions.yaml", "approved"],
  ]);
  await expect(files.nth(1).locator("[data-df-decision-label]")).toHaveText("Approved — cleared for merge");
  // Never claims the edit "will be applied" — the file was already written to
  // disk; the decision only clears (or blocks) the merge.
  await expect(files.nth(1)).not.toContainText("will be applied");
  await expect(card.locator("[data-cs-review-note]")).toHaveText("1 of 2 pending review");

  await files.nth(0).getByRole("button", { name: "Reject" }).click();
  await expect(files.nth(0).locator("[data-df-decision-label]")).toHaveText("Rejected — blocks merge until resolved");
  await expect(card.locator("[data-cs-review-note]")).toHaveText("All reviewed");
  await expect(card.locator('[data-diff-bulk="approved"]')).toBeHidden();
});

test("Approve all clears every still-pending file in one action", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Add a permission");
  await finishRequest(page, id, proposedScenario());

  const card = page.locator(".changeset-card");
  await card.locator('[data-diff-bulk="approved"]').click();

  await expect.poll(() => page.evaluate(() => window.__mock.diffDecisions)).toEqual([
    ["app.py", "approved"],
    ["configs/permissions.yaml", "approved"],
  ]);
  await expect(card.locator("[data-cs-reviewed]")).toBeVisible();
  await expect(card.locator("[data-cs-reviewed]")).toHaveText("All reviewed");
  await expect(card.locator(".diff-file2").nth(0).locator("[data-df-decision-label]")).toHaveText("Approved — cleared for merge");
});

test("an already-applied changeset is read-only — real diffs, no approve/reject", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Add rate limiting");
  await finishRequest(page, id, {
    answer: "Done.",
    agent_policy: { mode: "implement", label: "Implement" },
    changed_files: [" M auth/login.py"],
    workflow: {
      mode: "implement", phase: "completed", tests_status: "passed",
      diff_review: {
        summary: { files: 1, pending: 0, additions: 3, deletions: 0 },
        files: [{
          path: "auth/login.py", decision: "pending", additions: 3, deletions: 0,
          hunks: [{
            old_start: 8, old_count: 6, new_start: 8, new_count: 9,
            heading: "from flask import request", lines: [" existing", "+added one", "+added two"],
          }],
        }],
      },
    },
  });

  const card = page.locator(".changeset-card");
  await expect(card).toBeVisible();
  await expect(card.locator(".cs-badge")).toHaveText("Applied");
  await expect(card).not.toContainText("pending review");
  await expect(card.locator("[data-diff-decision]")).toHaveCount(0);
  await expect(card.locator(".diff-hunk-body")).toContainText("added one");
  await expect(card.locator(".dl-num.dl-new").first()).toBeVisible();
  // No fabricated actions: reverting an already-applied file isn't backed by
  // any real bridge method, so the card must never claim it can.
  await expect(card).not.toContainText("Revert");
});

test("file type badges reflect real git status, not guesses", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Refactor config loading");
  await finishRequest(page, id, {
    answer: "Done.",
    agent_policy: { mode: "implement", label: "Implement" },
    changed_files: ["?? middleware/rate_limit.py", " D utils/throttle.py"],
    workflow: {
      mode: "implement", phase: "completed", tests_status: "not_run",
      diff_review: {
        summary: { files: 2, pending: 0, additions: 5, deletions: 9 },
        files: [
          {
            path: "middleware/rate_limit.py", decision: "pending", additions: 5, deletions: 0, untracked: true,
            hunks: [{ old_start: 0, old_count: 0, new_start: 1, new_count: 5, heading: "untracked file", lines: ["+import time"] }],
          },
          {
            path: "utils/throttle.py", decision: "pending", additions: 0, deletions: 9,
            hunks: [{ old_start: 1, old_count: 9, new_start: 0, new_count: 0, heading: "", lines: ["-def throttle(): pass"] }],
          },
        ],
      },
    },
  });

  const card = page.locator(".changeset-card");
  const files = card.locator(".diff-file2");
  await expect(files.nth(0).locator(".df-type")).toHaveText("A");
  await expect(files.nth(0)).toContainText("new file");
  await expect(files.nth(1).locator(".df-type")).toHaveText("D");
  await expect(files.nth(1)).toContainText("deleted");
});

test("Copy path and Copy diff use the real clipboard bridge, never the DOM clipboard", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Add a permission");
  await finishRequest(page, id, proposedScenario());

  const file = page.locator(".diff-file2").first();
  await file.locator("[data-df-copy-path]").click();
  await expect.poll(() => page.evaluate(() => window.__mock.copiedTexts)).toContain("app.py");
  await expect(page.locator("#toast")).toContainText("Copied app.py");

  await file.locator("[data-df-copy-diff]").click();
  await expect.poll(() => page.evaluate(() => window.__mock.copiedTexts.at(-1))).toContain("-old");
  await expect(page.locator("#toast")).toContainText("Diff copied");
});

test("Open in editor calls the real open-path bridge with the exact file path", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Add a permission");
  await finishRequest(page, id, proposedScenario());

  await page.locator(".diff-file2").nth(1).locator("[data-df-open]").click();
  await expect.poll(() => page.evaluate(() => window.__mock.opened)).toContain("configs/permissions.yaml");
});
