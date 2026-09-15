import { describe, it, expect } from 'vitest';
import '../agents-team.js';
const team = globalThis.VestaAgentsTeam;

describe('AI team presentation', () => {
  it('keeps structured work details and excludes streaming status from the timeline', () => {
    const objective = { assignments: [{ assignment_id: 'a', title: 'Repair auth' }], timeline: [
      { sequence: 1, kind: 'activity', assignment_id: 'a', activity: JSON.stringify({ title: 'Edited file', detail: 'src/auth.ts', channel: 'feed' }) },
      { sequence: 2, kind: 'activity', assignment_id: 'a', activity: JSON.stringify({ title: 'Streaming response', channel: 'status' }) },
    ] };
    expect(team.feedHtml(objective)).toContain('src/auth.ts');
    expect(team.feedHtml(objective)).not.toContain('Streaming response');
    expect(team.feedHtml(objective)).not.toContain('team-state');
  });
  it('uses stored identities after assignment order changes', () => {
    const a = { assignment_id: 'a', display_name: 'Ada', avatar_index: 2, title: 'Implement', status: 'running' };
    expect(team.name(a, 0)).toBe('Ada');
    expect(team.feedHtml({ assignments: [a], timeline: [{ sequence: 1, kind: 'activity', assignment_id: 'a', activity: 'Edited auth.ts' }] })).toContain(team.avatar(2));
    expect(team.feedHtml({ assignments: [{ assignment_id: 'b' }, a], timeline: [{ sequence: 1, kind: 'activity', assignment_id: 'a', activity: 'Edited auth.ts' }] })).toContain(team.avatar(2));
  });
  it('renders untrusted names and findings as text and only shows real dependency links', () => {
    const objective = { objective_id: 'o', assignments: [{ assignment_id: 'a', display_name: '<img onerror="bad()">', activity: '<script>bad()</script>' }, { assignment_id: 'b', status: 'running', role: 'reviewer', depends_on: ['a'] }] };
    const html = team.panelHtml(objective, 'b');
    expect(html).toContain('Reviews work from');
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('<img');
    objective.timeline = [{ sequence: 1, kind: 'activity', assignment_id: 'a', activity: '<script>bad()</script>' }];
    expect(team.feedHtml(objective)).not.toContain('<script>');
    expect(team.feedHtml(objective)).toContain('&lt;script&gt;');
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
