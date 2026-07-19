import { test, expect } from "@playwright/test";

const MOCK = "opai/assets/web/__tests__/e2e/mock-bridge.js";

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: MOCK });
  await page.goto("/opai/assets/web/index.html");
  await page.waitForSelector("#wsSwitch");
});

test("workspace menu is hidden by default and opens anchored to the button", async ({ page }) => {
  await expect(page.locator("#wsMenu")).toBeHidden();
  await page.click("#wsSwitch");
  await expect(page.locator("#wsMenu")).toBeVisible();

  // The regression: the menu must be anchored to the button, not floating at a
  // fixed viewport offset over the whole app.
  const btn = await page.locator("#wsSwitch").boundingBox();
  const menu = await page.locator("#wsMenu").boundingBox();
  expect(Math.abs(menu.x - btn.x)).toBeLessThan(6); // left-aligned to the button
  expect(menu.y).toBeGreaterThan(btn.y + btn.height - 2); // sits below it
});

test("toggle and click-outside and Escape all close the menu", async ({ page }) => {
  await page.click("#wsSwitch");
  await expect(page.locator("#wsMenu")).toBeVisible();
  await page.click("#wsSwitch"); // toggle closed
  await expect(page.locator("#wsMenu")).toBeHidden();

  await page.click("#wsSwitch");
  await page.mouse.click(600, 400); // click outside
  await expect(page.locator("#wsMenu")).toBeHidden();

  await page.click("#wsSwitch");
  await page.keyboard.press("Escape");
  await expect(page.locator("#wsMenu")).toBeHidden();
});

test("open-another-folder calls the native picker; menu closes", async ({ page }) => {
  await page.click("#wsSwitch");
  await page.click("#wsMenu >> text=Open another folder…");
  expect(await page.evaluate(() => window.__mock.openWorkspaceCount)).toBe(1);
  await expect(page.locator("#wsMenu")).toBeHidden();
});

test("current project reveals in file manager (openPath root)", async ({ page }) => {
  await page.click("#wsSwitch");
  await page.click("#wsMenu .item.current");
  expect(await page.evaluate(() => window.__mock.opened)).toContain("");
});

test("a recent project switches workspace", async ({ page }) => {
  await page.click("#wsSwitch");
  await page.click("#wsMenu >> text=other/proj");
  expect(await page.evaluate(() => window.__mock.switched)).toContain("/other/proj");
});

test("chat history: recents render and reload into the composer", async ({ page }) => {
  await expect(page.locator("#recents .recent").first()).toContainText("summarize my changes");
  await page.locator("#recents .recent").first().click();
  await expect(page.locator("#input")).toHaveValue("summarize my changes");
});

test("chat history can be cleared in one click (#145)", async ({ page }) => {
  await expect(page.locator("#recents .recent").first()).toContainText("summarize my changes");
  await page.click("#clearRecents");
  expect(await page.evaluate(() => window.__mock.clearedRecents)).toBe(1);
  await expect(page.locator("#clearRecents")).toHaveCount(0);
  await expect(page.locator("#recents")).toContainText("No saved chats yet");
  await expect(page.locator("#recents").getByRole("button", { name: "New chat" })).toBeVisible();
});

test("sending a prompt saves it to history", async ({ page }) => {
  await page.fill("#input", "brand new prompt");
  await page.click("#send");
  await page.waitForSelector(".gen-stop");
  expect(await page.evaluate(() => window.__mock.savedRecents)).toContain("brand new prompt");
  await expect(page.locator("#recents .recent").first()).toContainText("brand new prompt");
});

test("changed files in a reply are clickable and open in the file manager", async ({ page }) => {
  await page.fill("#input", "edit something");
  await page.click("#send");
  await page.waitForSelector(".gen-stop");
  const id = await page.evaluate(() => window.__mock.reqId());
  await page.evaluate((id) => window.__mock.emitReply(id, {
    status: "answered", answer: "done", changed_files: [" M src/app.py"], receipt: {},
  }), id);
  await expect(page.locator(".file-chip")).toBeVisible();
  await page.click(".file-chip");
  // The git status prefix is stripped to a plain relative path.
  expect(await page.evaluate(() => window.__mock.opened)).toContain("src/app.py");
  await page.click('.files-card [data-openfolder]');
  expect(await page.evaluate(() => window.__mock.opened)).toContain("");
});
