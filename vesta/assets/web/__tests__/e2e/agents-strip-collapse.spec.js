import { test, expect } from '@playwright/test';
import { openApp, openNav } from './helpers/app.js';

test('agents strip can be hidden across pages and restored from the header', async ({ page }) => {
  await openApp(page);
  await expect(page.locator('#agentsTeamStrip')).toBeVisible();
  const width = await page.locator('.main').evaluate(node => node.clientWidth);
  await page.getByRole('button', { name: 'Hide agents sidebar' }).click();
  await expect(page.locator('#agentsTeamStrip')).toBeHidden();
  expect(await page.locator('.main').evaluate(node => node.clientWidth)).toBeGreaterThan(width);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(['show_agents_strip', 'false']);
  await openNav(page, 'Settings');
  await expect(page.locator('#agentsTeamStrip')).toBeHidden();
  await page.locator('#headerAgents').click();
  await expect(page.locator('#agentsTeamStrip')).toBeVisible();
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(['show_agents_strip', 'true']);
});

test('the persisted hidden preference is respected on startup', async ({ page }) => {
  await openApp(page, { boot: { prefs: { showAgentsStrip: false } } });
  await expect(page.locator('#agentsTeamStrip')).toBeHidden();
  await expect(page.locator('#app')).not.toHaveClass(/team-access/);
  await page.locator('#headerAgents').click();
  await expect(page.locator('#agentsTeamStrip')).toBeVisible();
});
