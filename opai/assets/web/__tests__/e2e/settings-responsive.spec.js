import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


async function openSettings(page, viewport) {
  await page.setViewportSize(viewport);
  await openApp(page);
  if (await page.locator("#headerSettings").isVisible()) {
    await openNav(page, "Settings");
  } else {
    await page.locator("#sidebarToggle").click();
    await page.locator("#footSettings").click();
  }
  await expect(page.locator("#settingsPage .settings-layout")).toBeVisible();
}

async function expectNoHorizontalOverflow(page) {
  const metrics = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth);
}

test("desktop settings reads as a dedicated app surface", async ({ page }) => {
  await openSettings(page, { width: 1440, height: 900 });

  const sidebar = page.locator(".settings-sidebar");
  const content = page.locator(".settings-content");
  const search = page.locator("#settingsSearch");
  await expect(sidebar).toBeVisible();
  await expect(content).toBeVisible();
  await expect(search).toBeVisible();

  const layout = await page.evaluate(() => {
    const sidebarBox = document.querySelector(".settings-sidebar").getBoundingClientRect();
    const searchBox = document.querySelector("#settingsSearch").getBoundingClientRect();
    const contentBox = document.querySelector(".settings-content").getBoundingClientRect();
    return { sidebarBox, searchBox, contentBox };
  });
  expect(layout.sidebarBox.right).toBeLessThan(layout.contentBox.left);
  expect(Math.abs(layout.searchBox.width - layout.sidebarBox.width)).toBeLessThan(36);
  await expectNoHorizontalOverflow(page);

  await expect(page.locator("#view-settings")).toHaveScreenshot("settings-desktop-1440.png", {
    animations: "disabled",
    maxDiffPixelRatio: 0.01,
  });
});

test("tablet settings uses a compact scrollable page strip", async ({ page }) => {
  await openSettings(page, { width: 768, height: 1024 });

  const rail = page.locator(".settings-rail");
  await expect(rail).toBeVisible();
  const layout = await rail.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      flexWrap: style.flexWrap,
      overflowX: style.overflowX,
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    };
  });
  expect(layout.flexWrap).toBe("nowrap");
  expect(["auto", "scroll"]).toContain(layout.overflowX);
  expect(layout.scrollWidth).toBeGreaterThan(layout.clientWidth);

  await page.locator('.settings-rail-item[data-rail-target="appearance"]').click();
  await expect(page.locator("#set-sec-appearance")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await expect(page.locator("#view-settings")).toHaveScreenshot("settings-tablet-768.png", {
    animations: "disabled",
    maxDiffPixelRatio: 0.01,
  });
});

test("phone settings stacks controls and keeps every surface inside the viewport", async ({ page }) => {
  await openSettings(page, { width: 390, height: 844 });
  await page.locator('.settings-rail-item[data-rail-target="appearance"]').click();

  const measurements = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const rows = Array.from(document.querySelectorAll("#set-sec-appearance .appearance-row"));
    return {
      viewport,
      pageRight: document.querySelector("#settingsPage").getBoundingClientRect().right,
      sidebarBottom: document.querySelector(".settings-sidebar").getBoundingClientRect().bottom,
      titleTop: document.querySelector("#set-sec-appearance .pane-title").getBoundingClientRect().top,
      controls: rows.map((row) => {
        const label = row.querySelector(".appearance-label").getBoundingClientRect();
        const control = row.querySelector(".seg, .v").getBoundingClientRect();
        return { label, control };
      }),
    };
  });
  expect(measurements.pageRight).toBeLessThanOrEqual(measurements.viewport + 0.5);
  expect(measurements.titleTop).toBeGreaterThanOrEqual(measurements.sidebarBottom);
  for (const { label, control } of measurements.controls) {
    expect(control.top).toBeGreaterThanOrEqual(label.bottom);
    expect(control.right).toBeLessThanOrEqual(measurements.viewport);
  }
  await expectNoHorizontalOverflow(page);

  await expect(page.locator("#view-settings")).toHaveScreenshot("settings-mobile-390.png", {
    animations: "disabled",
    maxDiffPixelRatio: 0.01,
  });
});

test("every settings page remains viewport-safe on a phone", async ({ page }) => {
  await openSettings(page, { width: 390, height: 844 });

  const targets = await page.locator(".settings-rail-item").evaluateAll((items) =>
    items.map((item) => item.dataset.railTarget),
  );
  for (const target of targets) {
    await page.locator(`.settings-rail-item[data-rail-target="${target}"]`).click();
    const pane = page.locator(`#set-sec-${target}`);
    await expect(pane).toBeVisible();

    const escaped = await pane.evaluate((element) => {
      const viewport = document.documentElement.clientWidth;
      return Array.from(element.querySelectorAll("button, input, select"))
        .filter((control) => {
          const box = control.getBoundingClientRect();
          return box.width > 0 && box.height > 0;
        })
        .map((control) => ({
          name: control.getAttribute("aria-label") || control.textContent.trim() || control.tagName,
          box: control.getBoundingClientRect(),
        }))
        .filter(({ box }) => box.left < -0.5 || box.right > viewport + 0.5);
    });
    expect(escaped, `${target} has controls outside the phone viewport`).toEqual([]);
    await expectNoHorizontalOverflow(page);
  }
});
