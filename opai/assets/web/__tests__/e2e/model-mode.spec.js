import { test, expect } from "@playwright/test";

import { MODELS } from "./helpers/fixtures.js";
import { openApp, sendPrompt } from "./helpers/app.js";


test("model selector exposes Auto, Claude, Codex, Copilot, free, and local choices in optgroups", async ({ page }) => {
  await openApp(page);
  // `allTextContents()` and `allInnerTexts()` below do not retry: they read
  // whatever exists at that instant. Without a retrying guard first, a slow
  // hosted paint yields an empty list and the assertion fails immediately --
  // the same race that made settings-pages.spec.js an intermittent required
  // failure. Wait for the select to populate, then the plain reads are safe.
  await expect(page.locator("#modelSel option").first()).toBeAttached();
  await expect(page.locator("#modelSel optgroup").first()).toBeAttached();
  const labels = await page.locator("#modelSel option").allTextContents();
  expect(labels).toEqual(expect.arrayContaining([
    expect.stringContaining("Auto"),
    expect.stringContaining("Claude"),
    expect.stringContaining("Codex"),
    expect.stringContaining("Copilot"),
    expect.stringContaining("free"),
    expect.stringContaining("local"),
  ]));
  // Verify optgroup headings are rendered
  const optgroups = await page.locator("#modelSel optgroup").allInnerTexts();
  expect(optgroups.length).toBeGreaterThan(0);
  const groupLabels = await page.locator("#modelSel optgroup").evaluateAll(
    (els) => els.map((el) => el.getAttribute("label"))
  );
  expect(groupLabels).toContain("Claude");
  expect(groupLabels).toContain("Codex");
  expect(groupLabels).toContain("Copilot");
  expect(groupLabels).toContain("Free models");
  expect(groupLabels).toContain("OPai routing");
  expect(groupLabels).toContain("Local models");
});

test("Safe Auto is default and Full Auto is never silently selected", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
  await expect(page.locator("#modeSel")).not.toHaveValue("full-auto");
});

test("the header and composer use the same novice-facing run-mode name", async ({ page }) => {
  await openApp(page);

  await expect(page.locator("#modeBtnLabel")).toHaveText("Auto");
  await expect(page.locator("#statusLine")).toContainText("Auto");
  await expect(page.locator("#statusLine")).not.toContainText("Safe Auto");
  await expect(page.locator(".insp-row", { hasText: "Run mode" })).toContainText("Auto");
});

test("leaving Full Auto immediately updates the header as well as the composer", async ({ page }) => {
  await openApp(page, {
    boot: {
      prefs: { mode: "full-auto", fullAutoPinned: true },
      autonomy: {
        requested_mode: "full-auto", effective_mode: "full-auto",
        full_auto_pinned: true, downgraded: false, reason: "",
      },
      status: { on: true, line: "Claude · Full Auto · $0.00 today · $0.00 saved" },
    },
  });
  await expect(page.locator("#modeSel")).toHaveValue("full-auto");

  await page.selectOption("#modeSel", "ask");

  await expect(page.locator("#modeSel")).toHaveValue("ask");
  await expect(page.locator("#modeBtnLabel")).toHaveText("Ask");
  await expect(page.locator("#statusLine")).toContainText("Ask");
  await expect(page.locator("#statusLine")).not.toContainText("Full Auto");
});

test("model selection updates the provider signal and CLI mirror", async ({ page }) => {
  await openApp(page);
  await page.selectOption("#modelSel", "account:codex:gpt-5.5");
  await expect(page.locator("#cliMirrorCmd")).toContainText("codex:gpt-5.5");
  const color = await page.locator("#providerDot").evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(color).not.toBe("rgba(0, 0, 0, 0)");
});

test("selected mode and model remain attached to the request", async ({ page }) => {
  await openApp(page);
  await page.selectOption("#modelSel", "ollama:qwen2.5-coder");
  await page.selectOption("#modeSel", "approve-edits");
  await sendPrompt(page, "Prepare edits for review");
  expect(await page.evaluate(() => window.__mock.lastRequest)).toMatchObject({
    model: "ollama:qwen2.5-coder",
    mode: "approve-edits",
  });
});

test("unavailable model options are disabled with their reason", async ({ page }) => {
  await openApp(page, {
    boot: {
      models: MODELS.concat([{
        id: "account:claude:preview",
        label: "Claude Preview · unavailable",
        kind: "account",
        group: "claude",
        provider: "claude",
        available: false,
        disabled_reason: "Preview access is not enabled.",
      }]),
    },
  });
  const option = page.locator('#modelSel option[value="account:claude:preview"]');
  await expect(option).toBeDisabled();
  await expect(option).toHaveAttribute("title", /Preview access is not enabled/);
});

test("selecting Full Auto just selects it, with no confirmation card", async ({ page }) => {
  // It used to open an acknowledgement card, because a plain savePref for
  // full-auto was rewritten to Safe Auto server side and the card was what
  // pinned it instead. Nothing is rewritten now, so the card is gone -- it was
  // firing on every launch for anyone whose chosen mode was Full Auto.
  await openApp(page);
  let dialogs = 0;
  page.on("dialog", async (dialog) => { dialogs += 1; await dialog.dismiss(); });
  await page.selectOption("#modeSel", "full-auto");
  await expect(page.locator(".inline-confirm")).toHaveCount(0);
  expect(dialogs).toBe(0);
  await expect(page.locator("#modeSel")).toHaveValue("full-auto");
});

test("a picked mode is saved as the durable default, Full Auto included", async ({ page }) => {
  // The reported bug: OPai forgot the chosen mode on every restart. It is
  // persisted through the ordinary savePref path now, like any other setting,
  // rather than through a dedicated pin slot guarded by a modal.
  await openApp(page);
  await expect(page.locator("#modeBtnLabel")).toHaveText("Auto");
  await page.selectOption("#modeSel", "full-auto");
  const saved = await page.evaluate(() =>
    window.__mock.savedPrefs.filter((p) => p[0] === "default_mode" && p[1] === "full-auto").length
  );
  expect(saved).toBe(1);
  expect(await page.evaluate(() => window.__mock.fullAutoPins)).toBe(0);
  // Round 5 finding 4: every mode surface must agree immediately, with no send
  // in between. That still holds, and now without a card to confirm first.
  await expect(page.locator("#modeBtnLabel")).toHaveText("Bypass permissions");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});

