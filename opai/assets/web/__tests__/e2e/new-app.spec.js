import { test, expect } from "@playwright/test";

import { openApp } from "./helpers/app.js";


// OPai Build in the GUI (#276): describe an app → free scaffold → open it.
test.describe("New app flow", () => {
  test("the sidebar button opens the create card", async ({ page }) => {
    await openApp(page);
    await page.locator("#newApp").click();
    const card = page.getByRole("group", { name: "New app" });
    await expect(card).toBeVisible();
    await expect(card).toContainText("scaffolded for free (0 tokens)");
    await expect(card.locator("input")).toBeFocused();
  });

  test("an empty description is refused inline without a bridge call", async ({ page }) => {
    await openApp(page);
    await page.locator("#newApp").click();
    await page.getByRole("button", { name: "Create app" }).click();
    await expect(page.locator(".nac-note")).toContainText("Describe the app");
    expect(await page.evaluate(() => window.__mock.scaffolded.length)).toBe(0);
  });

  test("creating scaffolds and shows the free-tokens success card", async ({ page }) => {
    await openApp(page);
    await page.locator("#newApp").click();
    await page.locator(".nac-input").fill("a todo app with dark mode");
    await page.getByRole("button", { name: "Create app" }).click();
    const done = page.getByRole("group", { name: "App created" });
    await expect(done).toBeVisible();
    await expect(done).toContainText("demo-app is ready");
    await expect(done).toContainText("4 files scaffolded for free");
    await expect(done).toContainText("1,018 tokens never spent");
    const sent = await page.evaluate(() => window.__mock.scaffolded);
    expect(sent[0].description).toBe("a todo app with dark mode");
  });

  test("Enter in the input creates too", async ({ page }) => {
    await openApp(page);
    await page.locator("#newApp").click();
    await page.locator(".nac-input").fill("a notes app");
    await page.locator(".nac-input").press("Enter");
    await expect(page.getByRole("group", { name: "App created" })).toBeVisible();
  });

  test("Open app workspace hands off to the workspace switcher", async ({ page }) => {
    await openApp(page);
    await page.locator("#newApp").click();
    await page.locator(".nac-input").fill("a todo app");
    await page.getByRole("button", { name: "Create app" }).click();
    await page.getByRole("button", { name: "Open app workspace" }).click();
    const switched = await page.evaluate(() => window.__mock.switched);
    expect(switched).toContain("/ws/demo-app");
  });

  test("Copy preview command copies cd + serve", async ({ page }) => {
    await openApp(page);
    await page.locator("#newApp").click();
    await page.locator(".nac-input").fill("a todo app");
    await page.getByRole("button", { name: "Create app" }).click();
    await page.getByRole("button", { name: "Copy preview command" }).click();
    await expect(page.locator("#toast")).toContainText("Preview command copied");
    const copied = await page.evaluate(() => window.__mock.copiedTexts);
    expect(copied[copied.length - 1]).toContain("http.server");
  });

  test("a scaffold error is shown honestly and the card stays editable", async ({ page }) => {
    await openApp(page, {
      scaffoldResponse: { ok: false, error: "already exists and is not empty" },
    });
    await page.locator("#newApp").click();
    await page.locator(".nac-input").fill("taken");
    await page.getByRole("button", { name: "Create app" }).click();
    await expect(page.locator(".nac-note")).toContainText("already exists");
    await expect(page.getByRole("button", { name: "Create app" })).toBeEnabled();
  });

  test("Cancel removes the card and restores the empty-chat welcome", async ({ page }) => {
    await openApp(page);
    await page.locator("#newApp").click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator("#newAppCard")).toHaveCount(0);
    await expect(page.locator("#empty")).toBeVisible();
    await expect(page.getByRole("button", { name: "Explain this repo" })).toBeVisible();
  });

  test("the command palette lists and launches New app", async ({ page }) => {
    await openApp(page);
    await page.keyboard.press("Control+k");
    await page.fill("#paletteInput", "new app");
    await expect(page.locator("#paletteList")).toContainText("New app (free scaffold)");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("group", { name: "New app" })).toBeVisible();
  });
});
