import { test, expect } from '@playwright/test';
import { openApp, sendPrompt, expectNoFatalErrors } from './helpers/app.js';

const objective = {
  objective_id: 'recover-team', objective: 'Build the sign-in flow', status: 'needs-attention',
  revision: 5, team_revision: 0, cost_usd: null, cost_complete: false,
  planning: { status: 'failed', result: { error: 'No eligible model is available. Connect a provider in Settings.' } },
  team_controls: { can_add: true, editable: true }, assignments: [], allowed_actions: [],
};

async function start(page, value = objective) {
  const diagnostics = await openApp(page, { dashboards: { agents: { objectives: [value], cards: [] } } });
  await page.getByRole('button', { name: 'Enable AI Team', exact: true }).click();
  const requestId = await sendPrompt(page, value.objective);
  await page.evaluate(({ requestId, objective }) => window.__mock.emitObjective({ requestId, objective, workspaceRoot: '/demo' }), { requestId, objective: value });
  return diagnostics;
}

test('failed setup explains the cause and allows a manual agent without an endless spinner', async ({ page }, testInfo) => {
  const diagnostics = await start(page);
  await expect(page.locator('.agents-chat-card')).toContainText('Team setup needs attention');
  await expect(page.locator('#agentsTeam')).toContainText(objective.planning.result.error);
  await expect(page.locator('#agentsTeam')).not.toContainText('Putting your team together');
  await expect(page.locator('#thread .team-feed')).toContainText(objective.planning.result.error);
  await expect(page.locator('#thread')).not.toContainText('Your team is getting ready');
  await expect(page.getByRole('button', { name: '+ Add agent', exact: true })).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath('team-setup-recovery.png'), animations: 'disabled' });
  expectNoFatalErrors(diagnostics);
});

test('manual Add starts by default, blocks duplicate submits, and retains the draft on a correlated failure', async ({ page }) => {
  const diagnostics = await start(page);
  await page.getByRole('button', { name: '+ Add agent', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Add an agent' });
  const task = dialog.getByLabel('What should this agent do?');
  await task.fill('Review sign-in validation');
  await expect(dialog.getByLabel('Start immediately')).toBeChecked();
  const submit = dialog.getByRole('button', { name: 'Add agent', exact: true });
  await submit.click();
  await expect(submit).toBeDisabled();
  await expect(dialog).toHaveAttribute('aria-busy', 'true');
  await dialog.locator('form').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
  const request = await page.evaluate(() => window.__mock.objectiveControls.at(-1));
  expect(request.value.start).toBe(true);
  expect(await page.evaluate(() => window.__mock.objectiveControls.length)).toBe(1);
  await page.evaluate(request => window.__mock.emitObjectiveControl({ ok: false, workspaceRoot: '/demo', error: 'Unrelated error', control: { ...request, objective_id: 'other-team', revision: request.value.revision } }), request);
  await expect(dialog).not.toContainText('Unrelated error');
  await expect(submit).toBeDisabled();
  await page.evaluate(request => window.__mock.emitObjectiveControl({ ok: false, workspaceRoot: '/demo', error: { userMessage: 'Choose an available model.' }, control: { ...request, revision: request.value.revision } }), request);
  await expect(dialog).toContainText('Choose an available model.');
  await expect(task).toHaveValue('Review sign-in validation');
  await expect(submit).toBeEnabled();
  await submit.click();
  await page.evaluate(({ request, objective }) => window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: { ...objective, revision: 6, team_revision: 1 }, control: { ...request, revision: request.value.revision } }), { request, objective });
  await expect(dialog).toHaveCount(0);
  expectNoFatalErrors(diagnostics);
});

test('active planning cannot accept an agent and cancellation stays visible', async ({ page }) => {
  await start(page, { ...objective, status: 'planning', planning: { status: 'running' }, team_controls: { can_add: false } });
  await expect(page.locator('.agents-chat-card')).toContainText('Putting your team together');
  await expect(page.getByRole('button', { name: '+ Add agent', exact: true })).toBeDisabled();
  await page.evaluate(objective => window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: { ...objective, status: 'cancelled', revision: 6 } }), objective);
  await expect(page.locator('.agents-chat-card')).toContainText('Team cancelled');
  await expect(page.locator('#agentsTeam')).not.toContainText('Putting your team together');
});
