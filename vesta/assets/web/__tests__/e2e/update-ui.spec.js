import { test, expect } from "@playwright/test";
import { openApp, openSettings } from "./helpers/app.js";

/**
 * What the update surface says to a person.
 *
 * It had grown a developer's vocabulary: "cannot update transactionally",
 * "cached result · remote checked 47 min ago", "4 commits behind origin/main",
 * a shell command in backticks. All of it true, none of it the user's problem.
 * They need to know whether there is an update and what to press.
 *
 * The detail did not go away -- it lives in `vesta update doctor`, where
 * somebody debugging the updater looks. These tests pin that it stays there.
 */

const payload = (operation, discovery) => ({
  schema_version: 1,
  operation,
  policy: {},
  installed: { version: "0.2.1a1", install_type: "source_checkout" },
  restart_available: true,
  discovery: { ownership: {}, ...discovery },
});

const scenario = (operation, discovery) => ({
  settings: {
    prefs: {},
    firewall: {},
    permissions: [],
    accounts: [],
    about: { version: "0.2.1a1", update: payload(operation, discovery) },
  },
  boot: { update: payload(operation, discovery) },
});

// The vocabulary that must never reach the surface.
const JARGON = [
  "transactionally",
  "origin/main",
  "commits behind",
  "cached result",
  "remote checked",
  "min ago",
  "hr ago",
  "pipx",
  "brew ",
  "install --upgrade",
];

async function expectNoJargon(locator) {
  const text = (await locator.innerText()).toLowerCase();
  for (const term of JARGON) {
    expect(text, `surface must not say "${term}"`).not.toContain(term.toLowerCase());
  }
}

test("a source checkout behind its remote is simply told an update is ready", async ({
  page,
}) => {
  // This state is `unsupported_install`, whose internal diagnostic reads
  // "This source checkout is 4 commits behind origin/main; update with the
  // explicit developer update command." The user has a working button.
  await openApp(page, scenario(
    { state: "unsupported_install", candidate: {},
      safe_diagnostic: "This source checkout is 4 commits behind origin/main; update with the explicit developer update command." },
    { self_updatable: true,
      summary: { title: "Update available", message: "A new version of Vesta is ready." } },
  ));

  await page.locator("#updateBanner").click();
  const sheet = page.locator("#updateSheet");

  await expect(page.locator("#updateSheetTitle")).toContainText("Update available");
  await expect(page.locator("#updateSheetDescription"))
    .toHaveText("A new version of Vesta is ready.");
  await expectNoJargon(sheet);
  await expect(page.locator('[data-update-action="developer_apply"]')).toBeVisible();
});

test("an installation Vesta cannot update says so without naming a command", async ({
  page,
}) => {
  await openApp(page, scenario(
    { state: "unsupported_install", candidate: {},
      safe_diagnostic: "This installation cannot update transactionally." },
    { self_updatable: false,
      summary: { title: "Managed elsewhere",
                 message: "Updates for this installation are handled outside Vesta." } },
  ));

  await page.locator("#updateBanner").click();
  const sheet = page.locator("#updateSheet");

  await expect(page.locator("#updateSheetDescription"))
    .toContainText("handled outside Vesta");
  await expectNoJargon(sheet);
  // And it is not offered a button that would act on an installation another
  // tool owns -- while checking again stays available, because that is safe.
  await expect(page.locator('[data-update-action="developer_apply"]')).toHaveCount(0);
  await expect(page.locator('[data-update-action="check"]')).toBeVisible();
});

test("a finished update asks for a restart and nothing else", async ({ page }) => {
  await openApp(page, scenario(
    { state: "completed", candidate: {},
      safe_diagnostic: "Vesta was updated on disk. Restart to use the new version." },
    { self_updatable: true,
      summary: { title: "Update ready", message: "Restart Vesta to finish updating." } },
  ));

  await page.locator("#updateBanner").click();

  await expect(page.locator("#updateSheetDescription"))
    .toHaveText("Restart Vesta to finish updating.");
  await expectNoJargon(page.locator("#updateSheet"));
  await expect(page.locator('[data-update-action="restart_now"]')).toBeVisible();
});

test("a quiet installation says one thing and offers one action", async ({ page }) => {
  await openApp(page, scenario(
    { state: "up_to_date", candidate: {}, safe_diagnostic: "" },
    { self_updatable: true,
      summary: { title: "You're on the latest version", message: "" } },
  ));

  // About & updates is part of the Advanced page.
  await openSettings(page, "about");

  const card = page.locator(".update-card");
  await expect(card).toContainText("You're on the latest version");
  await expectNoJargon(card);
  await expect(page.locator("#settingsCheckUpdate")).toBeVisible();
});

test("the banner stays away when there is nothing to say", async ({ page }) => {
  // The counterpart: without this, every "is visible" assertion above is
  // satisfied by a surface that is simply always visible.
  await openApp(page, scenario(
    { state: "up_to_date", candidate: {}, safe_diagnostic: "" },
    { self_updatable: true, summary: { title: "You're on the latest version", message: "" } },
  ));

  await expect(page.locator("#updateShell")).toBeHidden();
});
