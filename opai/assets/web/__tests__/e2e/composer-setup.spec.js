import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";

test("composer shows effective mode, autonomy, model, and conservative cost posture", async ({ page }) => {
  await openApp(page);
  const setup = page.locator("#composerContext");
  await expect(setup).toContainText("Safe Auto");
  await expect(setup).toContainText("Asks before edits");
  await expect(setup).toContainText("OPai · Auto mode");
  await expect(setup).toContainText("Routes local first");

  await page.locator("#modeSel").selectOption("plan");
  await expect(setup).toContainText("Plans without changes");
  await page.locator("#modelSel").selectOption("ollama:qwen2.5-coder");
  await expect(setup).toContainText("No provider spend");
  await page.locator("#modelSel").selectOption("account:claude:opus");
  await expect(setup).toContainText("May spend within your limits");
  await setup.getByRole("button", { name: /cost posture/i }).click();
  await expect(page.locator("#view-settings")).toBeVisible();
  await expect(page.locator('[data-pane="firewall"]')).toHaveClass(/active/);
});

test("composer summary stays honest across every autonomy mode and cost class", async ({ page }) => {
  await openApp(page);
  const setup = page.locator("#composerContext");
  for (const [mode, consequence] of [
    ["ask", "Answers without changes"],
    ["plan", "Plans without changes"],
    ["safe-auto", "Asks before edits"],
    ["approve-edits", "Asks before commands"],
  ]) {
    await page.locator("#modeSel").selectOption(mode);
    await expect(setup).toContainText(consequence);
  }
  await page.locator("#modelSel").selectOption("free:gemini:gemini-3.1-flash-lite");
  await expect(setup).toContainText("No provider spend");
});

test("pinned Full Auto shows its effective consequence", async ({ page }) => {
  await openApp(page, {
    boot: {
      prefs: { mode: "full-auto", fullAutoPinned: true },
      autonomy: { requested_mode: "full-auto", effective_mode: "full-auto", full_auto_pinned: true },
    },
  });
  await expect(page.locator("#composerContext")).toContainText("Edits and runs commands");
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

test("starter prompts respect the same disconnected-provider send gate", async ({ page }) => {
  await openApp(page, {
    boot: { accounts: [{ id: "claude", label: "Claude", connected: false, authenticated: false }] },
  });
  await page.locator("#modelSel").selectOption("account:claude:opus");
  await page.getByRole("button", { name: "Summarize my changes" }).click();
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});

test("path-only context hints survive navigation and are sent without file contents", async ({ page }) => {
  await openApp(page);
  await page.locator("#contextPath").fill("src/router.py");
  await page.getByRole("button", { name: "Add context" }).click();
  await expect(page.locator("#contextHints")).toContainText("@src/router.py");
  await page.locator("#input").fill("Explain this route");
  await openNav(page, "Settings");
  await openNav(page, "Chat");
  await expect(page.locator("#input")).toHaveValue("Explain this route");
  await page.getByRole("button", { name: "Send prompt" }).click();
  expect(await page.evaluate(() => window.__mock.lastRequest.contextHints)).toEqual(["src/router.py"]);
  expect(await page.evaluate(() => window.__mock.lastRequest.text)).toBe("Repository context references:\n@src/router.py\n\nExplain this route");
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

test("context hints reject Windows absolute paths", async ({ page }) => {
  await openApp(page);
  await page.locator("#contextPath").fill("C:\\Users\\Frist\\secret.txt");
  await page.getByRole("button", { name: "Add context" }).click();
  await expect(page.locator("#contextHints")).toBeEmpty();
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
