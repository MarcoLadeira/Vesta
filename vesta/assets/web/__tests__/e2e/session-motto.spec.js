import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openNav, sendPrompt } from "./helpers/app.js";

// The empty-state headline is a motto drawn once per launch (vesta/brand.py).
// These specs hold the two things the draw cannot: the line is never swapped
// once it is on screen, and every line fits the room it is drawn into.

// Mirrors brand.VESTA_MOTTOS; tests/test_brand_copy_drift.py keeps them equal.
const VESTA_MOTTOS = [
  "The Living Flame.",
  "By a Light That Never Fails.",
  "Keep the Intelligence Burning.",
  "Intelligence, Always Burning.",
  "The Fire Behind Your Work.",
  "An Undying Intelligence.",
  "Where Intelligence Comes Alive.",
  "Intelligence Comes First.",
  "First, Vesta.",
  "Where Every Task Begins.",
  "Conceive of Vesta as naught but the living flame.",
  "An undying fire is hidden in that temple.",
  "Vesta guards it, because she sees all things by her light that never fails.",
  "Guardian of Fire.",
  "She Occupies the First Place.",
];
const LONGEST = VESTA_MOTTOS.reduce((a, b) => (b.length > a.length ? b : a));

// Every headline the page has shown, in order, from the first parsed frame.
// A motto that is ever replaced shows up here as a second entry.
async function recordHeadlines(page) {
  await page.addInitScript(() => {
    const seen = (window.__headlines = []);
    new MutationObserver(() => {
      const h1 = document.querySelector("#empty h1");
      const text = h1 ? h1.textContent.trim() : "";
      if (!seen.length && !text) return;
      if (seen[seen.length - 1] !== text) seen.push(text);
    }).observe(document, { childList: true, subtree: true, characterData: true });
  });
}

test("the motto the first frame carries is kept, not swapped for the payload's", async ({ page }) => {
  // The launch copy of index.html is stamped with this run's motto
  // (gui_web._stamp_empty_title). Even if the payload names a different one,
  // what the user already read stays.
  await page.route("**/vesta/assets/web/index.html", async (route) => {
    const response = await route.fetch();
    const body = (await response.text()).replace("<h1></h1>", "<h1>First, Vesta.</h1>");
    await route.fulfill({ response, body });
  });
  await recordHeadlines(page);
  await openApp(page, { boot: { brand: { emptyTitle: "The Living Flame." } } });

  await expect(page.locator("#empty h1")).toHaveText("First, Vesta.");
  expect(await page.evaluate(() => window.__headlines)).toEqual(["First, Vesta."]);
});

test("the motto holds for the whole session", async ({ page }) => {
  await recordHeadlines(page);
  await openApp(page);
  const h1 = page.locator("#empty h1");
  await expect(h1).toHaveText("The Living Flame.");

  // Everything that re-renders the chat surface without relaunching Vesta.
  await openNav(page, "Settings");
  await openNav(page, "Chat");
  const id = await sendPrompt(page, "a turn in between");
  await finishRequest(page, id);
  await page.locator("#headerNewChat").click();
  await expect(page.locator(".msg")).toHaveCount(0);
  await page.setViewportSize({ width: 1040, height: 700 });
  await page.setViewportSize({ width: 1340, height: 880 });

  await expect(h1).toHaveText("The Living Flame.");
  expect(await page.evaluate(() => window.__headlines)).toEqual(["The Living Flame."]);
});

// The window's default and minimum sizes (gui_web.MainWindow), then the
// narrow layouts the web UI also supports.
for (const viewport of [
  { width: 1340, height: 880 },
  { width: 1040, height: 700 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
]) {
  test(`every motto fits the empty state at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize(viewport);
    // The longest arrives the real way, through the boot payload.
    await openApp(page, { boot: { brand: { emptyTitle: LONGEST } } });
    await expect(page.locator("#empty h1")).toHaveText(LONGEST);

    for (const motto of VESTA_MOTTOS) {
      const layout = await page.evaluate(async (text) => {
        const h1 = document.querySelector("#empty h1");
        h1.textContent = text;
        // The stage re-centres the group on resize; ask it to, then let it land.
        window.dispatchEvent(new Event("resize"));
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

        const empty = document.querySelector("#empty");
        const style = getComputedStyle(empty);
        const box = empty.getBoundingClientRect();
        const inner = {
          left: box.left + parseFloat(style.paddingLeft),
          right: box.right - parseFloat(style.paddingRight),
        };
        const range = document.createRange();
        range.selectNodeContents(h1);
        // Width of each rendered line, and how even they are (shortest / longest).
        const measureLines = () => {
          const lines = new Map();
          for (const rect of range.getClientRects()) {
            const top = Math.round(rect.top);
            lines.set(top, (lines.get(top) || 0) + rect.width);
          }
          const widths = [...lines.values()];
          return { count: widths.length, balance: Math.min(...widths) / Math.max(...widths) };
        };
        const lines = measureLines();
        // The same line under plain greedy wrapping, for comparison.
        h1.style.textWrap = "wrap";
        const greedy = measureLines();
        h1.style.textWrap = "";
        const title = h1.getBoundingClientRect();
        const rect = (selector) => document.querySelector(selector).getBoundingClientRect();
        const scroll = document.querySelector("#chatScroll");
        return {
          pageOverflows: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          threadOverflows: scroll.scrollWidth > scroll.clientWidth,
          titleOverflows: h1.scrollWidth > h1.clientWidth,
          textLeft: Math.min(...[...range.getClientRects()].map((r) => r.left)),
          textRight: Math.max(...[...range.getClientRects()].map((r) => r.right)),
          inner,
          offCentre: Math.abs((title.left + title.right) / 2 - (inner.left + inner.right) / 2),
          textAlign: getComputedStyle(h1).textAlign,
          fontSize: parseFloat(getComputedStyle(h1).fontSize),
          minFontSize: parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--type-title")),
          lineCount: lines.count,
          lineBalance: lines.balance,
          greedyLineCount: greedy.count,
          greedyLineBalance: greedy.balance,
          markTop: rect("#empty .empty-mark").top,
          mainTop: rect(".main").top,
          titleTop: title.top,
          titleBottom: title.bottom,
          markBottom: rect("#empty .empty-mark").bottom,
          chipsTop: rect("#chips").top,
          chipsBottom: rect("#chips").bottom,
          composerTop: rect(".composer-wrap .composer").top,
        };
      }, motto);

      const where = `${motto} at ${viewport.width}px`;
      // Nothing leaves the room, sideways or out of the headline's own box.
      expect(layout.pageOverflows, where).toBe(false);
      expect(layout.threadOverflows, where).toBe(false);
      expect(layout.titleOverflows, where).toBe(false);
      expect(layout.textLeft, where).toBeGreaterThanOrEqual(layout.inner.left - 1);
      expect(layout.textRight, where).toBeLessThanOrEqual(layout.inner.right + 1);
      // Centred like the line it replaced, at a size that was not shrunk to fit.
      expect(layout.textAlign, where).toBe("center");
      expect(layout.offCentre, where).toBeLessThanOrEqual(1);
      expect(layout.fontSize, where).toBeGreaterThanOrEqual(layout.minFontSize);
      // A motto that wraps does so evenly: never more lines than plain wrapping,
      // never less even. (Some cannot be very even -- "Intelligence" is one word.)
      expect(layout.lineCount, where).toBe(layout.greedyLineCount);
      expect(layout.lineBalance, where).toBeGreaterThanOrEqual(layout.greedyLineBalance - 0.01);
      // The longest has room to split well, and does: no line is a stray tail.
      if (motto === LONGEST) {
        expect(layout.lineCount, where).toBeGreaterThan(1);
        expect(layout.lineBalance, where).toBeGreaterThan(0.75);
      }
      // The group still stacks: mascot, headline, chips, composer, none clipped.
      expect(layout.markTop, where).toBeGreaterThanOrEqual(layout.mainTop);
      expect(layout.markBottom, where).toBeLessThanOrEqual(layout.titleTop + 1);
      expect(layout.titleBottom, where).toBeLessThanOrEqual(layout.chipsTop + 1);
      expect(layout.chipsBottom, where).toBeLessThanOrEqual(layout.composerTop + 1);
    }
  });
}
