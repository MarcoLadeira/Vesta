import { describe, it, expect } from 'vitest';
import '../agents-team.js';
const team = globalThis.OPaiAgentsTeam;

describe('AI team presentation', () => {
  it('uses stored identities after assignment order changes', () => {
    const a = { assignment_id: 'a', display_name: 'Ada', avatar_index: 2, title: 'Implement', status: 'running' };
    expect(team.name(a, 0)).toBe('Ada');
    expect(team.feedHtml({ assignments: [a] })).toContain(team.avatar(2));
    expect(team.feedHtml({ assignments: [{ assignment_id: 'b' }, a] })).toContain(team.avatar(2));
  });
  it('renders untrusted names and findings as text and only shows real dependency links', () => {
    const objective = { objective_id: 'o', assignments: [{ assignment_id: 'a', display_name: '<img onerror="bad()">', activity: '<script>bad()</script>' }, { assignment_id: 'b', role: 'reviewer', depends_on: ['a'] }] };
    const html = team.panelHtml(objective, 'b');
    expect(html).toContain('Reviews work from');
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('<img');
    expect(team.feedHtml(objective)).not.toContain('<script>');
    expect(team.panelHtml(objective, 'a')).not.toContain('Reviews work from');
  });
  it('only exposes review and execution actions advertised by the journal', () => {
    const objective = { assignments: [{ assignment_id: 'a', allowed_actions: ['stop'] }], allowed_actions: [] };
    const html = team.panelHtml(objective, 'a');
    expect(html).toContain('Stop agent');
    expect(html).not.toContain('Approve once');
    expect(html).not.toContain('Ask for team review');
  });
});
