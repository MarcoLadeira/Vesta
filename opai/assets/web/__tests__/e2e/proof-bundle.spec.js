import { test, expect } from "@playwright/test";

import { expectNoUiSentinels, openApp, openNav } from "./helpers/app.js";


test("proof bundle shows signature, artifacts, changes, cost, and privacy", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Proof Bundle");
  const dashboard = page.locator("#dashPage");
  await expect(dashboard).toContainText("verified");
  await expect(dashboard).toContainText("What changed");
  await expect(dashboard).toContainText("What it cost");
  await expect(dashboard).toContainText("$0.0420 actual spend");
  await expect(dashboard).toContainText("No raw prompt or secret");
});

test("proof bundle empty state is safe and explicit", async ({ page }) => {
  await openApp(page, {
    dashboards: { proof: { title: "Proof Bundle", subtitle: "No proof bundle generated yet.", kpis: [], cards: [], actions: [] } },
  });
  await openNav(page, "Proof Bundle");
  await expect(page.locator("#dashPage")).toContainText("No proof bundle generated yet");
  await expectNoUiSentinels(page, page.locator("#dashPage"));
});

test("proof action copies the private local command", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Proof Bundle");
  await page.getByRole("button", { name: "Copy proof command" }).click();
  await expect(page.locator("#toast")).toContainText("vesta proof bundle");
});

test("proof surface does not render injected secret fixture text", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Proof Bundle");
  await expect(page.locator("#dashPage")).not.toContainText("sk-supersecret");
  await expect(page.locator("#dashPage")).not.toContainText("token=private");
});

test("production proof exports require scoped approval before writing", async ({ page }) => {
  await openApp(page, {
    dashboards: {
      proof: {
        title: "Proof Bundle",
        kpis: [],
        cards: [],
        actions: [
          { id: "export_proof_json", label: "Export JSON" },
          { id: "export_proof_markdown", label: "Export Markdown" },
        ],
      },
    },
    toolResponses: {
      proof_json: {
        title: "Export proof bundle",
        text: "Write a redacted, signed JSON proof bundle.",
        needs_confirm: true,
        apply: "proof_json",
      },
      proof_markdown: {
        title: "Export proof bundle",
        text: "Write a redacted Markdown proof bundle.",
        needs_confirm: true,
        apply: "proof_markdown",
      },
    },
  });

  await openNav(page, "Proof Bundle");
  await page.getByRole("button", { name: "Export JSON" }).click();
  await expect(page.locator(".approval-card")).toContainText(".opaihub/proof-bundle.json");
  await expect.poll(() => page.evaluate(() => window.__mock.appliedTools)).toEqual([]);
  await page.getByRole("button", { name: "Approve once" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.appliedTools)).toEqual(["proof_json"]);

  await openNav(page, "Proof Bundle");
  await page.getByRole("button", { name: "Export Markdown" }).click();
  await expect(page.locator(".approval-card").last()).toContainText(".opaihub/proof-bundle.md");
  await page.getByRole("button", { name: "Deny" }).last().click();
  await expect.poll(() => page.evaluate(() => window.__mock.appliedTools)).toEqual(["proof_json"]);
});
