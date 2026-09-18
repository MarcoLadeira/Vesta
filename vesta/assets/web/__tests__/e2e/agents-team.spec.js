import { test, expect } from '@playwright/test';
import { openApp, sendPrompt, expectNoFatalErrors } from './helpers/app.js';

const objective = {
  objective_id: 'team-1', objective: 'Build the authentication flow', status: 'running', revision: 1, cost_usd: '0.12', cost_complete: true, allowed_actions: [],
  assignments: [
    { assignment_id: 'alex', name: 'api', display_name: 'Alex', avatar_index: 0, title: 'Implementing authentication', status: 'running', activity: 'Added token validation in auth.ts', changed_files: ['src/auth.ts'], worktree: '/workers/alex', allowed_actions: ['stop'] },
    { assignment_id: 'sam', name: 'review', display_name: 'Sam', avatar_index: 1, title: 'Reviewing security', role: 'reviewer', status: 'running', depends_on: ['alex'], activity: 'Waiting for the authentication changes', allowed_actions: [] },
    { assignment_id: 'taylor', name: 'tests', display_name: 'Taylor', avatar_index: 2, title: 'Checking sign-in behavior', status: 'completed', activity: 'Finished the sign-in checks', verification: { passed: true }, allowed_actions: [] },
  ], integration: { status: 'pending' },
  timeline: [
    { sequence: 1, assignment_id: 'alex', kind: 'activity', occurred_at: '2026-09-13T10:24:00Z', activity: 'Added token validation in auth.ts' },
    { sequence: 2, assignment_id: 'sam', kind: 'claimed', occurred_at: '2026-09-13T10:25:00Z' },
    { sequence: 3, assignment_id: 'sam', kind: 'activity', occurred_at: '2026-09-13T10:26:00Z', activity: 'Found an issue with refresh-token expiry' },
    { sequence: 4, assignment_id: 'alex', kind: 'activity', occurred_at: '2026-09-13T10:27:00Z', activity: 'Fixed refresh-token expiry in auth.ts' },
    { sequence: 5, assignment_id: 'taylor', kind: 'activity', occurred_at: '2026-09-13T10:28:00Z', activity: 'Ran the sign-in checks' },
    { sequence: 6, assignment_id: 'taylor', kind: 'assignment-finished', status: 'completed', occurred_at: '2026-09-13T10:29:00Z', verification_summary: '28/28 tests passed' },
  ],
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
  expect(await page.evaluate(() => window.__vesta.state.view)).toBe('chat');
  await expect(page.getByRole('complementary', { name: 'AI Team', exact: true })).toBeVisible();
  await expect(page.locator('.team-roster .team-person')).toHaveCount(3);
  await expect(page.locator('.team-roster svg')).toHaveCount(3);
  await expect(page.locator('[data-agent-chat-objective] .team-feed')).toContainText('Added token validation in auth.ts');
  await expect(page.locator('.team-roster')).toContainText('Sam');
  await expect(page.locator('#agentsTeam input')).toHaveCount(0);
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('ai-team.png') });
  expectNoFatalErrors(diagnostics);
});

test('agent selection exposes recorded work and real collaboration links', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const { diagnostics } = await startTeam(page);
  await page.locator('.team-roster [data-team-select="sam"]').click();
  await expect(page.locator('.team-detail')).toContainText('Reviews work from Alex');
  await page.locator('.team-connection [data-team-select="alex"]').click();
  await expect(page.locator('.team-detail-person')).toHaveText('Alex');
  await expect(page.locator('.team-detail')).toContainText('Added token validation');
  expect(await page.evaluate(() => window.__vesta.state.view)).toBe('chat');
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('agent-drawer.png') });
  await page.evaluate(() => {
    window.__mock.bridge.inspectObjectiveArtifact = (raw, callback) => {
      const payload = JSON.parse(raw); window.__mock.artifactRequest = payload;
      callback(JSON.stringify({ ...payload, ok: true, workspaceRoot: '/demo', summary: 'Recorded changes', base_sha: 'a', head_sha: 'b', text: '+ token validation' }));
    };
  });
  await page.getByRole('button', { name: 'Inspect diff', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('+ token validation');
  expect(await page.evaluate(() => window.__mock.artifactRequest)).toEqual({ objective_id: 'team-1', assignment_id: 'alex', kind: 'diff' });
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Inspect diff', exact: true })).toBeFocused();
  expect(await page.evaluate(() => window.__vesta.state.view)).toBe('chat');

  await page.getByRole('button', { name: 'Back to team', exact: false }).click();
  await expect(page.locator('.team-roster')).toBeVisible();
  await page.getByRole('button', { name: 'Full Agents workspace', exact: true }).click();
  await expect(page.locator('.agents-objective h2')).toHaveText(objective.objective);
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click();
  await expect(page.locator('[data-agent-chat-objective] .team-feed')).toBeVisible();
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
  await expect(page.locator('.team-detail-person')).toHaveText('Ada');
  await expect(page.locator('[data-agent-chat-objective] .team-feed')).toContainText('Ada');
});

test('team review keeps its canonical revision and approval keeps its request fence', async ({ page }) => {
  await startTeam(page);
  await page.evaluate((o) => window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: { ...o, revision: 8, allowed_actions: ['request_review'], assignments: [{ ...o.assignments[0], allowed_actions: ['approve'], pending_approval: { request_id: 'grant-1', kind: 'command', command: ['python', 'check.py'], reason: 'Run the recorded check' } }] } }), objective);
  await page.locator('.team-roster [data-team-select="alex"]').click();
  await expect(page.locator('.team-attention-detail')).toContainText('check.py');
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
  const composer = await page.locator('.composer').boundingBox();
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(composer.y);
  await page.locator('#input').fill('Keep working on the edge cases');
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('ai-team-mobile.png') });
  await page.getByRole('button', { name: 'Collapse AI Team' }).click();
  await expect(panel).toBeHidden();
  await expect(page.locator('#agentsTeamStrip')).toBeVisible();
  await page.getByRole('button', { name: 'Expand AI Team', exact: true }).click();
  await expect(panel).toBeVisible();
});

test('unsupported host explains the limitation without enabling execution', async ({ page }) => {
  await openApp(page, { boot: { agentsRuntime: { supported: false, platform: 'darwin', reason: 'Multi-agent execution is unavailable on this host.' }, prefs: { multiAgentEnabled: true } } });
  await expect(page.getByRole('button', { name: 'Enable AI Team', exact: true })).toBeDisabled();
  await page.locator('#modeBtn').click();
  await expect(page.locator('[data-multi-agent]')).toBeDisabled();
  await expect(page.locator('[data-multi-agent]')).toContainText('unavailable on this host');
  expect(await page.evaluate(() => window.__vesta.state.multiAgentEnabled)).toBe(false);
});

test('reopened teams refresh in chat and reject older journal snapshots', async ({ page }) => {
  await openApp(page, { boot: { prefs: { multiAgentEnabled: true } }, dashboards: { agents: { objectives: [objective] } } });
  await page.getByRole('button', { name: 'Team on: options', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Show team', exact: true }).click();
  await expect(page.locator('.team-roster')).toContainText('Alex');
  await page.locator('.team-roster [data-team-select="sam"]').click();
  await page.evaluate((o) => {
    window.__mock.updateDashboard('agents', { objectives: [{ ...o, revision: 5, timeline: [...o.timeline, { sequence: 7, assignment_id: 'sam', kind: 'activity', activity: 'Reviewing the completed changes' }], assignments: o.assignments.map((a) => a.assignment_id === 'sam' ? { ...a, activity: 'Reviewing the completed changes', status: 'running' } : a) }] });
  }, objective);
  await expect(page.locator('.team-detail')).toContainText('Reviewing the completed changes');
  // The compact inspector keeps Back in its options menu; focus lands on close.
  await expect(page.locator('#agentsTeam [data-team-close]')).toBeFocused();
  await page.evaluate((o) => window.__mock.updateDashboard('agents', { objectives: [o] }), objective);
  await expect.poll(() => page.evaluate(() => window.__mock.dashboardRequests.filter((r) => r.requestId.startsWith('team-')).length)).toBeGreaterThan(2);
  await expect(page.locator('.team-detail')).toContainText('Reviewing the completed changes');
  await page.getByRole('button', { name: 'Collapse AI Team' }).click();
  // Collapsing closes the panel; a team that is still running keeps its updates
  // coming so its shortcuts and chat card stay live everywhere.
  expect(await page.evaluate(() => window.__vesta.state.teamOpen)).toBe(false);
  expect(await page.evaluate(() => window.__vesta.state.teamPollTimer)).not.toBeNull();
});

test('live chat updates keep keyboard focus on the same agent', async ({ page }) => {
  const { id } = await startTeam(page);
  await page.locator('[data-agent-chat-objective] [data-team-event="3"]').focus();
  await page.evaluate(({ id, objective }) => window.__mock.emitObjective({ requestId: id, objective: { ...objective, revision: 2, timeline: [...objective.timeline, { sequence: 7, assignment_id: 'sam', kind: 'activity', activity: 'Checking the new API response' }], assignments: objective.assignments.map((a) => a.assignment_id === 'sam' ? { ...a, activity: 'Checking the new API response' } : a) }, workspaceRoot: '/demo' }), { id, objective });
  await expect(page.locator('[data-agent-chat-objective] [data-team-event="3"]')).toBeFocused();
  await expect(page.locator('[data-agent-chat-objective] .team-feed')).toContainText('Checking the new API response');
});


test('team options separate sizing from permissions and keep full consent visible', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 520, height: 900 });
  await openApp(page);
  await page.getByRole('button', { name: 'Enable AI Team', exact: true }).click();
  await expect(page.locator('#modeBtn')).toBeHidden();
  await expect(page.locator('#modelBtnLabel')).toHaveText('Auto model');
  await expect(page.locator('#statusLine')).toContainText('Auto mode');
  await expect(page.locator('#statusLine')).not.toContainText('Auto · Auto');
  await page.getByRole('button', { name: 'Team on: options' }).click();
  const menu = page.locator('#teamPop');
  await expect(menu).toContainText('paid or account quota');
  const desc = menu.locator('[data-team-cloud] .cpop-desc');
  expect(await desc.evaluate((n) => n.scrollHeight <= n.clientHeight + 1 && n.scrollWidth <= n.clientWidth + 1)).toBe(true);
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('team-options.png') });
  await menu.getByRole('menuitemradio', { name: 'Up to 3 agents at once' }).click();
  const id = await sendPrompt(page, 'Repair the API');
  expect(await page.evaluate(() => window.__mock.lastRequest)).toMatchObject({ maxParallel: 3, mode: 'safe-auto', allowCloud: false });
  await page.evaluate((rid) => window.__mock.emitReply(rid, { status: 'failed' }), id);
  await page.getByRole('button', { name: 'Team on: options' }).click();
  await menu.getByRole('menuitem', { name: 'Turn Team off' }).click();
  await expect(page.locator('#modeBtn')).toBeVisible();
});

test('timeline retains work in order and the collapsed team gives space back', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const { id } = await startTeam(page);
  const feed = page.locator('[data-agent-chat-objective] .team-feed');
  await expect(feed.locator('[data-team-event]')).toHaveCount(6);
  expect(await feed.locator('[data-team-event]').evaluateAll((nodes) => nodes.map((n) => n.dataset.teamEvent))).toEqual(['1', '2', '3', '4', '5', '6']);
  await expect(feed).toContainText('28/28 tests passed');
  await expect(feed.locator('.team-state')).toHaveCount(0);
  await expect(page.locator('.agents-chat-card h3')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Open in Agents', exact: true })).toHaveCount(0);
  await page.evaluate(({ id, o }) => window.__mock.emitObjective({ requestId: id, workspaceRoot: '/demo', objective: { ...o, revision: 2, assignments: o.assignments.map((a) => a.assignment_id === 'sam' ? { ...a, status: 'pending' } : a) } }), { id, o: objective });
  await expect(page.locator('.team-roster')).toContainText('Waiting for Alex · Vesta will continue');
  const before = await page.locator('#view-chat').boundingBox();
  await page.getByRole('button', { name: 'Collapse AI Team' }).click();
  await expect.poll(async () => (await page.locator('#view-chat').boundingBox()).width).toBeGreaterThan(before.width + 200);
  await page.getByRole('button', { name: 'Expand AI Team', exact: true }).focus();
  await page.evaluate(({ id, o }) => window.__mock.emitObjective({ requestId: id, workspaceRoot: '/demo', objective: { ...o, revision: 3 } }), { id, o: objective });
  await expect(page.getByRole('button', { name: 'Expand AI Team', exact: true })).toBeFocused();
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('team-collapsed.png') });
});
