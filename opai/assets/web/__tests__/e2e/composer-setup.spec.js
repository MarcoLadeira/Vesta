import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";

// The Composer Redesign keeps every capability of the old composer but changes
// the information architecture: one quiet toolbar with mode/model popovers and
// on-demand context, plus three switchable directions. These specs assert the
// new surfaces and the behaviour that must be preserved (send gate, path-only
// context, send↔stop) unchanged.

test("composer summarises the effective mode and model in one quiet line", async ({ page }) => {
  await openApp(page);
  const summary = page.locator("#composerSummary");
  // Defaults: Safe Auto → "Ask before edits"; Auto model routes local-first.
  await expect(summary).toContainText("Ask before edits");
  await expect(summary).toContainText("Auto");
  await expect(summary).toContainText("local");

  // Change the mode through its popover — no duplicate control anywhere.
  await page.locator("#modeBtn").click();
  await page.getByRole("menuitemradio", { name: /Plan only/ }).click();
  await expect(page.locator("#modeBtnLabel")).toHaveText("Plan only");
  await expect(summary).toContainText("Plan only");
  // The redesign drives the real (hidden) mode control, so the pipeline is unchanged.
  await expect(page.locator("#modeSel")).toHaveValue("plan");

  // Pick a paid cloud model — summary drops "local".
  await page.locator("#modelBtn").click();
  await page.getByRole("menuitemradio", { name: /Claude · Opus/ }).click();
  await expect(page.locator("#modelBtnLabel")).toHaveText("Claude");
  await expect(summary).not.toContainText("local");
  await expect(page.locator("#modelSel")).toHaveValue("account:claude:opus");

  // A local model brings "local" back.
  await page.locator("#modelBtn").click();
  await page.getByRole("menuitemradio", { name: /Qwen 2.5 Coder/ }).click();
  await expect(page.locator("#modelBtnLabel")).toHaveText("Local");
  await expect(summary).toContainText("local");
});

test("mode popover offers every autonomy level with plain-language descriptions", async ({ page }) => {
  await openApp(page);
  await page.locator("#modeBtn").click();
  const menu = page.locator("#modePop");
  await expect(menu).toContainText("Review each change before it is applied.");
  await expect(menu).toContainText("Describe the changes without touching files.");
  // All five underlying autonomy modes remain reachable (no capability dropped).
  await expect(menu.locator(".cpop-title")).toHaveText([
    "Ask",
    "Plan only",
    "Ask before edits",
    "Approve edits",
    "Auto-apply",
  ]);
});

test("model popover groups providers and exposes the keep-work-local toggle", async ({ page }) => {
  await openApp(page);
  await page.locator("#modelBtn").click();
  const menu = page.locator("#modelPop");
  await expect(menu).toContainText("Claude");
  await expect(menu).toContainText("Free models");
  await expect(menu).toContainText("Local models");
  // Unavailable models are shown but disabled with the reason.
  const groq = menu.getByRole("menuitemradio", { name: /Groq · GPT-OSS/ });
  await expect(groq).toBeDisabled();
  // The local-first toggle is the former "routes local first" preference.
  await expect(menu.getByRole("menuitemcheckbox", { name: /Keep work on this machine/ })).toHaveAttribute("aria-checked", "true");
});

test("context is added on demand and sent as a path-only reference", async ({ page }) => {
  await openApp(page);
  await page.locator("#ctxBtn").click();
  await page.locator("#ctxPathDraft").fill("src/router.py");
  await page.locator("#ctxPathDraft").press("Enter");
  await expect(page.locator("#contextHints")).toContainText("@src/router.py");

  await page.locator("#input").fill("Explain this route");
  // Survives navigation away and back.
  await openNav(page, "Settings");
  await openNav(page, "Chat");
  await expect(page.locator("#input")).toHaveValue("Explain this route");
  await page.getByRole("button", { name: "Send prompt" }).click();
  expect(await page.evaluate(() => window.__mock.lastRequest.contextHints)).toEqual(["src/router.py"]);
  expect(await page.evaluate(() => window.__mock.lastRequest.text)).toBe(
    "Repository context references:\n@src/router.py\n\nExplain this route",
  );
});

test("Use this repository attaches the project as a context chip", async ({ page }) => {
  await openApp(page);
  await page.locator("#ctxBtn").click();
  await page.getByRole("menuitem", { name: /Use this repository/ }).click();
  await expect(page.locator("#contextHints")).not.toBeEmpty();
});

test("the context path input rejects Windows absolute paths", async ({ page }) => {
  await openApp(page);
  await page.locator("#ctxBtn").click();
  await page.locator("#ctxPathDraft").fill("C:\\Users\\Frist\\secret.txt");
  await page.locator("#ctxPathDraft").press("Enter");
  await expect(page.locator("#contextHints")).toBeEmpty();
});

test("dropped files add path-only context and never read the file payload", async ({ page }) => {
  await openApp(page);
  await page.locator(".composer").evaluate((composer) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["private source must not be read"], "src/components/Composer.jsx"));
    composer.dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
  });
  await expect(page.locator("#contextHints")).toContainText("@src/components/Composer.jsx");
  await page.locator("#input").fill("Review the component");
  await page.getByRole("button", { name: "Send prompt" }).click();
  expect(await page.evaluate(() => window.__mock.lastRequest.contextHints)).toEqual(["src/components/Composer.jsx"]);
  expect(await page.evaluate(() => JSON.stringify(window.__mock.lastRequest))).not.toContain("private source");
});

test("disabled send explains an unconfigured account and links to Settings", async ({ page }) => {
  await openApp(page, {
    boot: { accounts: [{ id: "claude", label: "Claude", connected: false, authenticated: false }] },
  });
  await page.locator("#modelSel").selectOption("account:claude:opus");
  await expect(page.locator("#send")).toBeDisabled();
  await expect(page.locator("#composerReason")).toContainText("Connect Claude before sending");
  await page.getByRole("button", { name: "Open Settings" }).click();
  await expect(page.locator("#view-settings")).toBeVisible();
});

test("empty prompts are explained instead of silently discarded", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#send")).toBeDisabled();
  await expect(page.locator("#composerReason")).toContainText("Write a prompt before sending");
  await page.locator("#input").fill("Check the project setup");
  await expect(page.locator("#send")).toBeEnabled();
});

test("Shift+Enter adds a line while Enter sends and the hint explains both", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#composerHelp")).toContainText("Enter to send · Shift+Enter for a new line");
  await page.locator("#input").fill("first");
  await page.locator("#input").press("Shift+Enter");
  await page.locator("#input").pressSequentially("second");
  await expect(page.locator("#input")).toHaveValue("first\nsecond");
  await page.locator("#input").press("Enter");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});

test("Send changes to Stop immediately and Stop cancels the active request", async ({ page }) => {
  await openApp(page);
  await page.locator("#input").fill("Investigate the router");
  await page.getByRole("button", { name: "Send prompt" }).click();
  await expect(page.locator("#send")).toHaveAccessibleName("Stop generation");
  await page.locator("#send").click();
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  await expect(page.getByRole("button", { name: "Send prompt" })).toBeVisible();
});

test("the composer direction can be switched between the three designs in Settings", async ({ page }) => {
  await openApp(page);
  const composer = page.locator("#composer");
  await expect(composer).toHaveAttribute("data-composer-style", "toolbar");

  await openNav(page, "Settings");
  await page.locator('[data-rail-target="appearance"]').click();
  const seg = page.locator('[data-composer-style-key]');
  await seg.getByRole("button", { name: "Single line" }).click();
  await openNav(page, "Chat");
  await expect(composer).toHaveAttribute("data-composer-style", "single");

  await openNav(page, "Settings");
  await page.locator('[data-rail-target="appearance"]').click();
  await page.locator('[data-composer-style-key]').getByRole("button", { name: "Command bar" }).click();
  await openNav(page, "Chat");
  await expect(composer).toHaveAttribute("data-composer-style", "command");
  await expect(page.locator("#composerTokens")).toBeVisible();

  // The persisted preference reaches the backend.
  const saved = await page.evaluate(() => window.__mock.savedPrefs);
  expect(saved).toContainEqual(["composer_style", "single"]);
  expect(saved).toContainEqual(["composer_style", "command"]);
});
