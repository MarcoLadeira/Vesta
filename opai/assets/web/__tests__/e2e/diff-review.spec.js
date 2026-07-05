import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test("visual diff review navigates files and persists decisions", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "Fix the permissions bug");
  await finishRequest(page, id, {
    answer: "Implemented.",
    agent_policy: { mode: "implement", label: "Implement" },
    workflow: {
      mode: "implement", phase: "reviewing_diff", tests_status: "passed",
      diff_review: {
        summary: { files: 2, pending: 2, risky: 1 },
        files: [
          {
            path: "app.py", decision: "pending", additions: 1, deletions: 1,
            hunks: [{
              old_start: 1, old_count: 1, new_start: 1, new_count: 1,
              heading: "route", lines: ["-old", "+new"],
            }],
          },
          { path: "configs/permissions.yaml", decision: "pending", additions: 1, deletions: 0, risky: true, risk_reasons: ["permissions"], hunks: [] },
        ],
      },
    },
  });

  const review = page.getByRole("region", { name: "Changed-file review" });
  await expect(review).toContainText("app.py");
  await expect(review).toContainText("Tests: passed");
  await page.getByRole("button", { name: "Next changed file" }).click();
  await expect(review).toContainText("configs/permissions.yaml");
  await expect(review).toContainText("permissions");
  await review.getByRole("button", { name: "Approve" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.diffDecisions)).toEqual([
    ["configs/permissions.yaml", "approved"],
  ]);
  await expect(review.locator(".diff-file:not([hidden]) .diff-decision")).toHaveText("approved");
  await expect(review.locator("[data-diff-counts]")).toContainText("1 pending · 1 approved");
});
