import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test("benchmark displays effectiveness, reductions, risk blocks, and caveat", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Benchmark");
  const dashboard = page.locator("#dashPage");
  await expect(dashboard).toContainText("99.4");
  await expect(dashboard).toContainText("50x");
  await expect(dashboard).toContainText("16");
  await expect(dashboard).toContainText("6");
  await expect(dashboard).toContainText("not an official external leaderboard");
});

test("benchmark approved claim remains exact and locally scoped", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Benchmark");
  await expect(page.locator("#dashPage")).toContainText(
    "OPai reduced context by 50x and avoided 16 paid calls on the 16-task local benchmark suite.",
  );
});

test("benchmark failure state is explicit", async ({ page }) => {
  await openApp(page, { dashboardErrors: { benchmark: "Benchmark history is unavailable." } });
  await openNav(page, "Benchmark");
  const card = page.locator("#dashPage .state-card.error");
  await expect(card).toHaveAttribute("role", "alert");
  await expect(card).toContainText("Dashboard data is temporarily unavailable.");
});

test("benchmark command can be copied without starting a run", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Benchmark");
  await page.getByRole("button", { name: "Copy benchmark command" }).click();
  await expect(page.locator("#toast")).toContainText("opai benchmark run --suite max --mode both");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});
