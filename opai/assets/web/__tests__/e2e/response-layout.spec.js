import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";

const answer = [
  "# Parser result",
  "",
  "This explanation is intentionally long enough to occupy the readable prose measure without forcing engineering artifacts into the same narrow column. It keeps the result comfortable to scan while leaving room for evidence.",
  "",
  "| Run | Code | Result |",
  "| --- | --- | ---: |",
  "| A | with fix | 76 passed |",
  "| B | fix reverted | 3 failed |",
  "",
  "```typescript",
  'const longLine = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".repeat(4);',
  "```",
].join("\n");

test("prose stays readable while tables and code use the wider engineering canvas", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await openApp(page);
  const requestId = await sendPrompt(page, "Inspect a long parser result with code and a table");
  await finishRequest(page, requestId, { answer });

  const response = page.locator(".msg.bot").last();
  const metrics = await response.evaluate((element) => {
    const paragraph = element.querySelector(".response-prose > p");
    const table = element.querySelector(".response-table-scroll");
    const code = element.querySelector(".response-prose > pre");
    const proseStyle = getComputedStyle(paragraph);
    const codeStyle = getComputedStyle(code.querySelector("code"));
    return {
      paragraphWidth: paragraph.getBoundingClientRect().width,
      tableWidth: table.getBoundingClientRect().width,
      codeWidth: code.getBoundingClientRect().width,
      proseFontSize: proseStyle.fontSize,
      proseLineHeight: proseStyle.lineHeight,
      codeFontSize: codeStyle.fontSize,
      codeLineHeight: codeStyle.lineHeight,
    };
  });
  expect(metrics.paragraphWidth).toBeLessThanOrEqual(760);
  expect(metrics.tableWidth).toBeGreaterThan(metrics.paragraphWidth + 80);
  expect(metrics.codeWidth).toBeGreaterThan(metrics.paragraphWidth + 80);
  expect(metrics.proseFontSize).toBe("15px");
  expect(parseFloat(metrics.proseLineHeight)).toBeGreaterThanOrEqual(23);
  expect(metrics.codeFontSize).toBe("13px");
  expect(parseFloat(metrics.codeLineHeight)).toBeGreaterThanOrEqual(20);

  const tableRegion = response.getByRole("region", { name: "Scrollable table" });
  await expect(tableRegion).toHaveAttribute("tabindex", "0");
  await expect(response.locator(".code-block-head .code-language")).toHaveText("typescript");
  await expect(response.locator(".code-block-head .code-copy")).toHaveAttribute("aria-label", "Copy code");
});

test("wide tables and code scroll locally without overflowing a 375px chat", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await openApp(page);
  const requestId = await sendPrompt(page, "Show narrow layout behavior");
  await finishRequest(page, requestId, { answer });

  const response = page.locator(".msg.bot").last();
  const metrics = await page.evaluate(() => {
    const table = document.querySelector(".msg.bot:last-child .response-table-scroll");
    const code = document.querySelector(".msg.bot:last-child .response-prose > pre");
    return {
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      tableClient: table.clientWidth,
      tableScroll: table.scrollWidth,
      codeClient: code.clientWidth,
      codeScroll: code.scrollWidth,
    };
  });
  expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth);
  expect(metrics.tableScroll).toBeGreaterThan(metrics.tableClient);
  expect(metrics.codeScroll).toBeGreaterThan(metrics.codeClient);
  await expect(response.getByRole("region", { name: "Scrollable table" })).toBeVisible();
  await expect(page.locator("#input")).toBeVisible();
});
