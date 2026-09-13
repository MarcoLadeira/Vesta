(function (global) {
  "use strict";
  const esc = (v) => String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const rows = (v) => Array.isArray(v) ? v : [];
  const names = ["Alex", "Sam", "Taylor", "Riley", "Jordan", "Morgan", "Casey", "Robin"];
  const palettes = [["#bde9f6", "#b87850", "#242e43"], ["#dacdf8", "#f0c39c", "#2e243b"], ["#c8e6e5", "#e0a77c", "#32333b"], ["#ffe1b2", "#9d613f", "#29242d"]];
  const profileIndex = (agent, index) => Number.isInteger(agent.avatar_index) && agent.avatar_index >= 0 && agent.avatar_index < 32 ? agent.avatar_index : index;
  const name = (agent, index) => agent.display_name || names[profileIndex(agent, index) % names.length] + (profileIndex(agent, index) >= names.length ? " " + (Math.floor(profileIndex(agent, index) / names.length) + 1) : "");
  function avatar(index) {
    const [background, skin, hair] = palettes[index % palettes.length];
    const long = index % 2 === 1;
    return '<svg class="team-avatar" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="24" fill="' + background + '"/>' + (long ? '<path d="M11 35V20C11 3 37 3 37 20V35Z" fill="' + hair + '"/>' : '') + '<path d="M7 48C7 31 41 31 41 48" fill="' + hair + '"/><path d="M20 29h8v9c-3 3-5 3-8 0" fill="' + skin + '"/><ellipse cx="24" cy="21" rx="10" ry="13" fill="' + skin + '"/><path d="M13 21C9 4 37 2 35 20L30 12c-5 7-12 1-17 9" fill="' + hair + '"/><path d="M19 22v1m10-1v1" stroke="' + hair + '" stroke-width="2" stroke-linecap="round"/><path d="M21 29q3 2 6 0" fill="none" stroke="' + hair + '" stroke-linecap="round"/>' + (index % 4 === 2 ? '<path d="M15 19h8v7h-8zm10 0h8v7h-8zm-2 2h2" fill="none" stroke="' + hair + '"/>' : '') + '</svg>';
  }
  function state(agent, assignments) {
    const upstream = dependencies(agent, assignments).filter((a) => a.status !== "completed");
    const waiting = ["pending", "queued", "blocked"].includes(agent.status) && upstream.length;
    let label = agent.pending_approval ? "Needs your approval" : waiting ? "Waiting for " + upstream.map((a) => name(a, assignments.indexOf(a))).join(", ") : agent.blocked_reason || agent.admission?.reason || ({ running: "Working", completed: "Done", failed: "Needs attention", pending: "Queued", blocked: "Needs attention" })[agent.status] || String(agent.status || "Queued").replace(/[-_]/g, " ");
    const tone = agent.pending_approval ? "attention" : waiting ? "waiting" : agent.status === "running" ? "active" : agent.status === "completed" ? "done" : ["failed", "needs-attention", "blocked"].includes(agent.status) ? "attention" : "waiting";
    const automatic = waiting && upstream.every((a) => a.status === "running" && !a.pending_approval);
    if (automatic) label += " · OPai will continue";
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
    const events = rows(objective.timeline).filter((e) => !selectedId || e.assignment_id === selectedId);
    let previous = '';
    const timeline = events.map((event) => {
      const agent = assignments.find((a) => a.assignment_id === event.assignment_id);
      if (!agent) return '';
      const text = eventText(event, agent, assignments);
      const signature = JSON.stringify([event.assignment_id, event.kind, text]);
      if (!text || signature === previous) return '';
      previous = signature;
      const date = new Date(event.occurred_at);
      const time = Number.isNaN(date.valueOf()) ? '' : '<time datetime="' + esc(event.occurred_at) + '" title="' + esc(date.toLocaleString()) + '">' + esc(date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })) + '</time>';
      return '<li><button type="button" class="team-update" data-team-select="' + esc(agent.assignment_id) + '" data-team-event="' + esc(event.sequence) + '">' + avatar(profileIndex(agent, assignments.indexOf(agent))) + '<span class="team-update-body"><strong class="team-event-work">' + esc(text) + '</strong><span class="team-event-by">' + esc(name(agent, assignments.indexOf(agent))) + time + '</span>' + (event.summary ? '<span class="team-current">' + esc(event.summary) + '</span>' : '') + (event.verification_summary ? '<span class="team-check">' + esc(event.verification_summary) + '</span>' : '') + '</span></button></li>';
    }).join('');
    return '<section class="team-feed" aria-label="Team updates">' + (objective.timeline_truncated ? '<p class="team-empty">Recent activity · earlier events remain in the journal</p>' : '') + (timeline ? '<ol class="team-timeline">' + timeline + '</ol>' : '<p class="team-empty">' + (assignments.length ? 'Waiting for recorded activity.' : 'Your team is getting ready.') + '</p>') + '</section>';
  }
  function stripHtml(objective) {
    const assignments = rows(objective?.assignments);
    return '<button type="button" class="team-strip-open" aria-label="Expand AI Team"><span aria-hidden="true">' + assignments.slice(0, 3).map((a, index) => avatar(profileIndex(a, index))).join('') + '</span><span>' + assignments.length + '</span></button>';
  }
  function panelHtml(objective, selectedId, unavailable) {
    const assignments = rows(objective && objective.assignments);
    const selected = assignments.find((a) => a.assignment_id === selectedId);
    let html = '<header class="team-header"><h2>AI Team</h2><button type="button" class="team-quiet" data-team-close aria-label="Collapse AI Team">Collapse</button></header>';
    if (!objective) return html + '<p class="team-empty">' + esc(unavailable || 'Send an objective and your team will get to work. Each agent will appear here.') + '</p>';
    if (unavailable) html += '<p class="team-empty" role="status">' + esc(unavailable) + '</p>';
    html += '<p class="team-objective">' + esc(objective.objective) + '</p><nav class="team-roster" aria-label="Your agents"' + (selected ? ' hidden' : '') + '>';
    html += assignments.map((a, index) => '<button type="button" class="team-person" data-team-select="' + esc(a.assignment_id) + '" aria-pressed="' + (a === selected) + '">' + avatar(profileIndex(a, index)) + '<span><strong>' + esc(name(a, index)) + '</strong><span class="team-current">' + esc(a.title || a.objective) + '</span>' + state(a, assignments) + '</span></button>').join('');
    html += '</nav>';
    if (!assignments.length) html += '<p class="team-empty">Putting your team together…</p>';
    if (selected) {
      const index = assignments.indexOf(selected);
      html += '<section class="team-detail" aria-label="Agent details"><button type="button" class="team-quiet" data-team-back>← Back to team</button><h3>' + esc(selected.title || selected.objective) + '</h3><header><span class="team-detail-person">' + avatar(profileIndex(selected, index)) + esc(name(selected, index)) + '</span><details class="team-name-editor"><summary>Rename</summary><form data-team-rename><label>Agent name<input name="agentName" aria-label="Agent name" maxlength="40" required value="' + esc(name(selected, index)) + '"></label><button type="submit" class="btn">Save name</button></form></details></header>';
      html += state(selected, assignments) + connection(selected, assignments);
      if (selected.rationale) html += '<details class="team-explanation"><summary>Why this task</summary><p>' + esc(selected.rationale) + '</p></details>';
      html += feedHtml(objective, selectedId);
      if (selected.pending_approval) html += '<div class="team-approval"><strong>Needs your approval</strong><p>' + esc(selected.pending_approval.reason) + '</p><pre>' + esc(JSON.stringify(selected.pending_approval.command || selected.pending_approval.files, null, 2)) + '</pre></div>';
      if (selected.blocked_reason) html += '<p class="team-empty">' + esc(selected.blocked_reason) + '</p>';
      const findings = selected.result && selected.result.handoff && selected.result.handoff.summary;
      if (findings) html += '<p class="team-result">' + esc(findings) + '</p>';
      if (rows(selected.changed_files).length) html += '<details class="team-explanation"><summary>' + rows(selected.changed_files).length + (selected.changed_files.length === 1 ? ' changed file' : ' changed files') + '</summary><ul>' + selected.changed_files.map((v) => '<li>' + esc(typeof v === 'string' ? v : v.path) + '</li>').join('') + '</ul></details>';
      const checks = selected.verification;
      if (checks && (checks.status || typeof checks.passed === 'boolean')) html += '<p class="team-check">Checks: ' + esc(checks.status || (checks.passed ? 'passed' : 'failed')) + '</p>';
      html += '<div class="team-actions">' + rows(selected.allowed_actions).filter((a) => ['approve', 'retry', 'stop'].includes(a)).map((action) => '<button type="button" class="btn" data-team-action="' + action + '">' + ({ approve: 'Approve once', retry: 'Retry', stop: 'Stop agent' })[action] + '</button>').join('') + (selected.worktree ? '<button type="button" class="team-quiet" data-team-diff>Inspect diff</button>' : '') + '</div></section>';
    }
    html += '<footer class="team-footer">' + (rows(objective.allowed_actions).includes('request_review') ? '<button type="button" class="btn" data-team-review>Ask for team review</button>' : '') + '<button type="button" class="team-quiet" data-team-workspace>Full Agents workspace</button></footer>';
    return html;
  }
  function mountPanel(element, objective, selectedId, options) {
    const key = JSON.stringify([objective, selectedId, options.unavailable]);
    if (element._teamKey === key) return;
    const sameAgent = element._teamAgent === selectedId && element._teamObjective === objective?.objective_id;
    const active = element.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = (node) => JSON.stringify(Object.keys(node.dataset).length ? [node.tagName, { ...node.dataset }] : [node.tagName, node.className, node.textContent]);
    const previousFocus = active && focusKey(active);
    const edit = sameAgent && element.querySelector('.team-name-editor[open]');
    const draft = edit && edit.querySelector('input');
    const savedDraft = draft ? { value: draft.value, focused: draft === active, start: draft.selectionStart, end: draft.selectionEnd } : null;
    const open = sameAgent ? Array.from(element.querySelectorAll('.team-explanation[open]')).map((d) => d.querySelector('summary').textContent) : [];
    const scrollTop = element.scrollTop;
    element.innerHTML = panelHtml(objective, selectedId, options.unavailable);
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
    element.querySelector('[data-team-close]').onclick = options.onClose;
    element.onkeydown = (event) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault(); event.stopPropagation();
      const editor = element.querySelector('.team-name-editor[open]');
      if (editor) { editor.open = false; editor.querySelector('summary').focus(); }
      else options.onClose();
    };
    element.querySelectorAll('[data-team-select]').forEach((button) => { button.onclick = () => options.onSelect(button.dataset.teamSelect); });
    const back = element.querySelector('[data-team-back]');
    if (back) back.onclick = () => options.onSelect(null);
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
  function assignmentsFor(objective) { return rows(objective && objective.assignments); }
  global.OPaiAgentsTeam = { feedHtml, panelHtml, mountPanel, stripHtml, name, avatar };
})(typeof window !== "undefined" ? window : globalThis);
