import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";

// F16/F4: after a workspace switch the composer run-mode, the header status,
// and the inspector must all follow the NEW workspace's payload — never the
// previous workspace's selection. The mock now emits workspaceChanged from
// switchWorkspace, like the real bridge.

const PINNED_FULL_AUTO = {
  requested_mode: "full-auto",
  effective_mode: "full-auto",
  full_auto_pinned: true,
  downgraded: false,
  reason: "",
};

// A workspace whose stored default is Full Auto. It used to arrive downgraded
// (effective_mode "safe-auto") with a pin card offered on top; a chosen mode is
// now simply in force, in whichever workspace stored it.
const WORKSPACE_IN_FULL_AUTO = {
  requested_mode: "full-auto",
  effective_mode: "full-auto",
  full_auto_pinned: true,
  downgraded: false,
  reason: "",
};

function fullAutoWorkspaceA() {
  return {
    boot: {
      prefs: { mode: "full-auto", fullAutoPinned: true },
      autonomy: PINNED_FULL_AUTO,
      status: { on: true, line: "Auto · Full Auto · $0.00 today · $0.00 saved" },
      inspector: {
        rows: [
          { label: "Model", value: "Auto" },
          { label: "Run mode", value: "Full Auto" },
        ],
        budget: { pct: 0, text: "$0.00 today" },
        permissions: [],
        privacy: [],
      },
    },
  };
}

test("composer, header, and inspector follow the fresh payload after a workspace switch (F16/F4)", async ({ page }) => {
  await openApp(page, {
    ...fullAutoWorkspaceA(),
    workspaceSwitch: {
      boot: {
        // The new workspace never asked for Full Auto: plain Safe Auto.
        prefs: { mode: "safe-auto", fullAutoPinned: false },
        autonomy: {
          requested_mode: "safe-auto",
          effective_mode: "safe-auto",
          full_auto_pinned: false,
          downgraded: false,
          reason: "",
        },
        status: { on: true, line: "Auto · Safe Auto · $0.00 today · $0.00 saved" },
        inspector: {
          rows: [
            { label: "Model", value: "Auto" },
            { label: "Run mode", value: "Safe Auto" },
          ],
          budget: { pct: 0, text: "$0.00 today" },
          permissions: [],
          privacy: [],
        },
      },
    },
  });
  await expect(page.locator("#modeSel")).toHaveValue("full-auto");

  await page.evaluate(() => window.__mock.switchWorkspace("/repo/opai"));

  // The stale "Bypass permissions" display was the bug: everything must repaint the
  // novice-facing Ask before edits label for the new workspace.
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
  await expect(page.locator("#statusLine")).toContainText("Auto");
  await expect(page.locator("#statusLine")).not.toContainText("Safe Auto");
  await expect(page.locator("#statusLine")).not.toContainText("Full Auto");
  await expect(page.locator("#wsLabel")).toHaveText("opai");
  const runModeRow = page.locator(".insp-row", { hasText: "Run mode" });
  await expect(runModeRow).toContainText("Auto");
  // No pin ack is offered when the new workspace doesn't request Full Auto.
  await expect(page.locator(".inline-confirm")).toHaveCount(0);
});

test("switching to a workspace whose mode is Full Auto just uses it, with no card", async ({ page }) => {
  // This pair used to cover the pin acknowledgement being re-offered on a
  // workspace switch — the path that made the modal appear without the user
  // touching the dropdown at all. A stored mode is now simply in force.
  await openApp(page, {
    ...fullAutoWorkspaceA(),
    workspaceSwitch: {
      boot: {
        prefs: { mode: "full-auto" },
        autonomy: WORKSPACE_IN_FULL_AUTO,
      },
    },
  });
  let dialogs = 0;
  page.on("dialog", async (dialog) => { dialogs += 1; await dialog.dismiss(); });

  await page.evaluate(() => window.__mock.switchWorkspace("/repo/opai"));

  await expect(page.locator("#modeSel")).toHaveValue("full-auto");
  await expect(page.locator(".inline-confirm")).toHaveCount(0);
  expect(await page.evaluate(() => window.__mock.fullAutoPins)).toBe(0);
  expect(dialogs).toBe(0);
});

test("the composer never shows a mode the workspace is not actually in", async ({ page }) => {
  // F16's real invariant. It survives the pin's removal because the desync it
  // guarded against needed a gap between requested and effective — and there
  // is no longer anything that can open one.
  await openApp(page, {
    ...fullAutoWorkspaceA(),
    workspaceSwitch: {
      boot: {
        prefs: { mode: "approve-edits" },
        autonomy: {
          requested_mode: "approve-edits",
          effective_mode: "approve-edits",
          full_auto_pinned: false,
          downgraded: false,
          reason: "",
        },
      },
    },
  });

  await page.evaluate(() => window.__mock.switchWorkspace("/repo/opai"));

  await expect(page.locator("#modeSel")).toHaveValue("approve-edits");
  await expect(page.locator("#modeBtnLabel")).toHaveText("Manual");
  await expect(page.locator(".inline-confirm")).toHaveCount(0);
});

test("inspector shows the live derived agent mode until a run completes (F21)", async ({ page }) => {
  await openApp(page, {
    boot: {
      taskModes: [
        { id: "general", label: "General" },
        { id: "build", label: "Build" },
        { id: "review", label: "Review" },
        { id: "explain", label: "Explain" },
      ],
      prefs: { focus: "explain" },
      inspector: {
        rows: [
          { label: "Model", value: "Auto" },
          { label: "Run mode", value: "Safe Auto" },
          { label: "Agent mode", value: "Explain" },
        ],
        budget: { pct: 0, text: "$0.00 today" },
        permissions: [],
        privacy: [],
      },
    },
  });
  const agentRow = page.locator(".insp-row", { hasText: "Agent mode" });
  // Persisted value matches the derivation: no pending annotation.
  await expect(agentRow).toContainText("Explain");
  await expect(agentRow).not.toContainText("(next run)");

  // Changing the current controls immediately shows the derived next-run mode.
  await page.selectOption("#focusSel", "build");
  await expect(agentRow).toContainText("Implement (next run)");

  // The run completes and the authoritative value arrives: annotation gone.
  const requestId = await sendPrompt(page, "Implement the fix");
  await page.evaluate(() => window.__mock.updateBoot({
    inspector: {
      rows: [
        { label: "Model", value: "Auto" },
        { label: "Run mode", value: "Safe Auto" },
        { label: "Agent mode", value: "Implement" },
      ],
      budget: { pct: 0, text: "$0.00 today" },
      permissions: [],
      privacy: [],
    },
  }));
  await finishRequest(page, requestId);
  await expect(agentRow).toContainText("Implement");
  await expect(agentRow).not.toContainText("(next run)");
});
