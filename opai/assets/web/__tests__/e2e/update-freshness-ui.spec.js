import { test, expect } from "@playwright/test";
import { openApp, openNav } from "./helpers/app.js";

/**
 * Freshness and ownership, shown where a person looks (#832 scope item 7).
 *
 * The backend renders these sentences. `report.py` decides once what "cached
 * result · remote checked 47 min ago" means, and the CLI prints the same
 * string -- so these tests assert the UI *displays* it, never that the UI
 * derives it. A JavaScript copy of that rule would be the second
 * interpretation layer the epic forbids, and the two would disagree the first
 * time either moved.
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

test("the update sheet says when the source was really contacted", async ({ page }) => {
  await openApp(page, scenario(
    { state: "available", candidate: { version: "0.3.0" }, safe_diagnostic: "" },
    { summary: { freshness: "cached result · remote checked 47 min ago", remediation: "" } },
  ));

  await page.locator("#updateBanner").click();

  const freshness = page.locator("#updateSheetFreshness");
  await expect(freshness).toBeVisible();
  await expect(freshness).toHaveText("cached result · remote checked 47 min ago");
});

test("a turn with no freshness to report shows no empty line", async ({ page }) => {
  // The counterpart: without this, "visible" proves nothing, because an
  // element that is always visible would satisfy the test above too.
  await openApp(page, scenario(
    { state: "available", candidate: { version: "0.3.0" }, safe_diagnostic: "" },
    { summary: { freshness: "", remediation: "" } },
  ));

  await page.locator("#updateBanner").click();

  await expect(page.locator("#updateSheetFreshness")).toBeHidden();
});

test("an installation OPai does not own is told what does own it", async ({ page }) => {
  await openApp(page, scenario(
    { state: "unsupported_install", candidate: {},
      safe_diagnostic: "This installation cannot update transactionally." },
    { self_updatable: false,
      summary: { freshness: "checked remotely 5 min ago",
                 remediation: "Update with `pipx upgrade opai`." } },
  ));

  await page.locator("#updateBanner").click();

  await expect(page.locator("#updateSheetRemediation"))
    .toHaveText("Update with `pipx upgrade opai`.");
  // And it is not offered a button that would act on an installation another
  // tool is responsible for.
  await expect(page.locator('[data-update-action="developer_apply"]')).toHaveCount(0);
  // Checking again is always safe, whoever owns it -- removing both left the
  // sheet with no action at all, which is less honest than the button it
  // replaced.
  await expect(page.locator('[data-update-action="check"]')).toBeVisible();
});

test("a source checkout OPai does own keeps its apply button", async ({ page }) => {
  await openApp(page, scenario(
    { state: "unsupported_install", candidate: {},
      safe_diagnostic: "This source checkout is 4 commits behind origin/main." },
    { self_updatable: true, summary: { freshness: "checked remotely 1 min ago", remediation: "" } },
  ));

  await page.locator("#updateBanner").click();

  await expect(page.locator('[data-update-action="developer_apply"]')).toBeVisible();
  await expect(page.locator("#updateSheetRemediation")).toBeHidden();
});

test("Settings tells a quiet installation when it was last really checked", async ({ page }) => {
  // The state that hides the banner entirely still has to be answerable: this
  // is where someone goes to ask "is it actually checking?".
  await openApp(page, scenario(
    { state: "up_to_date", candidate: {}, safe_diagnostic: "" },
    { self_updatable: true, cadence_reason: "source checkout tracking origin/main",
      summary: { freshness: "checked remotely 3 min ago", remediation: "" } },
  ));

  await openNav(page, "Settings");
  await page.getByRole("button", { name: "About" }).click();

  const card = page.locator(".update-card");
  await expect(card).toBeVisible();
  await expect(card.locator(".update-freshness")).toHaveText("checked remotely 3 min ago");
  // The cadence sentence is read from policy, not hard-coded: it used to say
  // "every four hours", which stopped being true when cadence became
  // per-installation.
  await expect(card).toContainText("source checkout tracking origin/main");
  await expect(card).not.toContainText("four hours");
});

test("Settings offers no apply button for an installation it does not own", async ({ page }) => {
  await openApp(page, scenario(
    { state: "unsupported_install", candidate: {},
      safe_diagnostic: "This installation cannot update transactionally." },
    { self_updatable: false, cadence_reason: "stable release channel",
      summary: { freshness: "cached result · remote checked 2 hr ago",
                 remediation: "Update with `brew upgrade opai`." } },
  ));

  await openNav(page, "Settings");
  await page.getByRole("button", { name: "About" }).click();

  const card = page.locator(".update-card");
  await expect(card.locator(".update-remediation")).toContainText("brew upgrade opai");
  await expect(page.locator("#settingsApplyUpdate")).toHaveCount(0);
  await expect(page.locator("#settingsCheckUpdate")).toBeVisible();
});
