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

test("cleanup actions preview in app and require approval before writing ignores", async ({ page }) => {
  await openApp(page, {
    dashboards: {
      context: {
        title: "Context Waste",
        subtitle: "Find expensive generated context.",
        kpis: [],
        cards: [{ title: "dist/", body: "Generated output" }],
        actions: [
          { id: "cleanup_preview", label: "Preview cleanup" },
          { id: "generate_ignores", label: "Generate ignore files" },
        ],
      },
    },
    toolResponses: {
      context_preview: {
        title: "Cleanup preview",
        text: "Preview only. No files are deleted.",
        needs_confirm: false,
      },
      ignores: {
        title: "Generate ignore files",
        text: "Append managed ignore blocks?",
        needs_confirm: true,
        apply: "ignores",
      },
    },
  });

  await openNav(page, "Context Waste");
  await page.getByRole("button", { name: "Preview cleanup" }).click();
  await expect(page.locator(".tool-card")).toContainText("Preview only. No files are deleted.");
  await expect.poll(() => page.evaluate(() => window.__mock.runTools)).toEqual(["context_preview"]);

  await openNav(page, "Context Waste");
  await page.getByRole("button", { name: "Generate ignore files" }).click();
  await expect(page.locator(".approval-card")).toContainText("Append managed ignore blocks?");
  await expect(page.locator(".approval-card")).toContainText("Approve once");
  await expect.poll(() => page.evaluate(() => window.__mock.appliedTools)).toEqual([]);

  await page.getByRole("button", { name: "Deny" }).click();
  await expect(page.locator(".approval-card")).toContainText("Denied — nothing was changed.");
  await expect.poll(() => page.evaluate(() => window.__mock.appliedTools)).toEqual([]);
});
