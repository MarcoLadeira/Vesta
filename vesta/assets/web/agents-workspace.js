/* A projection of the canonical journal. No scheduling or outcome inference. */
(function (global) {
  "use strict";
  const esc = (value) => String(value == null ? "" : value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const list = (value) => Array.isArray(value) ? value : [];
  const text = (value) => value == null ? "Not reported" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  const status = (value) => esc(value ? String(value).replace(/[_-]/g, " ").replace(/^./, (c) => c.toUpperCase()) : "Not reported");
  function decimal(value) {
    // Format the journal's exact decimal without a floating point conversion.
    if (typeof value === "number" && Number.isFinite(value) && value >= 0) value = String(value);
    if (typeof value !== "string" || value.length > 100) return null;
    const match = /^(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d{1,3}))?$/.exec(value);
    if (!match || Math.abs(Number(match[3] || 0)) > 100) return null;
    const digits = match[1] + (match[2] || "");
    const point = match[1].length + Number(match[3] || 0);
    const expanded = point <= 0 ? "0." + "0".repeat(-point) + digits : point >= digits.length ? digits + "0".repeat(point - digits.length) : digits.slice(0, point) + "." + digits.slice(point);
    return expanded.replace(/^0+(?=\d)/, "");
  }
  function cost(value) {
    const amount = decimal(value);
    if (amount === null) return "Not reported";
    const parts = amount.split(".");
    return "$" + parts[0] + "." + (parts[1] || "").padEnd(2, "0");
  }
  function runSettings(maxParallel, budgetUsd) {
    if (!Number.isInteger(maxParallel) || maxParallel < 1 || maxParallel > 4) throw new Error("Choose between 1 and 4 concurrent agents in Team limits.");
    const budget = budgetUsd == null ? "" : String(budgetUsd).trim();
    if ((budgetUsd != null && typeof budgetUsd !== "string") || (budget && (budget.length > 100 || !/^\d+(?:\.\d+)?$/.test(budget) || budget.replace(/^0+/, "").split(".")[0].length > 31))) throw new Error("Enter a nonnegative USD amount in Team limits, or leave the budget blank for no cap.");
    return { maxParallel, budgetUsd: budget || null };
  }
  const labels = { run: "Run queued work", pause: "Pause", resume: "Resume", stop: "Stop", cancel: "Stop", sequential: "Run sequentially", budget: "Set budget", set_budget: "Set budget", prioritize: "Move first", reroute: "Reroute", reconcile: "Reconcile", verify: "Verify integration", approve: "Approve once", retry: "Retry blocked attempt", request_review: "Request review" };
  const secondaryActions = new Set(["budget", "set_budget", "reroute", "prioritize", "sequential", "request_review"]);
  function disclosure(label, content, key) {
    return '<details class="agents-disclosure" data-disclosure="' + esc(key || label) + '"><summary>' + esc(label) + '</summary><div class="agents-disclosure-body">' + content + '</div></details>';
  }
  function stateBadge(value) {
    const tones = new Map([["running", "active"], ["completed", "success"], ["failed", "attention"], ["blocked", "attention"], ["needs-attention", "attention"]]);
    const tone = tones.get(value) || "muted";
    return '<span class="agents-state agents-state-' + tone + '"><i aria-hidden="true"></i>' + status(value) + '</span>';
  }
  function needsAttention(assignment) {
    return !!assignment.pending_approval || list(assignment.allowed_actions).includes("retry") || ["failed", "needs-attention"].includes(assignment.status);
  }
  function attentionSummary(objective, selectedId) {
    const attention = list(objective.assignments).filter(needsAttention);
    if (!attention.length) return "";
    const next = attention[(attention.findIndex((a) => a.assignment_id === selectedId) + 1) % attention.length];
    return '<div class="agents-attention"><span>' + attention.length + (attention.length === 1 ? ' assignment needs attention' : ' assignments need attention') + '</span><button type="button" class="btn" data-agent-attention="' + esc(next.assignment_id) + '" data-objective-id="' + esc(objective.objective_id) + '">' + (attention.length > 1 ? 'Next to review' : 'Review assignment') + '</button></div>';
  }
  function progress(objective) {
    const assignments = list(objective.assignments);
    if (!assignments.length) return '<p class="agents-muted">Assignments will appear here when the plan is ready.</p>';
    const completed = assignments.filter((a) => a.status === "completed").length;
    return '<div class="agents-progress"><span>' + completed + ' of ' + assignments.length + ' assignments completed</span><div class="agents-progress-track" aria-hidden="true">' + assignments.map((a) => '<span class="' + (a.status === "completed" ? 'done' : a.status === "running" ? 'active' : '') + '"></span>').join("") + '</div></div>';
  }
  function controls(item, objectiveId, assignmentId) {
    const primary = [], secondary = [];
    list(item.allowed_actions).filter((a) => Object.prototype.hasOwnProperty.call(labels, a)).forEach((action) => {
      let field = "";
      if (action === "set_budget" || action === "budget") field = '<label>Budget in USD<input aria-label="Budget in USD" data-agent-value type="text" inputmode="decimal" pattern="[0-9]+([.][0-9]+)?" value="' + esc(decimal(item.budget_usd)) + '"></label>';
      if (action === "reroute") field = '<label>Assignment model<input aria-label="Assignment model" data-agent-value value="' + esc(item.model) + '"></label>';
      const ids = ' data-objective-id="' + esc(objectiveId) + '"' + (assignmentId ? ' data-assignment-id="' + esc(assignmentId) + '"' : '');
      const removeCap = (action === "budget" || action === "set_budget") && decimal(item.budget_usd) !== null ? '<button type="button" class="btn" data-agent-action="budget" data-agent-clear-budget' + ids + '>Remove cap</button>' : '';
      const controlKey = esc(JSON.stringify([objectiveId, assignmentId || "", action]));
      (secondaryActions.has(action) ? secondary : primary).push('<div class="agents-control" data-control-key="' + controlKey + '">' + field + '<button type="button" class="btn" data-agent-action="' + action + '"' + ids + '>' + labels[action] + '</button>' + removeCap + '</div>');
    });
    return (primary.length ? '<div class="agents-controls">' + primary.join("") + '</div>' : '') + (secondary.length ? disclosure(assignmentId ? "Assignment settings" : "Objective settings", '<div class="agents-settings">' + secondary.join("") + '</div>', "settings:" + objectiveId + ":" + (assignmentId || "")) : '');
  }
  function evidence(label, value) {
    return '<details class="agents-evidence"><summary>' + label + '</summary><pre>' + esc(text(value)) + '</pre></details>';
  }
  function fact(label, value) {
    return value ? '<div class="agents-fact"><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>' : "";
  }
  function artifacts(item, objectiveId, assignmentId) {
    const ids = ' data-objective-id="' + esc(objectiveId) + '"' + (assignmentId ? ' data-assignment-id="' + esc(assignmentId) + '"' : '');
    const recorded = item.result && (item.result.git_evidence || item.result);
    return '<div class="agents-controls">' + (item.worktree ? '<button type="button" class="btn" data-agent-worktree' + ids + '>Open worktree</button>' : '') + (item.worktree && recorded && recorded.base_sha && recorded.head_sha ? '<button type="button" class="btn" data-agent-artifact="diff"' + ids + '>Inspect diff</button><button type="button" class="btn" data-agent-artifact="pr"' + ids + '>Find existing PR</button>' : '') + (item.receipt ? '<button type="button" class="btn" data-agent-receipt' + ids + '>Copy receipt</button>' : '') + '</div>';
  }
  function items(label, values, empty) {
    const entries = list(values);
    if (!entries.length && !empty) return "";
    return '<section class="agents-section"><h4>' + label + '</h4>' + (entries.length ? '<ul class="agents-list">' + entries.map((v) => '<li>' + esc(typeof v === "object" ? v.path || v.title || v.name || text(v) : v) + '</li>').join("") + '</ul>' : '<p class="agents-muted">' + empty + '</p>') + '</section>';
  }
  function verification(value) {
    const v = value && typeof value === "object" ? value : {};
    let html = '<section class="agents-section"><h4>Verification</h4><p>' + status(v.status || (v.passed === true ? "passed" : v.passed === false ? "failed" : "not recorded")) + '</p>';
    if (v.summary) html += '<p>' + esc(v.summary) + '</p>';
    html += '<ul class="agents-checks">' + list(v.checks).map((check) => '<li><span>' + esc(check.label || check.name || check.command || check.id || "Check") + '</span><strong>' + status(check.status || (check.passed === true ? "passed" : check.passed === false ? "failed" : null)) + '</strong></li>').join("") + '</ul>';
    return html + '</section>';
  }
  function activity(value) {
    if (typeof value === "string" && value.startsWith("{")) { try { value = JSON.parse(value); } catch (_) {} }
    const events = Array.isArray(value) ? value : value ? [value] : [];
    if (!events.length) return "";
    return '<section class="agents-section agents-activity"><h4>Latest activity</h4><ol>' + events.slice(-6).map((entry) => '<li>' + esc(typeof entry === "object" ? entry.title || entry.message || entry.detail || "Activity recorded" : entry) + '</li>').join("") + '</ol></section>';
  }
  function renderHtml(snapshot, selection) {
    selection = selection || {};
    const objectives = list(snapshot && snapshot.objectives);
    let html = '<div class="agents-workspace"><div class="agents-page-heading"><div class="page-title">Agents</div><p class="page-sub">One objective. A coordinated team.</p><button type="button" class="team-quiet" data-agent-back-chat>Back to chat</button></div>';
    const unavailable = snapshot && snapshot.agentsRuntime && snapshot.agentsRuntime.supported === false;
    if (unavailable) html += '<p class="agents-note">' + esc(snapshot.agentsRuntime.reason) + '</p>';
    if (!objectives.length) html += '<div class="card"><div class="ct">No objectives yet</div>' + (!unavailable ? '<p>Press Team in the composer, then send an objective. You can also enable “Allow multiple agents mode” in the Mode menu.</p>' : '') + '</div>';
    const activeObjective = objectives.find((o) => o.objective_id === selection.objectiveId) || objectives[0];
    if (objectives.length > 1) html += '<nav class="agents-objective-nav" aria-label="Engineering objectives">' + objectives.map((o) => '<button type="button" class="agents-assignment" data-objective-select="' + esc(o.objective_id) + '" aria-pressed="' + (o === activeObjective) + '"><strong>' + esc(o.objective) + '</strong><span>' + status(o.status) + ' · ' + cost(o.cost_usd) + '</span></button>').join("") + '</nav>';
    (activeObjective ? [activeObjective] : []).forEach((o) => {
      const assignments = list(o.assignments);
      const selected = assignments.find((a) => a.assignment_id === selection.assignmentId && (!selection.objectiveId || selection.objectiveId === o.objective_id)) || assignments[0];
      html += '<section class="agents-objective card" data-objective-id="' + esc(o.objective_id) + '"><header><div><p class="agents-eyebrow">Objective</p><h2>' + esc(o.objective) + '</h2></div>' + stateBadge(o.status) + '</header>';
      html += '<div class="agents-metrics"><span>Cost <strong>' + cost(o.cost_usd) + '</strong></span><span>Budget <strong>' + (o.budget_usd === null ? 'No cap' : cost(o.budget_usd)) + '</strong></span><span>Parallel limit <strong>' + esc(text(o.max_parallel)) + '</strong></span></div>';
      html += progress(o);
      if (o.cost_complete !== true) html += '<p class="agents-note">Cost reporting incomplete</p>';
      if (o.planning && o.planning.status !== "pending") html += '<section class="agents-section"><h3>Planning · ' + status(o.planning.status) + '</h3>' + (o.planning.result && o.planning.result.error ? '<p class="agents-note">' + esc(o.planning.result.error) + '</p>' : '') + '</section>';
      html += controls(o, o.objective_id);
      html += attentionSummary(o, selected && selected.assignment_id);
      if (assignments.length) {
        html += '<div class="agents-split"><nav class="agents-assignments" aria-label="Assignments"><h3>Assignments</h3>';
        assignments.forEach((a) => {
          html += '<button type="button" class="agents-assignment" aria-pressed="' + (a === selected) + '" data-agent-select="' + esc(a.assignment_id) + '" data-objective-id="' + esc(o.objective_id) + '"><strong>' + esc(a.title || a.assignment_id) + '</strong><span>' + status(a.status) + (a.role ? ' · ' + esc(a.role) : '') + (a.pending_approval ? ' · Approval requested' : '') + '</span><span>' + esc(a.observed_model || a.model || "Model not reported") + ' · ' + cost(a.cost_usd) + (a.cost_complete === false ? ' (incomplete)' : '') + '</span></button>';
        });
        html += '</nav><section class="agents-detail" data-assignment-id="' + esc(selected && selected.assignment_id || '') + '" aria-label="Selected assignment">';
        if (selected) {
          html += '<div class="agents-detail-heading"><p class="agents-eyebrow">Selected assignment</p>' + stateBadge(selected.status) + '</div><h3>' + esc(selected.title || selected.assignment_id) + '</h3>';
          if (selected.objective) html += '<p>' + esc(selected.objective) + '</p>';
          if (selected.parallel_eligible === false) html += '<p class="agents-note">Runs sequentially by plan</p>';
          if (selected.pending_approval) html += '<section class="agents-section"><h4>Operation awaiting approval</h4><p>' + esc(selected.pending_approval.reason) + '</p><pre>' + esc(text(selected.pending_approval.command || selected.pending_approval.files)) + '</pre><p>' + (selected.pending_approval.kind === "edits" ? 'Starts one continuation with file editing enabled within the assignment scope. The listed files are the edits that prompted this request.' : 'Starts a new attempt with permission for this command once.') + '</p></section>';
          html += controls(selected, o.objective_id, selected.assignment_id);
          if (list(selected.allowed_actions).includes("retry")) html += '<p class="agents-note">No provider call was dispatched. Adjust the model or budget, then retry with a new attempt.</p>';
          if (selected.blocked_reason) html += '<p class="agents-note">' + esc(selected.blocked_reason) + '</p>';
          if (selected.admission && selected.admission.reason !== selected.blocked_reason) html += '<p class="agents-note">' + esc(selected.admission.reason) + '</p>';
          html += activity(selected.activity);
          if (selected.result && selected.result.handoff && selected.result.handoff.summary) html += '<section class="agents-section"><h4>Reported findings</h4><p>' + esc(selected.result.handoff.summary) + '</p></section>';
          html += items("Changed files", selected.changed_files);
          if (selected.verification) html += verification(selected.verification);
          html += artifacts(selected, o.objective_id, selected.assignment_id);
          html += '<details class="agents-disclosure" data-disclosure="details:' + esc(o.objective_id) + ':' + esc(selected.assignment_id) + '"><summary>Assignment details &amp; evidence</summary><div class="agents-disclosure-body">';
          if (selected.rationale) html += '<section class="agents-section"><h4>Why this assignment</h4><p>' + esc(selected.rationale) + '</p></section>';
          html += '<dl class="agents-facts">' + fact("Owner", selected.owner || selected.last_owner) + fact("Model", selected.observed_model || selected.model) + fact("Provider", selected.observed_provider || selected.provider) + fact("Route", typeof selected.route === "string" ? selected.route : selected.route && selected.route.kind) + '</dl>';
          html += '<div class="agents-detail-grid">' + items("Intended paths", selected.intended_paths, "Scope not recorded") + items("Dependencies", list(selected.depends_on).map((id) => { const dependency = assignments.find((a) => (a.assignment_id === id || a.name === id)); return dependency ? (dependency.title || id) + " · " + id : id; }), "No dependencies recorded") + '</div>';
          html += '<dl class="agents-facts">' + fact("Worktree", selected.worktree) + fact("Branch", selected.branch) + '</dl>';
          if (selected.receipt) html += evidence("Agent receipt", selected.receipt);
          html += evidence("Assignment evidence", selected);
          html += '</div></details>';
        }
        html += '</section></div>';
      }
      const integration = o.integration || {};
      html += '<section class="agents-integration"><header><h3>Integration</h3>' + stateBadge(integration.status) + '</header>';
      if (integration.result && (integration.result.summary || integration.result.error)) html += '<p>' + esc(integration.result.summary || integration.result.error) + '</p>';
      html += items("Conflicts", integration.conflicts);
      html += '<details class="agents-disclosure" data-disclosure="integration:' + esc(o.objective_id) + '"><summary>Integration details &amp; verification</summary><div class="agents-disclosure-body">';
      html += verification(integration.verification);
      html += items("Integrated files", integration.result && integration.result.changed_files);
      html += '<dl class="agents-facts">' + fact("Integrated commit", integration.result && integration.result.head_sha) + '</dl>';
      html += '<dl class="agents-facts">' + fact("Integration branch", integration.branch) + fact("Integration worktree", integration.worktree) + '</dl>';
      html += evidence("Integration evidence", integration);
      if (o.receipt) html += evidence("Result receipt", o.receipt);
      html += '</div></details>';
      html += artifacts({ ...integration, receipt: o.receipt }, o.objective_id);
      html += '</section></section>';
    });
    // Provider readiness remains visible even before the first objective.
    if (list(snapshot && snapshot.cards).length) html += '<details class="agents-disclosure" data-disclosure="providers"' + (!objectives.length ? ' open' : '') + '><summary>Provider readiness</summary><div class="agents-disclosure-body">';
    list(snapshot && snapshot.cards).forEach((card) => {
      html += '<section class="card"><header><h3>' + esc(card.title) + '</h3><span class="pill neutral">' + esc(card.status) + '</span></header><p>' + esc(card.body) + '</p>' + list(card.items).map((v) => '<p>' + esc(text(v)) + '</p>').join("");
      html += '<div class="card-metrics">' + list(card.metrics).map((m) => '<div class="card-metric"><span>' + esc(m.label) + '</span><strong>' + esc(m.value) + '</strong></div>').join("") + '</div>';
      if (card.footnote) html += '<p>' + esc(card.footnote) + '</p>';
      html += '</section>';
    });
    html += '<div class="actions">' + list(snapshot && snapshot.actions).map((a) => '<button type="button" class="btn" data-readiness-action="' + esc(a.id) + '" data-command="' + esc(a.command || "") + '">' + esc(a.label) + '</button>').join("") + '</div>';
    if (list(snapshot && snapshot.cards).length) html += '</div></details>';
    return html + '</div>';
  }
  function renderCompact(objective) {
    const assignments = global.VestaAgentsTeam ? global.VestaAgentsTeam.agents(objective) : list(objective.assignments);
    const working = assignments.filter((a) => a.status === 'running').length;
    const attention = assignments.filter(needsAttention).length;
    const summary = working ? working + (working === 1 ? ' agent working' : ' agents working') : objective.status === 'completed' ? 'Team finished' : objective.status === 'ready-to-integrate' ? 'Ready for combined checks' : objective.status === 'planning' || !assignments.length ? 'Putting your team together' : assignments.length + ' agents · ' + status(objective.status);
    return '<section class="agents-chat-card" aria-label="Team summary"><span class="agents-state' + (working ? ' agents-state-active' : '') + '"><i aria-hidden="true"></i>' + summary + '</span><span class="team-summary-cost">' + cost(objective.cost_usd) + (objective.cost_complete !== true ? ' · incomplete' : '') + '</span>' + (attention ? '<span class="agents-note">' + attention + (attention === 1 ? ' assignment needs attention' : ' assignments need attention') + '</span>' : '') + '<button type="button" class="team-quiet" data-open-team>View team</button></section>';
  }
  function mount(element, snapshot, options) {
    options = options || {};
    const projection = JSON.stringify([snapshot, options.selection]);
    if (element._agentsProjection === projection && element.querySelector('.agents-workspace')) return;
    const html = renderHtml(snapshot, options.selection);
    const detailKey = (detail) => detail.dataset.disclosure || JSON.stringify([
      detail.closest("[data-objective-id]")?.dataset.objectiveId,
      detail.closest("[data-assignment-id]")?.dataset.assignmentId,
      detail.querySelector("summary").textContent,
    ]);
    const expanded = element._agentsExpanded || new Map();
    element.querySelectorAll("details").forEach((detail) => expanded.set(detailKey(detail), detail.open));
    const fieldKey = (node) => node.closest('[data-control-key]').dataset.controlKey;
    const drafts = element._agentsDrafts || new Map();
    element.querySelectorAll('[data-agent-value]').forEach((input) => {
      if (input.value !== input.defaultValue) drafts.set(fieldKey(input), { value: input.value, baseline: input.defaultValue, conflict: input._agentsConflict === true });
      else drafts.delete(fieldKey(input));
    });
    const identity = (node) => JSON.stringify([node.tagName, node.textContent, { ...node.dataset }, node.closest("[data-disclosure]")?.dataset.disclosure, node.closest('[data-control-key]')?.dataset.controlKey]);
    const active = element.contains(document.activeElement) ? identity(document.activeElement) : null;
    const caret = active && document.activeElement.matches('[data-agent-value]') ? [document.activeElement.selectionStart, document.activeElement.selectionEnd, document.activeElement.selectionDirection] : null;
    const scrollKey = (node) => JSON.stringify([node.closest('[data-objective-id]')?.dataset.objectiveId, node.className || detailKey(node.closest('details'))]);
    const scroll = new Map(Array.from(element.querySelectorAll('.agents-assignments, .agents-objective-nav, .agents-evidence pre')).map((node) => [scrollKey(node), [node.scrollLeft, node.scrollTop]]));
    element.innerHTML = html;
    element._agentsProjection = projection;
    element._agentsExpanded = expanded;
    element._agentsDrafts = drafts;
    element.querySelectorAll('[data-agent-value]').forEach((input) => {
      const draft = drafts.get(fieldKey(input));
      if (draft && draft.value !== input.defaultValue) {
        input.value = draft.value;
        if (draft.conflict || draft.baseline !== input.defaultValue) {
          input._agentsConflict = true;
          input.setCustomValidity('This value changed elsewhere. Edit it to confirm your intended value before applying.');
        }
      } else drafts.delete(fieldKey(input));
      input.oninput = () => { input._agentsConflict = false; input.setCustomValidity(''); };
    });
    const detail = element.querySelector('.agents-detail');
    const selectedKey = detail ? JSON.stringify([detail.closest('[data-objective-id]').dataset.objectiveId, detail.dataset.assignmentId]) : null;
    if (detail && selectedKey !== element._agentsSelected) detail.classList.add('agents-selection-enter');
    element._agentsSelected = selectedKey;
    element.querySelectorAll("details").forEach((detail) => { if (expanded.has(detailKey(detail))) detail.open = expanded.get(detailKey(detail)); });
    element.querySelectorAll('.agents-assignments, .agents-objective-nav, .agents-evidence pre').forEach((node) => {
      const position = scroll.get(scrollKey(node));
      if (position) { node.scrollLeft = position[0]; node.scrollTop = position[1]; }
    });
    if (active) {
      const target = Array.from(element.querySelectorAll("button, summary, input")).find((node) => identity(node) === active);
      target?.focus({ preventScroll: true });
      if (target && caret) target.setSelectionRange(...caret);
    }
    element.querySelectorAll("[data-readiness-action]").forEach((button) => { button.onclick = () => options.onAction && options.onAction(button.dataset.readinessAction, button.dataset.command); });
    element.querySelector('[data-agent-back-chat]').onclick = () => options.onBackToChat && options.onBackToChat();
    element.querySelectorAll("[data-objective-select]").forEach((button) => { button.onclick = () => options.onSelect && options.onSelect({ objectiveId: button.dataset.objectiveSelect }); });
    element.querySelectorAll("[data-agent-worktree]").forEach((button) => { button.onclick = () => options.onOpenWorktree && options.onOpenWorktree({ objective_id: button.dataset.objectiveId, assignment_id: button.dataset.assignmentId }); });
    element.querySelectorAll("[data-agent-artifact]").forEach((button) => { button.onclick = () => options.onInspectArtifact && options.onInspectArtifact({ objective_id: button.dataset.objectiveId, assignment_id: button.dataset.assignmentId, kind: button.dataset.agentArtifact }); });
    element.querySelectorAll("[data-agent-receipt]").forEach((button) => { button.onclick = () => {
      const objective = list(snapshot.objectives).find((item) => item.objective_id === button.dataset.objectiveId);
      const item = button.dataset.assignmentId ? objective && list(objective.assignments).find((row) => row.assignment_id === button.dataset.assignmentId) : objective;
      if (item && item.receipt && options.onCopyReceipt) options.onCopyReceipt(item.receipt);
    }; });
    element.querySelectorAll("[data-agent-select], [data-agent-attention]").forEach((button) => { button.onclick = () => options.onSelect && options.onSelect({ objectiveId: button.dataset.objectiveId, assignmentId: button.dataset.agentSelect || button.dataset.agentAttention }); });
    element.querySelectorAll("[data-agent-action]").forEach((button) => { button.onclick = () => {
      const payload = { objective_id: button.dataset.objectiveId, action: button.dataset.agentAction };
      if (button.dataset.assignmentId) payload.assignment_id = button.dataset.assignmentId;
      const objective = list(snapshot.objectives).find((item) => item.objective_id === payload.objective_id);
      if (payload.action === "retry") {
        const assignment = objective && list(objective.assignments).find((item) => item.assignment_id === payload.assignment_id);
        if (!assignment || !assignment.run_id) return;
        payload.value = { run_id: assignment.run_id };
      }
      if (payload.action === "request_review") payload.value = { revision: objective && objective.revision };
      if (payload.action === "approve") {
        const assignment = objective && list(objective.assignments).find((item) => item.assignment_id === payload.assignment_id);
        if (!assignment || !assignment.pending_approval || !assignment.pending_approval.request_id) return;
        payload.value = { request_id: assignment.pending_approval.request_id };
      }
      const input = button.parentElement.querySelector("[data-agent-value]");
      if (button.hasAttribute("data-agent-clear-budget")) payload.value = null;
      else if (input) {
        if (!input.reportValidity() || !input.value.trim()) return;
        payload.value = input.type === "number" ? Number(input.value) : input.value.trim();
      }
      if (options.onControl) options.onControl(payload);
    }; });
  }
  global.VestaAgentsWorkspace = { renderHtml, renderCompact, runSettings, mount };
})(typeof window !== "undefined" ? window : globalThis);
