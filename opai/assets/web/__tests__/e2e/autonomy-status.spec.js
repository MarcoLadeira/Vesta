import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test("active repo and coding workflow status stay visible", async ({ page }) => {
  await openApp(page, {
    boot: {
      workspace: {
        label: "acme/demo",
        root: "/work/demo",
        name: "demo",
        branch: "codex/autonomy",
        remote: "https://github.com/acme/demo.git",
        dirty: true,
        dirty_paths: ["opaihub/agent_policy.py"],
        file_count: 42,
        recents: [],
      },
      workflow: {
        mode: "implement",
        phase: "testing",
        tests_status: "running",
        pr_url: "",
        merge_status: "not_requested",
        blockers: [],
      },
      inspector: {
        rows: [
          { label: "Agent mode", value: "Implement" },
          { label: "Workflow", value: "Testing" },
        ],
      },
    },
  });

  await expect(page.locator("#wsLabel")).toHaveText("acme/demo");
  await expect(page.locator("#wsContext")).toContainText("codex/autonomy");
  await expect(page.locator("#wsContext")).toContainText("1 uncommitted");
  await expect(page.locator("#wsSwitch")).toHaveAttribute("title", /github\.com\/acme\/demo/);
  await expect(page.locator("#inspector")).toContainText("Implement");

  const id = await sendPrompt(page, "Fix the issue and make a PR");
  await finishRequest(page, id, {
    answer: "Implemented and tested.",
    agent_policy: { mode: "implement", label: "Implement" },
    workflow: {
      task_id: "task-9",
      mode: "implement",
      phase: "completed",
      message: "Diff reviewed",
      tests_status: "reported_by_agent",
      pr_url: "https://github.test/pr/9",
      merge_status: "not_requested",
      blockers: [],
      next_actions: ["Inspect PR checks"],
      provider: { model: "account:codex:gpt-5" },
      cost: { estimated_actual_usd: 0.0123 },
      history: [
        { phase: "intent_resolved", message: "Implement mode selected" },
        { phase: "completed", message: "Diff reviewed" },
      ],
    },
  });

  const card = page.locator(".workflow-card");
  await expect(card).toContainText("Implement");
  await expect(card).toContainText("Completed");
  await expect(card).toContainText("reported by agent");
  await expect(card).toContainText("https://github.test/pr/9");
  await expect(card).toContainText("Inspect PR checks");
  await expect(card).toContainText("account:codex:gpt-5");
  await expect(card).toContainText("Timeline · 2 events");
});
