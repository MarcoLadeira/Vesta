import { test, expect } from "@playwright/test";

// #149: the render surface must be technically incapable of network egress.
// The CSP in index.html has no connect-src, remote img/script/font sources,
// or form-action, so even a compromised or injected script cannot exfiltrate
// chat content. Copy buttons go through the write-only bridge clipboard.

const MOCK = "opai/assets/web/__tests__/e2e/mock-bridge.js";

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: MOCK });
  await page.goto("/opai/assets/web/index.html");
  await page.waitForSelector("#wsSwitch");
});

test("index.html ships a deny-by-default Content-Security-Policy", async ({ page }) => {
  const csp = await page.evaluate(() =>
    document.querySelector('meta[http-equiv="Content-Security-Policy"]').content
  );
  expect(csp).toContain("default-src 'none'");
  expect(csp).not.toContain("connect-src");
  expect(csp).toContain("form-action 'none'");
});

test("the page cannot fetch remote hosts", async ({ page }) => {
  const result = await page.evaluate(() =>
    fetch("https://example.com/beacon").then(
      () => "sent",
      (e) => "blocked:" + e.name
    )
  );
  expect(result).toMatch(/^blocked:/);
});

test("the page cannot fetch even its own origin — no connect-src exists", async ({ page }) => {
  const result = await page.evaluate(() =>
    fetch("app.js").then(
      () => "sent",
      (e) => "blocked:" + e.name
    )
  );
  expect(result).toMatch(/^blocked:/);
});

test("websocket egress is blocked", async ({ page }) => {
  const result = await page.evaluate(
    () =>
      new Promise((resolve) => {
        try {
          const ws = new WebSocket("wss://example.com/exfil");
          ws.onerror = () => resolve("blocked:error");
          ws.onopen = () => resolve("open");
        } catch (e) {
          resolve("blocked:" + e.name);
        }
        setTimeout(() => resolve("blocked:timeout"), 3000);
      })
  );
  expect(result).toMatch(/^blocked:/);
});

test("remote image beacons are blocked", async ({ page }) => {
  const result = await page.evaluate(
    () =>
      new Promise((resolve) => {
        const img = new Image();
        img.onerror = () => resolve("blocked");
        img.onload = () => resolve("loaded");
        img.src = "https://example.com/pixel.gif";
        setTimeout(() => resolve("timeout"), 3000);
      })
  );
  expect(result).toBe("blocked");
});

test("copy buttons use the write-only bridge clipboard", async ({ page }) => {
  await page.click("#cliMirror");
  const copied = await page.evaluate(() => window.__mock.copiedTexts);
  expect(copied.length).toBe(1);
  expect(copied[0]).toContain("opai");
});
