(function (global) {
  'use strict';
  const esc = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const team = () => global.VestaAgentsTeam;
  const id = (a) => team().actorId(a);
  const list = (o) => team().agents(o);
  const CARD_HEIGHT = 100;
  function edges(objective) {
    const assignments = objective.assignments || [];
    return assignments.flatMap((target) => (target.depends_on || []).map((key) => {
      const source = assignments.find((a) => a.name === key || a.assignment_id === key);
      return source && id(source) !== id(target) ? { source, target } : null;
    }).filter(Boolean));
  }
  function arrange(objective) {
    const positions = {}, groups = new Map();
    list(objective).forEach((a) => { const group = a.group || ''; if (!groups.has(group)) groups.set(group, []); groups.get(group).push(a); });
    const assignments = objective.assignments || [], levels = new Map();
    const level = (a, visiting = new Set()) => {
      if (levels.has(a.assignment_id)) return levels.get(a.assignment_id);
      if (visiting.has(a.assignment_id)) return 0;
      const next = new Set(visiting); next.add(a.assignment_id);
      const parents = (a.depends_on || []).map((key) => assignments.find((p) => p.name === key || p.assignment_id === key)).filter(Boolean);
      const result = Math.min(31, parents.length ? Math.max(...parents.map((p) => level(p, next) + (id(p) === id(a) ? 0 : 1))) : 0);
      levels.set(a.assignment_id, result); return result;
    };
    let y = 64;
    groups.forEach((members) => {
      const lanes = new Map();
      members.forEach((a) => {
        const column = level(a), row = lanes.get(column) || 0;
        positions[id(a)] = { x: 40 + column * 280, y: y + row * 172 }; lanes.set(column, row + 1);
      });
      y += Math.max(1, ...lanes.values()) * 172 + 64;
    });
    return positions;
  }
  function safePositions(objective, stored) {
    const fallback = arrange(objective);
    return Object.fromEntries(list(objective).map((a) => {
      const point = stored?.[id(a)];
      return [id(a), point && [point.x, point.y].every((n) => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 10000) ? { x: point.x, y: point.y } : fallback[id(a)]];
    }));
  }
  function status(agent, objective) {
    if (agent.pending_approval) return { kind: 'attention', label: 'Needs you', icon: '!' };
    if (agent.status === 'failed') return { kind: 'failed', label: 'Failed', icon: '×' };
    if (['blocked', 'needs-attention'].includes(agent.status)) return { kind: 'attention', label: 'Needs you', icon: '!' };
    if (agent.status === 'completed') return { kind: 'done', label: 'Done', icon: '✓' };
    if (agent.status === 'running') return { kind: 'active', label: 'Working', icon: '◉' };
    if (agent.held) return { kind: 'waiting', label: 'Ready to start', icon: 'Ⅱ' };
    const waiting = (agent.depends_on || []).map((key) => (objective.assignments || []).find((a) => a.name === key || a.assignment_id === key)).filter((a) => a && a.status !== 'completed');
    return { kind: 'waiting', label: waiting.length === 1 && team().name(waiting[0], 0).length <= 18 ? 'Waiting on ' + team().name(waiting[0], 0) : waiting.length ? 'Waiting on ' + waiting.length + ' agents' : agent.status === 'cancelled' ? 'Stopped' : 'Queued', icon: '◷' };
  }
  function groupSummary(members, objective, collapsed) {
    const counts = new Map();
    members.forEach((a) => { const kind = status(a, objective).kind; counts.set(kind, (counts.get(kind) || 0) + 1); });
    if (counts.get('done') === members.length) return '✓ Complete';
    const kinds = collapsed ? ['attention', 'failed', 'active', 'waiting', 'done'] : ['attention', 'failed', 'active'];
    const summary = kinds.filter((kind) => counts.has(kind)).map((kind) => ({ active: '● ', attention: '! ', failed: '× ', waiting: '○ ', done: '✓ ' })[kind] + counts.get(kind) + ' ' + ({ active: 'working', attention: counts.get(kind) === 1 ? 'needs you' : 'need you', failed: 'failed', waiting: 'waiting', done: 'done' })[kind]).join(' · ');
    return summary || members.length + ' agents';
  }
  function graphHtml(objective, positions, view = {}) {
    positions = safePositions(objective, positions);
    const agents = list(objective), links = edges(objective), groups = new Map(), focused = view.focusId;
    if (!view.editing) {
      const columns = [...new Set(agents.map((a) => positions[id(a)].x))].sort((a, b) => a - b), gaps = [];
      columns.forEach((x, index) => { if (index && x - columns[index - 1] > 312) gaps.push({ x, amount: x - columns[index - 1] - 312 }); });
      agents.forEach((a) => { const point = positions[id(a)]; point.x -= gaps.filter((gap) => gap.x <= point.x).reduce((sum, gap) => sum + gap.amount, 0); });
    }
    const original = structuredClone(positions);
    const related = new Set(focused ? [focused] : []);
    if (focused) {
      for (const upstream of [true, false]) {
        const found = new Set([focused]);
        for (let step = 0; step < agents.length; step++) links.forEach(({ source, target }) => { if (found.has(id(upstream ? target : source))) found.add(id(upstream ? source : target)); });
        found.forEach((key) => related.add(key));
      }
    }
    agents.forEach((a) => { if (a.group) { if (!groups.has(a.group)) groups.set(a.group, []); groups.get(a.group).push(a); } });
    const boxes = Array.from(groups, ([name, members]) => {
      const points = members.map((a) => positions[id(a)]);
      const x = Math.max(0, Math.min(...points.map((p) => p.x)) - 16), y = Math.max(0, Math.min(...points.map((p) => p.y)) - 44);
      const bottom = Math.max(...points.map((p) => p.y)) + CARD_HEIGHT + 20;
      const automatic = agents.length > 7 && members.every((a) => ['done', 'waiting'].includes(status(a, objective).kind));
      const collapsed = !view.editing && (view.collapsed?.has(name) ? view.collapsed.get(name) : automatic) && !members.some((a) => related.has(id(a)));
      return { name, members, x, y, width: Math.max(...points.map((p) => p.x)) + 256 - x, height: bottom - y, bottom, collapsed };
    }).sort((a, b) => a.y - b.y);
    const bands = boxes.filter((box) => box.collapsed && !boxes.some((other) => other !== box && other.y < box.bottom && other.bottom > box.y) && !agents.some((a) => a.group !== box.name && original[id(a)].y + CARD_HEIGHT > box.y && original[id(a)].y < box.bottom));
    const offset = (y) => bands.filter((box) => box.bottom <= y).reduce((sum, box) => sum + box.height - 64, 0);
    agents.forEach((a) => { positions[id(a)].y = original[id(a)].y - offset(original[id(a)].y); });
    boxes.forEach((box) => { box.displayY = box.y - offset(box.y); });
    const collapsedFor = (agent) => boxes.find((box) => box.collapsed && box.name === agent.group);
    const visible = agents.filter((a) => !collapsedFor(a));
    const width = Math.max(480, ...visible.map((a) => positions[id(a)].x + 280), ...boxes.map((b) => b.x + b.width + 20));
    const height = Math.max(240, ...visible.map((a) => positions[id(a)].y + CARD_HEIGHT + 38), ...boxes.map((b) => b.displayY + (b.collapsed ? 64 : b.height) + 20));
    let html = boxes.map((box) => '<section class="team-map-group' + (box.collapsed ? ' is-collapsed' : '') + (box.members.every((a) => status(a, objective).kind === 'done') ? ' is-done' : '') + (box.members.some((a) => ['attention', 'failed'].includes(status(a, objective).kind)) ? ' has-attention' : '') + (focused && !box.members.some((a) => related.has(id(a))) ? ' is-muted' : '') + '" style="left:' + box.x + 'px;top:' + box.displayY + 'px;width:' + box.width + 'px;height:' + (box.collapsed ? 64 : box.height) + 'px"><header><button type="button" data-map-collapse="' + esc(box.name) + '" aria-expanded="' + !box.collapsed + '"' + (view.editing ? ' disabled' : '') + '><span aria-hidden="true">' + (box.collapsed ? '›' : '⌄') + '</span><strong>' + esc(box.name) + '</strong><span class="team-group-summary">' + esc(groupSummary(box.members, objective, box.collapsed)) + '</span></button><button type="button" data-map-group="' + esc(box.name) + '" class="team-group-handle" aria-label="Move ' + esc(box.name) + ' group" title="Drag group or use arrow keys">⠿</button></header></section>').join('');
    html += '<svg class="team-map-links" width="' + width + '" height="' + height + '" aria-label="Result handoffs"><defs><marker id="team-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" /></marker></defs>' + links.map(({ source, target }, index) => {
      const from = collapsedFor(source), to = collapsedFor(target);
      if (from && from === to) return '';
      const cross = source.group !== target.group;
      const relevant = focused && related.has(id(source)) && related.has(id(target));
      if (agents.length > 7 && !view.showAll && !view.editing && (focused ? !relevant : cross || (source.status !== 'running' && target.status !== 'running'))) return '';
      const a = from ? { x: from.x, y: from.displayY } : positions[id(source)], b = to ? { x: to.x, y: to.displayY } : positions[id(target)];
      const aw = from ? from.width : 240, bw = to ? to.width : 240, ah = from ? 64 : CARD_HEIGHT, bh = to ? 64 : CARD_HEIGHT;
      const forward = b.x >= a.x + aw;
      const x1 = a.x + (forward ? aw : aw / 2), y1 = a.y + (forward ? ah / 2 : ah);
      const x2 = b.x + (forward ? 0 : bw / 2), y2 = b.y + (forward ? bh / 2 : 0);
      let path = forward ? 'M' + x1 + ',' + y1 + ' C' + (x1 + 40) + ',' + y1 + ' ' + (x2 - 40) + ',' + y2 + ' ' + x2 + ',' + y2 : 'M' + x1 + ',' + y1 + ' C' + x1 + ',' + (y1 + 35) + ' ' + x2 + ',' + (y2 - 35) + ' ' + x2 + ',' + y2;
      const obstructed = forward && visible.some((node) => id(node) !== id(source) && id(node) !== id(target) && positions[id(node)].x < x2 && positions[id(node)].x + 240 > x1 && positions[id(node)].y < Math.max(y1, y2) + 8 && positions[id(node)].y + CARD_HEIGHT > Math.min(y1, y2) - 8);
      const lane = Math.max(a.y + ah, b.y + bh) + 18;
      if (obstructed) path = 'M' + x1 + ',' + y1 + ' C' + (x1 + 20) + ',' + y1 + ' ' + (x1 + 20) + ',' + lane + ' ' + (x1 + 32) + ',' + lane + ' L' + (x2 - 32) + ',' + lane + ' C' + (x2 - 20) + ',' + lane + ' ' + (x2 - 20) + ',' + y2 + ' ' + x2 + ',' + y2;
      const label = /review|critic/i.test(target.role || '') ? 'Review' : 'Hand off';
      return '<g class="team-map-link' + (focused && !relevant ? ' is-muted' : '') + '"><text class="team-edge-label" x="' + ((x1 + x2) / 2) + '" y="' + (obstructed ? lane + 14 : (y1 + y2) / 2 - 12) + '">' + label + '</text><path class="team-map-edge' + (relevant ? ' is-related' : '') + '" d="' + path + '" marker-end="url(#team-arrow)" data-map-edge="' + index + '" tabindex="0" role="button" aria-label="' + esc(team().name(source, 0) + ' sends results to ' + team().name(target, 0)) + '"><title>' + esc(source.title + ' → ' + target.title) + '</title></path></g>';
    }).join('') + '</svg>';
    html += visible.map((a, index) => {
      const point = positions[id(a)], current = status(a, objective);
      const details = [a.title || a.objective, current.label, a.blocked_reason, team().activities(a).at(-1)].filter(Boolean).join(' · ');
      return '<article class="team-map-node team-node-' + current.kind + (focused && !related.has(id(a)) ? ' is-muted' : '') + (focused === id(a) ? ' is-selected' : '') + '" data-map-node="' + esc(id(a)) + '" style="left:' + point.x + 'px;top:' + point.y + 'px"><button type="button" class="team-map-handle" data-map-move="' + esc(id(a)) + '" aria-label="Move ' + esc(team().name(a, index)) + '" title="Drag or use arrow keys">⠿</button><button type="button" class="team-map-person" data-map-select="' + esc(a.assignment_id) + '" title="' + esc(details) + '"><strong>' + esc(a.title || a.objective) + '</strong><span class="team-map-identity">' + team().avatar(a.avatar_index ?? index) + '<span>' + esc(team().name(a, index)) + '</span><span class="team-node-status"><span aria-hidden="true">' + current.icon + '</span> ' + esc(current.label) + '</span></span></button></article>';
    }).join('');
    return { html, width, height };
  }
  function mount(element, objective, options) {
    if (!objective) return;
    if (element._objectiveId !== objective.objective_id) {
      element.innerHTML = '<header class="team-map-header"><button type="button" class="team-quiet" data-map-back>← Back to chat</button><h2 data-map-title></h2><span class="team-map-overview" data-map-overview></span><button type="button" class="team-map-attention" data-map-attention hidden></button><button type="button" class="btn" data-map-edit aria-pressed="false">Configure</button><div class="team-actions team-map-edit-actions" hidden><button type="button" class="team-quiet" data-map-add>+ Add agent</button><button type="button" class="team-quiet" data-map-connect>Connect</button><button type="button" class="team-quiet" data-map-group-mode>Group</button><button type="button" class="team-quiet" data-map-arrange>Auto arrange</button></div></header><p class="team-map-hint" role="status"></p><form class="team-map-group-form" hidden><label>Group name<input name="groupName" maxlength="40" required placeholder="e.g. Authentication"></label><button type="submit" class="btn">Group selected</button><span data-group-count>0 selected</span></form><div class="team-map-viewport"><div class="team-map-canvas"></div></div><footer class="team-map-footer"><span data-map-link-hint></span><div><details class="team-view-menu"><summary>View</summary><div><button type="button" class="team-quiet team-fit" data-map-fit aria-label="Fit" aria-keyshortcuts="F">⛶ Fit to view <kbd>F</kbd></button><button type="button" class="team-quiet" data-map-actual>Actual size</button><button type="button" class="team-quiet" data-map-clear hidden>Clear focus</button><button type="button" class="team-quiet" data-map-links aria-pressed="false">Relevant links</button><details class="team-map-zoom-control"><summary>Zoom</summary><div><button type="button" class="team-quiet" data-map-zoom="-1" aria-label="Zoom out">−</button><output class="team-map-zoom">100%</output><button type="button" class="team-quiet" data-map-zoom="1" aria-label="Zoom in">+</button></div></details></div></details></div></footer>';
      element.querySelector('.team-map-hint').textContent = 'Select an agent to follow its work. Click empty space to return.';
      try { if (sessionStorage.getItem('vesta.teamMap.learned')) element.querySelector('.team-map-hint').textContent = ''; } catch (_) {}
      element._objectiveId = objective.objective_id; element._positions = {}; element._zoom = 1; element._collapsed = new Map(); element._focusId = null; element._showAll = false;
      element._layoutPending = null; element._layoutQueued = null; element._dragging = false; element._editing = false; element.classList.remove("is-editing"); element._selected = new Set(); element._grouping = false; element._connecting = false; element._source = null;
    }
    element._objective = objective; element._options = options;
    element.querySelector('[data-map-title]').textContent = objective.objective || 'Your team';
    if (options.selectedId && options.selectedId !== element._inspectedId) element._focusId = id((objective.assignments || []).find((a) => a.assignment_id === options.selectedId) || {});
    element._inspectedId = options.selectedId;
    const defaults = arrange(objective);
    if (!element._dragging && !element._layoutPending) element._positions = safePositions(objective, objective.team_layout || defaults);
    const canvas = element.querySelector('.team-map-canvas'), viewport = element.querySelector('.team-map-viewport');
    const hint = (text) => { element.querySelector('.team-map-hint').textContent = text; };
    const learn = () => { if (!element._editing) hint(''); try { sessionStorage.setItem('vesta.teamMap.learned', '1'); } catch (_) {} };
    const people = list(objective), attention = people.filter((a) => ['attention', 'failed'].includes(status(a, objective).kind));
    element.querySelector('[data-map-overview]').textContent = people.length + ' agents · ' + people.filter((a) => status(a, objective).kind === 'active').length + ' working';
    const attentionButton = element.querySelector('[data-map-attention]');
    attentionButton.hidden = !attention.length; attentionButton.textContent = attention.length + (attention.length === 1 ? ' needs you →' : ' need you →');
    attentionButton.onclick = () => { const next = attention[(attention.findIndex((a) => id(a) === element._focusId) + 1) % attention.length]; if (next) { element._focusId = id(next); learn(); paint(); options.onSelect(next.assignment_id); } };

    const control = (action, value, assignmentId) => options.onControl({ objective_id: element._objective.objective_id, ...(assignmentId ? { assignment_id: assignmentId } : {}), action, value: { revision: element._objective.team_revision || 0, ...value } });
    const save = () => {
      const positions = structuredClone(element._positions);
      if (element._layoutPending) { element._layoutQueued = positions; return; }
      element._layoutPending = { revision: element._objective.team_revision || 0, positions };
      control('team_layout', { positions });
    };
    const paint = () => {
      const active = document.activeElement, dataset = canvas.contains(active) ? { ...active.dataset } : null;
      const result = graphHtml(element._objective, element._positions, { collapsed: element._collapsed, editing: element._editing, focusId: element._focusId, showAll: element._showAll });
      canvas.innerHTML = result.html; canvas.style.width = result.width + 'px'; canvas.style.height = result.height + 'px';
      canvas.style.zoom = element._zoom;
      element.querySelector('[data-map-clear]').hidden = !element._focusId;
      canvas.querySelectorAll('[data-map-group]').forEach((button) => { button.tabIndex = element._editing ? 0 : -1; });
      canvas.querySelectorAll('[data-map-select]').forEach((button) => {
        button.setAttribute('aria-pressed', String(element._selected?.has(button.dataset.mapSelect) || element._focusId === id((objective.assignments || []).find((a) => a.assignment_id === button.dataset.mapSelect) || {})));
      });
      if (dataset) Array.from(canvas.querySelectorAll('button, [data-map-edge]')).find((b) => JSON.stringify({ ...b.dataset }) === JSON.stringify(dataset))?.focus({ preventScroll: true });
    };
    if (!element._dragging) paint();
    element.querySelector('[data-map-back]').onclick = options.onBack;
    const toggleEdit = () => {
      element._editing = !element._editing;
      element.classList.toggle('is-editing', element._editing);
      element.querySelector('[data-map-edit]').textContent = element._editing ? 'Done' : 'Configure';
      element.querySelector('[data-map-edit]').setAttribute('aria-pressed', String(element._editing));
      element.querySelector('.team-map-edit-actions').hidden = !element._editing;
      element._connecting = false; element._grouping = false; element._source = null; element._selected = new Set(); element._focusId = null;
      element.querySelector('.team-map-group-form').hidden = true;
      element.querySelector('[data-map-connect]').setAttribute('aria-pressed', 'false');
      element.querySelector('[data-map-group-mode]').setAttribute('aria-pressed', 'false');
      hint(element._editing ? 'Drag agents or groups. Connect tasks or group your team.' : '');
      paint();
    };
    element.querySelector('[data-map-edit]').onclick = toggleEdit;
    element._stopEditing = () => { if (element._editing) toggleEdit(); };
    element.querySelector('[data-map-add]').disabled = !objective.team_controls?.can_add;
    element.querySelector('[data-map-add]').onclick = options.onAdd;
    const groupForm = element.querySelector('.team-map-group-form');
    element.querySelector('[data-map-group-mode]').onclick = () => {
      element._grouping = !element._grouping; element._selected = new Set();
      element._connecting = false; element._source = null;
      element.querySelector('[data-map-connect]').setAttribute('aria-pressed', 'false');
      element.querySelector('[data-map-group-mode]').setAttribute('aria-pressed', String(element._grouping));
      groupForm.hidden = !element._grouping;
      hint(element._grouping ? 'Select the agents to group, then give the group a name.' : 'Drag agents or groups. Click an agent to open its thread.'); paint();
    };
    groupForm.onsubmit = (event) => {
      event.preventDefault();
      if (!element._selected?.size) { hint('Select at least one agent.'); return; }
      if (!groupForm.reportValidity()) return;
      control('group_agents', { assignment_ids: Array.from(element._selected), group: groupForm.elements.groupName.value.trim() });
      element._grouping = false; element._selected = new Set(); groupForm.hidden = true;
      element.querySelector('[data-map-group-mode]').setAttribute('aria-pressed', 'false'); paint();
    };
    element.querySelector('[data-map-connect]').onclick = () => {
      element._connecting = !element._connecting; element._source = null;
      element._grouping = false; element._selected = new Set(); groupForm.hidden = true;
      element.querySelector('[data-map-group-mode]').setAttribute('aria-pressed', 'false'); paint();
      element.querySelector('[data-map-connect]').setAttribute('aria-pressed', String(element._connecting));
      hint(element._connecting ? 'Choose the agent sending work, then the agent receiving it. Escape cancels.' : 'Drag agents or groups. Click an agent to open its thread.');
    };
    element.querySelector('[data-map-link-hint]').textContent = '';
    const clearFocus = () => { element._focusId = null; learn(); paint(); options.onClearFocus?.(); };
    element.querySelector('[data-map-clear]').onclick = clearFocus;
    viewport.onclick = (event) => { if (element._focusId && !element._editing && (event.target === viewport || event.target === canvas || event.target.matches('.team-map-group'))) clearFocus(); };
    element.querySelector('[data-map-links]').onclick = (event) => { element._showAll = !element._showAll; event.currentTarget.setAttribute('aria-pressed', String(element._showAll)); event.currentTarget.textContent = element._showAll ? 'All links' : 'Relevant links'; paint(); };
    const fit = () => {
      element._zoom = Math.min(1.15, Math.max(.02, Math.min((viewport.clientWidth - 24) / parseFloat(canvas.style.width), (viewport.clientHeight - 24) / parseFloat(canvas.style.height))));
      canvas.style.zoom = element._zoom; viewport.scrollTo(0, 0); element.querySelector('.team-map-zoom').textContent = Math.round(element._zoom * 100) + '%';
    };
    element.querySelector('[data-map-fit]').onclick = () => { fit(); element.querySelector('.team-view-menu').open = false; };
    element.querySelector('[data-map-actual]').onclick = () => { element._zoom = 1; canvas.style.zoom = 1; element.querySelector('.team-map-zoom').textContent = '100%'; element.querySelector('.team-view-menu').open = false; };
    viewport.tabIndex = 0;
    viewport.setAttribute('aria-label', 'Team map. Press F to fit.');
    viewport.ondblclick = (event) => { if (event.target === viewport || event.target === canvas || event.target.matches('.team-map-group')) { event.preventDefault(); fit(); } };
    element.querySelector('[data-map-arrange]').onclick = () => { element._positions = arrange(element._objective); paint(); save(); };
    element.querySelectorAll('[data-map-zoom]').forEach((button) => { button.onclick = () => { element._zoom = Math.min(1.5, Math.max(.5, element._zoom + Number(button.dataset.mapZoom) * .1)); canvas.style.zoom = element._zoom; element.querySelector('.team-map-zoom').textContent = Math.round(element._zoom * 100) + '%'; }; });
    canvas.onclick = (event) => {
      const collapse = event.target.closest('[data-map-collapse]');
      if (collapse && !element._editing) { element._collapsed.set(collapse.dataset.mapCollapse, collapse.getAttribute('aria-expanded') === 'true'); element._focusId = null; learn(); paint(); return; }
      const button = event.target.closest('[data-map-select]');
      if (button) {
        const agent = element._objective.assignments.find((a) => a.assignment_id === button.dataset.mapSelect);
        if (element._grouping) {
          if (element._selected.has(agent.assignment_id)) element._selected.delete(agent.assignment_id); else element._selected.add(agent.assignment_id);
          element.querySelector('[data-group-count]').textContent = element._selected.size + ' selected'; paint(); return;
        }
        if (!element._connecting) { element._focusId = id(agent); learn(); paint(); options.onSelect(agent.assignment_id); return; }
        if (!element._source) { element._source = agent; hint('Send ' + team().name(agent, 0) + '’s results to…'); return; }
        const target = element._objective.assignments.filter((a) => id(a) === id(agent) && a.team_controls?.can_connect).sort((a, b) => (b.team_order || 0) - (a.team_order || 0))[0];
        if (!target) { hint('This agent has already started. Send it a follow-up task, then connect that queued task.'); return; }
        if (id(element._source) === id(target)) { hint('Choose a different agent.'); return; }
        control('connect_agents', { source_id: element._source.assignment_id, connected: true }, target.assignment_id);
        element._connecting = false; element._source = null;
        element.querySelector('[data-map-connect]').setAttribute('aria-pressed', 'false'); hint('Connection requested. The receiving task waits for completed work.');
      }
      const edge = event.target.closest('[data-map-edge]');
      if (edge) {
        const { source, target } = edges(element._objective)[Number(edge.dataset.mapEdge)];
        connectionDialog(element._objective, source, target, { ...options, editing: element._editing });
      }
    };
    canvas.oncontextmenu = (event) => {
      const button = event.target.closest('[data-map-select]');
      if (!button || element._editing) return;
      event.preventDefault();
      button.click(); options.onAgentOptions?.();
    };
    const moveIds = (handle) => handle.dataset.mapGroup !== undefined ? list(element._objective).filter((a) => a.group === handle.dataset.mapGroup).map(id) : [handle.dataset.mapMove];
    const translate = (ids, start, dx, dy) => {
      const minX = Math.min(...ids.map((key) => start[key].x)), minY = Math.min(...ids.map((key) => start[key].y));
      const maxX = Math.max(...ids.map((key) => start[key].x)), maxY = Math.max(...ids.map((key) => start[key].y));
      dx = Math.max(-minX, Math.min(10000 - maxX, dx)); dy = Math.max(-minY, Math.min(10000 - maxY, dy));
      ids.forEach((key) => { element._positions[key] = { x: Math.round(start[key].x + dx), y: Math.round(start[key].y + dy) }; });
      paint();
    };
    canvas.onpointerdown = (event) => {
      const handle = event.target.closest('[data-map-move], [data-map-group]');
      if (!element._editing || !handle || event.button !== 0) return;
      event.preventDefault();
      const keys = moveIds(handle), start = structuredClone(element._positions), x = event.clientX, y = event.clientY;
      const scrollX = viewport.scrollLeft, scrollY = viewport.scrollTop;
      element._dragging = true; canvas.setPointerCapture(event.pointerId);
      canvas.onpointermove = (e) => translate(keys, start, (e.clientX - x + viewport.scrollLeft - scrollX) / element._zoom, (e.clientY - y + viewport.scrollTop - scrollY) / element._zoom);
      const end = (e) => { canvas.onpointermove = null; canvas.onpointerup = null; canvas.onpointercancel = null; element._dragging = false; if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId); if (e.type === 'pointercancel') { element._positions = start; paint(); } else save(); };
      canvas.onpointerup = end; canvas.onpointercancel = end;
    };
    canvas.onkeydown = (event) => {
      const handle = event.target.closest('[data-map-move], [data-map-group]');
      if (element._editing && handle && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
        event.preventDefault(); element._dragging = true;
        const step = event.shiftKey ? 40 : 10;
        translate(moveIds(handle), structuredClone(element._positions), event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0, event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0);
      }
      if (event.target.matches('[data-map-edge]') && ['Enter', ' '].includes(event.key)) { event.preventDefault(); event.target.dispatchEvent(new MouseEvent('click', { bubbles: true })); }
    };
    canvas.onkeyup = (event) => { if (element._dragging && event.key.startsWith('Arrow')) { element._dragging = false; save(); } };
    element.onkeydown = (event) => { if (event.key.toLowerCase() === 'f' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest('input, textarea, select, [contenteditable]')) { event.preventDefault(); fit(); return; } if (event.key === 'Escape' && element._focusId && !element._editing) { event.preventDefault(); event.stopPropagation(); clearFocus(); return; } if (event.key === 'Escape' && (element._connecting || element._grouping)) { event.stopPropagation(); element._connecting = false; element._source = null; element.querySelector('[data-map-connect]').setAttribute('aria-pressed', 'false'); element._grouping = false; element._selected = new Set(); groupForm.hidden = true; element.querySelector('[data-map-group-mode]').setAttribute('aria-pressed', 'false'); paint(); hint('Selection cancelled.'); } };
  }
  function dialog(title, html, objective, options) {
    document.querySelector('.team-edit-dialog')?.close();
    const previous = document.activeElement, element = document.createElement('dialog');
    element.className = 'agents-artifact-dialog team-edit-dialog';
    element.setAttribute('aria-label', title);
    element.innerHTML = '<header><h2>' + esc(title) + '</h2><button type="button" class="team-quiet" data-dialog-close aria-label="Close">×</button></header>' + html + '<p class="team-form-error" role="status"></p>';
    element._objectiveId = objective.objective_id;
    document.body.appendChild(element);
    element.querySelector('[data-dialog-close]').onclick = () => element.close();
    element.onclose = () => { element.remove(); if (previous?.isConnected) previous.focus({ preventScroll: true }); options.onDialogClose?.(); };
    element.showModal();
    element.submitControl = (action, value, assignmentId) => {
      const current = options.getObjective ? options.getObjective() : objective;
      if (!current || current.objective_id !== objective.objective_id) { element.close(); return; }
      element._control = { action, revision: current.team_revision || 0, assignment_id: assignmentId };
      options.onControl({ objective_id: objective.objective_id, ...(assignmentId ? { assignment_id: assignmentId } : {}), action, value: { revision: current.team_revision || 0, ...value } });
    };
    return element;
  }
  function addDialog(objective, options) {
    const groups = Array.from(new Set(list(objective).map((a) => a.group).filter(Boolean)));
    const element = dialog('Add an agent', '<form data-add-agent><label>What should this agent do?<textarea name="task" rows="3" maxlength="8000" required placeholder="Review the sign-in flow…"></textarea></label><div class="team-form-row"><label>Name<input name="agentName" maxlength="40" placeholder="Vesta chooses a name"></label><label>AI model<select name="model" aria-label="AI model">' + team().modelOptions('auto', options.models) + '</select></label></div><details><summary>Group & budget</summary><label>Group<input name="group" maxlength="40" list="mapGroups" placeholder="No group"></label><datalist id="mapGroups">' + groups.map((g) => '<option value="' + esc(g) + '"></option>').join('') + '</datalist><label>Task budget ($)<input name="budget" inputmode="decimal" pattern="[0-9]+([.][0-9]+)?" placeholder="Optional"></label></details><label class="team-form-check"><input type="checkbox" name="start">Start immediately</label><p class="team-empty">Leave this off to arrange connections before starting. Existing permissions and objective budget apply.</p><button type="submit" class="btn primary">Add agent</button></form>', objective, options);
    element.querySelector('form').onsubmit = (event) => {
      event.preventDefault(); const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const fields = form.elements;
      element.submitControl('add_agent', { objective: fields.task.value.trim(), ...(fields.agentName.value.trim() ? { name: fields.agentName.value.trim() } : {}), model: fields.model.value, group: fields.group.value.trim(), start: fields.start.checked, budget_usd: fields.budget.value || null });
    };
    element.querySelector('textarea').focus();
  }
  function connectionDialog(objective, source, target, options) {
    const element = dialog('Agent connection', '<p>' + esc(team().name(source, 0)) + ' → ' + esc(team().name(target, 0)) + '</p><p class="team-empty">' + esc(target.title) + ' receives the completed result and file changes from ' + esc(source.title) + '.</p>' + (target.team_controls?.can_connect && options.editing ? '<button type="button" class="btn" data-disconnect>Remove connection</button>' : '<p class="team-empty">' + (target.team_controls?.can_connect ? 'Choose Configure to change this connection.' : 'This task has already started. Its recorded inputs stay fixed.') + '</p>'), objective, options);
    const remove = element.querySelector('[data-disconnect]');
    if (remove) remove.onclick = () => element.submitControl('connect_agents', { source_id: source.assignment_id, connected: false }, target.assignment_id);
  }
  function settle(response) {
    const map = document.querySelector('#teamMap');
    const pending = map?._layoutPending;
    if (pending && response.control?.action === 'team_layout' && response.control?.revision === pending.revision && (response.objective?.objective_id || response.control?.objective_id) === map._objectiveId) {
      map._layoutPending = null;
      const queued = map._layoutQueued; map._layoutQueued = null;
      if (response.ok && queued) {
        const revision = response.objective.team_revision || 0;
        map._layoutPending = { revision, positions: queued };
        map._options.onControl({ objective_id: map._objectiveId, action: 'team_layout', value: { revision, positions: queued } });
      }
      if (!response.ok) {
        map.querySelector('.team-map-hint').textContent = response.error?.userMessage || 'Layout could not be saved. Move an agent to try again.';
      }
    }
    const element = document.querySelector('.team-edit-dialog');
    if (!element?._control) return;
    if (!response.ok) { element.querySelector('.team-form-error').textContent = response.error?.userMessage || (typeof response.error === 'string' ? response.error : 'Could not save. Your draft is still here.'); return; }
    if (response.objective?.objective_id === element._objectiveId && response.control?.action === element._control.action && response.control?.revision === element._control.revision) element.close();
  }
  global.VestaTeamMap = { mount, arrange, edges, graphHtml, addDialog, settle, status };
})(typeof window !== 'undefined' ? window : globalThis);
