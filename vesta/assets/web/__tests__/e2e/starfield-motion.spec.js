import { test, expect } from "@playwright/test";
import { openApp } from "./helpers/app.js";

/**
 * Where the stars come from, and how fast.
 *
 * The direction has been rebuilt twice by hand -- a CSS slide to the left,
 * then a radial burst out of a vanishing point -- and both times the thing
 * that was wrong was the *motion*, which no pixel assertion can catch
 * reliably: the sky is deliberately almost always empty, so a screenshot
 * mostly proves nothing either way.
 *
 * The first rewrite to this shape shipped broken in exactly that gap. `spawn()`
 * lost its `star.alive = true` line, so the loop ran, time advanced, the static
 * field shimmered -- every existing test stayed green -- and not one shooting
 * star was ever drawn again. These tests read the pool.
 */
test("stars are born above the top edge and fall past the view", async ({
  page,
}) => {
  test.setTimeout(45000);
  await page.setViewportSize({ width: 1280, height: 820 });
  await openApp(page);

  const track = await page.evaluate(async () => {
    const canvas = document.createElement("canvas");
    canvas.style.cssText =
      "position:absolute;left:0;top:0;width:600px;height:700px;opacity:0";
    document.body.appendChild(canvas);
    const handle = window.VestaStarfield.mount(canvas);
    const renderer = handle.__renderer;
    const samples = [];
    for (let i = 0; i < 8; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 400));
      const debug = renderer.__debug();
      samples.push(
        debug.pool
          .filter((star) => star.alive)
          .map((star) => ({ y: star.y, speed: star.speed, born: star.bornY })),
      );
    }
    handle.destroy();
    canvas.remove();
    return samples;
  });

  const seen = track.flat();
  // The loop must actually produce stars. This is the assertion the broken
  // rewrite would have failed, and the only one that catches a dead pool.
  expect(seen.length).toBeGreaterThan(0);

  // Every star is first seen above the top edge: it enters from out of bounds,
  // never appearing mid-screen.
  const firstSighting = track.find((frame) => frame.length > 0);
  expect(firstSighting[0].y).toBeLessThan(0);

  // And it travels downward, monotonically, for as long as it is alive.
  const descending = track
    .map((frame) => (frame.length ? frame[0].y : null))
    .filter((y) => y !== null);
  for (let i = 1; i < descending.length; i += 1) {
    if (descending[i] > descending[i - 1]) continue;
    // A drop means the previous star died and a new one was born above the
    // top -- which is still downward travel, not an upward jump.
    expect(descending[i]).toBeLessThan(0);
  }
});

test("stars cross at their own speeds, not one shared speed", async ({
  page,
}) => {
  test.setTimeout(45000);
  await page.setViewportSize({ width: 1280, height: 820 });
  await openApp(page);

  const speeds = await page.evaluate(async () => {
    // A short canvas with short trails, so a crossing takes a fraction of a
    // second and many stars cycle through the pool inside the sample. At the
    // shipped size only three ever exist at once -- the pool never recycles --
    // and three speeds say nothing about spread.
    window.VestaStarfield.CONFIG.spawnInterval = [0.02, 0.05];
    window.VestaStarfield.CONFIG.trailLength = [10, 20];
    const canvas = document.createElement("canvas");
    canvas.style.cssText =
      "position:absolute;left:0;top:0;width:600px;height:60px;opacity:0";
    document.body.appendChild(canvas);
    const handle = window.VestaStarfield.mount(canvas);
    const renderer = handle.__renderer;
    const found = new Set();
    for (let i = 0; i < 60; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 60));
      for (const star of renderer.__debug().pool) {
        if (star.alive) found.add(Math.round(star.speed));
      }
    }
    handle.destroy();
    canvas.remove();
    return [...found];
  });

  const { speed } = await page.evaluate(() => window.VestaStarfield.CONFIG);
  expect(speeds.length).toBeGreaterThan(3);
  expect(Math.min(...speeds)).toBeGreaterThanOrEqual(speed[0] - 1);
  expect(Math.max(...speeds)).toBeLessThanOrEqual(speed[1] + 1);
  // Genuinely varied, not a constant with rounding noise: the spread has to be
  // a real fraction of the configured range.
  const spread = Math.max(...speeds) - Math.min(...speeds);
  expect(spread).toBeGreaterThan((speed[1] - speed[0]) * 0.3);
});
