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

test("a gated Build can confirm the exact cloud model and continue as a Build", async ({ page }) => {
  await openApp(page, { boot: BUILD_WS.boot, buildCloudGate: true });
  await page.fill("#input", "add search");
  await page.locator("#send").click();

  const confirm = page.getByRole("button", {
    name: "Confirm Gemini · 3.1 Flash-Lite (free tier)",
  });
  await expect(confirm).toBeVisible();
  await expect(page.getByRole("group", { name: "Build failed" })).toHaveCount(0);
  await expect(page.locator("#ssConn")).toHaveText("Blocked");

  await confirm.click();
  await expect(page.getByRole("group", { name: "Build result" })).toBeVisible();
  await expect(page.locator(".msg.user")).toHaveCount(1);
  const [buildCount, sendCount, lastBuild] = await page.evaluate(() => [
    window.__mock.buildCount, window.__mock.sendCount, window.__mock.lastBuild,
  ]);
  expect(buildCount).toBe(2);
  expect(sendCount).toBe(0);
  expect(lastBuild.allowCloud).toBe(true);
  expect(lastBuild.model).toBe("free:gemini:gemini-3.1-flash-lite-preview");
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

test("a complete rollback says this build's changed files were restored", async ({ page }) => {
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
  await expect(card).toContainText("files changed by this build were restored");
  await expect(card).toContainText("2 unclosed");
});

test("an incomplete rollback names the files still changed", async ({ page }) => {
  await openApp(page, {
    boot: BUILD_WS.boot,
    buildResult: {
      ok: false,
      status: "partial_rollback",
      rolled_back: ["app.js"],
      remaining_changed_files: ["extra.js"],
      applied: [{ path: "extra.js", action: "created", added: 2, removed: 0 }],
      backup_dir: "/ws/todo/.opai-backups/run",
      verify: { ok: false, failed: 1, checks: [] },
    },
  });
  await page.fill("#input", "break two files");
  await page.locator("#send").click();

  const card = page.getByRole("group", { name: "Build rollback incomplete" });
  await expect(card).toContainText("Automatic rollback was incomplete");
  await expect(card).toContainText("extra.js");
  await expect(card).toContainText("Review the remaining file");
  await expect(card).not.toContainText("app is unchanged");
});

test("a preserved edit with failed verification is never presented as verified", async ({ page }) => {
  await openApp(page, {
    boot: BUILD_WS.boot,
    buildResult: {
      ok: false,
      status: "verification_failed",
      applied: [{ path: "app.js", action: "updated", added: 2, removed: 1 }],
      verify: {
        ok: false,
        failed: 1,
        checks: [{ path: "app.js", check: "balance", ok: false, detail: "2 unclosed '{'" }],
      },
    },
  });
  await page.fill("#input", "keep the broken edit for review");
  await page.locator("#send").click();

  const card = page.getByRole("group", { name: "Build verification failed" });
  await expect(card).toContainText("verification failed");
  await expect(card).toContainText("preserved for review or rollback");
  await expect(card).not.toContainText("✓ verified");
});

test("Build mode with a slash command still runs the local tool, not a build", async ({ page }) => {
  await openApp(page, BUILD_WS);
  await page.fill("#input", "/panic");
  await page.locator("#send").click();
  // A slash command lights the room too -- it puts a message on screen -- so
  // the tool runs once the composer has landed rather than on the click.
  await expect
    .poll(() => page.evaluate(() => window.__mock.runTools))
    .toContain("panic");
  expect(await page.evaluate(() => window.__mock.buildCount)).toBe(0);
});

test("switching to Chat mode sends a normal chat message", async ({ page }) => {
  await openApp(page, BUILD_WS);
  await page.locator("#buildToggle").click(); // -> Chat
  await page.fill("#input", "just explain the code");
  await page.locator("#send").click();
  await expect.poll(() => page.evaluate(() => window.__mock.sendCount)).toBe(1);
  expect(await page.evaluate(() => window.__mock.buildCount)).toBe(0);
});
