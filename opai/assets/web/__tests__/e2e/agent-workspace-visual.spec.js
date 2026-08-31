import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";

function completedWorkspace() {
  return {
    answer: [
      "# Fixed the updater regression",
      "",
      "The update listener now stays stable when Settings opens repeatedly.",
      "",
      "| Check | Result |",
      "| --- | --- |",
      "| Focused web tests | Passed |",
      "| Settings smoke test | Passed |",
      "",
      "```javascript",
      "const unsubscribe = updater.onStateChange(renderUpdateState);",
      "```",
    ].join("\n"),
    changed_files: [
      " M opai/assets/web/app.js",
      " M opai/assets/web/settings-connections.js",
      " M opai/assets/web/__tests__/e2e/updater-state.spec.js",
    ],
    presentation: {
      schema_version: 1,
      run: {
        state: "completed",
        label: "Ready for review",
        reason: "Focused verification passed.",
        next_action: "Review the changes.",
      },
      evidence: { verification: { applicable: true, verdict: "verified" } },
      tests: { status: "passed", passed: 79, failed: 0, skipped: 0 },
      changes: { summary: { files: 3, additions: 59, deletions: 3 } },
      activity: [
        { phase: "inspect", status: "completed", message: "Traced updater state" },
        { phase: "implement", status: "completed", message: "Stabilized the listener" },
        { phase: "test", status: "completed", message: "Focused checks passed" },
      ],
    },
    verification_manifest: {
      checks: [{
        check_id: "web",
        kind: "unit",
        requirement: "Run focused web checks",
        status: "passed",
        attempts: [{
          index: 1,
          status: "passed",
          command: ["npm", "run", "test:web"],
          exit_status: 0,
          output_summary: "9 files and 119 assertions passed.",
          teardown_verified: true,
        }],
      }],
    },
    agent_policy: { mode: "implement", label: "Implement" },
    workflow: {
      mode: "implement",
      phase: "completed",
      diff_review: {
        summary: { files: 3, pending: 0, additions: 59, deletions: 3 },
        files: [{
          path: "opai/assets/web/app.js",
          decision: "approved",
          additions: 24,
          deletions: 1,
          hunks: [],
        }],
      },
    },
  };
}

for (const scenario of [
  { density: "balanced", width: 1440, height: 900 },
  { density: "compact", width: 1024, height: 800 },
]) {
  test(`${scenario.density} agent workspace visual`, async ({ page }) => {
    await page.setViewportSize({ width: scenario.width, height: scenario.height });
    await openApp(page, { boot: { prefs: { responseDensity: scenario.density } } });
    const id = await sendPrompt(page, "Fix the updater regression and verify the result");
    await finishRequest(page, id, completedWorkspace());
    const response = page.locator(".msg.bot").last();
    await response.locator(".meta").evaluateAll((elements) => elements.forEach((element) => element.remove()));
    if (scenario.density === "balanced") {
      await page.locator("#chatScroll").evaluate((element) => { element.scrollTop = 0; });
    }
    await expect(page.locator("#view-chat")).toHaveScreenshot(`${scenario.density}-agent-workspace.png`, {
      animations: "disabled",
      caret: "hide",
      maxDiffPixelRatio: 0.01,
    });
  });
}
