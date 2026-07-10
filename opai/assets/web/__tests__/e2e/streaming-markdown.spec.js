import { test, expect } from "@playwright/test";

import { emitToken, finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("streamed markdown renders formatted blocks progressively", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "# Heading\n\nSome **bold** text.");
  const body = page.locator(".body.stream");
  await expect(body.locator("h2")).toHaveText("Heading"); // # -> h2 in mdToHtml
  await expect(body.locator("strong")).toHaveText("bold");
  await expect(body).toHaveClass(/streaming/);
});

test("a completed code fence renders as a code block with a copy button", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "Run:\n\n```\nnpm test\n```\n");
  const pre = page.locator(".body.stream pre");
  await expect(pre).toBeVisible();
  await expect(pre.locator("code")).toContainText("npm test");
  await expect(pre.locator(".code-copy")).toHaveAttribute("aria-label", "Copy code");
});

test("the code copy button copies the block's text", async ({ page }) => {
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "answered", answer: "```\nls -la\n```", receipt: {} });
  const btn = page.locator(".msg.bot pre .code-copy");
  await expect(btn).toHaveCount(1);
  await btn.click({ force: true });
  await expect(page.locator("#toast")).toContainText("Code copied");
  await expect(btn).toHaveText("Copied");
});

test("an unclosed fence never breaks into raw HTML mid-stream", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "opening a block:\n\n```\nline one");
  const body = page.locator(".body.stream");
  await expect(body).toContainText("line one"); // shown, gracefully, as literal until closed
  await expect(body.locator("pre")).toHaveCount(0); // not yet a real block
  // Now it completes and snaps to a real code block.
  await emitToken(page, id, "\nline two\n```\n");
  await expect(body.locator("pre code")).toContainText("line one");
  await expect(body.locator("pre code")).toContainText("line two");
});

test("HTML/script in a streamed answer renders inert (escape-first)", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "<img src=x onerror=alert(1)> <script>alert(2)</script> **safe**");
  const body = page.locator(".body.stream");
  await expect(body.locator("img")).toHaveCount(0);
  await expect(body.locator("script")).toHaveCount(0);
  await expect(body).toContainText("onerror=alert(1)"); // rendered as visible text
  await expect(body.locator("strong")).toHaveText("safe"); // markdown still works
});

test("the streaming class (and its caret) is gone once the answer finalizes", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "partial");
  await expect(page.locator(".body.stream")).toHaveClass(/streaming/);
  await finishRequest(page, id, { status: "answered", answer: "final answer", receipt: {} });
  await expect(page.locator(".body.streaming")).toHaveCount(0);
});

test("under reduced motion the streaming caret does not animate", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const id = await sendPrompt(page);
  await emitToken(page, id, "streaming under reduced motion");
  await expect(page.locator(".body.stream.streaming")).toBeVisible(); // wait for the rAF render
  const anim = await page.evaluate(() => {
    const el = document.querySelector(".body.stream.streaming");
    return getComputedStyle(el, "::after").animationName;
  });
  expect(anim === "none" || anim === "" || anim == null).toBeTruthy();
});
