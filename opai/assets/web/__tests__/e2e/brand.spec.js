import { test, expect } from "@playwright/test";

const MOCK = "opai/assets/web/__tests__/e2e/mock-bridge.js";

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: MOCK });
  await page.goto("/opai/assets/web/index.html");
  await page.waitForSelector("#input");
});

test("empty state speaks Vesta, not the generic prompt", async ({ page }) => {
  // The headline is the launch's motto, carried by the boot payload.
  await expect(page.locator("#empty h1")).toHaveText("The Living Flame.");
  await expect(page.locator("#empty h1")).not.toContainText("Better. Faster. Cheaper.");
  // The claim stands on its own: no second sentence describing the product
  // to someone already looking at it.
  await expect(page.locator("#emptySub")).toBeHidden();
  await expect(page.locator("#empty .hint")).toHaveCount(0);
  await expect(page.locator("#empty h1")).not.toContainText("What do you want to build");
});

test("composer placeholder carries the brand voice", async ({ page }) => {
  await expect(page.locator("#input")).toHaveAttribute(
    "placeholder", /build, fix, or explain/
  );
});

test("inspector shows the CLI mirror and it tracks the mode", async ({ page }) => {
  const cmd = page.locator("#cliMirrorCmd");
  await expect(cmd).toContainText("vesta ask --model claude:opus");
  await expect(cmd).toContainText("<your task>");
  // Switching run mode updates the terminal twin.
  await page.selectOption("#modeSel", "safe-auto");
  await expect(cmd).toContainText("--mode safe-auto");
});

test("CLI mirror reflects the last sent task", async ({ page }) => {
  await page.fill("#input", "fix the flaky auth test");
  await page.click("#send");
  await page.waitForSelector(".gen-stop");
  const id = await page.evaluate(() => window.__mock.reqId());
  await page.evaluate((id) => window.__mock.emitReply(id, { status: "answered", answer: "done", receipt: {} }), id);
  await expect(page.locator("#cliMirrorCmd")).toContainText("fix the flaky auth test");
});
