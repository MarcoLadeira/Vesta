import { test, expect } from "@playwright/test";

import { openApp, sendPrompt, finishRequest } from "./helpers/app.js";


const PLAN_ANSWER = [
  "Here is the plan:",
  "1. Add the ledger model",
  "2. Delete the legacy importer",
  "3. Write regression tests",
].join("\n");

async function planReply(page) {
  await page.selectOption("#modeSel", "plan");
  const id = await sendPrompt(page, "plan the ledger refactor");
  await finishRequest(page, id, {
    status: "answered",
    answer: PLAN_ANSWER,
    plan: { steps: ["Add the ledger model", "Delete the legacy importer", "Write regression tests"], source: "parsed_from_answer" },
    receipt: {},
  });
}

test("plan-mode answers render an editable plan checklist", async ({ page }) => {
  await openApp(page);
  await planReply(page);
  const card = page.getByRole("group", { name: "Plan steps" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("Plan · 3 steps");
  await expect(card.locator(".plan-step")).toHaveCount(3);
  await expect(card.getByRole("button", { name: "Build this plan" })).toBeEnabled();
  await expect(card).toContainText("Safe Auto");
});

test("building sends only the kept steps, in Safe Auto, through the real pipeline", async ({ page }) => {
  await openApp(page);
  await planReply(page);
  const card = page.getByRole("group", { name: "Plan steps" });
  // The user rejects the destructive middle step.
  await card.locator(".plan-step input").nth(1).uncheck();
  await card.getByRole("button", { name: "Build this plan" }).click();
  await expect(page.locator(".gen-stop")).toBeVisible(); // a real new request started
  const request = await page.evaluate(() => window.__mock.lastRequest);
  expect(request.mode).toBe("safe-auto");
  expect(request.text).toContain("1. Add the ledger model");
  expect(request.text).toContain("2. Write regression tests");
  expect(request.text).not.toContain("Delete the legacy importer");
  // The composer mode control reflects the switch honestly.
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
});

test("unticking every step disables Build (nothing to run)", async ({ page }) => {
  await openApp(page);
  await planReply(page);
  const card = page.getByRole("group", { name: "Plan steps" });
  const boxes = card.locator(".plan-step input");
  const count = await boxes.count();
  for (let i = 0; i < count; i++) await boxes.nth(i).uncheck();
  await expect(card.getByRole("button", { name: "Build this plan" })).toBeDisabled();
});

test("non-plan answers never grow a plan card", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "explain something");
  await finishRequest(page, id, { status: "answered", answer: PLAN_ANSWER, receipt: {} });
  await expect(page.locator(".plan-card")).toHaveCount(0);
});
