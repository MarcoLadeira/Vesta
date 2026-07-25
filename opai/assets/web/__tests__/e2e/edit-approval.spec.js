import { test, expect } from "@playwright/test";

import { openApp, sendPrompt } from "./helpers/app.js";

// F26: file edits refused by the provider's permission gate in Safe Auto must
// be approvable from chat. The pipeline replies with status
// needs_edit_approval + the exact file paths; the card offers "Allow edits
// once" / Deny; Approve re-sends the original message with
// allowEditsOnce=true and nothing else changed.

const FILES = ["opaihub/provider_tools.py", "docs/PLAN.md"];

function gateScenario() {
  return {
    editApproval: {
      files: FILES,
      approvedAnswer: "Edits applied.",
    },
  };
}

test("blocked edits render an approval card naming the exact files; Allow edits once re-sends with allowEditsOnce", async ({ page }) => {
  await openApp(page, gateScenario());
  await page.fill("#input", "Fix the bug in provider_tools");
  await page.getByRole("button", { name: "Send" }).click();

  const card = page.locator(".approval-card.edit-approval");
  await expect(card).toBeVisible();
  await expect(card.locator(".ap-why")).toContainText("In Ask before edits, OPai asks before changing files.");
  await expect(card.locator(".ap-why")).not.toContainText("Safe Auto");
  // The exact file paths are listed verbatim — never paraphrased.
  for (const file of FILES) {
    await expect(card.locator(".ap-files")).toContainText(file);
  }

  await card.locator('[data-ap="approve"]').click();

  await expect.poll(() => page.evaluate(() => window.__mock.sendCount)).toBe(2);
  const resent = await page.evaluate(() => window.__mock.lastRequest);
  // The ORIGINAL message is re-sent; only allowEditsOnce is added.
  expect(resent.text).toBe("Fix the bug in provider_tools");
  expect(resent.allowEditsOnce).toBe(true);
  await expect(page.locator(".msg.bot").last()).toContainText("Edits applied.");
  await expect(card.locator(".ap-state")).toHaveText("Approved — re-running with edits allowed once…");
});

test("Deny changes nothing and posts a cancellation", async ({ page }) => {
  await openApp(page, gateScenario());
  await page.fill("#input", "Fix the bug in provider_tools");
  await page.getByRole("button", { name: "Send" }).click();

  const card = page.locator(".approval-card.edit-approval");
  await expect(card).toBeVisible();
  await card.locator('[data-ap="deny"]').click();

  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  await expect(card.locator(".ap-state")).toHaveText("Denied — no files were changed.");
  await expect(card.locator('[data-ap="approve"]')).toBeDisabled();
  await expect(card.locator('[data-ap="deny"]')).toBeDisabled();
});

test("a normal send never carries an allowEditsOnce field", async ({ page }) => {
  await openApp(page);
  await sendPrompt(page, "Summarize my changes");
  const request = await page.evaluate(() => window.__mock.lastRequest);
  expect("allowEditsOnce" in request).toBe(false);
});
