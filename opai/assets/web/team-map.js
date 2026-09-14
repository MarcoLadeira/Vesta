(function (global) {
  'use strict';
  const esc = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const team = () => global.OPaiAgentsTeam;
  const id = (a) => team().actorId(a);
  const list = (o) => team().agents(o);
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
  function graphHtml(objective, positions) {
    positions = safePositions(objective, positions);
    const agents = list(objective), links = edges(objective);
    const width = Math.max(880, ...Object.values(positions).map((p) => p.x + 280));
    const height = Math.max(420, ...Object.values(positions).map((p) => p.y + 190));
    const groups = new Map();
    agents.forEach((a) => { if (a.group) { if (!groups.has(a.group)) groups.set(a.group, []); groups.get(a.group).push(a); } });
    let html = Array.from(groups).map(([group, members]) => {
      const points = members.map((a) => positions[id(a)]);
      const x = Math.max(0, Math.min(...points.map((p) => p.x)) - 16), y = Math.max(0, Math.min(...points.map((p) => p.y)) - 36);
      const right = Math.max(...points.map((p) => p.x)) + 256, bottom = Math.max(...points.map((p) => p.y)) + 148;
      return '<div class="team-map-group" style="left:' + x + 'px;top:' + y + 'px;width:' + (right - x) + 'px;height:' + (bottom - y) + 'px"><button type="button" data-map-group="' + esc(group) + '" title="Drag group or use arrow keys">' + esc(group) + '</button></div>';
    }).join('');
    html += '<svg class="team-map-links" width="' + width + '" height="' + height + '" aria-label="Result handoffs"><defs><marker id="team-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" /></marker></defs>' + links.map(({ source, target }, index) => {
      const a = positions[id(source)], b = positions[id(target)];
      const forward = b.x >= a.x + 200;
      const x1 = a.x + (forward ? 240 : 120), y1 = a.y + (forward ? 62 : 126);
      const x2 = b.x + (forward ? 0 : 120), y2 = b.y + (forward ? 62 : 0);
      const path = forward ? 'M' + x1 + ',' + y1 + ' C' + (x1 + 50) + ',' + y1 + ' ' + (x2 - 50) + ',' + y2 + ' ' + x2 + ',' + y2 : 'M' + x1 + ',' + y1 + ' C' + x1 + ',' + (y1 + 45) + ' ' + x2 + ',' + (y2 - 45) + ' ' + x2 + ',' + y2;
      const label = /review|critic/i.test(target.role || '') ? 'Review' : 'Hand off';
      return '<text class="team-edge-label" x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 12) + '">' + label + '</text><path class="team-map-edge' + (target.status === 'running' ? ' is-active' : '') + '" d="' + path + '" marker-end="url(#team-arrow)" data-map-edge="' + index + '" tabindex="0" role="button" aria-label="' + esc(team().name(source, 0) + ' sends results to ' + team().name(target, 0)) + '"><title>' + esc(source.title + ' → ' + target.title) + '</title></path>';
    }).join('') + '</svg>';
    html += agents.map((a, index) => {
      const point = positions[id(a)];
      const activity = team().activities(a).at(-1) || '';
      return '<article class="team-map-node" data-map-node="' + esc(id(a)) + '" style="left:' + point.x + 'px;top:' + point.y + 'px"><button type="button" class="team-map-handle" data-map-move="' + esc(id(a)) + '" aria-label="Move ' + esc(team().name(a, index)) + '" title="Drag or use arrow keys">⠿</button><button type="button" class="team-map-person" data-map-select="' + esc(a.assignment_id) + '">' + team().avatar(a.avatar_index ?? index) + '<span><strong>' + esc(a.title || a.objective) + '</strong><span class="team-current">' + esc(team().name(a, index)) + '</span>' + team().state(a, objective.assignments) + (activity ? '<span class="team-map-current" title="' + esc(activity) + '">' + esc(activity) + '</span>' : '') + '</span></button></article>';
    }).join('');
    return { html, width, height };
  }
  function mount(element, objective, options) {
    if (!objective) return;
    if (element._objectiveId !== objective.objective_id) {
      element.innerHTML = '<header class="team-map-header"><button type="button" class="team-quiet" data-map-back>← Back to chat</button><h2>Your team</h2><button type="button" class="btn" data-map-edit aria-pressed="false">Edit team</button><div class="team-actions team-map-edit-actions" hidden><button type="button" class="btn" data-map-add>+ Add agent</button><button type="button" class="btn" data-map-connect>Connect</button><button type="button" class="btn" data-map-group-mode>Group</button><button type="button" class="team-quiet" data-map-arrange>Auto arrange</button></div></header><p class="team-map-hint" role="status">Watch your team work. Click an agent or connection to inspect it.</p><form class="team-map-group-form" hidden><label>Group name<input name="groupName" maxlength="40" required placeholder="e.g. Authentication"></label><button type="submit" class="btn">Group selected</button><span data-group-count>0 selected</span></form><div class="team-map-viewport"><div class="team-map-canvas"></div></div><footer class="team-map-footer"><span>Arrows carry completed work to the next task.</span><div><button type="button" class="team-quiet" data-map-zoom="-1" aria-label="Zoom out">−</button><output class="team-map-zoom">100%</output><button type="button" class="team-quiet" data-map-zoom="1" aria-label="Zoom in">+</button></div></footer>';
      element._objectiveId = objective.objective_id; element._positions = {}; element._zoom = 1;
      element._layoutPending = null; element._layoutQueued = null; element._dragging = false; element._editing = false; element.classList.remove("is-editing"); element._selected = new Set(); element._grouping = false; element._connecting = false; element._source = null;
    }
    element._objective = objective; element._options = options;
    const defaults = arrange(objective);
    if (!element._dragging && !element._layoutPending) element._positions = safePositions(objective, objective.team_layout || defaults);
    const canvas = element.querySelector('.team-map-canvas'), viewport = element.querySelector('.team-map-viewport');
    const hint = (text) => { element.querySelector('.team-map-hint').textContent = text; };
    const control = (action, value, assignmentId) => options.onControl({ objective_id: element._objective.objective_id, ...(assignmentId ? { assignment_id: assignmentId } : {}), action, value: { revision: element._objective.team_revision || 0, ...value } });
    const save = () => {
      const positions = structuredClone(element._positions);
      if (element._layoutPending) { element._layoutQueued = positions; return; }
      element._layoutPending = { revision: element._objective.team_revision || 0, positions };
      control('team_layout', { positions });
    };
    const paint = () => {
      const active = document.activeElement, dataset = canvas.contains(active) ? { ...active.dataset } : null;
      const result = graphHtml(element._objective, element._positions);
      canvas.innerHTML = result.html; canvas.style.width = result.width + 'px'; canvas.style.height = result.height + 'px';
      canvas.style.zoom = element._zoom;
      canvas.querySelectorAll('[data-map-group]').forEach((button) => { button.tabIndex = element._editing ? 0 : -1; });
      canvas.querySelectorAll('[data-map-select]').forEach((button) => {
        button.setAttribute('aria-pressed', String(element._selected?.has(button.dataset.mapSelect) || false));
      });
      if (dataset) Array.from(canvas.querySelectorAll('button, [data-map-edge]')).find((b) => JSON.stringify({ ...b.dataset }) === JSON.stringify(dataset))?.focus({ preventScroll: true });
    };
    if (!element._dragging) paint();
    element.querySelector('[data-map-back]').onclick = options.onBack;
    const toggleEdit = () => {
      element._editing = !element._editing;
      element.classList.toggle('is-editing', element._editing);
      element.querySelector('[data-map-edit]').textContent = element._editing ? 'Done editing' : 'Edit team';
      element.querySelector('[data-map-edit]').setAttribute('aria-pressed', String(element._editing));
      element.querySelector('.team-map-edit-actions').hidden = !element._editing;
      element._connecting = false; element._grouping = false; element._source = null; element._selected = new Set();
      element.querySelector('.team-map-group-form').hidden = true;
      element.querySelector('[data-map-connect]').setAttribute('aria-pressed', 'false');
      element.querySelector('[data-map-group-mode]').setAttribute('aria-pressed', 'false');
      hint(element._editing ? 'Drag agents or groups. Connect tasks or group your team.' : 'Watch your team work. Click an agent or connection to inspect it.');
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
    element.querySelector('[data-map-arrange]').onclick = () => { element._positions = arrange(element._objective); paint(); save(); };
    element.querySelectorAll('[data-map-zoom]').forEach((button) => { button.onclick = () => { element._zoom = Math.min(1.5, Math.max(.5, element._zoom + Number(button.dataset.mapZoom) * .1)); canvas.style.zoom = element._zoom; element.querySelector('.team-map-zoom').textContent = Math.round(element._zoom * 100) + '%'; }; });
    canvas.onclick = (event) => {
      const button = event.target.closest('[data-map-select]');
      if (button) {
        const agent = element._objective.assignments.find((a) => a.assignment_id === button.dataset.mapSelect);
        if (element._grouping) {
          if (element._selected.has(agent.assignment_id)) element._selected.delete(agent.assignment_id); else element._selected.add(agent.assignment_id);
          element.querySelector('[data-group-count]').textContent = element._selected.size + ' selected'; paint(); return;
        }
        if (!element._connecting) { options.onSelect(agent.assignment_id); return; }
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
    element.onkeydown = (event) => { if (event.key === 'Escape' && (element._connecting || element._grouping)) { event.stopPropagation(); element._connecting = false; element._source = null; element.querySelector('[data-map-connect]').setAttribute('aria-pressed', 'false'); element._grouping = false; element._selected = new Set(); groupForm.hidden = true; element.querySelector('[data-map-group-mode]').setAttribute('aria-pressed', 'false'); paint(); hint('Selection cancelled.'); } };
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
    const element = dialog('Add an agent', '<form data-add-agent><label>What should this agent do?<textarea name="task" rows="3" maxlength="8000" required placeholder="Review the sign-in flow…"></textarea></label><div class="team-form-row"><label>Name<input name="agentName" maxlength="40" placeholder="OPai chooses a name"></label><label>AI model<select name="model" aria-label="AI model">' + team().modelOptions('auto', options.models) + '</select></label></div><details><summary>Group & budget</summary><label>Group<input name="group" maxlength="40" list="mapGroups" placeholder="No group"></label><datalist id="mapGroups">' + groups.map((g) => '<option value="' + esc(g) + '"></option>').join('') + '</datalist><label>Task budget ($)<input name="budget" inputmode="decimal" pattern="[0-9]+([.][0-9]+)?" placeholder="Optional"></label></details><label class="team-form-check"><input type="checkbox" name="start">Start immediately</label><p class="team-empty">Leave this off to arrange connections before starting. Existing permissions and objective budget apply.</p><button type="submit" class="btn primary">Add agent</button></form>', objective, options);
    element.querySelector('form').onsubmit = (event) => {
      event.preventDefault(); const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const fields = form.elements;
      element.submitControl('add_agent', { objective: fields.task.value.trim(), ...(fields.agentName.value.trim() ? { name: fields.agentName.value.trim() } : {}), model: fields.model.value, group: fields.group.value.trim(), start: fields.start.checked, budget_usd: fields.budget.value || null });
    };
    element.querySelector('textarea').focus();
  }
  function connectionDialog(objective, source, target, options) {
    const element = dialog('Agent connection', '<p>' + esc(team().name(source, 0)) + ' → ' + esc(team().name(target, 0)) + '</p><p class="team-empty">' + esc(target.title) + ' receives the completed result and file changes from ' + esc(source.title) + '.</p>' + (target.team_controls?.can_connect && options.editing ? '<button type="button" class="btn" data-disconnect>Remove connection</button>' : '<p class="team-empty">' + (target.team_controls?.can_connect ? 'Choose Edit team to change this connection.' : 'This task has already started. Its recorded inputs stay fixed.') + '</p>'), objective, options);
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
  global.OPaiTeamMap = { mount, arrange, edges, graphHtml, addDialog, settle };
})(typeof window !== 'undefined' ? window : globalThis);
