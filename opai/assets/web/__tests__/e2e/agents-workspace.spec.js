import { test, expect } from "@playwright/test";
import { openApp, openNav, sendPrompt, expectNoFatalErrors } from "./helpers/app.js";

const objective = { objective_id: 'obj-1', objective: 'Repair independent regressions', status: 'running', budget_usd: '4', cost_usd: null, cost_complete: false, max_parallel: 2, allowed_actions: ['pause', 'budget'], assignments: [{ assignment_id: 'a-1', title: 'API repair', status: 'blocked', depends_on: ['a-0'], intended_paths: ['api/'], blocked_reason: 'Waiting for contract', allowed_actions: ['stop', 'reroute'], activity: ['Read api/server.py'] }], integration: { status: 'pending' } };

test('live objective updates continue after acknowledgement and reject older revisions', async ({ page }) => {
  const diagnostics = await openApp(page, { boot: { prefs: { multiAgentEnabled: true } } });
  await expect(page.locator('#modeBtn')).toContainText('Agents');
  const id = await sendPrompt(page);
  await page.evaluate(({ o, id }) => window.__mock.emitObjective({ requestId: id, workspaceRoot: '/demo', objective: { ...o, revision: 1 } }), { o: objective, id });
  await expect(page.locator('.agents-objective')).toContainText(objective.objective);
  await page.evaluate(({ o, id }) => {
    window.__mock.emitObjective({ requestId: id, workspaceRoot: '/demo', objective: { ...o, revision: 3, cost_usd: '0.42' } });
    window.__mock.emitObjective({ requestId: id, workspaceRoot: '/demo', objective: { ...o, revision: 2, cost_usd: '0.01' } });
  }, { o: objective, id });
  await expect(page.locator('.agents-metrics')).toContainText('$0.42');
  expect(await page.evaluate(() => window.__opai.state.busy)).toBe(false);
  expectNoFatalErrors(diagnostics);
});

test('objective selection preserves canonical worktree and receipt targets', async ({ page }) => {
  const recorded = { ...objective, receipt: { report: 'opai-objective-receipt', objective_id: 'obj-1' }, assignments: [{ ...objective.assignments[0], worktree: '/workers/a', branch: 'codex/a', receipt: { report: 'opai-agent-receipt', assignment_id: 'a-1' } }] };
  const diagnostics = await openApp(page, { dashboards: { agents: { objectives: [recorded, { ...objective, objective_id: 'obj-2', objective: 'Another objective' }], cards: [] } } });
  await page.evaluate(() => {
    window.__mock.bridge.openObjectiveWorktree = (raw, callback) => { window.__mock.worktreeTarget = JSON.parse(raw); callback(JSON.stringify({ ok: true })); };
  });
  await openNav(page, 'Agents');
  await page.locator('[data-objective-select="obj-2"]').click();
  await expect(page.locator('.agents-objective h2')).toHaveText('Another objective');
  await page.locator('[data-objective-select="obj-1"]').click();
  await page.getByRole('button', { name: 'Open worktree' }).click();
  expect(await page.evaluate(() => window.__mock.worktreeTarget)).toEqual({ objective_id: 'obj-1', assignment_id: 'a-1' });
  await page.locator('[data-agent-receipt][data-assignment-id="a-1"]').click();
  expect(await page.evaluate(() => JSON.parse(window.__mock.copiedTexts.at(-1)))).toEqual(recorded.assignments[0].receipt);
  expectNoFatalErrors(diagnostics);
});

test('artifact inspection sends canonical IDs and displays hostile diffs as inert text', async ({ page }) => {
  const recorded = { ...objective, assignments: [{ ...objective.assignments[0], worktree: '/workers/a', result: { git_evidence: { base_sha: 'a'.repeat(40), head_sha: 'b'.repeat(40) }, url: 'https://evil.invalid' } }] };
  const diagnostics = await openApp(page, { dashboards: { agents: { objectives: [recorded], cards: [] } } });
  await page.evaluate(() => {
    window.__mock.bridge.inspectObjectiveArtifact = (raw, callback) => {
      const payload = JSON.parse(raw);
      window.__mock.artifactRequest = payload;
      callback(JSON.stringify({ ...payload, ok: true, workspaceRoot: '/demo', summary: 'Recorded worker changes', base_sha: 'a'.repeat(40), head_sha: 'b'.repeat(40), text: '<img src=x onerror="window.artifactExecuted=true">\n+new content', truncated: true }));
    };
  });
  await openNav(page, 'Agents');
  await page.getByRole('button', { name: 'Inspect diff' }).click();
  expect(await page.evaluate(() => window.__mock.artifactRequest)).toEqual({ objective_id: 'obj-1', assignment_id: 'a-1', kind: 'diff' });
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('<img src=x onerror="window.artifactExecuted=true">');
  await expect(dialog).toContainText('Preview truncated');
  await expect(dialog.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => window.artifactExecuted)).toBeUndefined();
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole('button', { name: 'Find existing PR' }).click();
  expect(await page.evaluate(() => window.__mock.artifactRequest.kind)).toBe('pr');
  expectNoFatalErrors(diagnostics);
});

test('approve once and request review carry canonical request and revision fences', async ({ page }) => {
  const recorded = { ...objective, revision: 8, allowed_actions: ['request_review'], assignments: [{ ...objective.assignments[0], allowed_actions: ['approve'], pending_approval: { request_id: 'approval-1', kind: 'command', command: ['python', 'check.py'], reason: 'Approval required' } }] };
  const diagnostics = await openApp(page, { dashboards: { agents: { objectives: [recorded], cards: [] } } });
  await openNav(page, 'Agents');
  await expect(page.locator('.agents-detail')).toContainText('check.py');
  await page.getByRole('button', { name: 'Approve once' }).click();
  await page.getByRole('button', { name: 'Request review' }).click();
  expect(await page.evaluate(() => window.__mock.objectiveControls)).toEqual([
    { objective_id: 'obj-1', assignment_id: 'a-1', action: 'approve', value: { request_id: 'approval-1' } },
    { objective_id: 'obj-1', action: 'request_review', value: { revision: 8 } },
  ]);
  expectNoFatalErrors(diagnostics);
});

test('multiple agents checkbox preserves permission mode and model, persists, and travels with retry', async ({ page }) => {
  const diagnostics = await openApp(page, { boot: { prefs: { multiAgentEnabled: true } } });
  const selection = await page.evaluate(() => ({ mode: window.__opai.state.mode.id, model: window.__opai.state.model.id }));
  await page.locator('#modeBtn').click();
  const toggle = page.getByRole('menuitemcheckbox', { name: /Allow multiple agents mode/ });
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await page.keyboard.press('Escape');
  const id = await sendPrompt(page);
  expect(await page.evaluate(() => window.__mock.lastRequest)).toMatchObject({ ...selection, multiAgentEnabled: true });
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(['multi_agent_enabled', 'true']);
  await page.evaluate((rid) => window.__mock.emitReply(rid, { status: 'failed', error: 'Temporary provider failure' }), id);
  await page.evaluate(() => window.__opai.setMultiAgentEnabled(false));
  await page.evaluate(() => window.__opai.send(window.__opai.state.lastSend));
  expect(await page.evaluate(() => window.__mock.lastRequest.multiAgentEnabled)).toBe(true);
  expectNoFatalErrors(diagnostics);
});

test('cloud permission is explicit and applies only to the next objective', async ({ page }) => {
  const diagnostics = await openApp(page, { boot: { prefs: { multiAgentEnabled: true } } });
  await page.locator('#modeBtn').click();
  const cloud = page.getByRole('menuitemcheckbox', { name: /Allow cloud providers for this objective/ });
  await expect(cloud).toHaveAttribute('aria-checked', 'false');
  await expect(cloud).toContainText('Sends code and context');
  await expect(cloud).toContainText('paid or account quota');
  await cloud.click();
  await expect(cloud).toHaveAttribute('aria-checked', 'true');
  await page.keyboard.press('Escape');
  const first = await sendPrompt(page, 'First objective');
  expect(await page.evaluate(() => window.__mock.lastRequest)).toMatchObject({ multiAgentEnabled: true, allowCloud: true });
  expect(await page.evaluate(() => window.__opai.state.agentsAllowCloud)).toBe(false);
  expect(await page.evaluate(() => window.__mock.savedPrefs)).not.toContainEqual(['agents_allow_cloud', 'true']);
  await page.evaluate((rid) => window.__mock.emitReply(rid, { status: 'failed', error: 'Temporary failure' }), first);
  // Consent remembered for one free model cannot authorize all objective routes.
  await page.evaluate(() => {
    window.__opai.state.model.kind = 'free';
    window.__opai.state.freeConsent.add(window.__opai.state.model.id);
  });
  await sendPrompt(page, 'Second objective');
  expect(await page.evaluate(() => window.__mock.lastRequest)).toMatchObject({ multiAgentEnabled: true, allowCloud: false });
  expectNoFatalErrors(diagnostics);
});

test('disabling agents or changing workspace clears pending cloud consent', async ({ page }) => {
  const diagnostics = await openApp(page, { boot: { prefs: { multiAgentEnabled: true } } });
  await page.locator('#modeBtn').click();
  const agents = page.getByRole('menuitemcheckbox', { name: /Allow multiple agents mode/ });
  const cloud = page.getByRole('menuitemcheckbox', { name: /Allow cloud providers for this objective/ });
  await cloud.click();
  await agents.click();
  await expect(cloud).toHaveCount(0);
  await agents.click();
  await expect(cloud).toHaveAttribute('aria-checked', 'false');
  await cloud.click();
  await page.evaluate(() => window.__opai.applyBootSelection(window.__opai.state.boot));
  expect(await page.evaluate(() => window.__opai.state.agentsAllowCloud)).toBe(false);
  expectNoFatalErrors(diagnostics);
});

test('canonical recovery renders with provider readiness and controls use journal IDs', async ({ page }) => {
  const diagnostics = await openApp(page, { dashboards: { agents: { objectives: [objective], cards: [{ title: 'Provider readiness', body: 'Local worker ready' }] } } });
  await openNav(page, 'Agents');
  await expect(page.locator('#dashPage')).toContainText('Repair independent regressions');
  await expect(page.locator('#dashPage')).toContainText('Local worker ready');
  await expect(page.locator('#dashPage')).toContainText('Not reported');
  await page.locator('[data-agent-action="pause"]').click();
  expect(await page.evaluate(() => window.__mock.objectiveControls)).toEqual([{ objective_id: 'obj-1', action: 'pause' }]);
  await page.getByLabel('Budget in USD').fill('7.5');
  await page.locator('[data-agent-action="budget"]').click();
  await page.getByLabel('Assignment model').fill('local-coder');
  await page.locator('[data-agent-action="reroute"]').click();
  expect(await page.evaluate(() => window.__mock.objectiveControls.slice(1))).toEqual([{ objective_id: 'obj-1', action: 'budget', value: '7.5' }, { objective_id: 'obj-1', assignment_id: 'a-1', action: 'reroute', value: 'local-coder' }]);
  await page.evaluate((o) => window.__mock.emitObjectiveControl({ ok: true, objective: { ...o, status: 'paused', allowed_actions: ['resume'] }, workspaceRoot: '/demo' }), objective);
  await expect(page.locator('.agents-objective > header')).toContainText('Paused');
  await expect(page.locator('[data-agent-action="pause"]')).toHaveCount(0);
  await page.evaluate((o) => window.__mock.emitObjectiveControl({ ok: true, objective: { ...o, status: 'completed' }, workspaceRoot: '/other' }), objective);
  await expect(page.locator('.agents-objective > header')).toContainText('Paused');
  expectNoFatalErrors(diagnostics);
});

test('objective signal releases chat only for the active request and workspace', async ({ page }) => {
  await openApp(page, { boot: { prefs: { multiAgentEnabled: true } }, workspaceSwitch: { boot: { prefs: { multiAgentEnabled: false } } }, dashboards: { agents: { objectives: [objective] } } });
  const id = await sendPrompt(page);
  await page.evaluate((o) => window.__mock.emitObjective({ requestId: 'stale', objective: o, workspaceRoot: '/demo' }), objective);
  expect(await page.evaluate(() => window.__opai.state.busy)).toBe(true);
  await page.evaluate(({ o, id }) => window.__mock.emitObjective({ requestId: id, objective: o, workspaceRoot: '/other' }), { o: objective, id });
  expect(await page.evaluate(() => window.__opai.state.busy)).toBe(true);
  await page.evaluate(({ o, id }) => window.__mock.emitObjective({ requestId: id, objective: o, workspaceRoot: '/demo' }), { o: objective, id });
  await expect(page.locator('#dashPage')).toContainText('Repair independent regressions');
  expect(await page.evaluate(() => ({ busy: window.__opai.state.busy, view: window.__opai.state.view }))).toEqual({ busy: false, view: 'agents' });
  await page.evaluate(() => window.__mock.switchWorkspace('/other'));
  await expect.poll(() => page.evaluate(() => window.__opai.state.boot.workspace.root)).toBe('/other');
  expect(await page.evaluate(() => window.__opai.state.multiAgentEnabled)).toBe(false);
  expect(await page.evaluate(() => window.__opai.state.agentsSnapshot)).toBe(null);
});

test('older objective and dashboard responses cannot replace a newer request or another view', async ({ page }) => {
  await openApp(page, { boot: { prefs: { multiAgentEnabled: true } }, dashboards: { agents: { objectives: [objective] } } });
  const oldId = await sendPrompt(page, 'First objective');
  await page.evaluate(({ o, id }) => window.__mock.emitObjective({ requestId: id, objective: o, workspaceRoot: '/demo' }), { o: objective, id: oldId });
  await expect(page.locator('#dashPage')).toContainText(objective.objective);
  const oldPoll = await page.evaluate(() => window.__mock.dashboardRequests.at(-1).requestId);
  await openNav(page, 'Chat');
  const newId = await sendPrompt(page, 'Second objective');
  await page.evaluate(({ o, id, poll }) => {
    window.__mock.emitObjective({ requestId: id, objective: { ...o, objective: 'Stale objective' }, workspaceRoot: '/demo' });
    window.__mock.emitDashboard({ requestId: poll, workspaceRoot: '/demo', data: { objectives: [{ ...o, objective: 'Stale dashboard' }] } });
  }, { o: objective, id: oldId, poll: oldPoll });
  expect(await page.evaluate(() => ({ view: window.__opai.state.view, requestId: window.__opai.state.currentRequest, busy: window.__opai.state.busy }))).toEqual({ view: 'chat', requestId: newId, busy: true });
  await page.evaluate(({ o, id }) => window.__mock.emitObjective({ requestId: id, objective: { ...o, objective_id: 'obj-2', objective: 'Second objective' }, workspaceRoot: '/demo' }), { o: objective, id: newId });
  expect(await page.evaluate(() => window.__opai.state.busy)).toBe(false);
});

for (const viewport of [{ width: 1440, height: 1080 }, { width: 520, height: 1000 }]) {
  test(`journal evidence is readable and contained at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    const recorded = { ...objective, cost_usd: '0.125001', assignments: [
      { ...objective.assignments[0], role: 'Implementer', owner: 'worker-api', objective: 'Repair the API response without changing the public contract.', model: 'local-coder', status: 'running', cost_usd: '0.125001', allowed_actions: ['stop'], depends_on: [], blocked_reason: '', changed_files: ['api/server.py'], worktree: '/worktrees/objective-1/api-repair', branch: 'codex/agents-api-repair', activity: 'Checking API response compatibility', verification: { status: 'pending-integration', summary: 'Worker checks are recorded; integrated verification is still pending.' } },
      { assignment_id: 'a-2', title: 'Cover response boundaries', role: 'Tester', status: 'pending', model: 'local-coder', intended_paths: ['tests/api/'], depends_on: ['a-1'], cost_usd: null, activity: 'Waiting for the API repair' },
    ] };
    const diagnostics = await openApp(page, { boot: { prefs: { showPanel: false } }, dashboards: { agents: { objectives: [recorded], cards: [] } } });
    await page.getByRole('button', { name: 'Agents workspace', exact: true }).click();
    await expect(page.locator('.agents-metrics')).toContainText('$0.125001');
    await expect(page.locator('.agents-detail')).toContainText('Checking API response compatibility');
    await expect(page.locator('.agents-evidence').first()).not.toHaveAttribute('open');
    expect(await page.locator('.agents-workspace').evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`agents-${viewport.width}.png`), fullPage: true });
    await page.locator('[data-agent-select="a-2"]').click();
    await expect(page.locator('.agents-detail h3')).toHaveText('Cover response boundaries');
    await expect(page.locator('.agents-detail')).toContainText('API repair · a-1');
    await page.evaluate(() => window.__mock.emitObjectiveControl({ ok: true, workspaceRoot: '/demo', objective: { objective_id: 'unsafe', objective: '<img src=x onerror="alert(1)">', status: '<script>unsafe</script>', assignments: [] } }));
    await expect(page.locator('.agents-workspace img, .agents-workspace script')).toHaveCount(0);
    expectNoFatalErrors(diagnostics);
  });
}
