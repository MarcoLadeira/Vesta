import { test, expect } from "@playwright/test";

import { openApp } from "./helpers/app.js";


// GUI Build mode (#276): inside a scaffolded app, chat edits it with cheap,
// verified, targeted diffs — the whole loop, in the cockpit.
const BUILD_WS = {
  boot: { workspace: { label: "todo", root: "/ws/todo", name: "todo", build_app: true, build_app_name: "todo" } },
};

test("the Build toggle is hidden for a normal workspace", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#buildToggle")).toBeHidden();
  await expect(page.locator("#send")).toHaveText("Send");
});

test("a Build app shows the toggle on, and Send reads Build", async ({ page }) => {
  await openApp(page, BUILD_WS);
  await expect(page.locator("#buildToggle")).toBeVisible();
  await expect(page.locator("#buildToggle")).toHaveClass(/on/);
  await expect(page.locator("#send")).toHaveText("Build");
});

test("the toggle flips between Build and Chat", async ({ page }) => {
  await openApp(page, BUILD_WS);
  await page.locator("#buildToggle").click();
  await expect(page.locator("#buildToggle")).not.toHaveClass(/on/);
  await expect(page.locator("#send")).toHaveText("Send");
  await expect(page.locator("#buildToggle")).toHaveAttribute("aria-pressed", "false");
});

test("sending in Build mode calls bridge.build, not chat", async ({ page }) => {
  await openApp(page, BUILD_WS);
  await page.fill("#input", "make the header sticky");
  await page.locator("#send").click();
  const [buildCount, sendCount, lastBuild] = await page.evaluate(() => [
    window.__mock.buildCount, window.__mock.sendCount, window.__mock.lastBuild,
  ]);
  expect(buildCount).toBe(1);
  expect(sendCount).toBe(0);
  expect(lastBuild.text).toBe("make the header sticky");
});

test("an applied build renders a result card with files, verify, and savings", async ({ page }) => {
  await openApp(page, BUILD_WS);
  await page.fill("#input", "change the theme");
  await page.locator("#send").click();
  const card = page.getByRole("group", { name: "Build result" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("Applied 1 change");
  await expect(card).toContainText("styles.css");
  await expect(card).toContainText("verified");
  await expect(card).toContainText("60% of the app left out");
  // The savings receipt rides along.
  await expect(page.locator(".footer-note")).toContainText("$0.0021");
});

test("a rolled-back build says the app is unchanged and lists why", async ({ page }) => {
  await openApp(page, {
    boot: BUILD_WS.boot,
    buildResult: {
      ok: false, status: "rolled_back", rolled_back: ["app.js"],
      verify: { ok: false, failed: 1, checks: [{ path: "app.js", check: "balance", ok: false, detail: "2 unclosed '{'" }] },
    },
  });
  await page.fill("#input", "break it");
  await page.locator("#send").click();
  const card = page.getByRole("group", { name: "Build rolled back" });
  await expect(card).toContainText("rolled back");
  await expect(card).toContainText("app is unchanged");
  await expect(card).toContainText("2 unclosed");
});

test("Build mode with a slash command still runs the local tool, not a build", async ({ page }) => {
  await openApp(page, BUILD_WS);
  await page.fill("#input", "/panic");
  await page.locator("#send").click();
  const [buildCount, runTools] = await page.evaluate(() => [
    window.__mock.buildCount, window.__mock.runTools,
  ]);
  expect(buildCount).toBe(0);
  expect(runTools).toContain("panic");
});

test("switching to Chat mode sends a normal chat message", async ({ page }) => {
  await openApp(page, BUILD_WS);
  await page.locator("#buildToggle").click(); // -> Chat
  await page.fill("#input", "just explain the code");
  await page.locator("#send").click();
  const [buildCount, sendCount] = await page.evaluate(() => [
    window.__mock.buildCount, window.__mock.sendCount,
  ]);
  expect(buildCount).toBe(0);
  expect(sendCount).toBe(1);
});
