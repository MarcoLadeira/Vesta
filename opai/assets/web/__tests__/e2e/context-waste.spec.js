import { test, expect } from "@playwright/test";

import { expectNoUiSentinels, openApp, openNav } from "./helpers/app.js";


test("context page ranks waste and labels estimates", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Context Waste");
  const dashboard = page.locator("#dashPage");
  await expect(dashboard).toContainText("Estimated wasted tokens");
  await expect(dashboard).toContainText("46,200 tokens");
  await expect(dashboard).toContainText("Estimated cost if sent");
  await expect(dashboard).toContainText("Not money spent — projection for uncompressed context.");
  await expect(dashboard).toContainText("node_modules");
  await expect(dashboard).toContainText("Potential reduction");
});

test("context page has an intentional no-waste state", async ({ page }) => {
  await openApp(page, {
    dashboards: { context: { title: "Context Waste", subtitle: "No context waste found.", kpis: [], cards: [], actions: [] } },
  });
  await openNav(page, "Context Waste");
  await expect(page.locator("#dashPage")).toContainText("No context waste found");
  await expect(page.locator("#dashPage .card")).toHaveCount(0);
});

test("empty context metrics do not produce broken-value sentinels", async ({ page }) => {
  await openApp(page, { dashboards: { context: { title: "Context Waste", subtitle: "No profile yet.", kpis: [], cards: [] } } });
  await openNav(page, "Context Waste");
  await expectNoUiSentinels(page, page.locator("#dashPage"));
});

test("context action copies a read-only profiling command", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Context Waste");
  await page.getByRole("button", { name: "Copy profile command" }).click();
  await expect(page.locator("#toast")).toContainText("opai context profile");
});
