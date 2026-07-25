import { test, expect } from "@playwright/test";

import { openApp, openNav, finishRequest } from "./helpers/app.js";


// First-run onboarding (#250): a three-step, skippable tour that ends on a
// rendered savings receipt. Shown once per profile; a Replay entry lives in
// Settings -> About. It reuses the real send() so it can never bypass a gate.

const fresh = { boot: { prefs: { onboardingSeen: false } } };
const overlay = (page) => page.locator("#onboarding");
const step = (page, action) => page.locator(`#onboarding .ob-footer [data-ob="${action}"]`);
// Bug 11: sending the first task against the user's real folder is no longer
// the footer's primary CTA — a brand-new user reflexively clicking "next,
// next, primary" must not kick off a run on their working tree. The footer
// finishes the tour; running the task is a deliberate, labelled opt-in in the
// step body.
const runFirstTask = (page) => page.locator('#onboarding [data-ob="send"]');

test("a fresh profile is walked through all three steps and ends on a receipt", async ({ page }) => {
  await openApp(page, fresh);
  await expect(overlay(page)).toBeVisible();
  await expect(overlay(page)).toContainText("Step 1 of 3");
  await expect(overlay(page)).toContainText("Connect a provider");

  await step(page, "next").click();
  await expect(overlay(page)).toContainText("Step 2 of 3");
  await expect(page.locator('#onboarding [data-ob="model"]')).toBeVisible();

  await step(page, "next").click();
  await expect(overlay(page)).toContainText("Step 3 of 3");
  await expect(overlay(page)).toContainText("Summarize my uncommitted changes");

  await runFirstTask(page).click();
  // The tour got out of the way and the first task was actually sent.
  await expect(overlay(page)).toHaveCount(0);
  const req = await page.evaluate(() => window.__mock.lastRequest);
  expect(req.text).toBe("Summarize my uncommitted changes");
  // Completion renders the first savings receipt in the chat.
  const id = await page.evaluate(() => window.__mock.reqId());
  await finishRequest(page, id, {
    status: "answered",
    answer: "Here is a summary.",
    receipt: { estimated_savings_usd: 0.0123, confidence: "actual" },
  });
  await expect(page.locator(".receipt-card")).toBeVisible();
  // The tour is marked seen so it never returns.
  const saved = await page.evaluate(() => window.__mock.savedPrefs);
  expect(saved).toContainEqual(["onboarding_seen", "true"]);
});

test("skip works at the first step and marks the tour seen", async ({ page }) => {
  await openApp(page, fresh);
  await expect(overlay(page)).toBeVisible();
  await step(page, "skip").click();
  await expect(overlay(page)).toHaveCount(0);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["onboarding_seen", "true"]);
});

test("skip works at a later step too", async ({ page }) => {
  await openApp(page, fresh);
  await step(page, "next").click(); // to step 2
  await step(page, "skip").click();
  await expect(overlay(page)).toHaveCount(0);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["onboarding_seen", "true"]);
});

test("a returning user never sees the tour", async ({ page }) => {
  await openApp(page); // fixture default: onboardingSeen true
  await expect(overlay(page)).toHaveCount(0);
});

test("onboarding cannot force a cloud call — it sends through the normal gate", async ({ page }) => {
  await openApp(page, fresh);
  await step(page, "next").click();
  await step(page, "next").click();
  await runFirstTask(page).click();
  // The first task is sent with allowCloud=false, so the pipeline still gates
  // any paid/free-model call behind the usual confirmation — onboarding cannot
  // silently reach the cloud.
  const req = await page.evaluate(() => window.__mock.lastRequest);
  expect(req.allowCloud).toBe(false);
});

test("onboarding describes the confirmation boundary for every cloud model", async ({ page }) => {
  await openApp(page, fresh);
  await step(page, "next").click();
  await expect(overlay(page)).toContainText("only uses a cloud model after you confirm");
  await expect(overlay(page)).not.toContainText("paid cloud model");

  await step(page, "next").click();
  await expect(overlay(page)).toContainText("a cloud model always asks first");
  await expect(overlay(page)).not.toContainText("a paid model always asks first");
});

test("finishing the tour does not run anything against the user's folder", async ({ page }) => {
  // Bug 11: the footer's primary CTA used to be "Send my first task", so
  // clicking through the tour on a real work folder started a run nobody asked
  // for. It finishes the tour now, and the step names the project it would read.
  await openApp(page, fresh);
  await step(page, "next").click();
  await step(page, "next").click();
  await expect(overlay(page)).toContainText("it only summarizes, it never edits");
  await step(page, "finish").click();

  await expect(overlay(page)).toHaveCount(0);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual([
    "onboarding_seen",
    "true",
  ]);
});

test("picking a model in step 2 persists the default", async ({ page }) => {
  await openApp(page, fresh);
  await step(page, "next").click();
  await page.locator('#onboarding [data-ob="model"]').selectOption("account:claude:opus");
  const saved = await page.evaluate(() => window.__mock.savedPrefs);
  expect(saved).toContainEqual(["default_model", "account:claude:opus"]);
});

test("Replay tour in Settings -> About re-opens the tour", async ({ page }) => {
  await openApp(page); // returning user: no tour on boot
  await expect(overlay(page)).toHaveCount(0);
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="about"]').click();
  await page.locator("#settingsReplayTour").click();
  await expect(overlay(page)).toBeVisible();
  await expect(overlay(page)).toContainText("Step 1 of 3");
});
