import { test, expect } from "@playwright/test";

import { MODELS } from "./helpers/fixtures.js";
import { openApp, sendPrompt } from "./helpers/app.js";


test("model selector exposes Auto, Claude, Codex, Copilot, free, and local choices in optgroups", async ({ page }) => {
  await openApp(page);
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

test("selecting Full Auto shows a styled in-chat confirm, no native dialog (#151)", async ({ page }) => {
  await openApp(page);
  let dialogs = 0;
  page.on("dialog", async (dialog) => { dialogs += 1; await dialog.dismiss(); });
  await page.selectOption("#modeSel", "full-auto");
  // The confirmation is an in-chat card, never a native window.confirm.
  await expect(page.locator(".inline-confirm")).toBeVisible();
  await expect(page.locator(".inline-confirm .ic-title")).toContainText("Pin Full Auto");
  expect(dialogs).toBe(0);
  // Cancelling keeps the current mode and does not pin (#137).
  await page.click('.inline-confirm [data-ic="cancel"]');
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
  expect(await page.evaluate(() => window.__mock.fullAutoPins)).toBe(0);
});

test("confirming the Full Auto card pins it via the dedicated bridge slot (#137/#151)", async ({ page }) => {
  await openApp(page);
  page.on("dialog", async (dialog) => { await dialog.accept(); });
  await page.selectOption("#modeSel", "full-auto");
  await page.click('.inline-confirm [data-ic="ok"]');
  expect(await page.evaluate(() => window.__mock.fullAutoPins)).toBe(1);
  await expect(page.locator("#modeSel")).toHaveValue("full-auto");
  // A plain savePref for full-auto must never be used to persist it.
  const savedFullAuto = await page.evaluate(() =>
    window.__mock.savedPrefs.filter((p) => p[0] === "default_mode" && p[1] === "full-auto").length
  );
  expect(savedFullAuto).toBe(0);
});

test("the Full Auto confirm is keyboard-operable (#151)", async ({ page }) => {
  await openApp(page);
  await page.selectOption("#modeSel", "full-auto");
  await expect(page.locator(".inline-confirm")).toBeVisible();
  // Enter on the focused confirm button pins Full Auto.
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => window.__mock.fullAutoPins)).toBe(1);
});
