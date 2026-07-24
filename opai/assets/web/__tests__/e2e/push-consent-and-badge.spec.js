import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openSettings, sendPrompt } from "./helpers/app.js";

// QA retest round 2 (2026-07-24). Two findings, both about the UI telling the
// user something that was not true:
//
//   1. OPai kept instructing users to click "Enable pushes & PRs" in Settings ->
//      Providers & Connections. The control existed, but it sat below the
//      free-provider key list, and once consent was granted its only label read
//      "Disable pushes & PRs" — so the phrase the user was told to look for was
//      genuinely absent from the page.
//   2. The header's "N uncommitted" badge came from the boot payload and was
//      never recomputed, so it kept showing the startup count after a run had
//      committed the files.

test("the push control leads the page and is findable in both states", async ({ page }) => {
  await openApp(page, {
    github: { connected: true, login: "octocat", allow_push: false, ready_for_push: false, readiness_reason: "consent_off" },
  });
  await openSettings(page, "providers");

  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("GitHub · pushes & pull requests");
  await expect(page.locator("[data-github-allowpush]")).toHaveText("Enable pushes & PRs");

  // It must come BEFORE the API-key list, not be buried under it.
  const order = await page.evaluate(() => {
    const text = document.querySelector("#settingsPage").innerText;
    return {
      github: text.indexOf("GitHub · pushes & pull requests"),
      keys: text.indexOf("Free model API keys"),
    };
  });
  expect(order.github).toBeGreaterThanOrEqual(0);
  expect(order.keys).toBeGreaterThanOrEqual(0);
  expect(order.github).toBeLessThan(order.keys);
});

test("an already-enabled push setting still names itself", async ({ page }) => {
  await openApp(page, {
    github: { connected: true, login: "octocat", allow_push: true, ready_for_push: true, readiness_reason: "ready" },
  });
  await openSettings(page, "providers");

  const settings = page.locator("#settingsPage");
  // The button necessarily reads "Disable…" now, so the card itself has to
  // carry the phrase — otherwise a user told to find "Enable pushes & PRs"
  // searches Settings and correctly concludes it does not exist.
  await expect(page.locator("[data-github-allowpush]")).toHaveText("Disable pushes & PRs");
  await expect(settings).toContainText("Enable pushes & PRs");
  await expect(settings).toContainText("already enabled");
  await expect(page.locator("[data-github-status]")).toContainText("pushes & PRs ON");
});

test("searching Settings for 'push' finds the control while it is on", async ({ page }) => {
  await openApp(page, {
    github: { connected: true, login: "octocat", allow_push: true, ready_for_push: true, readiness_reason: "ready" },
  });
  await openSettings(page, "providers");

  await page.locator("#settingsSearch").fill("push");
  await expect(page.locator("[data-github-allowpush]")).toBeVisible();
  await expect(page.locator("#settingsNoResults")).toBeHidden();
});

test("the uncommitted badge refreshes when a run commits", async ({ page }) => {
  await openApp(page, {
    boot: { workspace: { branch: "feature/pricing", dirty_paths: ["a.js", "b.js"] } },
    // After the run, the two files are committed and the tree is clean.
    workspaceAfterRun: { branch: "feature/pricing", dirty_paths: [] },
  });

  await expect(page.locator("#wsContext")).toContainText("2 uncommitted");

  const id = await sendPrompt(page, "Stage and commit the two leftover files");
  await finishRequest(page, id, { answer: "Committed." });

  await expect(page.locator("#wsContext")).toContainText("feature/pricing");
  await expect(page.locator("#wsContext")).not.toContainText("uncommitted");
  expect(await page.evaluate(() => window.__mock.workspaceStateCalls)).toBeGreaterThan(0);
});
