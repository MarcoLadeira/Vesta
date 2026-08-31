import { test, expect } from "@playwright/test";

import { emitToken, finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("streamed markdown renders formatted blocks progressively", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "# Heading\n\nSome **bold** text.");
  const body = page.locator(".body.stream");
  await expect(body.locator("h1")).toHaveText("Heading");
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

test("fenced languages are preserved beside the copy action", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "```typescript\nconst ready: boolean = true;\n```\n");
  const pre = page.locator(".body.stream pre");
  await expect(pre.locator("code")).toHaveClass(/language-typescript/);
  await expect(pre.locator(".code-language")).toHaveText("typescript");
  await expect(pre.locator(".code-copy")).toHaveAttribute("aria-label", "Copy code");
});

test("GFM tables and nested Markdown structures remain semantic while streaming", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, [
    "> Verified **locally**",
    "",
    "- parent",
    "  - child",
    "",
    "| Check | Result |",
    "| --- | --- |",
    "| Unit | Passed |",
  ].join("\n"));
  const body = page.locator(".body.stream");
  await expect(body.locator("blockquote strong")).toHaveText("locally");
  await expect(body.locator("ul ul li")).toHaveText("child");
  await expect(body.locator("table thead th")).toHaveCount(2);
  await expect(body.locator("table tbody td")).toHaveCount(2);
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

test("hostile link, image, and attribute payloads never become active DOM", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, [
    "[js](javascript:alert(1)) [data](data:text/html,pwned) [file](file:///etc/passwd)",
    "![pixel](https://attacker.invalid/pixel.png)",
    '<a href="https://attacker.invalid" onclick="alert(1)">raw</a>',
    "[safe](https://example.com)",
  ].join("\n\n"));
  const body = page.locator(".body.stream");
  await expect(body.locator("img, script, [onclick]")).toHaveCount(0);
  await expect(body.locator("a")).toHaveCount(1);
  const safeLink = body.locator("a[data-ext='1']");
  await expect(safeLink).toHaveAttribute("href", /^https:\/\/example\.com\/?$/);
  await safeLink.click();
  await expect.poll(() => page.evaluate(() => window.__mock.externalUrls)).toEqual(["https://example.com/"]);
});

test("the streaming class (and its caret) is gone once the answer finalizes", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "partial");
  await expect(page.locator(".body.stream")).toHaveClass(/streaming/);
  await finishRequest(page, id, { status: "answered", answer: "final answer", receipt: {} });
  await expect(page.locator(".body.streaming")).toHaveCount(0);
});

test("a token burst is frame-batched and keeps the complete answer", async ({ page }) => {
  const id = await sendPrompt(page);
  const renders = await page.evaluate(async (requestId) => {
    const before = window.__opai.state.streamRenders;
    for (let i = 0; i < 200; i++) window.__mock.emitToken(requestId, String(i % 10));
    await new Promise((resolve) => setTimeout(resolve, 80));
    return window.__opai.state.streamRenders - before;
  }, id);
  expect(renders).toBeGreaterThan(0);
  expect(renders).toBeLessThanOrEqual(4);
  await expect(page.locator(".body.stream")).toHaveText("0123456789".repeat(20));
});

test("stream rendering preserves a user's text selection", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "Keep this stable selection while more text arrives.");
  await expect(page.locator(".body.stream")).toContainText("stable selection");
  await page.evaluate(() => {
    const node = document.querySelector(".body.stream p").firstChild;
    const start = node.textContent.indexOf("stable selection");
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, start + "stable selection".length);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await emitToken(page, id, " Additional output.");
  await expect.poll(() => page.evaluate(() => getSelection().toString())).toBe("stable selection");
});

test("a malformed terminal answer cannot be hidden by partial streamed text", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "Partial text");
  await finishRequest(page, id, { status: "answered", answer: { unexpected: true }, receipt: {} });
  await expect(page.locator(".error-card")).toContainText("unexpected response shape");
  await expect(page.locator(".response-shell")).toHaveCount(0);
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

test("the explicit motion override wins over the OS reduced-motion setting", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => { document.documentElement.dataset.motion = "off"; });
  const id = await sendPrompt(page);
  await emitToken(page, id, "motion is explicitly enabled");
  await expect(page.locator(".body.stream.streaming")).toBeVisible();
  const anim = await page.evaluate(() => getComputedStyle(document.querySelector(".body.stream.streaming"), "::after").animationName);
  expect(anim).toBe("caretBlink");
});
