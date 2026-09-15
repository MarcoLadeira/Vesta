import { describe, it, expect } from 'vitest';
import '../chat-time.js';
import '../agents-team.js';
import '../team-map.js';

const team = globalThis.OPaiAgentsTeam, map = globalThis.OPaiTeamMap;

describe('team customisation projections', () => {
  it('labels local calendar dates and never invents missing historical times', () => {
    const stamp = globalThis.OPaiChatTime.stamp, now = new Date(2026, 8, 14, 12);
    expect(stamp(new Date(2026, 8, 14, 9, 21), now).label).toBe('Today 09:21');
    expect(stamp(new Date(2026, 8, 13, 21, 21), now).label).toBe('Yesterday 21:21');
    expect(stamp(null, now)).toBeNull(); expect(stamp('invalid', now)).toBeNull();
  });
  it('keeps one actor while rendering the complete saved conversation outside the live event window', () => {
    const first = { assignment_id: 'a', agent_id: 'a', team_order: 0, status: 'completed', objective: 'First task', result: { handoff: { summary: 'Recorded result' } } };
    const second = { ...first, assignment_id: 'a2', team_order: 2, status: 'pending', user_message: '<img onerror="bad()">', result: {} };
    const objective = { assignments: [first, { assignment_id: 'b' }, second], timeline: [] };
    expect(team.agents(objective)).toHaveLength(2);
    expect(team.conversationHtml(objective, second)).toContain('Recorded result');
    expect(team.conversationHtml(objective, second)).toContain('&lt;img');
    expect(team.conversationHtml(objective, second)).not.toContain('<img');
  });
  it('arranges dependent agents after their source and derives arrows only from real tasks', () => {
    const objective = { assignments: [
      { assignment_id: 'a', name: 'api', group: 'Auth' },
      { assignment_id: 'b', name: 'review', group: 'Auth', depends_on: ['api'] },
      { assignment_id: 'a2', agent_id: 'a', team_order: 2, name: 'followup', group: 'Auth', depends_on: ['api'] },
    ] };
    const positions = map.arrange(objective);
    expect(Object.keys(positions)).toEqual(['a', 'b']);
    expect(positions.b.x).toBeGreaterThan(positions.a.x);
    expect(map.edges(objective)).toHaveLength(1);
    expect(map.graphHtml(objective, positions).html).toContain('sends results to');
    const hostile = map.graphHtml(objective, { a: { x: '0px"><img onerror="bad()">', y: 0 } }).html;
    expect(hostile).not.toContain('<img');
    expect(hostile).not.toContain('onerror');
    expect(team.state({ held: true, status: 'pending', depends_on: ['a'] }, [{ assignment_id: 'a', status: 'running' }])).not.toContain('OPai will continue');
  });
});


describe('large team map compression', () => {
  const objective = { assignments: Array.from({ length: 9 }, (_, i) => ({ assignment_id: 'a' + i, name: 'task' + i, display_name: 'Agent ' + i, group: 'Group ' + Math.floor(i / 3), title: 'Task ' + i, status: i < 3 ? 'running' : 'completed', depends_on: i === 3 ? ['task0'] : [] })) };
  it('compresses inactive groups without changing stored layout and exposes them for editing or tracing', () => {
    const positions = map.arrange(objective), before = structuredClone(positions);
    const normal = map.graphHtml(objective, positions);
    expect((normal.html.match(/data-map-node=/g) || [])).toHaveLength(3);
    expect(normal.html).toContain('✓ Complete');
    expect(positions).toEqual(before);
    expect((map.graphHtml(objective, positions, { editing: true }).html.match(/data-map-node=/g) || [])).toHaveLength(9);
    const traced = map.graphHtml(objective, positions, { focusId: 'a0' });
    expect(traced.html).toContain('data-map-node="a3"');
    expect(traced.html).toContain('data-map-edge="0"');
    expect(map.graphHtml(objective, positions, { showAll: true }).html).toContain('data-map-edge="0"');
    expect(map.graphHtml(objective, positions, { collapsed: new Map([['Group 1', false]]) }).html).toContain('data-map-node="a3"');
  });
  it('keeps approval and failed work visible, and does not compress overlapping group bands', () => {
    const attention = structuredClone(objective); attention.assignments[4].pending_approval = { tool: 'edit' };
    expect(map.graphHtml(attention, map.arrange(attention)).html).toContain('Needs you');
    const overlap = Object.fromEntries(objective.assignments.map((a, i) => [a.assignment_id, { x: i * 280, y: 64 }]));
    const html = map.graphHtml(objective, overlap).html;
    expect(html).toContain('left:0px;top:20px');
    expect(map.status({ status: 'pending', depends_on: ['task0'] }, objective).label).toBe('Waiting on Agent 0');
  });
});

it('shows the recorded attention reason before activity and preserves canonical approval controls', () => {
  const agent = { assignment_id: 'a', title: 'Review payment retries', status: 'blocked', pending_approval: { reason: '<img onerror="bad()">', command: ['git', 'diff'], request_id: 'request-1' }, allowed_actions: ['approve', 'stop'] };
  const html = team.panelHtml({ assignments: [agent] }, 'a');
  expect(html.indexOf('Needs you')).toBeLessThan(html.indexOf('class="team-feed"'));
  expect(html).toContain('&lt;img'); expect(html).not.toContain('<img');
  expect((html.match(/data-team-action="approve"/g) || [])).toHaveLength(1);
  expect(team.attentionHtml({ status: 'failed', role: 'reviewer', blocked_reason: 'Review checks failed' })).toContain('Review checks failed');
  expect(team.attentionHtml({ status: 'running' })).toBe('');
});


it('uses only explicitly running work for the current update', () => {
  expect(team.currentActivity({ status: 'running', activity: 'Read auth.ts' })).toBeNull();
  expect(team.currentActivity({ status: 'running', activity: JSON.stringify({ status: 'success', title: 'Checked token expiry' }) })).toBeNull();
  const activity = { status: 'running', title: 'Running sign-in checks', timestamp: 1789400000000 };
  expect(team.currentActivity({ status: 'running', activity: JSON.stringify(activity) })).toEqual({ title: activity.title, timestamp: activity.timestamp });
  expect(team.currentActivity({ status: 'completed', activity })).toBeNull();
  expect(map.status({ status: 'failed' }, {}).kind).toBe('failed');
  expect(map.status({ status: 'blocked' }, {}).label).toBe('Needs you');
});
