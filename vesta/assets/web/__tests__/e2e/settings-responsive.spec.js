import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


const DESTINATIONS = [
  ["general", "General"],
  ["appearance", "Appearance"],
  ["models", "Models & Routing"],
  ["agents", "Agents"],
  ["usage", "Usage & Budgets"],
  ["workspace", "Workspace"],
  ["connections", "Integrations"],
  ["safety", "Safety & Privacy"],
  ["advanced", "Advanced"],
];

async function openSettings(page, viewport) {
  await page.setViewportSize(viewport);
  await openApp(page);
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage .settings-layout")).toBeVisible();
}

async function expectNoHorizontalOverflow(page) {
  const metrics = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth);
}

test("desktop uses grouped destinations and a persistent detail pane", async ({ page }) => {
  await openSettings(page, { width: 1440, height: 900 });
  await expect(page.locator(".settings-sidebar")).toBeVisible();
  await expect(page.locator(".settings-content")).toBeVisible();
  await expect(page.locator(".settings-rail-item")).toHaveCount(DESTINATIONS.length);

  for (const [id, label] of DESTINATIONS) {
    const destination = page.locator(`.settings-rail-item[data-rail-target="${id}"]`);
    await expect(destination).toContainText(label);
    await destination.click();
    await expect(page.locator(`#set-sec-${id}`)).toBeVisible();
  }
  await expectNoHorizontalOverflow(page);
});

test("tablet keeps every destination discoverable without hiding content", async ({ page }) => {
  await openSettings(page, { width: 768, height: 1024 });
  const rail = page.locator(".settings-rail");
  await expect(rail).toBeVisible();
  await expect(page.locator(".settings-content")).toBeVisible();
  await page.locator('.settings-rail-item[data-rail-target="connections"]').click();
  await expect(page.locator("#set-sec-connections")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("phone starts at a Settings index and uses an explicit back path", async ({ page }) => {
  await openSettings(page, { width: 390, height: 844 });
  const layout = page.locator(".settings-layout");
  await expect(layout).not.toHaveClass(/mobile-detail/);
  await expect(page.locator(".settings-sidebar")).toBeVisible();
  await expect(page.locator(".settings-content")).toBeHidden();
  await expect(page.locator(".settings-rail-item")).toHaveCount(DESTINATIONS.length);
  await expect(page.locator(".settings-rail-group")).toHaveText([
    "Vesta",
    "AI",
    "Development",
    "Trust",
    "System",
  ]);

  await page.locator('.settings-rail-item[data-rail-target="safety"]').click();
  await expect(layout).toHaveClass(/mobile-detail/);
  await expect(page.locator("#set-sec-safety")).toBeVisible();
  await expect(page.locator("#settingsMobileBack")).toBeVisible();
  await page.locator("#settingsMobileBack").click();
  await expect(layout).not.toHaveClass(/mobile-detail/);
  await expect(page.locator(".settings-sidebar")).toBeVisible();
  await expect(page.locator(".settings-content")).toBeHidden();
});

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
  { width: 360, height: 800 },
]) {
  test(`every destination stays viewport-safe at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await openSettings(page, viewport);
    for (const [id] of DESTINATIONS) {
      if (viewport.width <= 600 && (await page.locator(".settings-layout").getAttribute("class")).includes("mobile-detail")) {
        await page.locator("#settingsMobileBack").click();
      }
      await page.locator(`.settings-rail-item[data-rail-target="${id}"]`).click();
      const pane = page.locator(`#set-sec-${id}`);
      await expect(pane).toBeVisible();
      const escaped = await pane.evaluate((element) => {
        const viewportWidth = document.documentElement.clientWidth;
        return Array.from(element.querySelectorAll("button, input, select, a"))
          .filter((control) => {
            const box = control.getBoundingClientRect();
            return box.width > 0 && box.height > 0;
          })
          .map((control) => ({
            name: control.getAttribute("aria-label") || control.textContent.trim() || control.tagName,
            box: control.getBoundingClientRect(),
          }))
          .filter(({ box }) => box.left < -0.5 || box.right > viewportWidth + 0.5);
      });
      expect(escaped, `${id} has controls outside the viewport`).toEqual([]);
      await expectNoHorizontalOverflow(page);
    }
  });
}
