import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openTurnDetails, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("a measured paid call shows the Measured badge and spend, never savings", async ({ page }) => {
  const id = await sendPrompt(page, "paid task");
  await finishRequest(page, id, {
    status: "answered", answer: "done",
    receipt: { estimated_actual_usd: 0.042, estimated_savings_usd: 0, paid_call: true, confidence: "actual" },
  });
  // The cost receipt is a record of the run, so it lives behind the turn
  // summary now rather than under every answer.
  await openTurnDetails(page);
  const card = page.locator(".receipt-card");
  await expect(card).toBeVisible();
  await expect(card.locator(".rc-badge")).toHaveText("Measured");
  await expect(card.locator(".rc-badge")).toHaveClass(/rc-measured/);
  await expect(card.locator(".footer-note")).toContainText("$0.0420 spent");
  await expect(card.locator(".footer-note")).not.toContainText("saved");
});

test("a local route shows Estimated and labels savings honestly", async ({ page }) => {
  const id = await sendPrompt(page, "local task");
  await finishRequest(page, id, {
    status: "answered", answer: "done",
    receipt: { estimated_actual_usd: 0, estimated_savings_usd: 0.084, paid_call_avoided: true, confidence: "estimated" },
  });
  // The cost receipt is a record of the run, so it lives behind the turn
  // summary now rather than under every answer.
  await openTurnDetails(page);
  const card = page.locator(".receipt-card");
  await expect(card.locator(".rc-badge")).toHaveText("Estimated");
  await expect(card.locator(".footer-note")).toContainText("$0.0840 saved");
  await expect(card.locator(".footer-note")).toContainText("paid call avoided");
});

test("a subscription-style $0 paid call shows the Subscription badge", async ({ page }) => {
  const id = await sendPrompt(page, "subscription task");
  await finishRequest(page, id, {
    status: "answered", answer: "done",
    receipt: { estimated_actual_usd: 0.001, estimated_savings_usd: 0, paid_call: true, confidence: "unknown" },
  });
  await expect(page.locator(".rc-badge")).toHaveText("Subscription");
  await expect(page.locator(".rc-badge")).toHaveClass(/rc-subscription/);
});

test("a receipt with no cost data invents no dollar figure", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "answered", answer: "done", receipt: {} });
  // The cost receipt is a record of the run, so it lives behind the turn
  // summary now rather than under every answer.
  await openTurnDetails(page);
  const card = page.locator(".receipt-card");
  await expect(card).toBeVisible();
  await expect(card.locator(".rc-bits")).not.toContainText("$"); // never a fake $0.00
});

test("clicking the strip copies a clean plaintext receipt", async ({ page }) => {
  const id = await sendPrompt(page, "copy me");
  await finishRequest(page, id, {
    status: "answered", answer: "done",
    receipt: { estimated_actual_usd: 0.0123, confidence: "actual" },
  });
  await openTurnDetails(page);
  const strip = page.locator(".footer-note");
  await expect(strip).toHaveAttribute("aria-label", "Copy receipt");
  await strip.click();
  await expect(page.locator("#toast")).toContainText("Receipt copied");
});

test("the Summary button jumps to the savings dashboard without copying", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    status: "answered", answer: "done",
    receipt: { estimated_actual_usd: 0.01, confidence: "actual" },
  });
  await openTurnDetails(page);
  const summary = page.locator(".rc-ledger");
  // #400: honest label — it opens the aggregate summary, not an itemized ledger.
  await expect(summary).toContainText("Summary");
  await expect(summary).not.toContainText("Ledger");
  await expect(summary).toHaveAttribute("aria-label", "Open the savings summary");
  await summary.click();
  await expect(page.locator("#view-dashboard")).toHaveClass(/active/);
  await expect(page.locator("#toast")).not.toContainText("Receipt copied"); // summary != copy
});

test("no receipt card appears when a request is blocked", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "blocked", answer: "This looked risky." });
  await expect(page.locator(".receipt-card")).toHaveCount(0);
  await expect(page.locator(".footer-note")).toHaveCount(0);
});
