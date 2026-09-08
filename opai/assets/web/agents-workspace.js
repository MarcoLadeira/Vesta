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
  const labels = { run: "Run queued work", pause: "Pause", resume: "Resume", stop: "Stop", cancel: "Stop", sequential: "Run sequentially", budget: "Set budget", set_budget: "Set budget", prioritize: "Move first", reroute: "Reroute", reconcile: "Reconcile", verify: "Verify integration" };
  function controls(item, objectiveId, assignmentId) {
    return '<div class="agents-controls">' + list(item.allowed_actions).filter((a) => Object.prototype.hasOwnProperty.call(labels, a)).map((action) => {
      let field = "";
      if (action === "set_budget" || action === "budget") field = '<input aria-label="Budget in USD" data-agent-value type="text" inputmode="decimal" pattern="[0-9]+([.][0-9]+)?" value="' + esc(decimal(item.budget_usd)) + '">';
      if (action === "reroute") field = '<input aria-label="Assignment model" data-agent-value value="' + esc(item.model) + '">';
      return '<span class="agents-control">' + field + '<button type="button" class="btn" data-agent-action="' + action + '" data-objective-id="' + esc(objectiveId) + '"' + (assignmentId ? ' data-assignment-id="' + esc(assignmentId) + '"' : '') + '>' + labels[action] + '</button></span>';
    }).join("") + '</div>';
  }
  function evidence(label, value) {
    return '<details class="agents-evidence"><summary>' + label + '</summary><pre>' + esc(text(value)) + '</pre></details>';
  }
  function fact(label, value) {
    return value ? '<div class="agents-fact"><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>' : "";
  }
  function artifacts(item, objectiveId, assignmentId) {
    const ids = ' data-objective-id="' + esc(objectiveId) + '"' + (assignmentId ? ' data-assignment-id="' + esc(assignmentId) + '"' : '');
    return '<div class="agents-controls">' + (item.worktree ? '<button type="button" class="btn" data-agent-worktree' + ids + '>Open worktree</button>' : '') + (item.receipt ? '<button type="button" class="btn" data-agent-receipt' + ids + '>Copy receipt</button>' : '') + '</div>';
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
    let html = '<div class="agents-workspace"><div class="page-title">Agents</div><p class="page-sub">Supervise assignments, inspect evidence, and verify the integrated result.</p>';
    if (!objectives.length) html += '<div class="card"><div class="ct">No objectives yet</div><p>Enable “Allow multiple agents mode” in the composer’s Mode menu, then send an objective.</p></div>';
    const activeObjective = objectives.find((o) => o.objective_id === selection.objectiveId) || objectives[0];
    if (objectives.length > 1) html += '<nav class="agents-objective-nav" aria-label="Engineering objectives">' + objectives.map((o) => '<button type="button" class="agents-assignment" data-objective-select="' + esc(o.objective_id) + '" aria-pressed="' + (o === activeObjective) + '"><strong>' + esc(o.objective) + '</strong><span>' + status(o.status) + ' · ' + cost(o.cost_usd) + '</span></button>').join("") + '</nav>';
    (activeObjective ? [activeObjective] : []).forEach((o) => {
      const assignments = list(o.assignments);
      const selected = assignments.find((a) => a.assignment_id === selection.assignmentId && (!selection.objectiveId || selection.objectiveId === o.objective_id)) || assignments[0];
      html += '<section class="agents-objective card" data-objective-id="' + esc(o.objective_id) + '"><header><div><p class="agents-eyebrow">Engineering objective</p><h2>' + esc(o.objective) + '</h2></div><span class="pill neutral">' + status(o.status) + '</span></header>';
      html += '<div class="agents-metrics"><span>Cost <strong>' + cost(o.cost_usd) + '</strong></span><span>Budget <strong>' + cost(o.budget_usd) + '</strong></span><span>Parallel limit <strong>' + esc(text(o.max_parallel)) + '</strong></span></div>';
      if (o.cost_complete !== true) html += '<p class="agents-note">Cost reporting incomplete</p>';
      if (o.planning && o.planning.status !== "pending") html += '<section class="agents-section"><h3>Planning · ' + status(o.planning.status) + '</h3>' + (o.planning.result && o.planning.result.error ? '<p class="agents-note">' + esc(o.planning.result.error) + '</p>' : '') + '</section>';
      html += controls(o, o.objective_id) + '<div class="agents-split"><div class="agents-assignments"><h3>Assignments</h3>';
      if (!assignments.length) html += '<p>No assignments recorded.</p>';
      assignments.forEach((a) => {
        html += '<button type="button" class="agents-assignment" aria-pressed="' + (a === selected) + '" data-agent-select="' + esc(a.assignment_id) + '" data-objective-id="' + esc(o.objective_id) + '"><strong>' + esc(a.title || a.assignment_id) + '</strong><span>' + status(a.status) + (a.role ? ' · ' + esc(a.role) : '') + '</span><span>' + esc(a.observed_model || a.model || "Model not reported") + ' · ' + cost(a.cost_usd) + (a.cost_complete === false ? ' (incomplete)' : '') + '</span></button>';
      });
      html += '</div><section class="agents-detail" aria-label="Selected assignment">';
      if (selected) {
        html += '<p class="agents-eyebrow">Selected assignment</p><h3>' + esc(selected.title || selected.assignment_id) + '</h3>';
        if (selected.objective) html += '<p>' + esc(selected.objective) + '</p>';
        if (selected.rationale) html += '<section class="agents-section"><h4>Why this assignment</h4><p>' + esc(selected.rationale) + '</p></section>';
        if (selected.parallel_eligible === false) html += '<p class="agents-note">Runs sequentially by plan</p>';
        html += '<dl class="agents-facts">' + fact("Owner", selected.owner || selected.last_owner) + fact("Model", selected.observed_model || selected.model) + fact("Provider", selected.observed_provider || selected.provider) + fact("Route", typeof selected.route === "string" ? selected.route : selected.route && selected.route.kind) + '</dl>';
        html += controls(selected, o.objective_id, selected.assignment_id);
        if (selected.blocked_reason) html += '<p class="agents-note">' + esc(selected.blocked_reason) + '</p>';
        if (selected.admission && selected.admission.reason !== selected.blocked_reason) html += '<p class="agents-note">' + esc(selected.admission.reason) + '</p>';
        html += activity(selected.activity);
        html += '<div class="agents-detail-grid">' + items("Intended paths", selected.intended_paths, "Scope not recorded") + items("Dependencies", list(selected.depends_on).map((id) => { const dependency = assignments.find((a) => (a.assignment_id === id || a.name === id)); return dependency ? (dependency.title || id) + " · " + id : id; }), "No dependencies recorded") + '</div>';
        html += items("Changed files", selected.changed_files, "No changed files recorded") + verification(selected.verification);
        html += '<dl class="agents-facts">' + fact("Worktree", selected.worktree) + fact("Branch", selected.branch) + '</dl>';
        html += artifacts(selected, o.objective_id, selected.assignment_id);
        if (selected.result && selected.result.handoff && selected.result.handoff.summary) html += '<section class="agents-section"><h4>Reported findings</h4><p>' + esc(selected.result.handoff.summary) + '</p></section>';
        if (selected.receipt) html += evidence("Agent receipt", selected.receipt);
        html += evidence("Assignment evidence", selected);
      }
      const integration = o.integration || {};
      html += '</section></div><section class="agents-integration"><header><h3>Integration</h3><span class="pill neutral">' + status(integration.status) + '</span></header>';
      if (integration.result && (integration.result.summary || integration.result.error)) html += '<p>' + esc(integration.result.summary || integration.result.error) + '</p>';
      html += '<div class="agents-detail-grid">' + verification(integration.verification) + items("Conflicts", integration.conflicts) + '</div>';
      html += items("Integrated files", integration.result && integration.result.changed_files);
      html += '<dl class="agents-facts">' + fact("Integrated commit", integration.result && integration.result.head_sha) + '</dl>';
      html += '<dl class="agents-facts">' + fact("Integration branch", integration.branch) + fact("Integration worktree", integration.worktree) + '</dl>';
      html += evidence("Integration evidence", integration);
      if (o.receipt) html += evidence("Result receipt", o.receipt);
      html += artifacts({ worktree: integration.worktree, receipt: o.receipt }, o.objective_id);
      html += '</section></section>';
    });
    // Provider readiness remains visible even before the first objective.
    if (list(snapshot && snapshot.cards).length) html += '<h2>' + esc(snapshot.title || "Provider readiness") + '</h2>';
    list(snapshot && snapshot.cards).forEach((card) => {
      html += '<section class="card"><header><h3>' + esc(card.title) + '</h3><span class="pill neutral">' + esc(card.status) + '</span></header><p>' + esc(card.body) + '</p>' + list(card.items).map((v) => '<p>' + esc(text(v)) + '</p>').join("");
      html += '<div class="card-metrics">' + list(card.metrics).map((m) => '<div class="card-metric"><span>' + esc(m.label) + '</span><strong>' + esc(m.value) + '</strong></div>').join("") + '</div>';
      if (card.footnote) html += '<p>' + esc(card.footnote) + '</p>';
      html += '</section>';
    });
    html += '<div class="actions">' + list(snapshot && snapshot.actions).map((a) => '<button type="button" class="btn" data-readiness-action="' + esc(a.id) + '" data-command="' + esc(a.command || "") + '">' + esc(a.label) + '</button>').join("") + '</div>';
    return html + '</div>';
  }
  function mount(element, snapshot, options) {
    options = options || {};
    const openEvidence = new Set(Array.from(element.querySelectorAll(".agents-evidence[open]")).map((detail) => (detail.closest("[data-objective-id]") || {}).dataset?.objectiveId + ":" + detail.querySelector("summary").textContent));
    element.innerHTML = renderHtml(snapshot, options.selection);
    element.querySelectorAll(".agents-evidence").forEach((detail) => { detail.open = openEvidence.has((detail.closest("[data-objective-id]") || {}).dataset?.objectiveId + ":" + detail.querySelector("summary").textContent); });
    element.querySelectorAll("[data-readiness-action]").forEach((button) => { button.onclick = () => options.onAction && options.onAction(button.dataset.readinessAction, button.dataset.command); });
    element.querySelectorAll("[data-objective-select]").forEach((button) => { button.onclick = () => options.onSelect && options.onSelect({ objectiveId: button.dataset.objectiveSelect }); });
    element.querySelectorAll("[data-agent-worktree]").forEach((button) => { button.onclick = () => options.onOpenWorktree && options.onOpenWorktree({ objective_id: button.dataset.objectiveId, assignment_id: button.dataset.assignmentId }); });
    element.querySelectorAll("[data-agent-receipt]").forEach((button) => { button.onclick = () => {
      const objective = list(snapshot.objectives).find((item) => item.objective_id === button.dataset.objectiveId);
      const item = button.dataset.assignmentId ? objective && list(objective.assignments).find((row) => row.assignment_id === button.dataset.assignmentId) : objective;
      if (item && item.receipt && options.onCopyReceipt) options.onCopyReceipt(item.receipt);
    }; });
    element.querySelectorAll("[data-agent-select]").forEach((button) => { button.onclick = () => options.onSelect && options.onSelect({ objectiveId: button.dataset.objectiveId, assignmentId: button.dataset.agentSelect }); });
    element.querySelectorAll("[data-agent-action]").forEach((button) => { button.onclick = () => {
      const payload = { objective_id: button.dataset.objectiveId, action: button.dataset.agentAction };
      if (button.dataset.assignmentId) payload.assignment_id = button.dataset.assignmentId;
      const input = button.parentElement.querySelector("[data-agent-value]");
      if (input) {
        if (!input.reportValidity() || !input.value.trim()) return;
        payload.value = input.type === "number" ? Number(input.value) : input.value.trim();
      }
      if (options.onControl) options.onControl(payload);
    }; });
  }
  global.OPaiAgentsWorkspace = { renderHtml, mount };
})(typeof window !== "undefined" ? window : globalThis);
