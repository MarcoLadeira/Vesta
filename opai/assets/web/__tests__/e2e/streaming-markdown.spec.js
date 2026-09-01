import { test, expect } from "@playwright/test";

import { emitToken, finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("provider updates render as recent blocks and fold earlier progress", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "First update.");
  await emitToken(page, id, "Second update.", { blockStart: true });
  await emitToken(page, id, "Third update.", { blockStart: true });
  await emitToken(page, id, "Fourth update.", { blockStart: true });
  await emitToken(page, id, "Fifth update.", { blockStart: true });

  await expect(page.locator(".stream-recent > .stream-block")).toHaveCount(3);
  await expect(page.locator(".stream-earlier summary")).toHaveText("Earlier progress (2)");
  await expect(page.locator(".stream-recent > .stream-block").last()).toContainText("Fifth update.");
  await expect(page.locator(".stream-earlier-body > .stream-block")).toHaveCount(2);
  await page.locator(".stream-earlier summary").click();
  await expect(page.locator(".stream-earlier-body")).toContainText("First update.");
  await expect.poll(() => page.evaluate(() => window.__opai.state.streamedText)).toBe(
    "First update.\n\nSecond update.\n\nThird update.\n\nFourth update.\n\nFifth update.",
  );
});

test("ordinary token chunks stay in one active block", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "Token one ");
  await emitToken(page, id, "and token two.");

  await expect(page.locator(".stream-block")).toHaveCount(1);
  await expect(page.locator(".stream-block")).toHaveText("Token one and token two.");
  await expect(page.locator(".stream-earlier")).toBeHidden();
});

test("a large provider chunk is progressively revealed with a fading tail", async ({ page }) => {
  const id = await sendPrompt(page);
  const text = "Smooth streaming writes each word in front of the user instead of dropping a completed paragraph into the conversation. ".repeat(4).trim();
  const firstFrame = await page.evaluate(async ({ requestId, value }) => {
    window.__mock.emitToken(requestId, value);
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const body = document.querySelector(".body.stream");
    const tail = body.querySelector(".stream-text-reveal");
    return {
      text: body.textContent,
      animation: tail ? getComputedStyle(tail).animationName : "none",
      opacity: tail ? Number(getComputedStyle(tail).opacity) : 1,
    };
  }, { requestId: id, value: text });

  expect(firstFrame.text.length).toBeGreaterThan(0);
  expect(firstFrame.text.length).toBeLessThan(text.length);
  expect(firstFrame.animation).toBe("streamTextReveal");
  expect(firstFrame.opacity).toBeLessThan(1);
  await expect(page.locator(".body.stream")).toHaveText(text, { timeout: 2_000 });
  expect(await page.evaluate(() => window.__opai.state.streamRenders)).toBeGreaterThan(1);
});

test("an immediate terminal reply lets the visible stream catch up before final presentation", async ({ page }) => {
  const id = await sendPrompt(page);
  const text = "The final response still arrives smoothly even when the provider returns the whole answer at once. ".repeat(5).trim();
  const firstFrame = await page.evaluate(async ({ requestId, value }) => {
    window.__mock.emitToken(requestId, value);
    window.__mock.emitReply(requestId, { status: "answered", answer: value, receipt: {} });
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const body = document.querySelector(".body.stream");
    return body ? body.textContent : null;
  }, { requestId: id, value: text });

  expect(firstFrame).not.toBeNull();
  expect(firstFrame.length).toBeLessThan(text.length);
  await expect(page.locator(".response-shell")).toContainText(text, { timeout: 4_000 });
  await expect(page.locator(".body.streaming")).toHaveCount(0);
});

test("a long final reply without token events is progressively revealed", async ({ page }) => {
  const id = await sendPrompt(page);
  const text = "OPai keeps the conversation moving by writing a long response into view instead of making the full paragraph suddenly appear. ".repeat(5).trim();
  const firstFrame = await page.evaluate(async ({ requestId, value }) => {
    window.__mock.emitReply(requestId, { status: "answered", answer: value, receipt: {} });
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const body = document.querySelector(".body.stream");
    return body ? body.textContent : null;
  }, { requestId: id, value: text });

  expect(firstFrame).not.toBeNull();
  expect(firstFrame.length).toBeGreaterThan(0);
  expect(firstFrame.length).toBeLessThan(text.length);
  await expect(page.locator(".response-shell")).toContainText(text, { timeout: 5_000 });
  expect(await page.evaluate(() => window.__opai.state.streamRenders)).toBeGreaterThan(2);
});

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

test("the live caret follows the active block's final text", async ({ page }) => {
  const id = await sendPrompt(page);
  await emitToken(page, id, "Caret stays with this update.");
  await expect(page.locator(".body.stream.streaming > :last-child")).toBeVisible();
  const caret = await page.evaluate(() => {
    const body = document.querySelector(".body.stream.streaming");
    const tail = body.lastElementChild;
    return {
      body: getComputedStyle(body, "::after").content,
      tail: getComputedStyle(tail, "::after").content,
    };
  });
  expect(caret.body === "none" || caret.body === "normal").toBeTruthy();
  expect(caret.tail).not.toBe("none");
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
    const root = document.querySelector(".body.stream p");
    const target = "stable selection";
    const nodes = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const fullText = nodes.map((node) => node.textContent).join("");
    const start = fullText.indexOf(target);
    let offset = 0;
    let startNode = null, startOffset = 0, endNode = null, endOffset = 0;
    for (const node of nodes) {
      const next = offset + node.textContent.length;
      if (!startNode && start >= offset && start <= next) {
        startNode = node;
        startOffset = start - offset;
      }
      if (start + target.length >= offset && start + target.length <= next) {
        endNode = node;
        endOffset = start + target.length - offset;
        break;
      }
      offset = next;
    }
    const range = document.createRange();
    range.setStart(startNode, startOffset);
    range.setEnd(endNode, endOffset);
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
    const tail = document.querySelector(".body.stream.streaming > :last-child");
    return getComputedStyle(tail, "::after").animationName;
  });
  expect(anim === "none" || anim === "" || anim == null).toBeTruthy();
});

test("the explicit motion override wins over the OS reduced-motion setting", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => { document.documentElement.dataset.motion = "off"; });
  const id = await sendPrompt(page);
  await emitToken(page, id, "motion is explicitly enabled");
  await expect(page.locator(".body.stream.streaming")).toBeVisible();
  const anim = await page.evaluate(() => getComputedStyle(document.querySelector(".body.stream.streaming > :last-child"), "::after").animationName);
  expect(anim).toBe("caretBlink");
});
