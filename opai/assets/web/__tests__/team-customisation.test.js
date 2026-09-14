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
