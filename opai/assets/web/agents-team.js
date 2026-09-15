(function (global) {
  "use strict";
  const esc = (v) => String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const rows = (v) => Array.isArray(v) ? v : [];
  const names = ["Alex", "Sam", "Taylor", "Riley", "Jordan", "Morgan", "Casey", "Robin"];
  const palettes = [["#bde9f6", "#b87850", "#242e43"], ["#dacdf8", "#f0c39c", "#2e243b"], ["#c8e6e5", "#e0a77c", "#32333b"], ["#ffe1b2", "#9d613f", "#29242d"]];
  const profileIndex = (agent, index) => Number.isInteger(agent.avatar_index) && agent.avatar_index >= 0 && agent.avatar_index < 32 ? agent.avatar_index : index;
  const name = (agent, index) => agent.display_name || names[profileIndex(agent, index) % names.length] + (profileIndex(agent, index) >= names.length ? " " + (Math.floor(profileIndex(agent, index) / names.length) + 1) : "");
  const actorId = (agent) => agent.agent_id || agent.assignment_id;
  function agents(objective) {
    const assignments = rows(objective?.assignments), profiles = new Map();
    assignments.forEach((a) => {
      const previous = profiles.get(actorId(a));
      if (!previous || (previous.status !== 'running' && (a.status === 'running' || (a.team_order || 0) >= (previous.team_order || 0)))) profiles.set(actorId(a), a);
    });
    return Array.from(profiles.values());
  }
  function modelOptions(current, models) {
    const available = rows(models).filter((m) => m.value);
    if (!available.some((m) => m.value === current)) available.unshift({ value: current || 'auto', label: current === 'auto' || !current ? 'Auto model' : current });
    return available.map((m) => '<option value="' + esc(m.value) + '"' + (m.value === current ? ' selected' : '') + '>' + esc(m.label) + '</option>').join('');
  }
  function avatar(index) {
    const [background, skin, hair] = palettes[index % palettes.length];
    const long = index % 2 === 1;
    return '<svg class="team-avatar" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="24" fill="' + background + '"/>' + (long ? '<path d="M11 35V20C11 3 37 3 37 20V35Z" fill="' + hair + '"/>' : '') + '<path d="M7 48C7 31 41 31 41 48" fill="' + hair + '"/><path d="M20 29h8v9c-3 3-5 3-8 0" fill="' + skin + '"/><ellipse cx="24" cy="21" rx="10" ry="13" fill="' + skin + '"/><path d="M13 21C9 4 37 2 35 20L30 12c-5 7-12 1-17 9" fill="' + hair + '"/><path d="M19 22v1m10-1v1" stroke="' + hair + '" stroke-width="2" stroke-linecap="round"/><path d="M21 29q3 2 6 0" fill="none" stroke="' + hair + '" stroke-linecap="round"/>' + (index % 4 === 2 ? '<path d="M15 19h8v7h-8zm10 0h8v7h-8zm-2 2h2" fill="none" stroke="' + hair + '"/>' : '') + '</svg>';
  }
  function state(agent, assignments) {
    const upstream = dependencies(agent, assignments).filter((a) => a.status !== "completed");
    const waiting = ["pending", "queued", "blocked"].includes(agent.status) && upstream.length;
    let label = agent.held ? "Ready when you are" : agent.pending_approval ? "Needs you" : waiting ? "Waiting for " + upstream.map((a) => name(a, assignments.indexOf(a))).join(", ") : agent.blocked_reason || agent.admission?.reason || ({ running: "Working", completed: "Done", failed: "Failed", pending: "Queued", blocked: "Needs you" })[agent.status] || String(agent.status || "Queued").replace(/[-_]/g, " ");
    const tone = agent.pending_approval ? "attention" : waiting ? "waiting" : agent.status === "running" ? "active" : agent.status === "completed" ? "done" : agent.status === "failed" ? "failed" : ["needs-attention", "blocked"].includes(agent.status) ? "attention" : "waiting";
    const automatic = !agent.held && waiting && upstream.every((a) => a.status === "running" && !a.pending_approval);
    if (automatic) label += " · Vesta will continue";
    return '<span class="team-state team-state-' + tone + '"><i aria-hidden="true"></i>' + esc(label) + '</span>';
  }
  function activities(agent) {
    let value = agent.activity;
    if (typeof value === "string" && /^[\[{]/.test(value.trim())) { try { value = JSON.parse(value); } catch (_) {} }
    return (Array.isArray(value) ? value : value ? [value] : []).filter((entry) => entry && entry.channel !== 'status').map((entry) => typeof entry === "string" ? entry : [entry.title || entry.message, entry.detail].filter((v) => typeof v === 'string' && v).join(' · ') || "Activity recorded");
  }
  function dependencies(agent, assignments) {
    return rows(agent.depends_on).map((id) => assignments.find((a) => a.assignment_id === id || a.name === id)).filter(Boolean);
  }
  function connection(agent, assignments) {
    const upstream = dependencies(agent, assignments);
    if (!upstream.length || agent.status !== "running") return "";
    const reviewing = /review/i.test(agent.role || "");
    return '<div class="team-connection"><span aria-hidden="true">' + (reviewing ? '◇' : '↳') + '</span><span>' + (reviewing ? 'Reviews work from ' : 'Receives work from ') + upstream.map((a) => '<button type="button" data-team-select="' + esc(a.assignment_id) + '">' + esc(name(a, assignments.indexOf(a))) + '</button>').join(', ') + '</span></div>';
  }
  function eventText(event, agent, assignments) {
    if (event.kind === 'team-updated' && event.message) return event.message;
    if (event.kind === 'activity') return activities({ activity: event.activity }).join(' · ');
    if (event.kind === 'claimed') {
      const upstream = dependencies(agent, assignments);
      return /review/i.test(agent.role || '') && upstream.length ? 'Started reviewing ' + upstream.map((a) => name(a, assignments.indexOf(a))).join(', ') + "’s work" : 'Started: ' + (agent.title || agent.objective);
    }
    if (event.kind === 'assignment-finished') return ({ completed: 'Completed: ', failed: 'Needs attention: ', cancelled: 'Stopped: ', 'needs-attention': 'Needs your attention: ' })[event.status] + (agent.title || agent.objective);
    return '';
  }
  function feedHtml(objective, selectedId) {
    const assignments = rows(objective.assignments);
    const selectedActor = actorId(assignments.find((a) => a.assignment_id === selectedId) || {});
    const events = rows(objective.timeline).filter((e) => !selectedId || actorId(assignments.find((a) => a.assignment_id === e.assignment_id) || {}) === selectedActor);
    let previous = '', previousMinute = null;
    const timeline = events.map((event) => {
      const agent = assignments.find((a) => a.assignment_id === event.assignment_id);
      if (!agent) return '';
      const text = eventText(event, agent, assignments);
      const signature = JSON.stringify([event.assignment_id, event.kind, text]);
      if (!text || signature === previous) return '';
      previous = signature;
      const date = new Date(event.occurred_at);
      const time = Number.isNaN(date.valueOf()) ? '' : '<time datetime="' + esc(event.occurred_at) + '" title="' + esc(date.toLocaleString()) + '">' + esc(date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })) + '</time>';
      const stamp = global.OPaiChatTime?.stamp(event.occurred_at);
      const separator = stamp && (previousMinute === null || stamp.minute - previousMinute >= 5) ? '<li class="team-time"><time class="chat-timestamp" datetime="' + esc(stamp.iso) + '" title="' + esc(stamp.title) + '">' + esc(stamp.label) + '</time></li>' : '';
      if (separator) previousMinute = stamp.minute;
      const fromUser = event.kind === 'team-updated' && event.message;
      return separator + '<li><button type="button" class="team-update' + (fromUser ? ' team-user-message' : '') + '" data-team-select="' + esc(agent.assignment_id) + '" data-team-event="' + esc(event.sequence) + '">' + avatar(profileIndex(agent, assignments.indexOf(agent))) + '<span class="team-update-body"><strong class="team-event-work">' + esc(text) + '</strong><span class="team-event-by">' + esc(fromUser ? 'You → ' + name(agent, assignments.indexOf(agent)) : name(agent, assignments.indexOf(agent))) + time + '</span>' + (event.summary ? '<span class="team-current">' + esc(event.summary) + '</span>' : '') + (event.verification_summary ? '<span class="team-check">' + esc(event.verification_summary) + '</span>' : '') + '</span></button></li>';
    }).join('');
    return '<section class="team-feed" aria-label="Team updates">' + (objective.timeline_truncated ? '<p class="team-empty">Recent activity · earlier events remain in the journal</p>' : '') + (timeline ? '<ol class="team-timeline">' + timeline + '</ol>' : '<p class="team-empty">' + (assignments.length ? 'Waiting for recorded activity.' : 'Your team is getting ready.') + '</p>') + '</section>';
  }
  function conversationHtml(objective, selected) {
    const chain = rows(objective.assignments).filter((a) => actorId(a) === actorId(selected)).sort((a, b) => (a.team_order || 0) - (b.team_order || 0));
    if (!chain.some((a) => a.user_message)) return '';
    const time = (value) => { const stamp = global.OPaiChatTime?.stamp(value); return stamp ? '<time class="chat-timestamp" datetime="' + esc(stamp.iso) + '">' + esc(stamp.label) + '</time>' : ''; };
    return '<section class="team-conversation" aria-label="Agent conversation">' + chain.map((a) => {
      const reply = a.result?.handoff?.summary;
      return time(a.created_at) + '<div class="team-conversation-message team-conversation-user"><span>' + (a.user_message ? 'You' : 'Task') + '</span><p>' + esc(a.user_message || a.objective) + '</p></div>' + (reply ? time(a.finished_at) + '<div class="team-conversation-message"><span>' + esc(name(a, 0)) + '</span><p>' + esc(reply) + '</p></div>' : '<p class="team-message-hint">' + (a.held ? 'Ready to start' : a.status === 'pending' ? 'Queued after earlier work' : a.status === 'running' ? 'Working on this message…' : 'No reply recorded') + '</p>');
    }).join('') + '</section>';
  }
  function stripHtml(objective, objectives, selectedId) {
    const teams = rows(objectives).length ? rows(objectives) : objective ? [objective] : [];
    const people = teams.flatMap((o) => {
      const groups = new Map();
      agents(o).forEach((agent, index) => { const key = agent.group || ''; if (!groups.has(key)) groups.set(key, []); groups.get(key).push({ agent, index, objective: o }); });
      return Array.from(groups.values()).flat();
    });
    let previousGroup = null;
    return '<button type="button" class="team-strip-open" aria-label="Expand AI Team" title="Your AI teams"><span>' + (people.length || '✧') + '</span><span class="team-strip-label">' + (people.length === 1 ? 'agent' : 'agents') + '</span></button><nav class="team-shortcuts" aria-label="Agent shortcuts">' + people.map(({ agent, index, objective: o }) => {
      const label = name(agent, index);
      const status = agent.held ? 'Ready to start' : agent.pending_approval ? 'Needs you' : ({ running: 'Working', completed: 'Done', pending: 'Waiting', failed: 'Failed', blocked: 'Needs you' })[agent.status] || agent.status || 'Waiting';
      const symbol = agent.pending_approval || ['failed', 'blocked', 'needs-attention'].includes(agent.status) ? '!' : agent.status === 'completed' ? '✓' : agent.status === 'running' ? '●' : '·';
      const group = o.objective_id + ':' + (agent.group || '');
      const separator = group !== previousGroup ? '<span class="team-shortcut-group" role="separator" aria-label="' + esc(agent.group || o.objective) + '" title="' + esc(agent.group || o.objective) + '">' + esc((agent.group || 'Team').slice(0, 3)) + '</span>' : '';
      previousGroup = group;
      return separator + '<button type="button" class="team-shortcut" data-team-shortcut="' + esc(agent.assignment_id) + '" data-team-objective="' + esc(o.objective_id) + '" aria-label="' + esc('Open ' + label + "’s agent chat") + '" aria-pressed="' + (selectedId === agent.assignment_id && objective?.objective_id === o.objective_id) + '" title="' + esc(label + ' · ' + status + '\n' + (agent.title || agent.objective) + '\n' + (agent.group ? agent.group + ' · ' : '') + o.objective) + '">' + avatar(profileIndex(agent, index)) + '<span class="team-shortcut-status" aria-hidden="true">' + symbol + '</span></button>';
    }).join('') + '</nav>';
  }
  function attentionHtml(agent) {
    if (!agent.pending_approval && !['blocked', 'failed', 'needs-attention'].includes(agent.status)) return '';
    const title = agent.status === 'failed' && !agent.pending_approval ? 'Failed' : 'Needs you';
    const reason = agent.pending_approval?.reason || agent.blocked_reason || agent.admission?.reason || agent.result?.handoff?.summary || 'No explanation was recorded. Inspect the recorded work before deciding how to continue.';
    const approval = agent.pending_approval;
    const evidence = approval && (approval.command || approval.files);
    return '<section class="team-attention-detail' + (title === 'Failed' ? ' is-failed' : '') + '"><strong>' + esc(title) + '</strong><p>' + esc(reason) + '</p>' + (evidence ? '<pre>' + esc(JSON.stringify(evidence, null, 2)) + '</pre>' : '') + '<div class="team-actions">' + rows(agent.allowed_actions).filter((action) => ['approve', 'retry'].includes(action)).map((action) => '<button type="button" class="btn" data-team-action="' + action + '">' + (action === 'approve' ? 'Approve once' : 'Retry') + '</button>').join('') + '</div></section>';
  }
  function focusContext(objective, agent) {
    const assignments = rows(objective.assignments), parents = dependencies(agent, assignments).filter((a) => a.status !== 'completed');
    const next = agents(objective).filter((a) => !['completed', 'cancelled'].includes(a.status) && dependencies(a, assignments).some((source) => actorId(source) === actorId(agent)));
    const paths = rows(agent.intended_paths);
    return '<dl class="team-focus-context">' + (paths.length ? '<dt>Scope</dt><dd><details class="team-explanation team-scope"><summary>' + paths.length + (paths.length === 1 ? ' path' : ' paths') + '</summary>' + paths.map((path) => '<code>' + esc(path) + '</code>').join('') + '</details></dd>' : '') + (parents.length ? '<dt>Waiting on</dt><dd>' + parents.map((a) => esc(name(a, assignments.indexOf(a)))).join(', ') + '</dd>' : '') + (next.length ? '<dt>Next</dt><dd>Hand off to ' + next.map((a) => esc(name(a, assignments.indexOf(a)))).join(', ') + '</dd>' : '') + '</dl><div class="team-focus-actions"><button type="button" class="team-quiet" data-team-view-work>View work</button>' + (agent.team_controls?.can_message ? '<button type="button" class="team-quiet" data-team-message-focus>Message ' + esc(name(agent, assignments.indexOf(agent))) + '</button>' : '') + '</div>';
  }
  function panelHtml(objective, selectedId, unavailable, models) {
    const assignments = rows(objective && objective.assignments);
    const selected = assignments.find((a) => a.assignment_id === selectedId);
    let html = '<header class="team-header"><h2>AI Team</h2><button type="button" class="team-quiet" data-team-close aria-label="Collapse AI Team">Collapse</button></header>';
    if (!objective) return html + '<p class="team-empty">' + esc(unavailable || 'Send an objective and your team will get to work. Each agent will appear here.') + '</p>';
    if (unavailable) html += '<p class="team-empty" role="status">' + esc(unavailable) + '</p>';
    html += '<p class="team-objective">' + esc(objective.objective) + '</p><nav class="team-roster" aria-label="Your agents"' + (selected ? ' hidden' : '') + '>';
    html += agents(objective).map((a, index) => '<button type="button" class="team-person" data-team-select="' + esc(a.assignment_id) + '" aria-pressed="' + (a === selected) + '">' + avatar(profileIndex(a, index)) + '<span><strong>' + esc(name(a, index)) + '</strong><span class="team-current">' + esc(a.title || a.objective) + '</span>' + state(a, assignments) + '</span></button>').join('');
    html += '</nav>';
    if (!selected && objective.team_controls) html += '<div class="team-actions"><button type="button" class="team-quiet" data-team-add' + (objective.team_controls.can_add ? '' : ' disabled') + '>+ Add agent</button><button type="button" class="team-quiet" data-team-map>Organise team</button></div>';
    if (!assignments.length) html += '<p class="team-empty">Putting your team together…</p>';
    if (selected) {
      const index = assignments.indexOf(selected);
      html += '<section class="team-detail" aria-label="Agent details"><button type="button" class="team-quiet" data-team-back>← Back to team</button><h3>' + esc(selected.title || selected.objective) + '</h3><header><span class="team-detail-person">' + avatar(profileIndex(selected, index)) + esc(name(selected, index)) + '</span><details class="team-name-editor"><summary>Rename</summary><form data-team-rename><label>Agent name<input name="agentName" aria-label="Agent name" maxlength="40" required value="' + esc(name(selected, index)) + '"></label><button type="submit" class="btn">Save name</button></form></details></header>';
      const attention = attentionHtml(selected);
      html += attention || state(selected, assignments);
      html += connection(selected, assignments) + focusContext(objective, selected);
      if (selected.rationale) html += '<details class="team-explanation"><summary>Why this task</summary><p>' + esc(selected.rationale) + '</p></details>';
      const conversation = conversationHtml(objective, selected);
      html += conversation ? conversation + '<details class="team-explanation"><summary>Recent work activity</summary>' + feedHtml(objective, selectedId) + '</details>' : feedHtml(objective, selectedId);
      if (selected.team_controls) {
        html += '<details class="team-explanation team-settings"><summary>Model & group</summary><form data-team-settings><label>AI model<select name="agentModel" aria-label="AI model">' + modelOptions(selected.preferred_model || selected.model || 'auto', models) + '</select></label><p class="team-empty">Applies to unstarted and future work. This objective’s permissions and budget still apply.</p><label>Group<input name="agentGroup" maxlength="40" list="teamGroups" placeholder="No group" value="' + esc(selected.group || '') + '"></label><datalist id="teamGroups">' + Array.from(new Set(assignments.map((a) => a.group).filter(Boolean))).map((g) => '<option value="' + esc(g) + '"></option>').join('') + '</datalist><button type="submit" class="btn">Save settings</button></form></details>';
        if (selected.team_controls.can_start) html += '<button type="button" class="btn primary" data-team-start>Start agent</button>';
        html += '<form class="team-message-form" data-team-message><label>Message ' + esc(name(selected, index)) + '<textarea name="agentMessage" rows="2" maxlength="8000" required placeholder="Ask a question or give the next task…"' + (selected.team_controls.can_message ? '' : ' disabled') + '></textarea></label><div class="team-message-hint">' + (selected.team_controls.can_message ? 'Queued after current work, in this agent’s thread.' : 'Resolve the current task first, or start a new objective if this team is full.') + '</div><button type="submit" class="btn"' + (selected.team_controls.can_message ? '' : ' disabled') + '>Send to agent</button></form>';
      }
      const findings = selected.result && selected.result.handoff && selected.result.handoff.summary;
      if (findings && !conversation) html += '<p class="team-result">' + esc(findings) + '</p>';
      if (rows(selected.changed_files).length) html += '<details class="team-explanation"><summary>' + rows(selected.changed_files).length + (selected.changed_files.length === 1 ? ' changed file' : ' changed files') + '</summary><ul>' + selected.changed_files.map((v) => '<li>' + esc(typeof v === 'string' ? v : v.path) + '</li>').join('') + '</ul></details>';
      const checks = selected.verification;
      if (checks && (checks.status || typeof checks.passed === 'boolean')) html += '<p class="team-check">Checks: ' + esc(checks.status || (checks.passed ? 'passed' : 'failed')) + '</p>';
      html += '<div class="team-actions">' + rows(selected.allowed_actions).filter((a) => (attention ? ['stop'] : ['approve', 'retry', 'stop']).includes(a)).map((action) => '<button type="button" class="btn" data-team-action="' + action + '">' + ({ approve: 'Approve once', retry: 'Retry', stop: 'Stop agent' })[action] + '</button>').join('') + (selected.worktree ? '<button type="button" class="team-quiet" data-team-diff>Inspect diff</button>' : '') + '</div></section>';
    }
    html += '<footer class="team-footer">' + (rows(objective.allowed_actions).includes('request_review') ? '<button type="button" class="btn" data-team-review>Ask for team review</button>' : '') + '<button type="button" class="team-quiet" data-team-workspace>Full Agents workspace</button></footer>';
    return html;
  }
  function compactInspector(element, objective, agent) {
    element.classList.add('team-inspector-compact');
    const heading = element.querySelector('.team-header h2');
    heading.innerHTML = avatar(profileIndex(agent, 0)) + '<span>' + esc(name(agent, 0)) + '</span>';
    const close = element.querySelector('[data-team-close]'); close.textContent = '×';
    element.querySelector('.team-objective')?.remove();
    element.querySelector('.team-detail > header')?.querySelector('.team-detail-person')?.remove();
    const menu = document.createElement('details'); menu.className = 'team-agent-menu';
    menu.innerHTML = '<summary aria-label="Agent options">•••</summary><div class="team-agent-menu-body"></div>';
    const body = menu.querySelector('div');
    element.querySelectorAll('.team-name-editor, .team-settings, .team-detail > .team-actions, .team-footer, [data-team-back]').forEach((node) => body.appendChild(node));
    element.querySelector('.team-header').insertBefore(menu, close);
    const role = document.createElement('p'); role.className = 'team-agent-role';
    role.textContent = [/review|critic/i.test(agent.role || '') ? 'Reviewer' : agent.role === 'planner' ? 'Planner' : 'Agent', agent.group].filter(Boolean).join(' · ');
    element.querySelector('.team-header').after(role);
    element.querySelector('[data-team-message]')?.setAttribute('hidden', '');
    element.querySelector('.team-focus-actions')?.remove();
    const feed = element.querySelector('.team-feed');
    if (feed) {
      const actor = actorId(agent), events = rows(objective.timeline).filter((event) => actorId(rows(objective.assignments).find((a) => a.assignment_id === event.assignment_id) || {}) === actor && eventText(event, agent, objective.assignments));
      if (events.length) {
        feed.innerHTML = '<h4>Activity</h4>' + feedHtml({ ...objective, timeline: events.slice(-3), timeline_truncated: false }, agent.assignment_id) + (events.length > 3 ? '<details class="team-explanation"><summary>View all activity</summary>' + feedHtml(objective, agent.assignment_id) + '</details>' : '');
      } else feed.innerHTML = '<p class="team-empty">No activity received yet.</p>';
    }
    const taskLabel = document.createElement('p'); taskLabel.className = 'team-task-label'; taskLabel.textContent = ['completed', 'cancelled'].includes(agent.status) ? 'Last task' : 'Current task';
    element.querySelector('.team-detail > h3').before(taskLabel);
    if (!agent.pending_approval && agent.status === 'running') {
      const current = document.createElement('div'); current.className = 'team-current-action';
      const report = currentActivity(agent);
      current.innerHTML = '<span class="team-current-label">Current</span><p>' + esc(report?.title || 'Waiting for the next progress update…') + '</p>';
      if (report?.timestamp) { const stamp = global.OPaiChatTime?.stamp(report.timestamp); if (stamp) current.innerHTML += '<time datetime="' + esc(new Date(report.timestamp).toISOString()) + '">Reported ' + esc(stamp.label) + '</time>'; }
      element.querySelector('.team-detail > .team-state')?.after(current);
    }
  }
  function currentActivity(agent) {
    if (agent.status !== 'running' || agent.pending_approval) return null;
    let value = agent.activity;
    if (typeof value === 'string') { try { value = JSON.parse(value); } catch (_) { return null; } }
    const report = Array.isArray(value) ? value.at(-1) : value;
    if (!report || report.status !== 'running' || report.channel === 'status' || typeof report.title !== 'string') return null;
    return { title: report.title, timestamp: report.timestamp };
  }

  function mountPanel(element, objective, selectedId, options) {
    const key = JSON.stringify([objective, selectedId, options.unavailable, options.models, options.unifiedComposer]);
    if (element._teamKey === key) return;
    element._messageDrafts ||= new Map();
    const oldMessage = element.querySelector('[name="agentMessage"]');
    if (oldMessage && element._draftKey) element._messageDrafts.set(element._draftKey, oldMessage.value);
    const profile = rows(objective?.assignments).find((a) => a.assignment_id === selectedId);
    const nextDraftKey = JSON.stringify([objective?.objective_id, actorId(profile || {})]);
    const sameAgent = element._teamAgent === selectedId && element._teamObjective === objective?.objective_id;
    const active = element.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = (node) => JSON.stringify(Object.keys(node.dataset).length ? [node.tagName, { ...node.dataset }] : [node.tagName, node.className, node.textContent]);
    const previousFocus = active && focusKey(active);
    const edit = sameAgent && element.querySelector('.team-name-editor[open]');
    const draft = edit && edit.querySelector('input');
    const savedDraft = draft ? { value: draft.value, focused: draft === active, start: draft.selectionStart, end: draft.selectionEnd } : null;
    const open = sameAgent ? Array.from(element.querySelectorAll('.team-explanation[open]')).map((d) => d.querySelector('summary').textContent) : [];
    const formDrafts = sameAgent ? Array.from(element.querySelectorAll('.team-message-form textarea, .team-settings[open] input, .team-settings[open] select')).filter((input) => input.name === 'agentMessage' || input.value !== input.dataset.initial).map((input) => ({ name: input.name, value: input.value, focused: input === active, start: input.selectionStart, end: input.selectionEnd })) : [];
    const menuOpen = sameAgent && element.querySelector('.team-agent-menu')?.open;
    const scrollTop = element.scrollTop;
    element.innerHTML = panelHtml(objective, selectedId, options.unavailable, options.models);
    element.classList.remove('team-inspector-compact');
    if (options.unifiedComposer && profile) { compactInspector(element, objective, profile); element.querySelector('.team-agent-menu').open = !!menuOpen; }
    element._teamKey = key; element._teamAgent = selectedId; element._teamObjective = objective?.objective_id;
    element.scrollTop = scrollTop;
    if (savedDraft) {
      const details = element.querySelector('.team-name-editor');
      if (details) {
        details.open = true;
        const input = details.querySelector('input'); input.value = savedDraft.value;
        if (savedDraft.focused) { input.focus({ preventScroll: true }); input.setSelectionRange(savedDraft.start, savedDraft.end); }
      }
    }
    element.querySelectorAll('.team-explanation').forEach((d) => { d.open = open.includes(d.querySelector('summary').textContent); });
    if (previousFocus && !savedDraft?.focused) Array.from(element.querySelectorAll('button, summary')).find((node) => focusKey(node) === previousFocus)?.focus({ preventScroll: true });
    element.querySelectorAll('.team-settings input, .team-settings select').forEach((input) => { input.dataset.initial = input.value; });
    formDrafts.forEach((draft) => {
      const input = element.querySelector('[name="' + draft.name + '"]');
      if (!input) return;
      input.value = draft.value;
      if (draft.focused) { input.focus({ preventScroll: true }); if (typeof draft.start === 'number') input.setSelectionRange(draft.start, draft.end); }
    });
    element._draftKey = nextDraftKey;
    const messageInput = element.querySelector('[name="agentMessage"]');
    if (messageInput && !sameAgent) messageInput.value = element._messageDrafts.get(nextDraftKey) || '';
    while (element._messageDrafts.size > 128) element._messageDrafts.delete(element._messageDrafts.keys().next().value);
    const teamControl = (action, value = {}) => {
      element._teamPending = { objectiveId: objective.objective_id, assignmentId: selectedId, action, revision: objective.team_revision || 0, draftKey: nextDraftKey, value };
      options.onControl({ objective_id: objective.objective_id, assignment_id: selectedId, action, value: { revision: objective.team_revision || 0, ...value } });
    };
    const add = element.querySelector('[data-team-add]'); if (add) add.onclick = options.onAdd;
    const map = element.querySelector('[data-team-map]'); if (map) map.onclick = options.onMap;
    const start = element.querySelector('[data-team-start]'); if (start) start.onclick = () => teamControl('start_agent');
    const message = element.querySelector('[data-team-message]');
    if (message) message.onsubmit = (event) => {
      event.preventDefault(); if (!message.reportValidity()) return;
      teamControl('agent_message', { message: message.elements.agentMessage.value.trim() });
    };
    const settings = element.querySelector('[data-team-settings]');
    if (settings) settings.onsubmit = (event) => {
      event.preventDefault(); if (!settings.reportValidity()) return;
      const value = {};
      if (settings.elements.agentModel.value !== settings.elements.agentModel.dataset.initial) value.model = settings.elements.agentModel.value;
      if (settings.elements.agentGroup.value.trim() !== settings.elements.agentGroup.dataset.initial) value.group = settings.elements.agentGroup.value.trim();
      if (Object.keys(value).length) teamControl('agent_settings', value);
    };
    element.querySelector('[data-team-close]').onclick = options.onClose;
    element.onkeydown = (event) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault(); event.stopPropagation();
      const menu = element.querySelector('.team-agent-menu[open]');
      if (menu) { menu.open = false; menu.querySelector('summary').focus(); return; }
      const editor = element.querySelector('.team-name-editor[open]');
      if (editor) { editor.open = false; editor.querySelector('summary').focus(); }
      else options.onClose();
    };
    element.querySelectorAll('[data-team-select]').forEach((button) => { button.onclick = () => options.onSelect(button.dataset.teamSelect); });
    const back = element.querySelector('[data-team-back]');
    if (back) back.onclick = () => options.onSelect(null);
    const viewWork = element.querySelector('[data-team-view-work]');
    if (viewWork) viewWork.onclick = () => { const feed = element.querySelector('.team-conversation, .team-feed'); if (feed) { feed.tabIndex = -1; feed.scrollIntoView({ block: 'start' }); feed.focus({ preventScroll: true }); } };
    const messageFocus = element.querySelector('[data-team-message-focus]');
    if (messageFocus) messageFocus.onclick = () => element.querySelector('[name="agentMessage"]')?.focus();
    const diff = element.querySelector('[data-team-diff]');
    if (diff) diff.onclick = () => options.onArtifact({ objective_id: objective.objective_id, assignment_id: selectedId, kind: 'diff' });
    const workspace = element.querySelector('[data-team-workspace]');
    if (workspace) workspace.onclick = () => options.onInspect();
    const selected = assignmentsFor(objective).find((a) => a.assignment_id === selectedId);
    element.querySelectorAll('[data-team-action]').forEach((button) => { button.onclick = () => {
      const action = button.dataset.teamAction;
      const payload = { objective_id: objective.objective_id, assignment_id: selectedId, action };
      if (action === 'approve') { if (!selected?.pending_approval?.request_id) return; payload.value = { request_id: selected.pending_approval.request_id }; }
      if (action === 'retry') { if (!selected?.run_id) return; payload.value = { run_id: selected.run_id }; }
      options.onControl(payload);
    }; });
    const review = element.querySelector('[data-team-review]');
    if (review) review.onclick = () => options.onControl({ objective_id: objective.objective_id, action: 'request_review', value: { revision: objective.revision } });
    const rename = element.querySelector('[data-team-rename]');
    if (rename) rename.onsubmit = (event) => {
      event.preventDefault();
      const value = rename.elements.agentName.value.trim();
      if (!value || !rename.reportValidity()) return;
      options.onControl({ objective_id: objective.objective_id, assignment_id: selectedId, action: 'rename', value });
      element.querySelector('.team-name-editor').open = false;
    };
  }
  function settle(element, response) {
    const pending = element?._teamPending;
    if (!pending || !response.ok || response.objective?.objective_id !== pending.objectiveId || response.control?.assignment_id !== pending.assignmentId || response.control?.action !== pending.action || response.control?.revision !== pending.revision) return;
    if (pending.action === 'agent_message' && element._messageDrafts?.get(pending.draftKey)?.trim() === pending.value.message) element._messageDrafts.delete(pending.draftKey);
    if (element._teamAgent === pending.assignmentId) {
      const input = element.querySelector('[name="agentMessage"]');
      if (pending.action === 'agent_message' && input?.value.trim() === pending.value.message) input.value = '';
      if (pending.action === 'agent_settings') element.querySelector('.team-settings')?.removeAttribute('open');
    }
    element._teamPending = null;
  }
  function assignmentsFor(objective) { return rows(objective && objective.assignments); }
  global.OPaiAgentsTeam = { feedHtml, panelHtml, mountPanel, stripHtml, name, avatar, agents, actorId, modelOptions, state, settle, conversationHtml, activities, currentActivity, attentionHtml };
})(typeof window !== "undefined" ? window : globalThis);
