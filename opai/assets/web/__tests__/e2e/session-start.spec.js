import { test, expect } from "@playwright/test";
import { openApp } from "./helpers/app.js";

/**
 * Starting OPai lands you in a new chat.
 *
 * There used to be a gate here: a card that stopped everything and asked
 * whether you wanted to resume your previous session, with the composer
 * disabled until you answered. It was replaced by a richer version of itself
 * and then removed altogether, because standing between the user and a new
 * chat is the wrong trade whatever the card looks like.
 *
 * What is left is the honest small version of the same idea: the sidebar says
 * which chat you were last in, and you go there yourself if you want to.
 *
 * These tests exist because "no gate" is a behaviour, not an absence -- boot
 * with a resumable session present has to reach a usable composer, and nothing
 * of that session may be restored into the thread on the way.
 */

const resume = {
  available: true,
  requires_choice: true,
  thread: {
    task_id: "task-313",
    messages: [
      { role: "user", text: "Continue the index work", status: "complete" },
      { role: "assistant", text: "Focused tests are green.", status: "complete" },
    ],
    changed_files: ["opai/gui_web.py"],
  },
  workflow: { phase: "testing", message: "Focused tests passed" },
  checkpoint: { id: "cp-313", completion_state: "interrupted" },
};

test("a resumable session does not stand between you and a new chat", async ({ page }) => {
  await openApp(page, { boot: { resume } });

  // No gate, and nothing of the saved session in the thread.
  await expect(page.locator(".resume-card")).toHaveCount(0);
  await expect(page.locator("#thread .msg")).toHaveCount(0);
  await expect(page.locator("#empty")).toBeVisible();

  // A usable composer, immediately.
  await expect(page.locator("#input")).toBeEnabled();

  // And nothing was restored or cleared behind the user's back: the saved work
  // is still there to be opened from the sidebar.
  expect(await page.evaluate(() => window.__mock.resumedSessions)).toBe(0);
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(0);
});

test("a fresh chat still opens in the dark stage, so the composer still flies", async ({ page }) => {
  // Removing the gate must not cost the stage animation. Before, the gate's
  // card was appended at boot, and appendMsg lights the room -- so it is worth
  // pinning that boot now ends up dark, with a lift measured to fly from.
  await openApp(page, { boot: { resume } });

  await expect(page.locator("#app")).toHaveAttribute("data-stage", "dark");
  const lift = await page.evaluate(() =>
    parseFloat(getComputedStyle(document.getElementById("app")).getPropertyValue("--stage-lift")));
  expect(lift).toBeLessThan(0);
});

test("the sidebar says which chat you were last in", async ({ page }) => {
  await openApp(page);

  const rows = page.locator("#recents .recent[data-conversation-id]");
  // list_conversations sorts by updated_ts descending, so the first row is the
  // chat you were last in -- and only that one is tagged.
  await expect(rows.first()).toHaveClass(/is-previous/);
  await expect(rows.first().locator(".recent-tag")).toHaveText("Previous chat");
  await expect(page.locator("#recents .recent-tag")).toHaveCount(1);
});

test("the tag does not eat the chat's title", async ({ page }) => {
  // The title and the tag share one narrow row. The first version of this
  // shipped without the flex row at all, and the button's own ellipsis
  // truncated the two of them together into "How does routing work?Previou…".
  //
  // The title here is long enough that the row genuinely has to compress it.
  // Worth saying what this does *not* prove: I first wrote it believing
  // `min-width: 0` was load-bearing, and it passed with that removed --
  // correctly, because `overflow: hidden` already zeroes a flex item's
  // automatic minimum size. The guard is the flex row plus the ellipsis.
  await openApp(page, {
    boot: {
      conversations: [
        { id: "c9", title: "Refactor the authentication flow end to end", message_count: 6, updated_at: "2026-08-02" },
        { id: "c1", title: "Explain the budget guard", message_count: 2, updated_at: "2026-08-01" },
      ],
    },
  });

  const row = page.locator("#recents .recent.is-previous");
  const box = await row.boundingBox();
  const tag = await row.locator(".recent-tag").boundingBox();
  const title = await row.locator(".recent-title").boundingBox();
  expect(tag.x + tag.width).toBeLessThanOrEqual(box.x + box.width + 1);
  expect(title.x + title.width).toBeLessThanOrEqual(tag.x + 1);
});

test("opening the previous chat from the sidebar restores it as a transcript", async ({ page }) => {
  await openApp(page, {
    boot: { resume },
    conversationTranscripts: {
      c2: {
        id: "c2", title: "How does routing work?", updated_at: "2026-08-02",
        messages: [
          { role: "user", text: "How does routing work?", status: "complete" },
          { role: "assistant", text: "Auto scores each capable model.", status: "complete" },
        ],
      },
    },
  });

  await page.locator('.recent[data-conversation-id="c2"]').click();

  await expect(page.locator(".msg.user")).toContainText("How does routing work?");
  await expect(page.locator("#thread")).toContainText("Auto scores each capable model");
});

test("New chat clears only through the session bridge", async ({ page }) => {
  await openApp(page, { boot: { resume } });

  await page.locator("#headerNewChat").click();

  await expect(page.locator("#empty")).toBeVisible();
  await expect(page.locator("#input")).toBeEnabled();
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(1);
});

test("a failed clear says so and leaves the composer usable", async ({ page }) => {
  // There is no card to bring the user back to any more, so a composer left
  // disabled after a failed clear would strand them with no explanation.
  await openApp(page, {
    boot: { resume },
    clearSessionResult: {
      ok: false,
      error: {
        code: "SESSION_CLEAR_FAILED",
        userMessage: "OPai could not clear the saved session.",
        recoveryActions: ["Close other OPai windows and try again."],
      },
      resume,
    },
  });

  await page.locator("#headerNewChat").click();

  await expect(page.getByRole("alert")).toContainText("could not clear");
  await expect(page.locator("#input")).toBeEnabled();
  expect(await page.evaluate(() => window.__mock.clearedSessions)).toBe(1);
});

test("a failed clear history keeps the saved chats listed", async ({ page }) => {
  await openApp(page, {
    boot: { resume },
    clearRecentsResult: {
      ok: false,
      error: {
        code: "SESSION_CLEAR_FAILED",
        userMessage: "OPai could not clear the saved history.",
        recoveryActions: ["Try again."],
      },
      resume,
      recents: ["summarize my changes"],
    },
  });

  await page.click("#clearRecents");
  await page.locator("#recents .inline-confirm [data-ic='ok']").click();

  await expect(page.getByRole("alert")).toContainText("could not clear");
  await expect(page.locator("#clearRecents")).toBeVisible();
  expect(await page.evaluate(() => window.__mock.clearedRecents)).toBe(1);
});
