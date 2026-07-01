import { test, expect } from "@playwright/test";

import { MODELS } from "./helpers/fixtures.js";
import { openApp, sendPrompt } from "./helpers/app.js";


test("model selector exposes Auto, Claude, Codex, Copilot, and local choices", async ({ page }) => {
  await openApp(page);
  const labels = await page.locator("#modelSel option").allTextContents();
  expect(labels).toEqual(expect.arrayContaining([
    expect.stringContaining("Auto"),
    expect.stringContaining("Claude"),
    expect.stringContaining("Codex"),
    expect.stringContaining("Copilot"),
    expect.stringContaining("local"),
  ]));
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

test("known bug: unavailable model options are disabled with their reason", async ({ page }) => {
  test.fail(true, "BUG-QA-008: model rendering ignores availability and disabled_reason fields");
  await openApp(page, {
    boot: {
      models: MODELS.concat([{
        id: "account:claude:preview",
        label: "Claude Preview · unavailable",
        kind: "account",
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

test("known bug: selecting Full Auto requires an explicit risk confirmation", async ({ page }) => {
  test.fail(true, "BUG-QA-009: web mode selector persists Full Auto without confirmation");
  await openApp(page);
  let dialogs = 0;
  page.on("dialog", async (dialog) => { dialogs += 1; await dialog.dismiss(); });
  await page.selectOption("#modeSel", "full-auto");
  expect(dialogs).toBe(1);
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
});
