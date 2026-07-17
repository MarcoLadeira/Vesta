import { test, expect } from "@playwright/test";

import { openApp, sendPrompt } from "./helpers/app.js";

// F9/F17: a command hard-blocked by the run-mode policy must be approvable
// from chat. The pipeline replies with status needs_command_approval + the
// exact command; the card offers Approve once / Deny; Approve re-sends the
// original message with allowCommand set to that exact string.

const COMMAND = "gh issue view 219 --repo MarcoLadeira/OPai";

function gateScenario() {
  return {
    commandApproval: {
      command: COMMAND,
      reason: "Run any command is blocked in Safe Auto.",
    },
  };
}

test("a blocked command renders an approval card with the exact command; Approve once re-sends with allowCommand", async ({ page }) => {
  await openApp(page, gateScenario());
  // The gated reply arrives immediately, so drive the composer directly
  // instead of the sendPrompt helper (which waits for the busy UI).
  await page.fill("#input", "Fetch issue 219 for me");
  await page.getByRole("button", { name: "Send" }).click();

  const card = page.locator(".approval-card.command-approval");
  await expect(card).toBeVisible();
  await expect(card.locator(".ap-why")).toContainText("Run any command is blocked in Safe Auto.");
  // The exact command string is shown verbatim — never paraphrased.
  await expect(card.locator("code")).toHaveText(COMMAND);

  await card.locator('[data-ap="approve"]').click();

  await expect.poll(() => page.evaluate(() => window.__mock.sendCount)).toBe(2);
  const resent = await page.evaluate(() => window.__mock.lastRequest);
  // The ORIGINAL message is re-sent; only allowCommand is added.
  expect(resent.text).toBe("Fetch issue 219 for me");
  expect(resent.allowCommand).toBe(COMMAND);
  // The approved re-send completes as a normal answer.
  await expect(page.locator(".msg.bot").last()).toContainText("Ran with the approved command.");
  await expect(card.locator(".ap-state")).toHaveText("Approved — re-running with this command allowed…");
});

test("Deny posts a cancellation and never re-sends the blocked command", async ({ page }) => {
  await openApp(page, gateScenario());
  await page.fill("#input", "Fetch issue 219 for me");
  await page.getByRole("button", { name: "Send" }).click();

  const card = page.locator(".approval-card.command-approval");
  await expect(card).toBeVisible();
  await card.locator('[data-ap="deny"]').click();

  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  await expect(card.locator(".ap-state")).toHaveText("Denied — the command was not run.");
  // Both actions are disabled after the decision.
  await expect(card.locator('[data-ap="approve"]')).toBeDisabled();
  await expect(card.locator('[data-ap="deny"]')).toBeDisabled();
});

test("a normal send never carries an allowCommand field", async ({ page }) => {
  await openApp(page);
  await sendPrompt(page, "Summarize my changes");
  const request = await page.evaluate(() => window.__mock.lastRequest);
  expect("allowCommand" in request).toBe(false);
});
