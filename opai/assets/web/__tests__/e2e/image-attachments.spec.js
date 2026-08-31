import { test, expect } from "@playwright/test";

import { openApp } from "./helpers/app.js";

const seen = { useInnerText: true };

// A one-pixel PNG, as bytes, so the paste and drop paths carry something a
// real host would accept rather than a string that only looks like an image.
const PNG_BYTES = [
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
  0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
];

/**
 * Fire a real paste or drop carrying a real File.
 *
 * Built in the page rather than passed in, because a DataTransfer cannot cross
 * the Playwright boundary — and because the thing under test is precisely how
 * the composer reads one.
 */
async function dispatchImage(page, { event, selector, kind }) {
  await page.evaluate(
    ({ event, selector, kind, bytes }) => {
      const file = new File([new Uint8Array(bytes)], "screenshot.png", { type: "image/png" });
      const transfer = new DataTransfer();
      if (kind === "items-only") {
        // A clipboard screenshot: present in `items`, absent from `files`.
        Object.defineProperty(transfer, "items", {
          value: [{ kind: "file", type: "image/png", getAsFile: () => file }],
        });
        Object.defineProperty(transfer, "files", { value: [] });
      } else {
        transfer.items.add(file);
      }
      const target = document.querySelector(selector);
      const init = { bubbles: true, cancelable: true };
      const evt = event === "paste"
        ? new ClipboardEvent("paste", { ...init, clipboardData: transfer })
        : new DragEvent("drop", { ...init, dataTransfer: transfer });
      target.dispatchEvent(evt);
    },
    { event, selector, kind, bytes: PNG_BYTES },
  );
}

test("pasting a screenshot attaches it and shows the picture, not a filename", async ({ page }) => {
  await openApp(page);
  await dispatchImage(page, { event: "paste", selector: "#input", kind: "items-only" });

  const chip = page.locator(".context-hint.context-image");
  await expect(chip).toHaveCount(1);
  // The preview is the bytes already in hand — nothing is read back from disk.
  await expect(chip.locator("img.context-thumb")).toHaveAttribute("src", /^data:image\/png;base64,/);
  await expect(chip).toContainText("screenshot.png", seen);
  expect(await page.evaluate(() => window.__mock.attachedImages.length)).toBe(1);
});

test("a pasted image travels as an ordinary context reference", async ({ page }) => {
  // The point of the whole design: past the composer there is nothing
  // image-shaped to handle, so no provider needs to know about this feature.
  await openApp(page);
  await dispatchImage(page, { event: "paste", selector: "#input", kind: "items-only" });
  await expect(page.locator(".context-hint.context-image")).toHaveCount(1);

  await page.fill("#input", "what is wrong here?");
  await page.click("#send");
  // The first message waits for the composer to fly down from the centre.
  await expect(page.locator(".gen-stop")).toBeVisible();

  const sent = await page.evaluate(() => window.__mock.lastRequest);
  expect(sent.contextHints).toContain(".opaihub/attachments/shot-1.png");
  expect(sent.text).toContain("@.opaihub/attachments/shot-1.png");
  expect(sent.text).toContain("what is wrong here?");
});

test("dropping an image onto the composer attaches it the same way", async ({ page }) => {
  await openApp(page);
  await dispatchImage(page, { event: "drop", selector: "#composer", kind: "files" });

  await expect(page.locator(".context-hint.context-image")).toHaveCount(1);
  expect(await page.evaluate(() => window.__mock.attachedImages.length)).toBe(1);
});

test("pasting ordinary text is left alone", async ({ page }) => {
  // preventDefault on every paste would break typing. It applies only when an
  // image is actually present.
  await openApp(page);
  await page.evaluate(() => {
    const transfer = new DataTransfer();
    transfer.setData("text/plain", "just words");
    document.querySelector("#input").dispatchEvent(
      new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: transfer }),
    );
  });

  await expect(page.locator(".context-hint.context-image")).toHaveCount(0);
  expect(await page.evaluate(() => window.__mock.attachedImages.length)).toBe(0);
});

test("a host refusal is shown to the user and attaches nothing", async ({ page }) => {
  // The host owns what counts as an image — it sniffs the bytes. Its wording
  // is the wording, so the user reads one explanation rather than two.
  await openApp(page, {
    attachImageReply: { ok: false, error: "That file is not an image OPai can send." },
  });
  await dispatchImage(page, { event: "paste", selector: "#input", kind: "items-only" });

  await expect(page.locator("#toast")).toContainText("not an image OPai can send", seen);
  await expect(page.locator(".context-hint.context-image")).toHaveCount(0);
});

test("an attached image can be removed again", async ({ page }) => {
  await openApp(page);
  await dispatchImage(page, { event: "paste", selector: "#input", kind: "items-only" });
  await expect(page.locator(".context-hint.context-image")).toHaveCount(1);

  await page.locator(".context-hint.context-image .context-remove").click();

  await expect(page.locator(".context-hint.context-image")).toHaveCount(0);
  await page.fill("#input", "hello");
  await page.click("#send");
  // The first message waits for the composer to fly down from the centre.
  await expect(page.locator(".gen-stop")).toBeVisible();
  const sent = await page.evaluate(() => window.__mock.lastRequest);
  expect(sent.contextHints).toEqual([]);
});

test("Attach images uses its own picker, so a screenshot anywhere on disk works", async ({ page }) => {
  // The context picker only accepts paths inside the workspace, which is right
  // for source files and wrong for a screenshot on the desktop.
  await openApp(page);
  await page.locator("#ctxBtn").click();
  await page.getByRole("menuitem", { name: /Attach images/ }).click();

  expect(await page.evaluate(() => window.__mock.imagePicks)).toBe(1);
  await expect(page.locator(".context-hint.context-image")).toHaveCount(1);
  await expect(page.locator(".context-hint.context-image")).toContainText("picked.png", seen);
});
