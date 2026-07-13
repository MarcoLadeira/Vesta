import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


// GUI GitHub connect + consent (#300): the only in-GUI path to let agents push
// branches and open pull requests. The user pastes their own PAT; consent and
// readiness are shown honestly.

test("a disconnected workspace shows the connect form", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("GitHub");
  await expect(page.locator("[data-github-status]")).toContainText("Not connected");
  await expect(page.locator("[data-github-token]")).toBeVisible();
});

test("pasting a token connects and never leaves the secret on the page", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await page.locator("[data-github-token]").fill("ghp_secret_pat_value");
  await page.locator("[data-github-connect]").click();

  // The token reached the bridge...
  expect(await page.evaluate(() => window.__mock.githubConnects)).toContain("ghp_secret_pat_value");
  // ...the row now reads connected...
  await expect(page.locator("[data-github-status]")).toContainText("Connected");
  await expect(page.locator("#settingsPage")).toContainText("octocat");
  // ...and the secret is not left sitting in the DOM.
  await expect(page.locator("#settingsPage")).not.toContainText("ghp_secret_pat_value");
});

test("enabling pushes on a connected account reports ready", async ({ page }) => {
  await openApp(page, {
    github: { connected: true, login: "octocat", allow_push: false, ready_for_push: false, readiness_reason: "consent_off" },
  });
  await openNav(page, "Settings");
  await expect(page.locator("[data-github-allowpush]")).toBeVisible();
  await page.locator("[data-github-allowpush]").click();

  expect(await page.evaluate(() => window.__mock.githubPushToggles)).toEqual([true]);
  await expect(page.locator("#settingsPage")).toContainText("push branches and open pull requests");
});

test("disconnect revokes the connection", async ({ page }) => {
  await openApp(page, {
    github: { connected: true, login: "octocat", allow_push: true, ready_for_push: true, readiness_reason: "ready" },
  });
  await openNav(page, "Settings");
  await page.locator("[data-github-disconnect]").click();
  expect(await page.evaluate(() => window.__mock.githubDisconnects)).toBe(1);
  await expect(page.locator("[data-github-status]")).toContainText("Not connected");
});
