import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";

const structuredPresentation = {
  schema_version: 1,
  run: { state: "completed", label: "Completed", reason: "The requested change was verified." },
  evidence: { verification: { applicable: true, verdict: "verified" } },
  tests: { status: "passed", passed: 3, failed: 0, skipped: 0 },
  changes: { summary: { files: 2, additions: 12, deletions: 4 } },
  activity: [
    { phase: "implement", status: "completed", message: "Updated two files" },
    { phase: "test", status: "completed", message: "Focused tests passed" },
  ],
};

/* Three things the chat surface was missing.
 *
 * 1. There was no way to copy an answer. Selecting long markdown by hand in a
 *    scrolling pane is exactly the interaction a "copy" button exists to avoid.
 *
 * 2. "Recent chats" listed prompt *strings*. Selecting one re-typed the
 *    question into the composer and discarded the answer — the sidebar's name
 *    described something the app did not have.
 *
 * 3. The prompt list was genuinely useful, just in the wrong place: it is shell
 *    history, so it now lives on the composer's Up arrow.
 */

test("an answer can be copied", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "explain the router");
  await finishRequest(page, id, { answer: "The router picks the cheapest capable model." });

  await page.locator('.msg [data-a="copy-answer"]').last().click();
  const copied = await page.evaluate(() => window.__mock.copiedTexts);
  expect(copied).toContain("The router picks the cheapest capable model.");
});

test("copying yields the markdown the model wrote, not rendered HTML", async ({ page }) => {
  // Pasting `<p>`/`<pre>` soup into an editor or an issue is useless; the raw
  // text is what the user came for.
  await openApp(page);
  const id = await sendPrompt(page, "show me code");
  await finishRequest(page, id, { answer: "Use `router.pick()`:\n\n```py\nrouter.pick()\n```" });

  await page.locator('.msg [data-a="copy-answer"]').last().click();
  const copied = await page.evaluate(() => window.__mock.copiedTexts);
  expect(copied.at(-1)).toContain("```py");
  expect(copied.at(-1)).not.toContain("<pre");
});

test("the copy control is reachable without a mouse", async ({ page }) => {
  // It is revealed on hover, which is nothing at all for keyboard users.
  await openApp(page);
  const id = await sendPrompt(page, "explain the router");
  await finishRequest(page, id, { answer: "An answer." });

  const copy = page.locator('.msg [data-a="copy-answer"]').last();
  await copy.focus();
  await expect(copy).toBeFocused();
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => window.__mock.copiedTexts)).toContain("An answer.");
});

test("the sidebar lists saved chats, not prompts", async ({ page }) => {
  await openApp(page);
  const recents = page.locator("#recents .recent");
  await expect(recents.first()).toContainText("How does routing work?");
  // The prompt strings are history for the composer now — they must not be
  // masquerading as chats in the sidebar.
  await expect(page.locator("#recents")).not.toContainText("third prompt");
});

test("selecting a saved chat restores the conversation, not just the question", async ({ page }) => {
  // The actual bug: this used to re-type the prompt and lose the answer.
  await openApp(page, {
    conversationTranscripts: {
      c2: {
        id: "c2",
        title: "How does routing work?",
        updated_at: "2026-08-02T10:00:00+00:00",
        messages: [
          { role: "user", text: "How does routing work?", status: "complete" },
          { role: "assistant", text: "Auto scores each capable model.", status: "complete" },
        ],
      },
    },
  });

  await page.locator("#recents .recent").first().click();
  const thread = page.locator("#thread");
  await expect(thread).toContainText("How does routing work?");
  await expect(thread).toContainText("Auto scores each capable model.");
  // And it did not silently refill the composer the old way.
  await expect(page.locator("#input")).toHaveValue("");
});

test("live and archived assistants use the same structured evidence renderer", async ({ page }) => {
  await openApp(page, {
    conversationTranscripts: {
      c2: {
        id: "c2",
        title: "How does routing work?",
        updated_at: "2026-08-02T10:00:00+00:00",
        messages: [
          { role: "user", text: "Implement it", status: "complete" },
          { role: "assistant", text: "The implementation is ready.", status: "complete", presentation: structuredPresentation },
        ],
      },
    },
  });
  const id = await sendPrompt(page, "Implement it live");
  await finishRequest(page, id, {
    answer: "The implementation is ready.",
    presentation: structuredPresentation,
  });

  const live = page.locator(".msg.bot").last();
  const liveEvidence = await live.locator(".evidence-item").allInnerTexts();
  const liveVerdict = await live.locator(".completion-verdict").innerText();
  await expect(live.locator(".gen-toggle.done")).toContainText("Work log (2)");

  await page.locator("#recents .recent", { hasText: "How does routing work?" }).click();
  const archived = page.locator(".msg", { has: page.locator(".evidence-bar") }).first();
  expect(await archived.locator(".evidence-item").allInnerTexts()).toEqual(liveEvidence);
  expect(await archived.locator(".completion-verdict").innerText()).toBe(liveVerdict);
  await expect(archived.locator(".gen-toggle.done")).toContainText("Work log (2)");
  await expect(archived.locator(".body")).toContainText("The implementation is ready");
});

test("a reopened chat says it is history", async ({ page }) => {
  // Otherwise an old transcript is indistinguishable from the live thread, and
  // the next message looks like it will continue this chat.
  await openApp(page, {
    conversationTranscripts: {
      c2: { id: "c2", title: "t", updated_at: "2026-08-02T10:00:00+00:00", messages: [{ role: "user", text: "q", status: "complete" }] },
    },
  });
  await page.locator("#recents .recent").first().click();
  await expect(page.locator(".conv-note")).toContainText("saved chat");
});

test("a chat that can no longer be opened says so and stops being offered", async ({ page }) => {
  await openApp(page, { conversationTranscripts: {} });
  await page.locator("#recents .recent").first().click();
  await expect(page.locator("#toast")).toContainText("no longer available");
  expect(await page.evaluate(() => window.__mock.conversationLists)).toBeGreaterThan(0);
});

test("Up recalls the previous prompt, like a shell", async ({ page }) => {
  await openApp(page);
  await page.click("#input");
  await page.press("#input", "ArrowUp");
  await expect(page.locator("#input")).toHaveValue("third prompt");
  await page.press("#input", "ArrowUp");
  await expect(page.locator("#input")).toHaveValue("second prompt");
});

test("Down walks back and restores what was being typed", async ({ page }) => {
  // Losing an in-progress draft to a history walk would make the feature a
  // trap rather than a convenience.
  await openApp(page);
  await page.fill("#input", "half-written thought");
  await page.press("#input", "ArrowUp");
  await expect(page.locator("#input")).toHaveValue("third prompt");
  await page.press("#input", "ArrowDown");
  await expect(page.locator("#input")).toHaveValue("half-written thought");
});

test("Escape abandons the history walk", async ({ page }) => {
  await openApp(page);
  await page.fill("#input", "my draft");
  await page.press("#input", "ArrowUp");
  await page.press("#input", "Escape");
  await expect(page.locator("#input")).toHaveValue("my draft");
});

test("Up past the oldest prompt stays there", async ({ page }) => {
  await openApp(page);
  await page.click("#input");
  for (let i = 0; i < 6; i += 1) await page.press("#input", "ArrowUp");
  await expect(page.locator("#input")).toHaveValue("first prompt");
});

test("Up still moves the caret inside a multi-line draft", async ({ page }) => {
  // The correctness constraint: stealing Up here would make the composer
  // unusable for exactly the long prompts most worth recalling.
  await openApp(page);
  await page.fill("#input", "line one\nline two");
  await page.press("#input", "ArrowUp");
  await expect(page.locator("#input")).toHaveValue("line one\nline two");
});

test("typing adopts the recalled prompt as your own draft", async ({ page }) => {
  await openApp(page);
  await page.click("#input");
  await page.press("#input", "ArrowUp");
  await page.type("#input", " extra");
  await page.press("#input", "ArrowDown");
  // Down no longer walks history — the text is the user's now.
  await expect(page.locator("#input")).toHaveValue("third prompt extra");
});

test("sending restarts history at the newest prompt", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "brand new prompt");
  await finishRequest(page, id, { answer: "done" });
  await page.click("#input");
  await page.press("#input", "ArrowUp");
  await expect(page.locator("#input")).toHaveValue("brand new prompt");
});

test("clearing history removes saved chats from the sidebar too", async ({ page }) => {
  // The privacy control promises to delete this workspace's history. Leaving
  // the transcripts listed would mean the user asked to delete their chats and
  // still saw them — caught by folder.spec before this test existed.
  await openApp(page);
  await expect(page.locator("#recents .recent").first()).toContainText("How does routing work?");
  await page.click("#clearRecents");
  await page.locator("#recents .inline-confirm").locator('[data-ic="ok"]').click();
  await expect(page.locator("#recents")).toContainText("No saved chats yet");
  await expect(page.locator("#recents")).not.toContainText("How does routing work?");
});

test("clearing history keeps the current in-flight chat working", async ({ page }) => {
  await openApp(page, {
    clearRecentsResult: {
      ok: true,
      recents: [],
      conversations: [
        { id: "live-1", title: "Keep this work", message_count: 1, updated_at: "2026-08-28" },
      ],
    },
  });
  const id = await sendPrompt(page, "Keep this work");

  await page.click("#clearRecents");
  await page.locator("#recents .inline-confirm").locator('[data-ic="ok"]').click();

  await expect(page.locator("#thread")).toContainText("Keep this work");
  await expect(page.locator(".gen-stop")).toBeVisible();
  await expect(page.locator("body")).toHaveClass(/ai-working/);
  await expect(page.locator("#recents")).toContainText("Keep this work");
  await expect(page.locator("#recents")).not.toContainText("How does routing work?");

  await finishRequest(page, id, { answer: "Current work finished." });
  await expect(page.locator("#thread")).toContainText("Current work finished.");
  await expect(page.locator("body")).not.toHaveClass(/ai-working/);
});

test("a restored transcript arrives as one thing, not twenty-five", async ({ page }) => {
  // Opening a saved chat used to append each message on its own: a forced
  // synchronous layout per message (read scrollTop, write, read scrollHeight)
  // and a `msgIn` entry animation per message, all at once. Measured on a
  // 25-message chat that cost a 67ms frame and six frames over 32ms.
  //
  // `msgIn` means "this just arrived". Twenty-five of them at once says it
  // twenty-five times about a transcript that already existed, which is the
  // stutter. The thread fades once instead.
  const messages = [];
  for (let i = 0; i < 12; i += 1) {
    messages.push({ role: "user", text: `Question ${i}`, status: "complete" });
    messages.push({ role: "assistant", text: `Answer ${i}.`, status: "complete" });
  }
  await openApp(page, {
    conversationTranscripts: {
      c2: { id: "c2", title: "How does routing work?", updated_at: "2026-08-02", messages },
    },
  });

  await page.locator('.recent[data-conversation-id="c2"]').click();
  await expect(page.locator("#thread .msg").first()).toBeVisible();

  const animations = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#thread .msg"))
      .map((el) => getComputedStyle(el).animationName));

  expect(animations.length).toBeGreaterThan(20);
  // Not one restored message animates itself in.
  expect(new Set(animations)).toEqual(new Set(["none"]));
});

test("a live message still animates in", async ({ page }) => {
  // The batching must not leak into normal use: a message that really has just
  // arrived should still be seen to arrive.
  await openApp(page);
  await page.locator("#input").fill("hello");
  await page.locator("#send").click();
  await expect(page.locator("#thread .msg").first()).toBeVisible();

  const name = await page.evaluate(() =>
    getComputedStyle(document.querySelector("#thread .msg")).animationName);
  expect(name).toBe("msgIn");
});
