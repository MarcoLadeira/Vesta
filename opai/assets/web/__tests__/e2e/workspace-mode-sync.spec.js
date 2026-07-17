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

const DOWNGRADED_FULL_AUTO = {
  requested_mode: "full-auto",
  effective_mode: "safe-auto",
  full_auto_pinned: false,
  downgraded: true,
  reason: "Full Auto is not pinned in this workspace.",
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

  // The stale "Full Auto" display was the bug: everything must repaint Safe Auto.
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
  await expect(page.locator("#statusLine")).toContainText("Safe Auto");
  await expect(page.locator("#statusLine")).not.toContainText("Full Auto");
  await expect(page.locator("#wsLabel")).toHaveText("opai");
  const runModeRow = page.locator(".insp-row", { hasText: "Run mode" });
  await expect(runModeRow).toContainText("Safe Auto");
  // No pin ack is offered when the new workspace doesn't request Full Auto.
  await expect(page.locator(".inline-confirm")).toHaveCount(0);
});

test("the pin ack is re-offered after switching to a workspace requesting unpinned Full Auto; confirming pins (F16)", async ({ page }) => {
  await openApp(page, {
    ...fullAutoWorkspaceA(),
    workspaceSwitch: {
      boot: {
        prefs: { mode: "safe-auto", fullAutoPinned: false },
        autonomy: DOWNGRADED_FULL_AUTO,
      },
    },
  });
  let dialogs = 0;
  page.on("dialog", async (dialog) => { dialogs += 1; await dialog.dismiss(); });

  await page.evaluate(() => window.__mock.switchWorkspace("/repo/opai"));

  // Composer shows the honest effective mode while the ack is pending…
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
  // …and the ack is offered even though no dropdown change event fired.
  const ack = page.locator(".inline-confirm");
  await expect(ack).toBeVisible();
  await expect(ack.locator(".ic-title")).toContainText("Pin Full Auto");

  await ack.locator('[data-ic="ok"]').click();
  expect(await page.evaluate(() => window.__mock.fullAutoPins)).toBe(1);
  await expect(page.locator("#modeSel")).toHaveValue("full-auto");
  expect(dialogs).toBe(0); // styled in-chat card, never a native confirm
});

test("cancelling the re-offered pin ack leaves the downgraded Safe Auto mode (F16)", async ({ page }) => {
  await openApp(page, {
    ...fullAutoWorkspaceA(),
    workspaceSwitch: {
      boot: {
        prefs: { mode: "safe-auto", fullAutoPinned: false },
        autonomy: DOWNGRADED_FULL_AUTO,
      },
    },
  });
  await page.evaluate(() => window.__mock.switchWorkspace("/repo/opai"));

  const ack = page.locator(".inline-confirm");
  await expect(ack).toBeVisible();
  await ack.locator('[data-ic="cancel"]').click();

  expect(await page.evaluate(() => window.__mock.fullAutoPins)).toBe(0);
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
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
