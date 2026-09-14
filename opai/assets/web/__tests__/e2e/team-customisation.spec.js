import { test, expect } from '@playwright/test';
import { openApp, sendPrompt, expectNoFatalErrors } from './helpers/app.js';

const objective = {
  objective_id: 'custom-team', objective: 'Build authentication', status: 'running', revision: 5, team_revision: 0,
  cost_usd: '0.12', cost_complete: true, allowed_actions: [], integration: { status: 'pending' },
  team_controls: { editable: true, can_add: true, remaining_tasks: 29 },
  assignments: [
    { assignment_id: 'alex', agent_id: 'alex', name: 'auth', display_name: 'Alex', title: 'Implement token validation', avatar_index: 0, team_order: 0, status: 'running', model: 'auto', group: 'Authentication', depends_on: [], team_controls: { can_message: true }, allowed_actions: ['stop'] },
    { assignment_id: 'sam', agent_id: 'sam', name: 'review', display_name: 'Sam', title: 'Review authentication', avatar_index: 1, team_order: 1, status: 'pending', held: true, model: 'auto', group: 'Authentication', depends_on: ['auth'], team_controls: { can_message: true, can_connect: true, can_start: true }, allowed_actions: ['stop'] },
    { assignment_id: 'taylor', agent_id: 'taylor', name: 'checks', display_name: 'Taylor', title: 'Check sign-in behavior', avatar_index: 2, team_order: 2, status: 'pending', held: true, model: 'auto', group: '', depends_on: [], team_controls: { can_message: true, can_connect: true, can_start: true }, allowed_actions: ['stop'] },
  ],
  timeline: [{ sequence: 5, kind: 'activity', assignment_id: 'alex', occurred_at: '2026-09-14T10:24:00Z', activity: 'Edited token validation in auth.ts' }],
};
async function start(page) {
  const diagnostics = await openApp(page, { dashboards: { agents: { objectives: [objective], cards: [] } } });
  await page.getByRole('button', { name: 'Enable AI Team', exact: true }).click();
  const id = await sendPrompt(page, objective.objective);
  await page.evaluate(({ id, objective }) => window.__mock.emitObjective({ requestId: id, objective, workspaceRoot: '/demo' }), { id, objective });
  return diagnostics;
}
async function respond(page, patch = {}) {
  return page.evaluate(({ objective, patch }) => {
    const request = window.__mock.objectiveControls.at(-1);
    const next = { ...objective, ...patch, team_revision: request.value.revision + 1, revision: 10 + request.value.revision };
    window.__mock.updateDashboard('agents', { objectives: [next], cards: [] });
    window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: next, control: { action: request.action, assignment_id: request.assignment_id, revision: request.value.revision } });
    return request;
  }, { objective, patch });
}

test('timestamps are centered and map keeps the composer with real directed links', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1500, height: 980 });
  const diagnostics = await start(page);
  const stamp = page.locator('#thread > .chat-timestamp');
  await expect(stamp).toHaveCount(1);
  await expect(stamp).toContainText('Today');
  expect(await stamp.evaluate((node) => getComputedStyle(node).textAlign)).toBe('center');
  await expect(page.locator('.team-time time')).toHaveAttribute('datetime', '2026-09-14T10:24:00.000Z');
  await page.getByRole('button', { name: 'Organise team', exact: true }).click();
  await expect(page.locator('#teamMap')).toBeVisible();
  await expect(page.locator('#chatScroll')).toBeHidden();
  await expect(page.locator('#input')).toBeVisible();
  await expect(page.locator('.team-map-node')).toHaveCount(3);
  await expect(page.locator('.team-map-edge')).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath('team-map-desktop.png'), animations: 'disabled' });
  await page.locator('[data-map-select="sam"]').click();
  await expect(page.locator('.team-detail')).toContainText('Ready when you are');
  await expect(page.getByRole('button', { name: 'Start agent', exact: true })).toBeVisible();
  await expect(page.locator('#teamMap')).toBeVisible();
  await page.getByRole('button', { name: 'Back to team', exact: false }).click();
  await page.getByRole('button', { name: 'Collapse AI Team' }).click();
  await page.getByRole('button', { name: 'Back to chat', exact: false }).click();
  await expect(page.locator('#chatScroll')).toBeVisible();
  expectNoFatalErrors(diagnostics);
});

test('add agent preserves its draft and exposes model/group before optional start', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 520, height: 900 });
  const diagnostics = await start(page);
  await page.getByRole('button', { name: '+ Add agent', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Add an agent' });
  await dialog.getByLabel('What should this agent do?').fill('Review token expiry');
  await dialog.getByLabel('Name', { exact: true }).fill('Riley');
  await dialog.locator('summary').click();
  await dialog.getByLabel('Group', { exact: true }).fill('Security');
  await page.evaluate((o) => window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: { ...o, revision: 6 } }), objective);
  await expect(dialog.getByLabel('What should this agent do?')).toHaveValue('Review token expiry');
  await expect(dialog.getByLabel('Start immediately')).not.toBeChecked();
  const bounds = await dialog.boundingBox(); expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(520);
  await page.screenshot({ path: testInfo.outputPath('add-agent-mobile.png'), animations: 'disabled' });
  await dialog.getByRole('button', { name: 'Add agent', exact: true }).click();
  const request = await respond(page);
  expect(request).toEqual({ objective_id: 'custom-team', action: 'add_agent', value: { revision: 0, objective: 'Review token expiry', name: 'Riley', model: 'auto', group: 'Security', start: false, budget_usd: null } });
  await expect(dialog).toHaveCount(0);
  expectNoFatalErrors(diagnostics);
});

test('group selection, keyboard movement and connection edits use canonical team fences', async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 980 });
  const diagnostics = await start(page);
  await page.getByRole('button', { name: 'Organise team', exact: true }).click();
  await page.getByRole('button', { name: 'Group', exact: true }).click();
  await page.locator('[data-map-select="sam"]').click();
  await page.locator('[data-map-select="taylor"]').click();
  await page.getByLabel('Group name').fill('Quality');
  await page.getByRole('button', { name: 'Group selected', exact: true }).click();
  const grouped = objective.assignments.map((a) => a.assignment_id === 'alex' ? a : { ...a, group: 'Quality' });
  expect(await respond(page, { assignments: grouped })).toEqual({ objective_id: 'custom-team', action: 'group_agents', value: { revision: 0, assignment_ids: ['sam', 'taylor'], group: 'Quality' } });
  await expect(page.locator('[data-map-group="Quality"]')).toBeVisible();
  const move = page.getByRole('button', { name: 'Move Sam', exact: true });
  await move.focus(); await page.keyboard.press('ArrowRight');
  const layout = await page.evaluate(() => window.__mock.objectiveControls.at(-1));
  expect(layout.action).toBe('team_layout'); expect(layout.value.revision).toBe(1);
  expect(layout.value.positions.sam.x).toBe(330);
  await respond(page, { assignments: grouped, team_layout: layout.value.positions });
  await expect(move).toBeFocused();
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.locator('[data-map-select="alex"]').click();
  await page.locator('[data-map-select="taylor"]').click();
  expect(await page.evaluate(() => window.__mock.objectiveControls.at(-1))).toEqual({ objective_id: 'custom-team', assignment_id: 'taylor', action: 'connect_agents', value: { revision: 2, source_id: 'alex', connected: true } });
  await respond(page, { assignments: grouped.map((a) => a.assignment_id === 'taylor' ? { ...a, depends_on: ['auth'] } : a), team_layout: layout.value.positions });
  await expect(page.locator('.team-map-edge')).toHaveCount(2);
  await page.locator('[data-map-edge="1"]').focus(); await page.keyboard.press('Enter');
  await page.getByRole('button', { name: 'Remove connection' }).click();
  expect(await respond(page, { assignments: grouped })).toEqual({ objective_id: 'custom-team', assignment_id: 'taylor', action: 'connect_agents', value: { revision: 3, source_id: 'alex', connected: false } });
  expectNoFatalErrors(diagnostics);
});

test('agent conversation persists across follow-ups without duplicate people and retains unsent drafts', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const diagnostics = await start(page);
  await page.locator('.team-roster [data-team-select="alex"]').click();
  const input = page.getByRole('textbox', { name: 'Message Alex', exact: true });
  await input.fill('Explain token expiry');
  await page.getByRole('button', { name: 'Back to team', exact: false }).click();
  await page.locator('.team-roster [data-team-select="sam"]').click();
  await page.getByRole('button', { name: 'Back to team', exact: false }).click();
  await page.locator('.team-roster [data-team-select="alex"]').click();
  await expect(input).toHaveValue('Explain token expiry');
  await input.focus();
  await page.evaluate((o) => window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: { ...o, revision: 6 } }), objective);
  await expect(input).toHaveValue('Explain token expiry'); await expect(input).toBeFocused();
  await page.getByRole('button', { name: 'Send to agent', exact: true }).click();
  const followup = { ...objective.assignments[0], assignment_id: 'alex-followup', name: 'followup', title: 'Explain token expiry', status: 'pending', team_order: 3, user_message: 'Explain token expiry', created_at: '2026-09-14T10:30:00Z', depends_on: ['auth'] };
  expect(await respond(page, { assignments: [...objective.assignments, followup] })).toEqual({ objective_id: 'custom-team', assignment_id: 'alex', action: 'agent_message', value: { revision: 0, message: 'Explain token expiry' } });
  await expect(input).toHaveValue('');
  await expect(page.getByRole('region', { name: 'Agent conversation' })).toContainText('Explain token expiry');
  await page.locator('.team-settings summary').click();
  const select = page.getByLabel('AI model', { exact: true });
  await expect(select).toBeVisible();
  await expect.poll(() => select.locator('option').count()).toBeGreaterThan(1);
  const model = await select.locator('option').evaluateAll((items) => items.find((i) => i.value !== 'auto').value);
  await select.selectOption(model);
  await page.getByRole('button', { name: 'Save settings', exact: true }).click();
  expect(await page.evaluate(() => window.__mock.objectiveControls.at(-1))).toEqual({ objective_id: 'custom-team', assignment_id: 'alex', action: 'agent_settings', value: { revision: 1, model } });
  await page.screenshot({ path: testInfo.outputPath('agent-conversation.png'), animations: 'disabled' });
  await page.getByRole('button', { name: 'Back to team', exact: false }).click();
  await expect(page.locator('.team-roster .team-person')).toHaveCount(3);
  expectNoFatalErrors(diagnostics);
});

test('dragging a whole group saves once and rapid moves keep the latest layout', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1500, height: 980 });
  const diagnostics = await start(page);
  await page.getByRole('button', { name: 'Organise team', exact: true }).click();
  const group = page.locator('[data-map-group="Authentication"]');
  const box = await group.boundingBox();
  await page.mouse.move(box.x + 20, box.y + 8); await page.mouse.down();
  await page.mouse.move(box.x + 100, box.y + 48, { steps: 6 }); await page.mouse.up();
  const request = await page.evaluate(() => window.__mock.objectiveControls.at(-1));
  expect(request.action).toBe('team_layout');
  expect(request.value.positions.alex).toEqual({ x: 120, y: 104 });
  expect(request.value.positions.sam).toEqual({ x: 400, y: 104 });
  await page.getByRole('button', { name: 'Move Sam', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  expect(await page.evaluate(() => window.__mock.objectiveControls.length)).toBe(1);
  await respond(page, { team_layout: request.value.positions });
  const queued = await page.evaluate(() => window.__mock.objectiveControls.at(-1));
  expect(queued.value.revision).toBe(1);
  expect(queued.value.positions.sam.x).toBe(410);
  await respond(page, { team_layout: queued.value.positions });
  await expect(page.locator('[data-map-node="sam"]')).toHaveCSS('left', '410px');
  await page.screenshot({ path: testInfo.outputPath('team-map-arranged.png'), animations: 'disabled' });
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  await expect(page.locator('#teamMap')).toBeHidden();
  await expect(page.locator('#chatScroll')).toBeVisible();
  await expect(page.locator('#thread > .chat-timestamp')).toHaveCount(0);
  expectNoFatalErrors(diagnostics);
});
