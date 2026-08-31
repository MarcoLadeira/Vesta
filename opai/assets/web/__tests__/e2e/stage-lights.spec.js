import { test, expect } from "@playwright/test";

import { openApp, finishRequest } from "./helpers/app.js";

const stage = (page) => page.locator("#app").getAttribute("data-stage");

test("a fresh chat opens in an unlit room with the composer in the middle", async ({ page }) => {
  await openApp(page);

  expect(await stage(page)).toBe("dark");
  // The composer is lifted off its real position by a measured amount…
  const lift = await page.evaluate(() =>
    parseFloat(getComputedStyle(document.getElementById("app")).getPropertyValue("--stage-lift")),
  );
  expect(lift).toBeLessThan(-40);
  // …and it lands under the empty block rather than on top of it.
  const chips = await page.locator("#empty .chips").boundingBox();
  const composer = await page.locator(".composer-wrap").boundingBox();
  expect(composer.y).toBeGreaterThan(chips.y + chips.height);
  // Nothing is clipped out of the frame.
  const mark = await page.locator(".empty-mark").boundingBox();
  const main = await page.locator(".main").boundingBox();
  expect(mark.y).toBeGreaterThanOrEqual(main.y);
});

test("the whole group is centred, not just the box", async ({ page }) => {
  // Centring the composer alone left everything sitting high: the empty block
  // is its own full-height flex box centring its children in the region
  // above, so the two were each centred on something different and the group
  // was centred on nothing. The space above the mascot and below the box is
  // the thing that has to match.
  await openApp(page);

  const gaps = await page.evaluate(() => {
    const main = document.querySelector(".main").getBoundingClientRect();
    const kids = Array.from(document.querySelector("#empty").children)
      .filter((el) => el.offsetParent !== null);
    const top = Math.min(...kids.map((el) => el.getBoundingClientRect().top));
    const bottom = document.querySelector(".composer-wrap").getBoundingClientRect().bottom;
    return { above: top - main.top, below: main.bottom - bottom };
  });

  expect(Math.abs(gaps.above - gaps.below)).toBeLessThanOrEqual(2);
});

test("the room is dark: no grid, no horizon", async ({ page }) => {
  await openApp(page);

  // Polled, not read: the layers transition to 0 on load, and a bare read
  // catches them partway there.
  const layers = () => page.evaluate(() => {
    const main = document.querySelector(".main");
    return [
      getComputedStyle(main, "::before").opacity,
      getComputedStyle(main, "::after").opacity,
    ];
  });

  await expect.poll(layers).toEqual(["0", "0"]);
});

test("the first message raises the lights, and the request waits for the landing", async ({ page }) => {
  await openApp(page);
  await page.locator("#input").fill("light it up");
  await page.locator("#send").click();

  // The stage flips immediately — the lights start coming up as you send.
  expect(await stage(page)).toBe("lit");
  // But nothing has been sent yet: the composer is still in flight.
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);

  await expect.poll(() => page.evaluate(() => window.__mock.sendCount)).toBe(1);

  await expect
    .poll(() => page.evaluate(() =>
      getComputedStyle(document.querySelector(".main"), "::before").opacity))
    .toBe("1");
});

test("the second message does not wait for anything", async ({ page }) => {
  // The animation is a first-message event. Paying 600ms per turn would be a
  // worse app than the one that never had the animation.
  await openApp(page);
  await page.locator("#input").fill("first");
  await page.locator("#send").click();
  await expect.poll(() => page.evaluate(() => window.__mock.sendCount)).toBe(1);
  // The run has to finish, or the second send hits the single-flight guard and
  // is queued rather than sent -- which would look exactly like the animation
  // blocking it, and prove nothing.
  const id = await page.evaluate(() => window.__mock.lastRequest.requestId);
  await finishRequest(page, id, { answer: "done" });

  await page.locator("#input").fill("second");
  await page.locator("#send").click();

  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
});

test("starting a new chat puts the lights back out", async ({ page }) => {
  // The stage follows what is on screen rather than being toggled by hand, so
  // every path back to an empty thread ends up dark without knowing about it.
  await openApp(page);
  await page.locator("#input").fill("first");
  await page.locator("#send").click();
  await expect.poll(() => stage(page)).toBe("lit");

  await page.locator("#newChat").click();

  await expect.poll(() => stage(page)).toBe("dark");
});

test("shooting stars fall in the dark and stop once the lights are up", async ({ page }) => {
  // They animate transform and opacity only, so the field runs on the
  // compositor and cannot cost a frame while a request is being built. Paused
  // rather than removed when lit: a paused animation burns nothing and is
  // ready again the moment a new chat starts.
  await openApp(page);

  const field = page.locator("#starfall");
  await expect(field).toHaveCSS("opacity", "1");
  const running = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#starfall i"))
      .map((el) => getComputedStyle(el).animationPlayState));
  expect(running.length).toBe(6);
  expect(new Set(running)).toEqual(new Set(["running"]));

  await page.locator("#input").fill("lights");
  await page.locator("#send").click();
  await expect.poll(() => page.evaluate(() => window.__mock.sendCount)).toBe(1);

  await expect(field).toHaveCSS("opacity", "0");
  const paused = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#starfall i"))
      .map((el) => getComputedStyle(el).animationPlayState));
  expect(new Set(paused)).toEqual(new Set(["paused"]));
});
