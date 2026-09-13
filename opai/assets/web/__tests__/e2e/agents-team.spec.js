import { test, expect } from '@playwright/test';
import { openApp, sendPrompt, expectNoFatalErrors } from './helpers/app.js';

const objective = {
  objective_id: 'team-1', objective: 'Build the authentication flow', status: 'running', revision: 1, cost_usd: '0.12', cost_complete: true, allowed_actions: [],
  assignments: [
    { assignment_id: 'alex', name: 'api', display_name: 'Alex', avatar_index: 0, title: 'Implementing authentication', status: 'running', activity: 'Added token validation in auth.ts', changed_files: ['src/auth.ts'], allowed_actions: ['stop'] },
    { assignment_id: 'sam', name: 'review', display_name: 'Sam', avatar_index: 1, title: 'Reviewing security', role: 'reviewer', status: 'blocked', depends_on: ['alex'], activity: 'Waiting for the authentication changes', allowed_actions: [] },
    { assignment_id: 'taylor', name: 'tests', display_name: 'Taylor', avatar_index: 2, title: 'Checking sign-in behavior', status: 'completed', activity: 'Finished the sign-in checks', verification: { passed: true }, allowed_actions: [] },
  ], integration: { status: 'pending' },
};
async function startTeam(page) {
  const diagnostics = await openApp(page, { dashboards: { agents: { objectives: [objective], cards: [] } } });
  await page.getByRole('button', { name: 'Enable AI Team', exact: true }).click();
  const id = await sendPrompt(page, objective.objective);
  await page.evaluate(({ id, objective }) => window.__mock.emitObjective({ requestId: id, objective, workspaceRoot: '/demo' }), { id, objective });
  return { diagnostics, id };
}

test('one click enables the team and chat remains the main workspace', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const { diagnostics } = await startTeam(page);
  expect(await page.evaluate(() => window.__opai.state.view)).toBe('chat');
  await expect(page.getByRole('complementary', { name: 'AI Team' })).toBeVisible();
  await expect(page.locator('.team-roster .team-person')).toHaveCount(3);
  await expect(page.locator('.team-roster svg')).toHaveCount(3);
  await expect(page.locator('.team-feed')).toContainText('Added token validation in auth.ts');
  await expect(page.locator('.team-roster')).toContainText('Sam');
  await expect(page.locator('#agentsTeam input')).toHaveCount(0);
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('ai-team.png') });
  expectNoFatalErrors(diagnostics);
});

test('agent selection exposes recorded work and real collaboration links', async ({ page }) => {
  const { diagnostics } = await startTeam(page);
  await page.locator('.team-roster [data-team-select="sam"]').click();
  await expect(page.locator('.team-detail')).toContainText('Reviews work from Alex');
  await page.locator('.team-connection [data-team-select="alex"]').click();
  await expect(page.locator('.team-detail h3')).toHaveText('Alex');
  await expect(page.locator('.team-detail')).toContainText('Added token validation');
  await page.getByRole('button', { name: 'Inspect work', exact: true }).click();
  await expect(page.locator('.agents-detail h3')).toHaveText('Implementing authentication');
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click();
  await expect(page.locator('.team-feed')).toBeVisible();
  expectNoFatalErrors(diagnostics);
});

test('renaming keeps its draft during live updates and sends canonical IDs', async ({ page }) => {
  const { id } = await startTeam(page);
  await page.locator('.team-roster [data-team-select="alex"]').click();
  await page.locator('.team-name-editor summary').click();
  await page.getByLabel('Agent name', { exact: true }).fill('Ada');
  await page.evaluate(({ id, objective }) => window.__mock.emitObjective({ requestId: id, objective: { ...objective, revision: 2 }, workspaceRoot: '/demo' }), { id, objective });
  await expect(page.getByLabel('Agent name', { exact: true })).toHaveValue('Ada');
  await expect(page.getByLabel('Agent name', { exact: true })).toBeFocused();
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  expect(await page.evaluate(() => window.__mock.objectiveControls.at(-1))).toEqual({ objective_id: 'team-1', assignment_id: 'alex', action: 'rename', value: 'Ada' });
  await page.evaluate((o) => window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: { ...o, revision: 3, assignments: o.assignments.map((a) => a.assignment_id === 'alex' ? { ...a, display_name: 'Ada' } : a) } }), objective);
  await expect(page.locator('.team-detail h3')).toHaveText('Ada');
  await expect(page.locator('.team-feed')).toContainText('Ada');
});

test('team review keeps its canonical revision and approval keeps its request fence', async ({ page }) => {
  await startTeam(page);
  await page.evaluate((o) => window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: { ...o, revision: 8, allowed_actions: ['request_review'], assignments: [{ ...o.assignments[0], allowed_actions: ['approve'], pending_approval: { request_id: 'grant-1', kind: 'command', command: ['python', 'check.py'], reason: 'Run the recorded check' } }] } }), objective);
  await page.locator('.team-roster [data-team-select="alex"]').click();
  await expect(page.locator('.team-approval')).toContainText('check.py');
  await page.getByRole('button', { name: 'Approve once', exact: true }).click();
  await page.getByRole('button', { name: 'Ask for team review', exact: true }).click();
  expect(await page.evaluate(() => window.__mock.objectiveControls)).toEqual([
    { objective_id: 'team-1', assignment_id: 'alex', action: 'approve', value: { request_id: 'grant-1' } },
    { objective_id: 'team-1', action: 'request_review', value: { revision: 8 } },
  ]);
});

test('mobile team panel collapses cleanly and can reopen from chat', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 520, height: 900 });
  await startTeam(page);
  const panel = page.locator('#agentsTeam');
  await expect(panel).toBeVisible();
  const bounds = await panel.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(520);
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('ai-team-mobile.png') });
  await page.getByRole('button', { name: 'Collapse AI Team' }).click();
  await expect(panel).toBeHidden();
  await page.getByRole('button', { name: 'View team', exact: true }).click();
  await expect(panel).toBeVisible();
});

test('unsupported host explains the limitation without enabling execution', async ({ page }) => {
  await openApp(page, { boot: { agentsRuntime: { supported: false, platform: 'darwin', reason: 'Multi-agent execution is unavailable on this host.' }, prefs: { multiAgentEnabled: true } } });
  await expect(page.getByRole('button', { name: 'Enable AI Team', exact: true })).toBeDisabled();
  await page.locator('#modeBtn').click();
  await expect(page.locator('[data-multi-agent]')).toBeDisabled();
  await expect(page.locator('[data-multi-agent]')).toContainText('unavailable on this host');
  expect(await page.evaluate(() => window.__opai.state.multiAgentEnabled)).toBe(false);
});

test('reopened teams refresh in chat and reject older journal snapshots', async ({ page }) => {
  await openApp(page, { boot: { prefs: { multiAgentEnabled: true } }, dashboards: { agents: { objectives: [objective] } } });
  await page.getByRole('button', { name: 'Toggle AI Team panel', exact: true }).click();
  await expect(page.locator('.team-roster')).toContainText('Alex');
  await page.locator('.team-roster [data-team-select="sam"]').click();
  await page.evaluate((o) => {
    window.__mock.updateDashboard('agents', { objectives: [{ ...o, revision: 5, assignments: o.assignments.map((a) => a.assignment_id === 'sam' ? { ...a, activity: 'Reviewing the completed changes', status: 'running' } : a) }] });
  }, objective);
  await expect(page.locator('.team-detail')).toContainText('Reviewing the completed changes');
  await expect(page.locator('.team-roster [data-team-select="sam"]')).toBeFocused();
  await page.evaluate((o) => window.__mock.updateDashboard('agents', { objectives: [o] }), objective);
  await expect.poll(() => page.evaluate(() => window.__mock.dashboardRequests.filter((r) => r.requestId.startsWith('team-')).length)).toBeGreaterThan(2);
  await expect(page.locator('.team-detail')).toContainText('Reviewing the completed changes');
  await page.getByRole('button', { name: 'Collapse AI Team' }).click();
  expect(await page.evaluate(() => window.__opai.state.teamPollTimer)).toBeNull();
});

test('live chat updates keep keyboard focus on the same agent', async ({ page }) => {
  const { id } = await startTeam(page);
  await page.locator('.team-feed [data-team-select="sam"]').focus();
  await page.evaluate(({ id, objective }) => window.__mock.emitObjective({ requestId: id, objective: { ...objective, revision: 2, assignments: objective.assignments.map((a) => a.assignment_id === 'sam' ? { ...a, activity: 'Checking the new API response' } : a) }, workspaceRoot: '/demo' }), { id, objective });
  await expect(page.locator('.team-feed [data-team-select="sam"]')).toBeFocused();
  await expect(page.locator('.team-feed')).toContainText('Checking the new API response');
});
