import { test, expect } from "@playwright/test";

import { emitToken, finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => {
  await openApp(page);
  await page.evaluate(() => {
    const empty = document.querySelector("#empty");
    if (empty) empty.style.display = "none";
    const filler = document.createElement("div");
    filler.dataset.testFiller = "true";
    filler.style.height = "2800px";
    document.querySelector("#thread").appendChild(filler);
  });
});

test("manual scrolling pauses follow and Jump to latest resumes it", async ({ page }) => {
  const id = await sendPrompt(page);
  await page.evaluate(() => {
    const scroller = document.querySelector("#chatScroll");
    scroller.scrollTop = 420;
    scroller.dispatchEvent(new Event("scroll"));
  });
  const before = await page.locator("#chatScroll").evaluate((node) => node.scrollTop);
  await emitToken(page, id, "A long streamed response. ".repeat(80));
  await expect(page.locator(".body.stream")).toContainText("A long streamed response");
  await expect.poll(() => page.locator("#chatScroll").evaluate((node) => node.scrollTop)).toBe(before);

  const jump = page.getByRole("button", { name: "Jump to latest response" });
  await expect(jump).toBeVisible();
  await jump.click();
  await expect(jump).toBeHidden();
  await expect.poll(() => page.locator("#chatScroll").evaluate((node) => (
    node.scrollHeight - node.scrollTop - node.clientHeight
  ))).toBeLessThan(3);

  await emitToken(page, id, " More output.".repeat(40));
  await expect.poll(() => page.locator("#chatScroll").evaluate((node) => (
    node.scrollHeight - node.scrollTop - node.clientHeight
  ))).toBeLessThan(3);
});

test("completion does not yank a reader who scrolled upward", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "Streaming answer. ".repeat(100));
  await page.evaluate(() => {
    const scroller = document.querySelector("#chatScroll");
    scroller.scrollTop = 360;
    scroller.dispatchEvent(new Event("scroll"));
  });
  const before = await page.locator("#chatScroll").evaluate((node) => node.scrollTop);
  await finishRequest(page, id, { status: "answered", answer: "Final answer. ".repeat(120), receipt: {} });
  await expect(page.locator(".response-shell")).toBeVisible();
  await expect.poll(() => page.locator("#chatScroll").evaluate((node) => node.scrollTop)).toBe(before);
});
