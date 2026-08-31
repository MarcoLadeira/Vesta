/* OPai web UI front-end. Renders JSON the Python bridge provides; never computes
   anything sensitive itself. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) =>
  String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const uiIcon = (name, options) => window.OPaiIcons.icon(name, options);

const PROVIDER_COLOR = { claude: "#e0937a", codex: "#6cc1e8", auto: "#98a2b0", local: "#34d399" };
const MODE_PRESENTATION_LABELS = {
  ask: "Ask",
  plan: "Plan",
  "approve-edits": "Manual",
  "safe-auto": "Auto",
  "auto-edits": "Accept edits",
  "full-auto": "Bypass permissions",
};
const modePresentationLabel = (mode) => {
  if (!mode) return "Auto";
  return MODE_PRESENTATION_LABELS[mode.id] || mode.label || "Mode";
};
const modePresentationCopy = (value) =>
  String(value == null ? "" : value)
    .replace(/\bSafe Auto\b/g, MODE_PRESENTATION_LABELS["safe-auto"])
    .replace(/\bApprove Edits\b/g, MODE_PRESENTATION_LABELS["approve-edits"])
    .replace(/\bFull Auto\b/g, MODE_PRESENTATION_LABELS["full-auto"]);
if (typeof window !== "undefined") {
  window.OPaiModePresentationLabel = modePresentationLabel;
  window.OPaiModePresentationCopy = modePresentationCopy;
}

const PALETTE = [
  { id: "new_chat", label: "New chat", hint: "Ctrl+N" },
  { id: "new_app", label: "New app (free scaffold)", hint: "" },
  { id: "focus_input", label: "Focus prompt", hint: "Ctrl+L" },
  { id: "stop", label: "Stop generation", hint: "Esc" },
  { id: "prompts", label: "Open prompt library", hint: "Ctrl+P" },
  { id: "inspector", label: "Toggle control panel", hint: "Ctrl+I" },
  { id: "workspace", label: "Open project folder", hint: "Ctrl+O" },
  { id: "change_model", label: "Change model", hint: "Ctrl+M" },
  { id: "savings", label: "Show savings", hint: "" },
  { id: "firewall", label: "Cost firewall", hint: "" },
  { id: "settings", label: "Open settings", hint: "" },
  { id: "doctor", label: "Run connection doctor", hint: "" },
  { id: "connect", label: "Connect accounts", hint: "" },
  { id: "shortcuts", label: "Keyboard shortcuts", hint: "?" },
];

let bridge = null;
const state = {
  boot: null, view: "chat", busy: false, pending: null,
  model: { id: "auto", label: "Auto", kind: "auto" },
  mode: { id: "safe-auto", label: "Safe Auto" },
  focus: "general", format: "normal",
  accounts: [], panel: true, message: null, lastFailedRequestId: null,
  responseDensity: "balanced",
  tlNodes: null, activityRenderPending: false, timelineRenders: 0,
  expandedGroups: new Set(), stripColor: "",
  followLatest: true,
  tokenRenderPending: false, tokenRenderTimer: null, tokenRenderFrame: null,
  lastStreamRenderAt: 0, streamRenderedText: "", streamRenders: 0,
  resumePending: false,
  contextHints: [],
  // path -> {name, thumb}. Kept beside contextHints rather than inside it
  // so an image is still an ordinary context reference on the wire: the
  // send path, the pipeline and every provider stay untouched. This map
  // only decides how the chip is drawn.
  attachments: {},
  // Shell-style prompt history for the composer. `index` is -1 when the user
  // is editing their own text; `draft` holds that text so stepping back down
  // past the newest entry restores it instead of losing it.
  history: { index: -1, draft: "" },
};
const providerLoginRequests = new Map();
let doctorRefreshRequestId = null;
let activityDisclosureSequence = 0;
let pendingActivitySnapshot = null;

/* ---------- markdown ---------- */
function mdToHtml(src) {
  return window.OPaiMarkdown.render(src);
}

function userMessageHtml(text) {
  return window.OPaiChatComponents.renderUserMessage(text);
}

function responseProseHtml(markdownHtml) {
  return window.OPaiChatComponents.renderProseRegion(markdownHtml);
}

function responseShellHtml(headerHtml, contentHtml) {
  return window.OPaiChatComponents.renderResponseShell({
    density: state.responseDensity,
    headerHtml,
    contentHtml,
  });
}

function assistantPresentationHtml(headerHtml, text, presentation, options = {}) {
  return window.OPaiChatComponents.renderAssistantPresentation({
    density: state.responseDensity,
    headerHtml,
    proseHtml: responseProseHtml(mdToHtml(text || "")),
    presentation,
    result: options.result,
    changesHtml: options.changesHtml || "",
    supportHtml: options.supportHtml || "",
    warningsHtml: options.warningsHtml || "",
    legacyWorkHtml: options.legacyWorkHtml || "",
    legacyFinalHtml: options.legacyFinalHtml || "",
    prefixHtml: options.prefixHtml || "",
    legacyBeforeHtml: options.legacyBeforeHtml || "",
    extraHtml: options.extraHtml || "",
    retryable: options.retryable === true,
  });
}

function renderStreamingBody(body, text) {
  const selected = captureSelection(body);
  body.classList.add("streaming", "response-prose");
  body.innerHTML = window.OPaiMarkdown.render(text, { streaming: true });
  enhanceCodeBlocks(body);
  restoreSelection(body, selected);
}

function selectableTextNodes(root) {
  const nodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.parentElement && node.parentElement.closest(".code-block-head")
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT;
    },
  });
  while (walker.nextNode()) nodes.push(walker.currentNode);
  return nodes;
}

function captureSelection(root) {
  const selection = window.getSelection && window.getSelection();
  if (!selection || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const nodes = selectableTextNodes(root);
  const offsetOf = (target, offset) => {
    let total = 0;
    for (const node of nodes) {
      if (node === target) return total + Math.min(offset, node.data.length);
      total += node.data.length;
    }
    return null;
  };
  const start = offsetOf(range.startContainer, range.startOffset);
  const end = offsetOf(range.endContainer, range.endOffset);
  return start == null || end == null ? null : { start, end };
}

function restoreSelection(root, snapshot) {
  if (!snapshot) return;
  const nodes = selectableTextNodes(root);
  const total = nodes.reduce((sum, node) => sum + node.data.length, 0);
  if (!nodes.length || snapshot.start > total) return;
  const pointAt = (wanted) => {
    let offset = Math.max(0, Math.min(wanted, total));
    for (const node of nodes) {
      if (offset <= node.data.length) return { node, offset };
      offset -= node.data.length;
    }
    const node = nodes[nodes.length - 1];
    return { node, offset: node.data.length };
  };
  const start = pointAt(snapshot.start);
  const end = pointAt(snapshot.end);
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}
// Every code block gets a copy button (#233). Idempotent so it survives the
// per-frame re-render during streaming and the final render.
function enhanceCodeBlocks(root) {
  root.querySelectorAll(".response-prose pre").forEach((pre) => {
    if (pre.classList.contains("has-copy")) return;
    const code = pre.querySelector("code");
    if (!code) return;
    pre.classList.add("has-copy");
    const head = document.createElement("span");
    head.className = "code-block-head";
    const languageClass = Array.from(code.classList).find((name) => name.startsWith("language-"));
    const language = document.createElement("span");
    language.className = "code-language";
    language.textContent = languageClass ? languageClass.slice("language-".length, 48) : "code";
    language.setAttribute("aria-hidden", "true");
    head.appendChild(language);
    const btn = document.createElement("button");
    btn.className = "code-copy";
    btn.type = "button";
    btn.textContent = "Copy";
    btn.setAttribute("aria-label", "Copy code");
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      copyText(code.textContent || "");
      btn.textContent = "Copied";
      toast("Code copied");
      setTimeout(() => { if (btn.isConnected) btn.textContent = "Copy"; }, 1500);
    });
    head.appendChild(btn);
    pre.insertBefore(head, code);
  });
}

/* ---------- boot ---------- */
// Appearance (#241): density scales spacing via a root class; reduced motion
// overrides the OS media query via a root attribute ("system" removes the
// attribute so the media query governs). Applied at boot and live on change.
function applyResponseDensity(shell, responseDensity) {
  shell.classList.remove("response-density-compact", "response-density-balanced", "response-density-detailed");
  shell.classList.add(`response-density-${responseDensity}`);
  shell.dataset.responseDensity = responseDensity;
  const detailed = responseDensity === "detailed";
  const compact = responseDensity === "compact";
  shell.querySelectorAll(".verification-check").forEach((details) => { details.open = detailed; });
  shell.querySelectorAll(".work-log-group").forEach((details) => { details.open = !compact; });
  shell.querySelectorAll(".wf-history").forEach((details) => { details.open = detailed; });
  shell.querySelectorAll(".changeset-card").forEach((card) => {
    card.querySelectorAll(".diff-file2").forEach((details, index) => {
      details.open = detailed || (!compact && index === 0);
    });
  });
  const timeline = shell.querySelector(".timeline.done");
  const toggle = shell.querySelector(".gen-toggle.done");
  if (timeline) timeline.hidden = !detailed;
  if (toggle) toggle.setAttribute("aria-expanded", detailed ? "true" : "false");
}

function applyAppearance(prefs) {
  const p = prefs || {};
  const root = document.documentElement;
  root.classList.toggle("density-compact", (p.density || "comfortable") === "compact");
  const requestedResponseDensity = p.responseDensity || p.response_density;
  const responseDensity = window.OPaiChatComponents.normalizeResponseDensity(requestedResponseDensity);
  state.responseDensity = responseDensity;
  root.dataset.responseDensity = responseDensity;
  root.classList.toggle("response-density-compact", responseDensity === "compact");
  root.classList.toggle("response-density-detailed", responseDensity === "detailed");
  if (typeof document.querySelectorAll === "function") {
    document.querySelectorAll(".response-shell").forEach((shell) => {
      applyResponseDensity(shell, responseDensity);
    });
  }
  const motion = p.reducedMotion === "on" || p.reducedMotion === "off" ? p.reducedMotion : "system";
  if (motion === "system") delete root.dataset.motion;
  else root.dataset.motion = motion;
  // Activity copy: off only by explicit choice — any other value (including
  // absence, e.g. an older saved prefs blob) keeps the rail selectable.
  root.classList.toggle("activity-select-off", p.activityCopy === "off");
}

// #238: a default changed on the settings Models page must show in the composer
// and inspector immediately (the pref is already persisted by settings.js).
// NOTE: the single implementation lives further below — a duplicate top-level
// declaration here was dead code in the browser (the later declaration wins
// hoisting) and a hard SyntaxError when imported as an ES module in tests.

// Shared dependencies the onboarding tour (#250) needs — it reuses the real
// bridge paths (settings navigation, the model default, the normal send) so it
// can never bypass a gate or drift from the rest of the app.
function onboardingCtx() {
  return {
    boot: state.boot,
    esc,
    bridge,
    currentModel: () => state.model.id,
    pickModel: (id) => { bridge.savePref("default_model", id); applyDefaults("default_model", id); },
    openProviders: () => { switchView("settings"); }, // Providers is the default settings page
    sendPrompt: (text) => {
      switchView("chat");
      setComposerDraft(text);
      send();
    },
    markSeen: () => {
      bridge.savePref("onboarding_seen", "true");
      if (state.boot && state.boot.prefs) state.boot.prefs.onboardingSeen = true;
    },
  };
}

// The payload's selection (model/mode/focus/format) is authoritative — applied
// identically at first boot and after every workspace switch, so the composer,
// inspector, and header can never disagree (F16/F4).
function applyBootSelection(b) {
  state.panel = b.prefs.showPanel !== false;
  state.focus = b.prefs.focus || "general";
  state.format = b.prefs.format || "normal";
  const m = (b.models || []).find((x) => x.id === b.selectedModel) || (b.models || [])[0];
  if (m) state.model = { ...m, advancedLabel: m.advanced_label };
  const md = (b.modes || []).find((x) => x.id === b.prefs.mode) || (b.modes || [])[0];
  if (md) state.mode = md;
}

function boot() {
  bridge.boot((json) => {
    state.boot = JSON.parse(json);
    const b = state.boot;
    state.accounts = b.accounts || [];
    applyBootSelection(b);
    // One-time consent per free-tier model id: after the first "Send to X"
    // click the card never appears again for that provider (persisted per
    // workspace by grantFreeConsent). Fresh install → empty Set.
    state.freeConsent = new Set(b.prefs.freeConsent || []);
    applyAppearance(b.prefs); // #241: density + reduced-motion on the root, live
    applyBrand(b.brand);
    renderSidebar(); renderWorkspace(); renderComposerSelects(); renderComposerContext(); renderInspector();
    renderStatus(b.status); renderAccount(); applyPanel();
    renderEmptyChips();
    wireUpdateSheet();
    wireImageAttachments();
    mountStarfield();
    syncStage();
    renderUpdateBanner(b.update);
    syncBuildMode();
    // Composer Redesign: apply the saved direction (toolbar / single / command).
    if (window.OPaiComposer) window.OPaiComposer.applyBootStyle();
    switchView("chat");
    if (b.initialTask) setComposerDraft(b.initialTask);
    renderResumeChoice();
    // F16: if this workspace requests Full Auto but has no pin, surface the
    // acknowledgement even though no dropdown change event fired.
    // #246: the inspector payload is deferred at boot; fetch it now only if the
    // panel is actually visible. When hidden (the default), togglePanel loads it
    // on first open — so cold boot skips the work entirely.
    if (state.panel) refreshInspector();
    // First-run onboarding (#250): shown once on a fresh profile, never after a
    // resume offer is pending (that takes precedence).
    if (window.OPaiOnboarding && !(b.resume && b.resume.requires_choice)) {
      window.OPaiOnboarding.maybeStart(onboardingCtx());
    }
    // #246: signal cold-start-to-interactive to the (opt-in) startup trace.
    if (bridge.markInteractive) { try { bridge.markInteractive(); } catch (_e) { /* trace is best-effort */ } }
  });
  // #612 AC6: give the message store somewhere durable to report a refused
  // transition. Without this the browser rejected the edge correctly and then
  // forgot it on reload, while Python journalled its half — so only one of the
  // two surfaces could be reconstructed after a restart. Optional-chained so a
  // renderer running against an older bridge simply keeps the in-memory list.
  if (window.OPaiMessageState && bridge.reportIllegalTransition) {
    window.OPaiMessageState.setRefusalSink((from, to) => bridge.reportIllegalTransition(from, to));
  }
  bridge.replyReady.connect(onReply);
  if (bridge.buildReady) bridge.buildReady.connect(onBuildReply);
  bridge.activity.connect(onActivity);
  if (bridge.activityBatch) bridge.activityBatch.connect(onActivityBatch);
  bridge.token.connect(onToken);
  bridge.toolReady.connect(onTool);
  if (bridge.cancelReady) bridge.cancelReady.connect(onCancelReady);
  bridge.workspaceChanged.connect((json) => {
    state.boot = JSON.parse(json);
    rebootFromState();
    toast("Workspace switched");
    // F16: the new workspace may request Full Auto without a pin — the ack
    // must be offered even though no dropdown change event fired.
  });
  if (bridge.modelsChanged) bridge.modelsChanged.connect((json) => {
    const catalog = JSON.parse(json);
    if (catalog.models) { state.boot.models = catalog.models; renderComposerSelects(); }
  });
  if (bridge.providerLoginReady) bridge.providerLoginReady.connect(onProviderLoginReady);
  if (bridge.connectionDoctorReady) bridge.connectionDoctorReady.connect(onConnectionDoctorReady);
  // #146: async data delivery — heavy payloads computed off the GUI thread.
  if (bridge.dashboardReady) bridge.dashboardReady.connect(onDashboardReady);
  if (bridge.settingsReady) bridge.settingsReady.connect(onSettingsReady);
  if (bridge.statusReady) bridge.statusReady.connect(onStatusReady);
  if (bridge.workspaceReady) bridge.workspaceReady.connect(onWorkspaceReady);
  if (bridge.inspectorReady) bridge.inspectorReady.connect(onInspectorReady);
  if (bridge.updateReady) bridge.updateReady.connect((raw) => {
    let update = {};
    try { update = JSON.parse(raw || "{}"); } catch (_e) { return; }
    renderUpdateBanner(update);
  });
  if (bridge.toolApplied) bridge.toolApplied.connect((json) => {
    let d = {}; try { d = JSON.parse(json); } catch (_e) { return; }
    const pending = (state.pendingToolApplies || {})[d.requestId];
    if (!pending) return;
    delete state.pendingToolApplies[d.requestId];
    pending(JSON.stringify(d.data || {}));
  });
  if (bridge.discoverModels) setTimeout(() => bridge.discoverModels(), 0);
  if (bridge.updateStatus) setInterval(() => {
    if (bridge.maintainUpdates) bridge.maintainUpdates();
    bridge.updateStatus((raw) => {
      let update = {};
      try { update = JSON.parse(raw || "{}"); } catch (_e) { return; }
      if (JSON.stringify(update.operation || {}) !== JSON.stringify((state.update || {}).operation || {})) {
        renderUpdateBanner(update);
      }
    });
  }, 2000);
}

// One brand voice, one source: copy comes from opai/brand.py via the boot
// payload, so the GUI, Qt fallback, and CLI never drift apart.
function applyBrand(brand) {
  if (!brand) return;
  state.brand = brand;
  const h1 = $("#empty h1");
  if (h1 && brand.emptyTitle) h1.textContent = brand.emptyTitle;
  if (brand.composerPlaceholder) $("#input").placeholder = brand.composerPlaceholder;
}

// Canonical app-wide updater projection. The backend owns every transition;
// this control only renders state and asks for named actions.
function renderUpdateBanner(update) {
  const shell = $("#updateShell");
  const control = $("#updateBanner");
  const operation = (update && update.operation) || {};
  const policy = (update && update.policy) || {};
  const candidate = operation.candidate || {};
  const status = String(operation.state || "idle");
  if (!shell || !control) return;
  state.update = update || {};
  // One-shot outcome of a developer apply action rides along with the status;
  // it is emitted once by the bridge and never persisted, so toast it here.
  const applyReply = state.update.developer_apply;
  if (applyReply && applyReply.message) toast(String(applyReply.message));
  // A restart that could not be arranged must say so while the window is
  // still open to read it — silence would look like a button that did nothing.
  const restartReply = state.update.restart;
  if (restartReply && restartReply.message) toast(String(restartReply.message));
  const states = {
    available: ["Update available", "A signed OPai update is ready to download.", "accent"],
    downloading: ["Downloading update", "You can keep working while OPai downloads.", "accent"],
    verifying: ["Verifying update", "Checking the artifact digest and publisher identity.", "accent"],
    ready_to_install: ["Ready to restart", "The verified update is staged and ready.", "accent"],
    waiting_for_idle: ["Restart when finished", operation.safe_diagnostic || "Waiting for active work to finish.", "warning"],
    install_on_quit: ["Installs on quit", "The verified update will install after OPai closes safely.", "accent"],
    deferred: ["Update deferred", "The verified update remains available for later.", "neutral"],
    failed_retriable: ["Update paused", operation.safe_diagnostic || "The update can be retried.", "warning"],
    failed_terminal: ["Update blocked", operation.safe_diagnostic || "The update failed a security check.", "danger"],
    policy_blocked: [policy.owner && policy.owner !== "opai" ? "Managed by administrator" : "Updates disabled by policy", "OPai will not race another update owner.", "neutral"],
    unsupported_install: ["Manual update required", operation.safe_diagnostic || "This installation cannot update transactionally.", "neutral"],
    rollback_pending: ["Recovery required", "The new build did not pass startup health checks.", "danger"],
    needs_attention: ["Update needs attention", operation.safe_diagnostic || "Automatic recovery could not complete.", "danger"],
    rolled_back: ["Update rolled back", "OPai restored the last-known-good build.", "warning"],
    unavailable: ["Couldn’t check for updates", operation.safe_diagnostic || "Update status is temporarily unavailable.", "warning"],
  };
  // COMPLETED is normally the quiet end of a packaged update: the app has
  // already restarted into the new build, so a banner would only nag. A
  // source checkout reaches the same state with the restart still pending
  // and a diagnostic that says so — that one has to be seen.
  if (operation.safe_diagnostic) states.completed = ["Update installed", operation.safe_diagnostic, "accent"];
  // CHECKING is silent right up until it starts doing something. A source
  // fast-forward runs inside the check — fetch, merge, reinstall — and the
  // reinstall alone takes seconds with nothing on screen. A progress label
  // is the updater saying it is mid-stage, so show the stage and the bar.
  if (operation.progress_label) states.checking = ["Updating OPai", operation.progress_label, "accent"];
  const visible = Object.prototype.hasOwnProperty.call(states, status);
  shell.hidden = !visible;
  // The update-state event fans out to Settings and other listeners, so it
  // must fire for every state — including hidden ones (up_to_date, idle,
  // checking); otherwise a "Checking…" settings card would never refresh
  // when a check finishes on a hidden state.
  try { window.dispatchEvent(new CustomEvent("opai-update-state", { detail: update })); } catch (_e) { /* old web engine */ }
  if (!visible) {
    $("#updateSheet").hidden = true;
    control.setAttribute("aria-expanded", "false");
    return;
  }
  const config = states[status];
  shell.dataset.state = status;
  shell.dataset.tone = config[2];
  $("#updateBannerText").textContent = config[0];
  $("#updateSheetTitle").textContent = candidate.version
    ? `${config[0]} · OPai ${candidate.version}`
    : config[0];
  $("#updateSheetDescription").textContent = config[1];
  const meta = $("#updateSheetMeta");
  meta.replaceChildren();
  const metadata = [
    ["Version", candidate.version],
    ["Channel", candidate.channel],
    ["Size", candidate.artifact_size ? formatUpdateBytes(candidate.artifact_size) : ""],
    ["Criticality", candidate.criticality],
    ["Publisher", candidate.publisher_identity],
    ["Verification", candidate.verification && candidate.verification.native_mechanism],
  ];
  metadata.forEach(([label, value]) => {
    if (!value) return;
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd"); dd.textContent = String(value).replace(/_/g, " ");
    meta.append(dt, dd);
  });
  const progress = $("#updateProgress");
  const total = Number(operation.total_bytes || 0);
  const downloaded = Number(operation.downloaded_bytes || 0);
  progress.hidden = !["downloading", "verifying"].includes(status) && !operation.progress_label;
  const percent = total > 0 ? Math.max(0, Math.min(100, Math.round(downloaded * 100 / total))) : 0;
  progress.setAttribute("aria-valuemin", "0");
  progress.setAttribute("aria-valuemax", "100");
  if (total > 0) progress.setAttribute("aria-valuenow", String(percent));
  else progress.removeAttribute("aria-valuenow");
  progress.querySelector("span").style.width = `${percent}%`;
  const notes = $("#updateReleaseNotes");
  notes.textContent = candidate.release_notes || "";
  notes.hidden = !candidate.release_notes;
  renderUpdateActions(status, operation);
}

function formatUpdateBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function updateActionButton(label, action, primary) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = primary ? "btn primary" : "btn ghost";
  button.textContent = label;
  button.dataset.updateAction = action;
  button.onclick = () => runUpdateAction(action, button);
  return button;
}

function renderUpdateActions(status, operation) {
  const host = $("#updateSheetActions");
  host.replaceChildren();
  const actions = {
    available: [["Download update", "download", true], ["Later", "later", false]],
    failed_retriable: [["Retry", "retry", true], ["Later", "later", false]],
    ready_to_install: [["Restart now", "install_now", true], ["When finished", "when_idle", false], ["On quit", "on_quit", false], ["Later", "later", false]],
    waiting_for_idle: [["Install on quit", "on_quit", true], ["Later", "later", false]],
    install_on_quit: [["Install now", "install_now", true], ["Later", "later", false]],
    deferred: [[operation.artifact_staged ? "Ready options" : "Resume" , "resume", true]],
    rollback_pending: [["Restore previous version", "rollback", true]],
    needs_attention: operation.rollback_available ? [["Try recovery", "rollback", true]] : [],
    unavailable: [["Check again", "check", true]],
    failed_terminal: [["Check for another release", "check", false]],
    rolled_back: [["Check for updates", "check", false]],
    completed: [["Check again", "check", false]],
  };
  const list = [...(actions[status] || [])];
  // An update that is on disk but not running is not finished. Offer the last
  // step, but only when the app has established it can actually start itself
  // again — a button that closes the window and does not bring it back is
  // worse than no button at all.
  if (status === "completed" && state.update && state.update.restart_available) {
    list.unshift(["Restart now", "restart_now", true]);
  }
  // A source checkout updates by fast-forwarding from origin/main, not by
  // downloading a package: offer the deliberate developer apply instead.
  if (status === "unsupported_install"
    && state.update && state.update.installed
    && state.update.installed.install_type === "source_checkout") {
    list.push(["Update now", "developer_apply", true], ["Check again", "check", false]);
    const apply = state.update.developer_apply;
    if (apply && apply.dirty) {
      // The plain apply refused on uncommitted changes; the explicit second
      // step stashes them and restores them after the fast-forward.
      list.splice(1, 0, ["Update anyway (stash & restore)", "developer_apply_force", false]);
    }
  }
  list.forEach((item) => host.appendChild(updateActionButton(item[0], item[1], item[2])));
}

function runUpdateAction(action, button) {
  if (!bridge || !bridge.updateAction) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  bridge.updateAction(action);
}

function wireUpdateSheet() {
  const control = $("#updateBanner");
  const sheet = $("#updateSheet");
  const close = $("#updateSheetClose");
  if (!control || control.dataset.wired) return;
  control.dataset.wired = "1";
  // The sheet is position:fixed so the sidebar's overflow clip cannot crop it;
  // anchor it just above the persistent control and keep it on-screen.
  const place = () => {
    const rect = control.getBoundingClientRect();
    const width = Math.min(360, window.innerWidth - 28);
    const left = Math.max(14, Math.min(rect.left, window.innerWidth - width - 14));
    sheet.style.left = `${Math.round(left)}px`;
    sheet.style.bottom = `${Math.round(window.innerHeight - rect.top + 8)}px`;
  };
  const hide = () => { sheet.hidden = true; control.setAttribute("aria-expanded", "false"); control.focus({ preventScroll: true }); };
  control.onclick = () => {
    sheet.hidden = !sheet.hidden;
    control.setAttribute("aria-expanded", String(!sheet.hidden));
    if (!sheet.hidden) {
      place();
      // preventScroll: focusing inside the (previously clipped) sheet used to
      // scroll the sidebar sideways and carry the whole rail off-screen.
      close.focus({ preventScroll: true });
    }
  };
  close.onclick = hide;
  sheet.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); hide(); } });
  window.addEventListener("resize", () => { if (!sheet.hidden) place(); });
}

function rebootFromState() {
  state.dashRequest = null;
  state.settingsRequest = null;
  state.statusRequest = null;
  state.workspaceRequest = null;
  state.inspectorRequest = null;
  state.dashPaint = null;
  state.settingsPaint = null;
  const b = state.boot;
  state.accounts = b.accounts || [];
  // F16/F4: re-apply the fresh payload's selection — without this the composer
  // kept the PREVIOUS workspace's mode while the header showed the new one.
  applyBootSelection(b);
  renderSidebar(); renderWorkspace(); renderComposerSelects(); renderComposerContext(); renderInspector();
  renderStatus(b.status); renderAccount(); renderEmptyChips();
  renderUpdateBanner(b.update);
  syncBuildMode();
  clearChat(); switchView("chat"); renderResumeChoice();
  // The inspector payload is deferred like at boot; refresh it for the new
  // workspace when the panel is actually visible.
  if (state.panel) refreshInspector();
}

/* OPai Build in the cockpit (#276): when the workspace is a scaffolded app,
   offer Build mode — a chat message becomes a cheap, verified targeted edit. */
function syncBuildMode() {
  const ws = (state.boot && state.boot.workspace) || {};
  const wasRoot = state.buildAppRoot;
  state.buildApp = !!ws.build_app;
  state.buildAppRoot = state.buildApp ? (ws.root || "") : null;
  // Default to Build mode when entering a build app; leaving one turns it off.
  // A user toggle within the same app is respected (root unchanged).
  if (!state.buildApp) state.buildMode = false;
  else if (state.buildAppRoot !== wasRoot) state.buildMode = true;
  const toggle = $("#buildToggle");
  if (toggle) {
    toggle.toggleAttribute("hidden", !state.buildApp);
    toggle.setAttribute("aria-pressed", String(state.buildMode));
    toggle.classList.toggle("on", state.buildMode);
    toggle.title = state.buildMode
      ? `Build mode: chat edits ${ws.build_app_name || "this app"} with cheap, verified diffs`
      : "Chat mode: ask normally";
  }
  updateSendLabel();
}
function updateSendLabel() {
  const btn = $("#send");
  if (btn && !state.busy) btn.textContent = (state.buildMode && state.buildApp) ? "Build" : "Send";
  updateComposerAvailability();
}
function submitComposer() {
  if (composerBlockReason()) return;
  const text = $("#input").value.trim();
  // A sent prompt starts history over, so the next Up recalls what was just
  // sent rather than resuming a half-finished walk through older entries.
  historyReset();
  // #295: "OPai must not silently ignore a new instruction because an older run
  // is active." Enter used to be dropped on the floor mid-run — the keystroke
  // vanished with no trace, which is the worst outcome for someone correcting
  // or redirecting the work. Hold it instead and send it when the run ends.
  if (state.busy && text) { queueMessage(text); return; }
  if (state.buildMode && state.buildApp && text && !text.startsWith("/")) {
    sendBuild(text);
    return;
  }
  send();
}

/* ---------- queued message (#295, conversational freedom) ----------
   Deliberately a queue and not a second concurrent run: the existing
   single-flight guarantee (one active request, no duplicate submits) is what
   keeps cost and side effects controllable. What changes is that the user's
   words survive. A queued message is visible, editable and removable, and it
   never auto-cancels the active run — inferring "stop" from prose next to a
   real Stop button would be guessing at a destructive action. */
function queueMessage(text) {
  state.queued = String(text || "").trim();
  setComposerDraft("");
  renderQueued();
}

function clearQueued() {
  state.queued = "";
  renderQueued();
}

function renderQueued() {
  const el = $("#composerQueued");
  if (!el) return;
  if (!state.queued) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML =
    `<span class="cq-label">Queued — sends when this finishes:</span>` +
    `<span class="cq-text" title="${esc(state.queued)}">${esc(state.queued)}</span>` +
    `<button type="button" class="cq-edit" data-a="edit">Edit</button>` +
    `<button type="button" class="cq-drop" data-a="drop" aria-label="Remove queued message">Remove</button>`;
  const edit = el.querySelector('[data-a="edit"]');
  if (edit) edit.onclick = () => { const t = state.queued; clearQueued(); setComposerDraft(t); $("#input").focus(); };
  const drop = el.querySelector('[data-a="drop"]');
  if (drop) drop.onclick = () => clearQueued();
}

function flushQueued() {
  if (!state.queued || state.busy) return;
  const text = state.queued;
  state.queued = "";
  renderQueued();
  setComposerDraft(text);
  submitComposer();
}

// Called when a turn ends. An awaiting-input turn is NOT an ending: the run is
// holding for a decision, and sending the queued message there would start a
// fresh run over an approval card the user has not answered — losing both the
// question and the work behind it. The queued text simply stays queued until
// the user resolves the card.
function maybeFlushQueued(result) {
  if (String((result && result.run_state) || "") === "awaiting_input") return;
  flushQueued();
}

/* ---------- sidebar: simple by default ---------- */
// Groups with no name render as plain items (no header noise). Groups marked
// collapsed fold behind one quiet toggle row so a first-time user sees a
// ChatGPT-simple list: Chat, Prompts, Recents — everything else one click away.
function renderSidebar() {
  const nav = $("#nav"); nav.innerHTML = "";
  state.navOpen = state.navOpen || {};
  (state.boot.navGroups || []).forEach((g) => {
    const collapsible = !!g.collapsed && !!g.group;
    let host = nav;
    if (collapsible) {
      const open = state.navOpen[g.group] === true;
      const toggle = document.createElement("button");
      toggle.className = "nav-group-toggle nav-group-btn" + (open ? " open" : "");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.innerHTML = `<span>${esc(g.group)}</span><span class="ngt-chev">${uiIcon(open ? "chevronDown" : "chevronRight")}</span>`;
      nav.appendChild(toggle);
      host = document.createElement("div");
      host.className = "nav-group-body";
      if (!open) host.hidden = true;
      nav.appendChild(host);
      toggle.onclick = () => {
        state.navOpen[g.group] = !(state.navOpen[g.group] === true);
        renderSidebar();
      };
    } else if (g.group) {
      const lab = document.createElement("div");
      lab.className = "nav-group-label"; lab.textContent = g.group;
      nav.appendChild(lab);
    }
    g.items.forEach((it) => {
      const b = document.createElement("button");
      b.className = "nav-item" + (it.id === state.view ? " active" : "");
      b.dataset.id = it.id; b.textContent = it.label;
      b.onclick = () => switchView(it.id);
      host.appendChild(b);
    });
  });
  const lab = document.createElement("div");
  lab.className = "nav-group-label recents-label";
  lab.textContent = "Recent chats";
  nav.appendChild(lab);
  const rec = document.createElement("div"); rec.className = "recents"; rec.id = "recents";
  nav.appendChild(rec);
  renderRecents();
}

function renderRecents() {
  const rec = $("#recents");
  if (!rec) return;
  // Saved *conversations*, not prompt strings. The sidebar called itself
  // "Recent chats" while listing prompts, so selecting one re-typed the
  // question and discarded the answer. The prompt list still exists — it is
  // the composer's Up-arrow history, which is what it was always good for.
  const list = state.boot.conversations || [];
  if (!list.length) {
    renderViewState(rec, {
      kind: "empty",
      title: "No saved chats yet",
      reason: "Start a chat and it will appear here.",
      action: "new_chat",
      actionLabel: "New chat",
      compact: true,
    }, () => startNewChat());
    return;
  }
  rec.innerHTML = "";
  list.forEach((conv) => {
    const title = String(conv.title || "Untitled chat");
    const b = document.createElement("button");
    b.className = "recent";
    b.dataset.conversationId = conv.id;
    const turns = Number(conv.message_count) || 0;
    b.textContent = title.length > 34 ? title.slice(0, 33) + "…" : title;
    b.title = `${title}\n${turns} message${turns === 1 ? "" : "s"}`;
    b.onclick = () => openConversation(conv.id);
    rec.appendChild(b);
  });
  // Privacy control (#145): history is per-workspace and deletable only after
  // an explicit confirmation.
  const clear = document.createElement("button");
  clear.className = "recent";
  clear.id = "clearRecents";
  clear.style.color = "var(--faint)";
  clear.textContent = "Clear history";
  clear.title = "Delete this workspace's previous chat history";
  clear.onclick = () => {
    inlineConfirm(rec, {
      title: "Clear saved chat history?",
      body: "Delete previous saved chats for this workspace? This cannot be undone. Your current chat will be kept.",
      confirmLabel: "Clear history",
      cancelLabel: "Cancel",
      danger: true,
    }).then((confirmed) => {
      if (!confirmed) return;
      if (!bridge || !bridge.clearRecents) {
        showSessionClearFailure(clearFailure("Saved history could not be cleared."));
        return;
      }
      bridge.clearRecents((raw) => {
        const response = parseClearResponse(raw);
        if (!Array.isArray(response) && response.ok === false) {
          showSessionClearFailure(response);
          return;
        }
        applyClearedHistory(response);
      });
    });
  };
  rec.appendChild(clear);
}

function applyClearedHistory(response) {
  if (!state.boot) return;
  state.boot.recents = Array.isArray(response) ? response : (response.recents || []);
  state.boot.conversations = Array.isArray(response) ? [] : (response.conversations || []);
  renderRecents();
}

function stateCardHtml(stateCard) {
  const state = stateCard || {};
  const kind = ["error", "empty", "loading", "degraded"].includes(state.kind) ? state.kind : "empty";
  const icons = { error: "error", empty: "sparkles", loading: "running", degraded: "warning" };
  const role = kind === "error" ? "alert" : "status";
  const title = state.title || (kind === "loading" ? "Loading" : "Nothing to show yet");
  const reason = state.reason || "";
  const action = state.action && state.actionLabel
    ? `<button class="btn ${state.primary ? "primary" : ""}" data-state-action="${esc(state.action)}">${esc(state.actionLabel)}</button>`
    : "";
  return `<section class="state-card ${kind}${state.compact ? " compact" : ""}" role="${role}"${kind !== "error" ? ' aria-live="polite"' : ""}>` +
    `<span class="state-card-icon" aria-hidden="true">${uiIcon(icons[kind])}</span><div class="state-card-copy"><div class="state-card-title">${esc(title)}</div>` +
    (reason ? `<div class="state-card-reason">${esc(reason)}</div>` : "") +
    (action ? `<div class="state-card-actions">${action}</div>` : "") +
    `</div></section>`;
}

function renderViewState(host, stateCard, onAction) {
  host.innerHTML = stateCardHtml(stateCard);
  const action = host.querySelector("[data-state-action]");
  if (action && onAction) action.onclick = onAction;
}

// Bridge failures can originate in local tools and provider adapters. Only an
// explicitly structured, user-facing message is safe to render by default;
// raw exception strings may contain paths, tokens, or implementation details.
function safeStateReason(value, fallback) {
  if (!value || typeof value !== "object" || typeof value.userMessage !== "string") return fallback;
  const message = value.userMessage.trim();
  return message || fallback;
}

function renderAccount() {
  const connected = state.accounts.filter((a) => a.connected);
  const el = $("#acct");
  if (connected.length) {
    el.innerHTML = connected.map((a) => `<span class="d" style="background:var(--green)"></span>${esc(a.label)}`).join(" ") + " connected";
  } else {
    el.innerHTML = `<span class="d" style="background:var(--faint)"></span>No account connected`;
  }
  $("#brandDot").style.background = state.boot.status.on ? "var(--green)" : "var(--amber)";
  $("#brandDot").style.boxShadow = state.boot.status.on ? "0 0 8px var(--green)" : "none";
}

/* ---------- workspace ---------- */
function renderWorkspace() {
  const w = state.boot.workspace;
  $("#wsLabel").textContent = w.label;
  const dirtyCount = (w.dirty_paths || []).length;
  $("#wsContext").textContent = `${w.branch || "Local workspace"}${dirtyCount ? ` · ${dirtyCount} uncommitted` : ""}`;
  // Single owner of the tooltip: workspace facts + the brand tagline together,
  // so a re-render can never drop the tagline (BUG-QA-007).
  const tagline = (state.brand && state.brand.tagline) ? ` — ${state.brand.tagline}` : "";
  $("#wsSwitch").title = `${w.name}${w.branch ? " · " + w.branch : ""}${w.remote ? " · " + w.remote : ""} · ${w.file_count} files indexed${tagline}`;
  const menu = $("#wsMenu"); menu.innerHTML = "";
  const frag = (html) => { const d = document.createElement("div"); d.innerHTML = html; return d.firstElementChild; };
  const label = (t) => menu.appendChild(frag(`<div class="mlabel">${esc(t)}</div>`));
  const sep = () => menu.appendChild(frag(`<div class="sep"></div>`));
  const item = (html, onClick, cls) => {
    const el = frag(`<div class="item ${cls || ""}" role="menuitem" tabindex="0">${html}</div>`);
    el.onclick = () => { closeWsMenu(); onClick(); };
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); } });
    menu.appendChild(el);
  };
  label("Current project");
  item(
    `<span class="m-ico">${uiIcon("folder")}</span><div class="m-body"><div class="m-name">${esc(w.label)}</div><div class="sub">${esc(w.root)}</div></div><span class="m-check">${uiIcon("check")}</span>`,
    () => bridge.openPath(""), "current",
  );
  item(`<span class="m-ico">${uiIcon("folder")}</span><div class="m-body"><div class="m-name">Open another folder…</div></div>`, () => bridge.openWorkspace());
  const recents = w.recents || [];
  if (recents.length) {
    sep();
    label("Recent projects");
    recents.forEach((r) => {
      item(
        `<span class="m-ico">${uiIcon("folder")}</span><div class="m-body"><div class="m-name">${esc(r.label)}</div><div class="sub">${esc(r.path)}</div></div>`,
        () => bridge.switchWorkspace(r.path),
      );
    });
  }
}
function closeWsMenu() {
  $("#wsMenu").classList.remove("open");
  $(".ws-switch-wrap").classList.remove("open");
  $("#wsSwitch").setAttribute("aria-expanded", "false");
}
function toggleWsMenu() {
  const open = !$("#wsMenu").classList.contains("open");
  $("#wsMenu").classList.toggle("open", open);
  $(".ws-switch-wrap").classList.toggle("open", open);
  $("#wsSwitch").setAttribute("aria-expanded", open ? "true" : "false");
}

/* ---------- composer selects ---------- */
function renderComposerSelects() {
  const modeSel = $("#modeSel"); modeSel.innerHTML = "";
  (state.boot.modes || []).forEach((m) => {
    const o = document.createElement("option"); o.value = m.id; o.textContent = m.label;
    if (m.id === state.mode.id) o.selected = true; modeSel.appendChild(o);
  });
  modeSel.onchange = () => {
    // A chosen mode is the mode, and it is durable. Full Auto used to be the
    // exception: picking it opened an acknowledgement modal and pinned via a
    // dedicated bridge slot, because a plain savePref was downgraded server
    // side. The downgrade is gone, so a mode now persists by being picked —
    // across restarts, reboots and workspace switches — like any other setting.
    state.mode = state.boot.modes.find((m) => m.id === modeSel.value) || state.mode;
    bridge.savePref("default_mode", state.mode.id);
    // Keep the local autonomy snapshot coherent: an explicit non-Full-Auto
    // choice becomes the requested mode for this workspace, so the pin ack is
    // not re-offered for a mode the user just deliberately left.
    if (state.boot.autonomy) {
      state.boot.autonomy.requested_mode = state.mode.id;
      state.boot.autonomy.effective_mode = state.mode.id;
    }
    // The run mode is a local, explicit user selection. Paint it in the header
    // immediately, then let the asynchronous status refresh fill in its
    // independently computed spend and savings values. This avoids showing the
    // previous (potentially more permissive) mode while that refresh is in flight.
    renderStatus({ line: $("#statusLine").textContent });
    renderComposerContext(); refreshInspector(); refreshStatus();
  };
  const modelSel = $("#modelSel"); modelSel.innerHTML = "";
  // Group models by their group field into optgroup sections
  const PICKER_GROUPS = [
    { id: "claude",  label: "Claude" },
    { id: "codex",   label: "Codex" },
    { id: "copilot", label: "Copilot" },
    { id: "free",    label: "Free models" },
    { id: "routing", label: "OPai routing" },
    { id: "local",   label: "Local models" },
  ];
  const allModels = state.boot.models || [];
  // Out-of-credit models are removed from selection entirely (the redesigned
  // picker popover shows the explanation). If the current selection just ran
  // out of credit, fall back to Auto — never leave a dead model selected.
  const selectable = allModels.filter((m) => !m.out_of_credit);
  const currentEntry = allModels.find((m) => m.id === state.model.id);
  if (currentEntry && currentEntry.out_of_credit) {
    state.model = { id: "auto", label: "OPai · Auto mode", kind: "auto", provider: "" };
    bridge.savePref("default_model", "auto");
  }
  const grouped = {};
  selectable.forEach((m) => {
    const g = m.group || "routing";
    if (!grouped[g]) grouped[g] = [];
    grouped[g].push(m);
  });
  PICKER_GROUPS.forEach(({ id: gid, label: glabel }) => {
    const members = grouped[gid];
    if (!members || members.length === 0) return;
    const grp = document.createElement("optgroup");
    grp.label = glabel;
    members.forEach((m) => {
      const o = document.createElement("option"); o.value = m.id; o.textContent = m.label; o.title = m.advanced_label || m.badge || "";
      // Unavailable models stay visible but unpickable, with the reason (BUG-QA-008).
      if (m.available === false) { o.disabled = true; o.title = m.disabled_reason || "Not available"; }
      if (m.id === state.model.id) o.selected = true;
      grp.appendChild(o);
    });
    modelSel.appendChild(grp);
  });
  // Append any models with unknown groups directly (backward compat)
  const knownGroups = new Set(PICKER_GROUPS.map((g) => g.id));
  selectable.filter((m) => m.group && !knownGroups.has(m.group)).forEach((m) => {
    const o = document.createElement("option"); o.value = m.id; o.textContent = m.label; o.title = m.advanced_label || m.badge || "";
    if (m.available === false) { o.disabled = true; o.title = m.disabled_reason || "Not available"; }
    if (m.id === state.model.id) o.selected = true; modelSel.appendChild(o);
  });
  modelSel.onchange = () => {
    const m = state.boot.models.find((x) => x.id === modelSel.value);
    if (m) state.model = { ...m, advancedLabel: m.advanced_label };
    setProviderDot(); renderComposerContext(); bridge.savePref("default_model", state.model.id); refreshInspector(); refreshStatus();
  };
  setProviderDot();
  renderComposerContext();
}

function autonomyConsequence(modeId) {
  return {
    ask: "Answers without changes",
    plan: "Plans without changes",
    "safe-auto": "Asks before edits",
    "approve-edits": "Asks before everything",
    "auto-edits": "Edits apply; commands ask",
    "full-auto": "Edits and runs commands",
  }[modeId] || "Uses your selected autonomy";
}

function costPosture() {
  if (state.model.kind === "local" || state.model.kind === "free") return "No provider spend";
  if (state.model.kind === "auto") return "Routes local first";
  return "May spend within your limits";
}

function selectedAccountNeedsConnection() {
  if (state.model.kind !== "account") return false;
  const account = (state.accounts || []).find((item) => item.id === state.model.provider);
  return !account || !(account.connected || account.authenticated);
}

// An empty box blocks sending, but it is not worth a sentence: the disabled
// Send button already says so, and the sentence sat under the composer
// permanently, before the user had done anything wrong. So this reason still
// *blocks*, it just does not *speak* -- the two were conflated, and collapsing
// them re-enabled Send on an empty prompt.
const EMPTY_PROMPT_REASON = "empty-prompt";

/* ---------- stages ----------
 *
 * A fresh chat opens in the `dark` stage: the room unlit and the composer in
 * the middle of it, because before the first message the composer is the only
 * thing on the screen worth putting in the middle. The first send raises the
 * lights and flies the composer down to the position it normally occupies,
 * and the request goes out when it lands.
 *
 * The composer is moved with a transform rather than by changing the layout.
 * That matters twice: a transform is a compositor animation, so it holds its
 * frame rate while the main thread is busy setting up a request; and the
 * position it lands in is the position it already had, so nothing can drift
 * out of alignment as the animation ends.
 */
let starfield = null;

// Mounted once. The stage owns whether the sky is *visible*; this owns whether
// it is *running*, so a lit room costs nothing rather than merely hiding an
// animation that is still burning frames.
function mountStarfield() {
  if (starfield || !global0().OPaiStarfield) return;
  starfield = global0().OPaiStarfield.mount(document.getElementById("starfall"));
}

function global0() { return window; }

function stageRoot() { return document.getElementById("app"); }

window.addEventListener("resize", () => {
  if (currentStage() === "dark") measureStageLift();
});

function currentStage() {
  const root = stageRoot();
  return (root && root.dataset.stage) || "lit";
}

// How far the composer has to rise to sit in the middle of the room. Measured
// rather than guessed: it depends entirely on the window height, and it is
// re-measured on resize because a window resized while dark would otherwise
// animate from a stale position.
function measureStageLift() {
  const root = stageRoot();
  const wrap = document.querySelector(".composer-wrap");
  const main = document.querySelector(".main");
  if (!root || !wrap || !main) return;
  const emptyEl = document.querySelector("#empty");
  const previous = wrap.style.transition;
  const previousEmpty = emptyEl ? emptyEl.style.transition : "";
  wrap.style.transition = "none";
  if (emptyEl) emptyEl.style.transition = "none";
  const wrapBox = wrap.getBoundingClientRect();
  const mainBox = main.getBoundingClientRect();
  // Centre the *group* -- mascot, headline, chips and box together -- not the
  // composer alone.
  //
  // Centring only the composer left everything sitting high: the empty block
  // is its own full-height flex box that centres its children in the region
  // above, so the two were each centred on different things and the group as
  // a whole was not centred on anything.
  //
  // Centring it there and lifting the headline to clear it was the obvious
  // approach and the wrong one. `.empty` is a full-height flex box that
  // centres its own children, so translating it moves its top edge above the
  // scroll region, which is clipped -- the mascot lost its head on a short
  // window and kept losing it as the window grew, because the lift scales
  // with height and the content does not.
  //
  // Measuring the content instead means the two can never overlap and nothing
  // can leave the frame: whatever the empty block turns out to be, the box
  // goes below it.
  const empty = document.querySelector("#empty");
  const children = empty ? Array.from(empty.children).filter((el) => el.offsetParent !== null) : [];
  if (!children.length) return;
  // getBoundingClientRect includes any transform already applied, so both
  // measurements are taken back to their untransformed positions first --
  // otherwise every re-measure would compound the previous one.
  const emptyApplied = currentStage() === "dark" ? stageValue(root, "--stage-empty-lift") : 0;
  const wrapApplied = currentStage() === "dark" ? stageValue(root, "--stage-lift") : 0;
  const contentTop = Math.min(...children.map((el) => el.getBoundingClientRect().top)) - emptyApplied;
  const contentBottom = Math.max(...children.map((el) => el.getBoundingClientRect().bottom)) - emptyApplied;
  const contentHeight = contentBottom - contentTop;

  const GAP = 26;
  const groupHeight = contentHeight + GAP + wrapBox.height;
  // Never push the group above the top of the room: on a window too short to
  // hold it, it pins to the top and the box simply sits lower.
  const groupTop = Math.max(mainBox.top + 12, mainBox.top + (mainBox.height - groupHeight) / 2);

  root.style.setProperty("--stage-empty-lift", `${Math.round(groupTop - contentTop)}px`);
  const composerTop = groupTop + contentHeight + GAP;
  root.style.setProperty(
    "--stage-lift",
    `${Math.min(0, Math.round(composerTop - (wrapBox.top - wrapApplied)))}px`,
  );
  wrap.style.transition = previous;
  if (emptyEl) emptyEl.style.transition = previousEmpty;
}

function stageValue(root, name) {
  const parsed = parseFloat(getComputedStyle(root).getPropertyValue(name).trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function setStage(stage) {
  const root = stageRoot();
  if (!root || root.dataset.stage === stage) return;
  if (stage === "dark") measureStageLift();
  root.dataset.stage = stage;
  if (stage === "dark") requestAnimationFrame(measureStageLift);
  if (starfield) starfield.setActive(stage === "dark");
}

// A fresh chat is dark; anything with a message in it is lit. Called wherever
// the thread is emptied or restored so the stage can never disagree with what
// is on screen.
function syncStage() {
  const thread = document.getElementById("thread");
  const empty = thread && thread.querySelector("#empty");
  setStage(empty && !thread.querySelector(".msg") ? "dark" : "lit");
}

/**
 * Raise the lights, and run `resume` once the composer has landed.
 *
 * Returns true when it took over -- the caller must stop and let `resume`
 * continue the work. Returns false when the room is already lit, which is
 * every message after the first, so the common path pays nothing.
 */
function raiseTheLights(resume) {
  if (currentStage() !== "dark") return false;
  const wrap = document.querySelector(".composer-wrap");
  setStage("lit");
  if (!wrap || matchMedia("(prefers-reduced-motion: reduce)").matches) {
    resume();
    return true;
  }
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    wrap.removeEventListener("transitionend", onEnd);
    resume();
  };
  // transitionend bubbles, so this fires for descendants too -- and the Send
  // button inside the composer has its own transform transition. Without the
  // target check, the button's animation ended the wait after about 80ms and
  // the request went out while the composer was still halfway down the room.
  const onEnd = (event) => {
    if (event.target === wrap && event.propertyName === "transform") finish();
  };
  wrap.addEventListener("transitionend", onEnd);
  // transitionend does not fire if the transition never starts -- a zero
  // lift, a hidden window, a browser that drops the frame. The request must
  // go out regardless, so the timer is the floor, not the mechanism.
  setTimeout(finish, 900);
  return true;
}

function composerBlockReason() {
  if (state.resumePending) return "Choose how to continue this saved session before sending.";
  if (selectedAccountNeedsConnection()) {
    return `Connect ${state.model.provider ? providerName(state.model.provider) : "this provider"} before sending.`;
  }
  if (!$("#input").value.trim()) return EMPTY_PROMPT_REASON;
  return "";
}

function renderComposerContext() {
  const root = $("#composerContext");
  if (!root || !state.boot) return;
  const modeLabel = modePresentationLabel(state.mode);
  const modelLabel = state.model.kind === "auto" ? "OPai · Auto mode" : (state.model.label || "Selected model");
  // These legacy pills now live in the visually-hidden .composer-native block
  // (the redesigned toolbar summarises the same state). tabindex="-1" keeps them
  // out of the tab order so their aria-hidden container has no focusable content.
  root.innerHTML = [
    `<button class="context-chip" type="button" tabindex="-1" data-composer-focus="mode" aria-label="Mode: ${esc(modeLabel)}">Mode · ${esc(modeLabel)}</button>`,
    `<button class="context-chip" type="button" tabindex="-1" data-composer-focus="mode" aria-label="Autonomy: ${esc(autonomyConsequence(state.mode.id))}">${esc(autonomyConsequence(state.mode.id))}</button>`,
    `<button class="context-chip" type="button" tabindex="-1" data-composer-focus="model" aria-label="Model: ${esc(modelLabel)}">${esc(modelLabel)}</button>`,
    `<button class="context-chip" type="button" tabindex="-1" data-composer-focus="cost" aria-label="Cost posture: ${esc(costPosture())}">${esc(costPosture())}</button>`,
  ].join("");
  root.querySelectorAll("[data-composer-focus]").forEach((button) => {
    button.onclick = () => {
      const target = button.dataset.composerFocus;
      if (target === "mode") $("#modeSel").focus();
      else if (target === "model") $("#modelSel").focus();
      else {
        try { window.history.replaceState(null, "", "#settings/firewall"); } catch (_e) { /* best-effort deep link */ }
        switchView("settings");
      }
    };
  });
  renderContextHints();
  updateComposerAvailability();
}

function updateComposerAvailability() {
  const send = $("#send"), reason = $("#composerReason");
  if (!send || !reason) return;
  // Keep the redesigned composer (labels, summary, status) in sync on every
  // availability change (typing, mode/model change, send lifecycle).
  if (window.OPaiComposer) window.OPaiComposer.refresh();
  const blocked = composerBlockReason();
  if (state.busy) { send.disabled = false; reason.innerHTML = ""; delete reason.dataset.tone; return; }
  send.disabled = Boolean(blocked);
  send.setAttribute("aria-label", (state.buildMode && state.buildApp) ? "Start build" : "Send prompt");
  if (!blocked) { reason.innerHTML = ""; delete reason.dataset.tone; send.removeAttribute("aria-describedby"); return; }
  if (blocked === EMPTY_PROMPT_REASON) {
    reason.innerHTML = "";
    delete reason.dataset.tone;
    send.removeAttribute("aria-describedby");
    return;
  }
  reason.dataset.tone = "warning";
  const action = !state.resumePending && selectedAccountNeedsConnection()
    ? ' <button class="reason-action" type="button">Open Settings</button>'
    : "";
  reason.innerHTML = `${esc(blocked)}${action}`;
  send.setAttribute("aria-describedby", "composerReason");
  const open = reason.querySelector(".reason-action");
  if (open) open.onclick = () => switchView("settings");
}

function normalizeContextHint(value) {
  const path = String(value || "").trim().replaceAll("\\", "/").replace(/^@+/, "");
  if (!path || path.startsWith("/") || path.includes("..") || path.length > 240) return "";
  if (/^(?:[a-z]:|\/\/|[a-z][a-z0-9+.-]*:)/i.test(path)) return "";
  return path;
}

function addContextHint(value) {
  const path = normalizeContextHint(value);
  if (!path || state.contextHints.includes(path)) return;
  state.contextHints.push(path);
  renderContextHints();
}

function renderContextHints() {
  const root = $("#contextHints");
  if (!root) return;
  root.innerHTML = state.contextHints.map((path, index) => {
    const image = state.attachments[path];
    const remove = `<button class="context-remove" type="button" aria-label="Remove ${esc(image ? image.name : path)}" data-context-index="${index}">${uiIcon("close")}</button>`;
    if (!image) {
      return `<span class="context-hint">@${esc(path)}${remove}</span>`;
    }
    // The preview is the bytes already in hand from the paste — no second
    // read, and nothing to load from disk.
    return (
      `<span class="context-hint context-image" title="${esc(path)}">` +
      `<img class="context-thumb" src="${esc(image.thumb)}" alt="" />` +
      `<span class="context-image-name">${esc(image.name)}</span>${remove}</span>`
    );
  }).join("");
  root.querySelectorAll("[data-context-index]").forEach((button) => {
    button.onclick = () => {
      const [path] = state.contextHints.splice(Number(button.dataset.contextIndex), 1);
      delete state.attachments[path];
      renderContextHints();
    };
  });
}

// Everything that can carry an image into the composer funnels through here:
// a clipboard paste, a drop from the file manager, and the native picker. They
// differ only in where the bytes come from; past this point there is one path,
// so a fix or a limit can never apply to some of them and not the others.
const IMAGE_ATTACH_LIMIT = 8;

function attachImageBlob(file) {
  if (!file || !bridge || !bridge.attachImage) return Promise.resolve(false);
  if (state.contextHints.length >= IMAGE_ATTACH_LIMIT + 24) return Promise.resolve(false);
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => { toast("That image could not be read."); resolve(false); };
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      bridge.attachImage(dataUrl, file.name || "", (raw) => {
        let result = {};
        try { result = JSON.parse(raw || "{}"); } catch (_e) { result = {}; }
        if (!result.ok || !result.path) {
          // The host decides what is and is not an image; it also writes the
          // refusal, so the user reads one explanation rather than two.
          toast(result.error || "That image could not be attached.");
          resolve(false);
          return;
        }
        state.attachments[result.path] = { name: result.name || "Image", thumb: dataUrl };
        addContextHint(result.path);
        resolve(true);
      });
    };
    reader.readAsDataURL(file);
  });
}

function imagesFromTransfer(transfer) {
  if (!transfer) return [];
  const out = [];
  // `items` is where a clipboard screenshot lives (it has no entry in
  // `files` in every browser); `files` is where a dragged or copied file
  // lives. Reading both, de-duplicated, is what makes paste and drop behave
  // the same way for the same picture.
  const items = transfer.items ? Array.from(transfer.items) : [];
  items.forEach((item) => {
    if (item.kind === "file" && String(item.type || "").startsWith("image/")) {
      const file = item.getAsFile();
      if (file) out.push(file);
    }
  });
  const files = transfer.files ? Array.from(transfer.files) : [];
  files.forEach((file) => {
    if (!String(file.type || "").startsWith("image/")) return;
    if (out.some((seen) => seen.name === file.name && seen.size === file.size)) return;
    out.push(file);
  });
  return out.slice(0, IMAGE_ATTACH_LIMIT);
}

async function attachImagesFrom(transfer) {
  const images = imagesFromTransfer(transfer);
  if (!images.length) return false;
  for (const file of images) await attachImageBlob(file);
  return true;
}

function wireImageAttachments() {
  const input = $("#input");
  if (!input || input.dataset.imagesWired) return;
  input.dataset.imagesWired = "1";
  input.addEventListener("paste", (event) => {
    // Only claim the paste when it actually carries an image. Text pasted
    // alongside one must still land in the box, so preventDefault is not a
    // blanket -- it applies to the image case only.
    if (!imagesFromTransfer(event.clipboardData).length) return;
    event.preventDefault();
    attachImagesFrom(event.clipboardData);
  });
  const dropZone = $("#composer") || input;
  ["dragenter", "dragover"].forEach((name) => {
    dropZone.addEventListener(name, (event) => {
      if (!Array.from((event.dataTransfer || {}).types || []).includes("Files")) return;
      event.preventDefault();
      dropZone.classList.add("drop-target");
    });
  });
  ["dragleave", "dragend"].forEach((name) => {
    dropZone.addEventListener(name, () => dropZone.classList.remove("drop-target"));
  });
  dropZone.addEventListener("drop", (event) => {
    if (!imagesFromTransfer(event.dataTransfer).length) return;
    event.preventDefault();
    dropZone.classList.remove("drop-target");
    attachImagesFrom(event.dataTransfer);
  });
}

function setProviderDot() {
  const k = state.model.provider || state.model.kind || "auto";
  $("#providerDot").style.background = PROVIDER_COLOR[k] || "var(--muted)";
}

/* ---------- inspector ---------- */
function selPayload() {
  return {
    model_label: state.model.label, model_advanced_label: state.model.advancedLabel,
    model_kind: state.model.kind,
    mode: state.mode.id, mode_label: state.mode.label,
    focus: state.focus, format: state.format, accounts: state.accounts,
  };
}
function refreshInspector() {
  if (bridge.requestInspector && bridge.inspectorReady) {
    state.inspectorRequest = `inspector-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    bridge.requestInspector(JSON.stringify(selPayload()), state.inspectorRequest);
  } else {
    bridge.inspector(JSON.stringify(selPayload()), (json) => renderInspector(JSON.parse(json)));
  }
}
function onInspectorReady(json) {
  let d = {}; try { d = JSON.parse(json); } catch (_e) { return; }
  if (d.requestId !== state.inspectorRequest) return;
  renderInspector(d.data || {});
}
function refreshStatus() {
  // #146: prefer the async path — the (cached) overview read runs on a worker
  // thread so the one post-turn recompute never stalls the window. Only the
  // latest request's result is applied (coalescing rapid refreshes).
  if (bridge.requestStatus && bridge.statusReady) {
    state.statusRequest = `status-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    bridge.requestStatus(JSON.stringify(selPayload()), state.statusRequest);
  } else {
    bridge.statusLine(JSON.stringify(selPayload()), (json) => renderStatus(JSON.parse(json)));
  }
}
function onStatusReady(json) {
  let d = {}; try { d = JSON.parse(json); } catch (_e) { return; }
  if (d.requestId !== state.statusRequest) return; // stale — a newer refresh won
  renderStatus(d.data || {});
}

// F21: the backend "Agent mode" inspector row only refreshes from persisted
// state after a run completes. Derive the pending agent mode from the CURRENT
// controls (run mode caps what focus can do — a read-only run mode stays
// read-only no matter the focus) so the inspector can show what the NEXT run
// will do instead of a stale value.
const IMPLEMENT_FOCI = ["build", "debug", "refactor", "test", "implement"];
function derivedAgentMode() {
  if (state.mode.id === "ask") return "Explain";
  if (state.mode.id === "plan") return "Plan";
  const focus = String(state.focus || "").toLowerCase();
  if (IMPLEMENT_FOCI.indexOf(focus) !== -1) return "Implement";
  if (focus === "review") return "Review";
  if (focus === "plan") return "Plan";
  if (focus === "explain") return "Explain";
  return ""; // no honest derivation — keep whatever the backend reported
}

function renderInspector(data) {
  // #246: the inspector payload is deferred at boot (null) and fetched on demand
  // when the panel is shown, so render an empty shell until it arrives.
  data = data || state.boot.inspector || {};
  const ins = $("#inspector");
  const focusOpts = (state.boot.taskModes || []).map((m) => `<option value="${m.id}"${m.id === state.focus ? " selected" : ""}>${esc(m.label)}</option>`).join("");
  const fmtOpts = (state.boot.outputFormats || []).map((f) => `<option value="${f.id}"${f.id === state.format ? " selected" : ""}>${esc(f.label)}</option>`).join("");
  // F21: when the derived next-run mode differs from the persisted one, show
  // the derivation — honestly labelled "(next run)" — until a completed run
  // delivers the authoritative value via onReply → refreshInspector.
  const preview = derivedAgentMode();
  let sawAgentRow = false;
  const rowData = (data.rows || []).map((r) => {
    if (r.label === "Run mode") return { label: r.label, value: modePresentationLabel(state.mode) };
    if (r.label !== "Agent mode") return r;
    sawAgentRow = true;
    return (preview && r.value !== preview) ? { label: r.label, value: preview + " (next run)" } : r;
  });
  if (preview && !sawAgentRow) rowData.push({ label: "Agent mode", value: preview + " (next run)" });
  const rows = rowData.map((r) => `<div class="insp-row"><span class="k">${esc(r.label)}</span><span class="v">${esc(r.value)}</span></div>`).join("");
  const perms = (data.permissions || []).map((p) => `<div class="perm"><span class="k">${esc(p.label)}</span><span class="s ${p.state}" title="${esc(p.note || "")}">${esc(p.state)}</span></div>`).join("");
  const badges = (data.privacy || []).map((b) => `<div class="badge ${b.tone}">${esc(b.label)}</div>`).join("");
  const bud = data.budget || { pct: 0, text: "" };
  // Essentials first (what's running, what it costs); every tuning control
  // sits below one quiet Advanced divider so the panel reads in two seconds.
  ins.innerHTML = `
    <div class="insp-title">Session</div>
    <div class="insp-live" id="inspLive" aria-live="polite" hidden>
      <div class="il-status"><span class="il-dot"></span><span id="inspLiveStep">Working…</span></div>
      <div class="il-meta"><span id="inspLiveElapsed">00:00</span><span id="inspLiveEvents"></span></div>
    </div>
    <div class="insp-rows" style="margin-top:14px">${rows}</div>
    <div class="insp-label">Budget</div>
    <div class="meter"><i style="width:${Math.max(2, bud.pct)}%"></i></div>
    <div class="meter-text">${esc(bud.text)}</div>
    <div class="insp-divider"><span>Advanced</span></div>
    <div class="insp-label">Task focus</div>
    <select class="select" id="focusSel" style="width:100%">${focusOpts}</select>
    <div class="insp-label">Output format</div>
    <select class="select" id="fmtSel" style="width:100%">${fmtOpts}</select>
    <div class="insp-label">Permissions</div>
    ${perms}
    <div class="insp-label">Privacy</div>
    <div class="badges">${badges}</div>
    <div class="insp-label">CLI mirror</div>
    <button class="cli-mirror" id="cliMirror" title="Copy the terminal twin of this selection">
      <code id="cliMirrorCmd"></code><span class="cm-copy">Copy</span>
    </button>`;
  $("#focusSel").onchange = (e) => { state.focus = e.target.value; bridge.savePref("default_task_mode", state.focus); refreshInspector(); };
  $("#fmtSel").onchange = (e) => { state.format = e.target.value; bridge.savePref("default_output_format", state.format); refreshInspector(); };
  updateCliMirror();
  $("#cliMirror").onclick = () => {
    copyText($("#cliMirrorCmd").textContent);
    toast("Copied — same run, from your terminal");
  };
  updateInspectorLive();
}

// GUI/CLI parity is a brand promise: everything the app does has a terminal
// twin. The mirror shows the current selection as a ready-to-copy command.
// Write-only copy path (#149): prefer the bridge (system clipboard via Qt) so
// the page needs no clipboard permission at all and can never read it back.
function copyText(text) {
  if (bridge.copyText) { bridge.copyText(String(text || "")); return; }
  if (navigator.clipboard) navigator.clipboard.writeText(String(text || "")).catch(() => {});
}

function updateCliMirror() {
  const el = $("#cliMirrorCmd");
  if (!el) return;
  const task = (state.lastSend && state.lastSend.text) || "";
  el.textContent = OPaiActivity.cliMirror(state.model.id, state.mode.id, task);
}

// Live generation block in the inspector (issue #110): current step, elapsed,
// event count — shown only while a request is active.
function updateInspectorLive(stepText) {
  const live = $("#inspLive");
  if (!live) return;
  if (!state.busy) { live.setAttribute("hidden", ""); return; }
  live.removeAttribute("hidden");
  if (stepText) $("#inspLiveStep").textContent = stepText;
  $("#inspLiveElapsed").textContent = OPaiActivity.formatElapsed(Date.now() - state.startTime);
  const n = state.store ? state.store.events.length : 0;
  $("#inspLiveEvents").textContent = n ? n + " step" + (n === 1 ? "" : "s") : "";
}

function renderStatus(st) {
  if (!st) return;
  const segments = String(st.line || "").split(" · ");
  // Mode is selected locally, while the rest of this line (provider, spend,
  // savings) is supplied by the backend. Keep the only immediately knowable
  // value authoritative even if a queued status response was generated before
  // the user changed modes.
  if (segments.length >= 2 && state.mode) segments[1] = modePresentationLabel(state.mode);
  $("#statusLine").innerHTML = esc(segments.join(" · ")).replace(/^([^·]+)/, "<b>$1</b>");
}

/* ---------- views ---------- */
function switchView(id) {
  state.view = id;
  closeMobileSidebar();
  // If the destination lives inside a folded group, unfold it so the active
  // item is visible (e.g. jumping to an Insights page from the palette).
  const navBtn = $(`.nav-item[data-id="${id}"]`);
  if (navBtn) {
    const body = navBtn.closest(".nav-group-body");
    if (body && body.hidden) {
      const group = body.previousElementSibling;
      const name = group && group.querySelector("span") && group.querySelector("span").textContent;
      if (name) { state.navOpen = state.navOpen || {}; state.navOpen[name] = true; renderSidebar(); }
    }
  }
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
  const map = { chat: "view-chat", prompts: "view-prompts", settings: "view-settings" };
  let target = map[id] || "view-dashboard";
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === target));
  if (target === "view-dashboard") renderDashboard(id);
  else if (id === "prompts") loadPrompts();
  else if (id === "settings") renderSettings();
  else $("#input").focus();
}

/* ---------- chat ---------- */
function renderEmptyChips() {
  // Agent-grade starters that show what OPai really does (plan, gate, receipt)
  // without promising anything the engine doesn't deliver.
  const chips = [
    ["Summarize my changes", "Summarize my uncommitted changes"],
    ["Explain this repo", "Give me a high-level tour of this codebase"],
    ["Plan a safe refactor", "Plan a safe refactor of this code: concrete steps, risks, and the tests to run. Don't edit files yet."],
  ];
  const connected = state.accounts.some((a) => a.connected);
  // The line under the headline earns its place only when it changes what the
  // reader does next. Describing the product to someone already looking at it
  // does not; telling them nothing will run until a provider is connected
  // does. So it is silent in the normal case and absent from the layout.
  const sub = $("#emptySub");
  sub.textContent = connected
    ? ""
    : "Connect your Claude, Codex, or Copilot account in Settings, then just type.";
  sub.hidden = connected;
  $("#chips").innerHTML = chips.map((c) => `<button class="chip" data-p="${esc(c[1])}">${esc(c[0])}</button>`).join("");
  $$("#chips .chip").forEach((b) => (b.onclick = () => { setComposerDraft(b.dataset.p); send(); }));
}
function clearChat() {
  const t = $("#thread");
  t.querySelectorAll(".msg").forEach((m) => m.remove());
  $("#empty").style.display = "";
  state.followLatest = true;
  state.tlNodes = null;
  $("#chatScroll").scrollTop = 0;
  updateJumpLatest();
  // Back to an empty thread means back to an unlit room.
  syncStage();
}
function setResumeGate(on) {
  state.resumePending = !!on;
  const input = $("#input"), buildToggle = $("#buildToggle");
  if (input) input.disabled = !!on;
  if (buildToggle) buildToggle.disabled = !!on;
  updateComposerAvailability();
}
function clearFailure(message) {
  return {
    ok: false,
    error: {
      code: "SESSION_CLEAR_FAILED",
      userMessage: message,
      recoveryActions: ["Try again after closing other OPai windows for this workspace."],
    },
  };
}
function parseClearResponse(raw) {
  try { return JSON.parse(raw || "{}"); }
  catch (_e) { return clearFailure("OPai could not confirm that the saved work was cleared."); }
}
// #416: drop the "Resume your previous work?" choice card from the DOM right now,
// on click — the dismissal must not wait for the async session bridge to answer,
// or a slow/contended clear leaves an interactive card the user clicks twice.
function dismissResumeChoice() {
  document.querySelectorAll(".msg.resume-choice").forEach((el) => el.remove());
}
function showSessionClearFailure(response) {
  if (response && response.resume) state.boot.resume = response.resume;
  const error = (response && response.error) || {};
  const action = (error.recoveryActions || [])[0] || "Try again.";
  const message = `${error.userMessage || "Saved work could not be cleared."} ${action}`;
  // The choice was dismissed synchronously on click (#416); the clear failed, so
  // bring it back rather than stranding the user with saved work they can't reach.
  let card = document.querySelector(".resume-card");
  if (!card && state.boot && state.boot.resume && state.boot.resume.requires_choice) {
    renderResumeChoice();
    card = document.querySelector(".resume-card");
  }
  let alert = card && card.querySelector("[data-clear-error]");
  if (card && !alert) {
    alert = document.createElement("div");
    alert.className = "rc-error";
    alert.setAttribute("role", "alert");
    alert.setAttribute("data-clear-error", "");
    card.appendChild(alert);
  }
  if (alert) {
    alert.textContent = message;
    setResumeGate(true);
  } else {
    appendMsg(
      roleHeader("OPai", "var(--red)") + `<div class="body" role="alert">${esc(message)}</div>`,
      "bot",
    );
    setResumeGate(false);
  }
}
function resumeSummaryHtml(resume) {
  const flow = resume.workflow || {}, checkpoint = resume.checkpoint || {};
  const plan = ((resume.thread || {}).plan || []).map((item) => item.step).filter(Boolean);
  const steps = plan.length ? plan : (flow.plan_steps || []);
  const changed = (resume.thread && resume.thread.changed_files) || [];
  const recovery = (flow.next_actions || []).length
    ? flow.next_actions
    : (checkpoint.recovery_actions || []);
  return `<div class="resume-summary" role="status">
    <div class="rs-title">Work restored</div>
    ${checkpoint.id ? `<div class="rs-row">Checkpoint ${esc(checkpoint.id)} · ${esc(checkpoint.completion_state || "saved")}</div>` : ""}
    ${flow.message ? `<div class="rs-row">${esc(flow.message)}</div>` : ""}
    ${steps.length ? `<div class="rs-label">Plan</div><ul>${steps.map((step) => `<li>${esc(step)}</li>`).join("")}</ul>` : ""}
    ${changed.length ? `<div class="rs-row">Changed files: ${esc(changed.join(", "))}</div>` : ""}
    ${recovery.length ? `<div class="rs-row">Next: ${esc(recovery[0])}</div>` : ""}
  </div>`;
}
function pendingResumeAction(resume) {
  const gates = ((resume.workflow || {}).safety_gates || {});
  const raw = gates.pending_action;
  if (!raw || raw.kind !== "auto_cloud_confirmation") return null;
  const modelId = String(raw.model_id || "");
  const modelLabel = String(raw.model_label || "");
  if (!/^(free|account):/.test(modelId) || !modelLabel) return null;
  return { kind: raw.kind, modelId, modelLabel };
}
function renderResumedPendingAction(resume, action, messages) {
  const lastUser = [...messages].reverse().find((item) => item.role === "user");
  const lastAssistant = [...messages].reverse().find((item) => item.role === "assistant");
  if (!lastUser) return false;
  const flow = resume.workflow || {};
  const provider = action.modelId.split(":")[1] || "";
  const sel = {
    text: String(lastUser.text || ""),
    model: action.modelId,
    mode: String((flow.provider || {}).run_mode || state.mode.id),
    focus: state.focus,
    format: state.format,
    modelKind: action.modelId.startsWith("free:") ? "free" : "account",
    modelLabel: action.modelLabel,
    modelProvider: provider,
    contextHints: [],
    attachments: {},
    build: String(((resume.thread || {}).mode) || "") === "build",
  };
  // Restoring the inert selection does not grant authority. renderErrorCard's
  // named Confirm button is still the only path that adds allowCloud: true.
  state.lastSend = sel;
  const el = appendMsg("", "bot resumed-approval");
  renderErrorCard(el, "needs_auto_confirmation", {
    answer: String((lastAssistant && lastAssistant.text) || "Confirm the named cloud model to continue."),
    fallbackModelId: action.modelId,
    fallbackModelLabel: action.modelLabel,
    cloudStarted: false,
  }, sel);
  return true;
}
function restoreSession(resume) {
  clearChat();
  const messages = ((resume.thread || {}).messages || []);
  const pendingAction = pendingResumeAction(resume);
  let pendingAssistantIndex = -1;
  if (pendingAction) {
    for (let index = messages.length - 1; index >= 0; index--) {
      if (messages[index].role === "assistant") {
        pendingAssistantIndex = index;
        break;
      }
    }
  }
  messages.forEach((message, index) => {
    if (message.role === "user") {
      appendMsg(userMessageHtml(message.text || ""), "user");
    } else if (message.role === "assistant" && index !== pendingAssistantIndex) {
      const el = appendMsg(
        assistantPresentationHtml(
          roleHeader("OPai", "var(--accent)"),
          message.text || "",
          message.presentation,
        ),
        "bot",
      );
      wireActivitySummary(el);
      wireStructuredEvidence(el);
      enhanceCodeBlocks(el);
    }
  });
  if (pendingAction) renderResumedPendingAction(resume, pendingAction, messages);
  appendMsg(resumeSummaryHtml(resume), "bot resume-restored");
  if (state.boot.resume) state.boot.resume.requires_choice = false;
  setResumeGate(false);
  $("#input").focus();
}
function activateResumeSession(resume) {
  setResumeGate(true);
  if (!bridge || !bridge.resumeSession) return;
  dismissResumeChoice();  // #416: dismiss on click; re-surface if activation fails
  bridge.resumeSession((raw) => {
    let activated = false;
    try { activated = !!JSON.parse(raw || "{}").activated; } catch (_e) { activated = false; }
    if (activated) restoreSession(resume);
    else renderResumeChoice();
  });
}
function startFreshSession() {
  const done = (raw) => {
    const response = parseClearResponse(raw);
    if (!response || response.ok === false) {
      showSessionClearFailure(response || clearFailure("Saved work could not be cleared."));
      return;
    }
    state.boot = response;
    if (state.boot.resume) state.boot.resume = { available: false, requires_choice: false };
    clearChat(); stripHide(); setResumeGate(false); switchView("chat");
    $("#input").focus();
  };
  setResumeGate(true);
  dismissResumeChoice();  // #416: remove the card now, not on the bridge callback
  if (bridge && bridge.clearSession) bridge.clearSession(done);
  else showSessionClearFailure(clearFailure("Saved work could not be cleared."));
}
function renderResumeChoice() {
  const resume = (state.boot && state.boot.resume) || {};
  if (!resume.available || !resume.requires_choice) { setResumeGate(false); return; }
  setResumeGate(true);
  const count = ((resume.thread || {}).messages || []).length;
  const phase = (resume.workflow || {}).phase || "saved";
  const el = appendMsg(
    `<div class="resume-card" role="group" aria-label="Resume previous work">
       <div class="rc-badge">Saved locally</div>
       <div class="rc-title">Resume your previous work?</div>
       <div class="rc-body">${count} saved message${count === 1 ? "" : "s"} · ${esc(String(phase).replaceAll("_", " "))}. Nothing is restored until you choose.</div>
       <div class="rc-actions">
         <button class="btn primary" data-resume="resume">Resume work</button>
         <button class="btn ghost" data-resume="fresh">Start fresh</button>
       </div>
     </div>`, "bot resume-choice");
  el.querySelector('[data-resume="resume"]').onclick = () => activateResumeSession(resume);
  el.querySelector('[data-resume="fresh"]').onclick = startFreshSession;
}
function startNewChat() {
  if (state.busy) stop();
  startFreshSession();
}

/* ---------- saved conversations ---------- */

// Reopen a saved chat as a readable transcript.
//
// Deliberately a *view*, not a resumed session: continuing an archived chat
// would need its execution context (plan, changed files, checkpoint) restored
// too, and quietly attaching a new turn to old state is how a run ends up
// acting on a repository that has moved on since. Resuming the interrupted
// thread is a separate, explicit affordance that already exists.
function openConversation(conversationId) {
  if (state.busy) { toast("Finish or stop the current run first"); return; }
  if (!bridge || !bridge.loadConversation) { toast("Saved chats are unavailable"); return; }
  bridge.loadConversation(String(conversationId || ""), (raw) => {
    let payload = {};
    try { payload = JSON.parse(raw || "{}"); } catch (_e) { payload = {}; }
    if (!payload.ok || !payload.conversation) {
      toast(payload.error || "That chat could not be opened");
      // A conversation that cannot be loaded should stop being offered.
      refreshConversations();
      return;
    }
    renderConversation(payload.conversation);
  });
}

function renderConversation(conv) {
  switchView("chat");
  clearChat();
  const messages = (conv && conv.messages) || [];
  $("#empty").style.display = "none";
  messages.forEach((m) => {
    const text = String(m.text || "");
    if (m.role === "user") {
      appendMsg(userMessageHtml(text), "user");
      return;
    }
    const el = appendMsg(
      assistantPresentationHtml(
        roleHeader("OPai", "var(--muted)", { copy: true }),
        text,
        m.presentation,
      )
    );
    wireAnswerCopy(el, text);
    wireActivitySummary(el);
    wireStructuredEvidence(el);
    enhanceCodeBlocks(el);
  });
  // Say plainly that this is history. Without it, an old transcript is
  // indistinguishable from the live thread and the next message looks like it
  // will continue this chat when it starts a new one.
  appendMsg(
    `<div class="conv-note">Viewing a saved chat` +
    (conv.updated_at ? ` from ${esc(String(conv.updated_at).slice(0, 10))}` : "") +
    `. Sending a message starts a new chat.` +
    ` <button class="btn ghost" type="button" data-a="new-chat">New chat</button></div>`
  ).querySelector('[data-a="new-chat"]').onclick = () => startNewChat();
}

function refreshConversations() {
  if (!bridge || !bridge.listConversations) return;
  bridge.listConversations((raw) => {
    let payload = {};
    try { payload = JSON.parse(raw || "{}"); } catch (_e) { payload = {}; }
    if (!state.boot) return;
    state.boot.conversations = (payload && payload.conversations) || [];
    renderRecents();
  });
}

/* ---------- OPai Build: New app (#276) ----------
   Describe an app; the runnable skeleton is scaffolded deterministically for
   zero tokens, then features are built with cheap targeted prompts. */
function startNewApp() {
  switchView("chat");
  const existing = $("#newAppCard");
  if (existing) { existing.querySelector("input").focus(); return; }
  const el = appendMsg(
    roleHeader("OPai Build", "var(--accent)") +
    `<div class="new-app-card" id="newAppCard" role="group" aria-label="New app">
       <div class="nac-t">Create a new app — the runnable skeleton is scaffolded for free (0 tokens).</div>
       <input class="nac-input" type="text" placeholder="e.g. a todo app with dark mode" aria-label="App description">
       <div class="nac-actions">
         <button class="btn primary" data-a="create">Create app</button>
         <button class="btn ghost" data-a="cancel">Cancel</button>
       </div>
       <div class="nac-note" aria-live="polite"></div>
     </div>`, "bot");
  const card = el.querySelector(".new-app-card");
  const input = card.querySelector("input");
  const note = card.querySelector(".nac-note");
  const create = () => {
    const description = input.value.trim();
    if (!description) { note.textContent = "Describe the app you want to create."; return; }
    if (!bridge.scaffoldApp) { note.textContent = "App scaffolding is unavailable in this build."; return; }
    card.querySelector('[data-a="create"]').disabled = true;
    note.textContent = "Scaffolding…";
    bridge.scaffoldApp(JSON.stringify({ description }), (json) => {
      let result = {};
      try { result = JSON.parse(json); } catch (_e) { /* keep {} */ }
      if (!result.ok) {
        card.querySelector('[data-a="create"]').disabled = false;
        note.textContent = result.error || "Could not scaffold the app.";
        return;
      }
      renderNewAppSuccess(el, result);
    });
  };
  card.querySelector('[data-a="create"]').onclick = create;
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); create(); } });
  card.querySelector('[data-a="cancel"]').onclick = () => {
    el.remove();
    if (!$("#thread .msg")) {
      $("#empty").style.display = "";
      renderEmptyChips();
    }
  };
  input.focus();
  scrollBottom(true);
}

function renderNewAppSuccess(el, result) {
  const tokens = Number(result.boilerplate_tokens_avoided || 0).toLocaleString();
  el.innerHTML = roleHeader("OPai Build", "var(--accent)") +
    `<div class="new-app-card done" role="group" aria-label="App created">
       <div class="nac-t">${uiIcon("check")} ${esc(result.name)} is ready — ${(result.files || []).length} files scaffolded for free (~${esc(tokens)} tokens never spent).</div>
       <div class="nac-sub">${esc(result.root)}</div>
       <div class="nac-actions">
         <button class="btn primary" data-a="open">Open app workspace</button>
         <button class="btn ghost" data-a="preview">Copy preview command</button>
       </div>
       <div class="nac-note">Open the workspace, then describe features in chat — every edit is a cheap targeted diff.</div>
     </div>`;
  el.querySelector('[data-a="open"]').onclick = () => bridge.switchWorkspace(result.root);
  el.querySelector('[data-a="preview"]').onclick = () => {
    copyText(`cd ${result.root} && ${result.preview_cmd || "python -m http.server 8000"}`);
    toast("Preview command copied");
  };
}

/* Build mode turn (#276): a chat message edits the workspace app with one
   cheap, verified targeted diff. Reuses the whole activity/timeline/status
   machinery — same request lifecycle as send() — but calls bridge.build and
   renders a build result card instead of a chat answer. */
function sendSelection(payload) {
  if (payload && payload.build) sendBuild(payload);
  else send(payload);
}

function sendBuild(value) {
  if (state.busy) return;
  const retryOf = value && typeof value === "object" ? value : null;
  const text = String(retryOf ? retryOf.text : (value || $("#input").value)).trim();
  if (!text) return;
  setComposerDraft("");
  if (!retryOf) appendMsg(userMessageHtml(text), "user");
  const sel = retryOf || {
    text, model: state.model.id, modelKind: state.model.kind,
    modelLabel: state.model.label, modelProvider: state.model.provider, build: true,
  };
  sel.build = true;
  if (sel.modelKind === "free" && sel.allowCloud !== true && state.freeConsent && state.freeConsent.has(sel.model)) {
    sel.allowCloud = true;
  }
  state.lastSend = sel;
  const requestId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "r" + Date.now() + Math.random();
  state.currentRequest = requestId;
  state.message = OPaiMessageState.beginRequest(requestId, {});
  state.message = OPaiMessageState.transition(state.message, "preparing");
  state.store = OPaiActivity.createStore();
  state.streaming = false; state.streamedText = "";
  state.startTime = Date.now();
  buildPending(sel);
  stripReset(sel);
  startTimer(sel);
  setBusy(true);
  bridge.build(JSON.stringify({
    requestId, text, model: sel.model, strict: false,
    allowCloud: sel.allowCloud === true,
    allowLimit: sel.allowLimit === true,
  }));
}

function onBuildReply(json) {
  const d = JSON.parse(json);
  if (!OPaiMessageState.canApply(state.message, d.requestId)) return; // stale reply ignored
  const r = d.result || {};
  const backendStatus = r.status || "failed";
  state.message = OPaiMessageState.transition(
    state.message,
    OPaiMessageState.fromBackendStatus(backendStatus, r.completion_verdict, r.run_result),
  );
  state.currentRequest = null;
  setBusy(false);
  finalizeBuild(r);
  maybeFlushQueued(r);
  refreshStatus(); refreshInspector();
}

function finalizeBuild(r) {
  stopTimer();
  const status = String(r.status || "error");
  if (["needs_model", "needs_free_confirmation", "needs_auto_confirmation", "needs_limit_confirmation"].includes(status)) {
    stripFinalize(status, r);
    const gated = state.pending;
    if (!gated) return;
    state.pending = null;
    state.lastFailedRequestId = state.message && state.message.requestId;
    renderErrorCard(gated, status, r, state.lastSend || {});
    return;
  }
  const kind = r.ok ? "answered" : (r.status === "rolled_back" ? "cancelled" : "error");
  stripFinalize(kind, r);
  const el = state.pending;
  if (!el) return;
  state.pending = null;
  const sel = state.lastSend || {};
  const durMs = Date.now() - state.startTime;
  el.innerHTML = assistantPresentationHtml(
    roleHeader("OPai Build", "var(--accent)"),
    typeof r.answer === "string" ? r.answer : "",
    r.presentation,
    {
      legacyBeforeHtml: activitySummaryHtml(),
      result: r,
      extraHtml: buildResultHtml(r) +
        (r.receipt ? metaFooter({ receipt: r.receipt }, sel, durMs) : ""),
    },
  );
  wireActivitySummary(el);
  wireStructuredEvidence(el);
  wireReceipt(el, sel);
  const previewBtn = el.querySelector('[data-a="preview"]');
  if (previewBtn) previewBtn.onclick = () => {
    copyText(r.preview_cmd || "python -m http.server 8000");
    toast("Preview command copied");
  };
}

function buildResultHtml(r) {
  const status = String(r.status || "error");
  if (status === "applied") {
    const files = (r.applied || []).map((f) =>
      `<li class="bres-file"><span class="bres-act ${esc(f.action)}">${esc(f.action)}</span> ` +
      `<code>${esc(f.path)}</code> <span class="bres-diff">+${f.added} −${f.removed}</span></li>`).join("");
    const v = r.verify || {};
    const verify = v.ok
      ? `<span class="bres-verify ok">${uiIcon("check")} verified (${v.passed} checks)</span>`
      : (v.failed ? `<span class="bres-verify warn">${uiIcon("warning")} ${v.failed} check(s) failed</span>` : "");
    const ctx = r.context || {};
    const saved = ctx.saved_pct ? `<div class="bres-note">${ctx.saved_pct}% of the app left out of the prompt — that's the saving.</div>` : "";
    return `<div class="build-card" role="group" aria-label="Build result">` +
      `<div class="bres-t">${uiIcon("check")} Applied ${(r.applied || []).length} change(s) ${verify}</div>` +
      `<ul class="bres-files">${files}</ul>${saved}` +
      `<div class="nac-actions"><button class="btn ghost" data-a="preview">Copy preview command</button></div>` +
      `</div>`;
  }
  if (status === "rolled_back") {
    const checks = ((r.verify || {}).checks || []).filter((c) => !c.ok).slice(0, 5)
      .map((c) => `<li><code>${esc(c.path)}</code> · ${esc(c.check)}: ${esc(c.detail)}</li>`).join("");
    return `<div class="build-card error" role="group" aria-label="Build rolled back">` +
      `<div class="bres-t">${uiIcon("error")} Verification failed — the edit was rolled back. The files changed by this build were restored.</div>` +
      `<ul class="bres-files">${checks}</ul></div>`;
  }
  if (status === "partial_rollback" || status === "rollback_failed") {
    const files = (r.remaining_changed_files || []).map((path) =>
      `<li><code>${esc(path)}</code></li>`).join("");
    const restored = (r.rolled_back || []).length;
    const detail = status === "partial_rollback"
      ? `${restored} file(s) restored; the following files are still changed:`
      : "No changed files could be restored automatically:";
    return `<div class="build-card error" role="group" aria-label="Build rollback incomplete">` +
      `<div class="bres-t">${uiIcon("error")} Verification failed. Automatic rollback was incomplete.</div>` +
      `<div class="body">${esc(detail)}</div><ul class="bres-files">${files}</ul>` +
      `<div class="bres-note">Review the remaining file changes and the saved backup before continuing.</div></div>`;
  }
  if (status === "verification_failed") {
    const checks = ((r.verify || {}).checks || []).filter((c) => !c.ok).slice(0, 5)
      .map((c) => `<li><code>${esc(c.path || "app")}</code> · ${esc(c.check)}: ${esc(c.detail)}</li>`).join("");
    const files = (r.applied || []).map((f) => `<li><code>${esc(f.path)}</code></li>`).join("");
    return `<div class="build-card error" role="group" aria-label="Build verification failed">` +
      `<div class="bres-t">${uiIcon("error")} Changes were applied, but verification failed. They were preserved for review or rollback.</div>` +
      `<ul class="bres-files">${checks || files}</ul></div>`;
  }
  if (status === "no_edits") {
    return `<div class="build-card" role="group" aria-label="No changes"><div class="bres-t">No file changes were needed.</div>` +
      `<div class="body">${mdToHtml(String(r.answer || ""))}</div></div>`;
  }
  const msg = (r.error && (r.error.userMessage || r.error)) || r.answer || status;
  return `<div class="build-card error" role="group" aria-label="Build failed"><div class="bres-t">${uiIcon("error")} ${esc(String(msg)).slice(0, 400)}</div></div>`;
}
function appendMsg(html, cls) {
  $("#empty").style.display = "none";
  // Every path that puts a message on screen lights the room, not just send():
  // slash commands, a restored session and a queued message all arrive here.
  setStage("lit");
  const d = document.createElement("div");
  d.className = "msg " + (cls || "");
  d.innerHTML = html;
  withChatScrollPreserved(() => $("#thread").appendChild(d));
  return d;
}
function roleHeader(label, color, opts) {
  return window.OPaiChatComponents.renderAssistantHeader({
    label,
    color,
    copy: !!(opts && opts.copy),
    copyIconHtml: (opts && opts.copy) ? uiIcon("copy") : "",
  });
}

// Copy the answer the model actually wrote — the markdown source, not the
// rendered HTML. Pasting `<p>`/`<pre>` soup into an editor or an issue is
// useless, and the raw text is what the user came for.
function wireAnswerCopy(el, answer) {
  const btn = el.querySelector('[data-a="copy-answer"]');
  if (!btn) return;
  btn.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();
    copyText(answer);
    toast("Response copied");
  };
}
const ICON = { pending: "pending", running: "running", success: "check", warning: "warning", error: "error", cancelled: "cancelled" };
const ANSWERED = ["answered", "cache_hit", "answered_by_account", "answered_by_free_api", "answered_locally"];
const ERROR_TITLES = {
  account_timeout: "Ran out of time", account_error: "The model hit an error",
  account_not_connected: "No account connected", needs_model: "No free model for this",
  needs_confirmation: "Needs a paid model",
  needs_free_confirmation: "Free-tier API confirmation required",
  needs_auto_confirmation: "Auto needs your confirmation",
  needs_limit_confirmation: "Usage limit reached",
  blocked: "Blocked as risky",
  blocked_panic: "Panic mode is on", runner_error: "Local model couldn't answer",
  error: "Something went wrong", empty: "Empty response",
};

function send(retryOf) {
  if (state.busy && !retryOf) return; // duplicate-submit protection
  if (!retryOf && composerBlockReason()) return;
  const text = retryOf ? retryOf.text : $("#input").value.trim();
  if (!text) return;
  // First message in a fresh chat: the lights come up and the composer flies
  // down to where it lives, and only then does the request go out. Placed
  // after the guards above so an empty or blocked send never triggers it, and
  // before everything below so no state is mutated twice on the way through.
  if (raiseTheLights(() => send(retryOf))) return;
  // Slash commands run local OPai tools ("/panic", "/savings", "/connect") —
  // they must NEVER be sent to a paid model as a prompt.
  if (!retryOf && text.startsWith("/")) {
    setComposerDraft("");
    const name = text.slice(1).trim().split(/\s+/)[0].toLowerCase();
    if (name) {
      appendMsg(userMessageHtml(text), "user");
      bridge.runTool(name);
    }
    return;
  }
  const sel = retryOf || {
    text, model: state.model.id, mode: state.mode.id, focus: state.focus, format: state.format,
    modelKind: state.model.kind, modelLabel: state.model.label, modelProvider: state.model.provider,
    contextHints: state.contextHints.slice(),
  };
  // Free-tier consent: one confirmation per provider, ever. If the user has
  // already confirmed this free model in the past (persisted per workspace),
  // send with allowCloud=true up front — no card. Otherwise the pipeline
  // returns needs_free_confirmation and the in-chat card handles it.
  if (sel.modelKind === "free" && sel.allowCloud !== true && state.freeConsent && state.freeConsent.has(sel.model)) {
    sel.allowCloud = true;
  }
  if (!retryOf) setComposerDraft("");
  state.lastSend = sel;
  if (!retryOf) {
    appendMsg(userMessageHtml(text), "user");
    if (bridge.saveRecent) bridge.saveRecent(text);
    // The prompt list feeds the composer's Up-arrow history; the sidebar lists
    // saved conversations. The backend archives the chat as the turn starts, so
    // re-read it rather than guessing the entry from here.
    state.boot.recents = [text].concat((state.boot.recents || []).filter((r) => r !== text)).slice(0, 12);
    refreshConversations();
  }
  const requestId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "r" + Date.now() + Math.random();
  state.currentRequest = requestId;
  state.message = OPaiMessageState.beginRequest(requestId, { retryOf: retryOf ? state.lastFailedRequestId : null });
  state.message = OPaiMessageState.transition(state.message, "preparing");
  state.store = OPaiActivity.createStore();
  state.streaming = false;
  state.streamedText = "";
  cancelTokenRender();
  state.streamRenderedText = "";
  state.lastStreamRenderAt = 0;
  state.startTime = Date.now();
  buildPending(sel);
  stripReset(sel);
  startTimer(sel);
  setBusy(true);
  // The bridge remains backwards-compatible with older hosts by receiving
  // metadata too, while the prompt itself carries safe path references for
  // the existing context-selection pipeline. Never read or serialize files.
  const contextHints = Array.isArray(sel.contextHints)
    ? sel.contextHints.map(normalizeContextHint).filter(Boolean)
    : [];
  const requestText = contextHints.length
    ? `Repository context references:\n${contextHints.map((path) => `@${path}`).join("\n")}\n\n${text}`
    : text;
  bridge.send(JSON.stringify({
    requestId, text: requestText, model: sel.model, mode: sel.mode, focus: sel.focus,
    format: sel.format, allowCloud: sel.allowCloud === true, allowLimit: sel.allowLimit === true,
    contextHints,
    // F9/F17: one-time approval for a policy-blocked command — the exact
    // string echoed by the pipeline, never a rewritten one. Omitted unless set.
    allowCommand: typeof sel.allowCommand === "string" && sel.allowCommand ? sel.allowCommand : undefined,
    // F26: one-time approval for provider-gated file edits. Omitted unless set.
    allowEditsOnce: sel.allowEditsOnce === true ? true : undefined,
  }));
}

function buildPending(sel) {
  const activityLogId = `activity-live-${++activityDisclosureSequence}`;
  const el = appendMsg(
    roleHeader("OPai", "var(--accent)") +
    `<div class="gen">
       <div class="gen-head">
         <span class="thinking"><i></i><i></i><i></i></span>
         <span class="gen-stage">Preparing request…</span>
         <span class="gen-time" aria-live="off">00:00</span>
         <button class="gen-stop" aria-label="Stop generation">Stop</button>
       </div>
       <div class="gen-reassure" aria-live="polite"></div>
       <button class="gen-toggle" type="button" aria-controls="${activityLogId}" aria-expanded="false">Show activity</button>
       <div class="timeline" id="${activityLogId}" role="log" aria-label="AI activity" hidden></div>
       <div class="body stream response-prose"></div>
     </div>`, "bot");
  state.pending = el;
  el.querySelector(".gen-stop").onclick = stop;
  el.querySelector(".gen-toggle").onclick = () => {
    const tl = el.querySelector(".timeline"), btn = el.querySelector(".gen-toggle");
    if (tl.hasAttribute("hidden")) {
      tl.removeAttribute("hidden"); btn.textContent = "Hide activity"; btn.setAttribute("aria-expanded", "true");
      renderTimeline();
    }
    else { tl.setAttribute("hidden", ""); btn.textContent = "Show activity (" + state.store.events.length + ")"; btn.setAttribute("aria-expanded", "false"); }
  };
}

function tlRowInner(e, startTime = state.startTime) {
  // Each row carries its real offset from the start of the run (the events
  // have true epoch timestamps). No timestamp -> no label, never invented.
  let ts = "";
  if (typeof e.timestamp === "number" && startTime && e.timestamp >= startTime) {
    ts = `<span class="tl-ts">+${((e.timestamp - startTime) / 1000).toFixed(1)}s</span>`;
  }
  return `<span class="tl-ic">${uiIcon(ICON[e.status] || "pending")}</span>` +
    `<span class="tl-t">${esc(e.title)}</span>${e.detail ? `<span class="tl-d">${esc(e.detail)}</span>` : ""}${ts}`;
}
function truncationRowInner(count) {
  return `<span class="tl-ic">${uiIcon("more")}</span>` +
    `<span class="tl-t">${count.toLocaleString()} earlier steps hidden</span>` +
    `<span class="tl-d">dropped to stay fast</span>`;
}
function timelineRows(snapshot) {
  const source = snapshot || {
    events: state.store.list(),
    startTime: state.startTime,
    truncated: state.store.truncatedCount ? state.store.truncatedCount() : 0,
  };
  const truncated = source.truncated || 0;
  const marker = truncated > 0
    ? `<div class="tl-row tl-truncation">${truncationRowInner(truncated)}</div>`
    : "";
  return marker + source.events.map((e) => `<div class="tl-row ${e.status}">${tlRowInner(e, source.startTime)}</div>`).join("");
}
// A group is auto-expanded when any child errored (surface the failure), else
// it honors the user's toggle.
function groupExpanded(row, identity) {
  if (row.children.some((c) => c.status === "error")) return true;
  return state.expandedGroups.has(identity);
}
function buildSingleNode(event) {
  const node = document.createElement("div");
  node.className = "tl-row " + event.status;
  node.innerHTML = tlRowInner(event);
  return { type: "single", node, cls: node.className, inner: node.innerHTML };
}
function updateSingleNode(entry, event) {
  const cls = "tl-row " + event.status;
  const inner = tlRowInner(event);
  if (entry.cls !== cls) { entry.node.className = cls; entry.cls = cls; }
  if (entry.inner !== inner) { entry.node.innerHTML = inner; entry.inner = inner; }
}
function groupHeaderInner(row, expanded) {
  return `<button class="tl-group-toggle" aria-expanded="${expanded}" tabindex="0">` +
    `<span class="tl-ic">${uiIcon(ICON[row.status] || "pending")}</span>` +
    `<span class="tl-caret">${uiIcon(expanded ? "chevronDown" : "chevronRight")}</span>` +
    `<span class="tl-t">${esc(row.label)}</span></button>`;
}
function reconcileGroupNode(entry, row, identity) {
  const expanded = groupExpanded(row, identity);
  if (!entry || entry.type !== "group") {
    const node = document.createElement("div");
    node.className = "tl-row tl-group " + row.status;
    const header = document.createElement("div");
    header.className = "tl-group-head";
    header.innerHTML = groupHeaderInner(row, expanded);
    const kids = document.createElement("div");
    kids.className = "tl-children";
    node.appendChild(header);
    node.appendChild(kids);
    entry = { type: "group", node, header, kids, childNodes: new Map() };
    header.querySelector(".tl-group-toggle").onclick = () => {
      if (state.expandedGroups.has(identity)) state.expandedGroups.delete(identity);
      else state.expandedGroups.add(identity);
      renderTimeline(); // immediate, not rAF — a click deserves a live response
    };
  }
  entry.node.className = "tl-row tl-group " + row.status;
  entry.header.innerHTML = groupHeaderInner(row, expanded);
  entry.header.querySelector(".tl-group-toggle").onclick = () => {
    if (state.expandedGroups.has(identity)) state.expandedGroups.delete(identity);
    else state.expandedGroups.add(identity);
    renderTimeline();
  };
  if (expanded) entry.kids.removeAttribute("hidden"); else entry.kids.setAttribute("hidden", "");
  if (!expanded) return entry;
  // Reconcile the group's children (keyed by event id within the group).
  const seenKids = new Set();
  for (const child of row.children) {
    const ck = String(child.id);
    seenKids.add(ck);
    let ce = entry.childNodes.get(ck);
    if (!ce) { ce = buildSingleNode(child); entry.childNodes.set(ck, ce); }
    else updateSingleNode(ce, child);
    entry.kids.appendChild(ce.node);
  }
  for (const [ck, ce] of entry.childNodes) {
    if (!seenKids.has(ck)) { ce.node.remove(); entry.childNodes.delete(ck); }
  }
  return entry;
}
function renderTimeline() {
  if (!state.pending) return;
  const tl = state.pending.querySelector(".timeline");
  const btn = state.pending.querySelector(".gen-toggle");
  if (btn && btn.getAttribute("aria-expanded") !== "true") {
    btn.textContent = "Show activity (" + state.store.events.length + ")";
  }
  if (!tl || tl.hasAttribute("hidden")) {
    return;
  }
  const grouped = OPaiActivity.groupRows(state.store.list());
  const scrollSnapshot = captureChatScroll();
  try {
    // Keyed reconcile over LOGICAL rows: singles keyed by event id, groups by
    // group key. Presentation is grouped/calm; storage keeps every raw event
    // (#231). A full innerHTML rewrite per event was O(n^2) (#228).
    if (!state.tlNodes || state.tlNodes.container !== tl) {
      state.tlNodes = { container: tl, rows: new Map() };
      state.expandedGroups = new Set();
      tl.textContent = "";
    }
    const rows = state.tlNodes.rows;
    const seen = new Set();
    const order = [];
    const groupOccurrences = new Map();
    // Honest truncation marker (#248, #400): when the store dropped the oldest
    // events to stay bounded, say so plainly — and don't promise a full record
    // the UI can't actually show (no itemized ledger view exists yet, #390).
    const truncated = state.store.truncatedCount ? state.store.truncatedCount() : 0;
    if (truncated > 0) {
      const key = "truncation";
      seen.add(key);
      const inner = truncationRowInner(truncated);
      let entry = rows.get(key);
      if (!entry || entry.type !== "single") {
        const node = document.createElement("div");
        node.className = "tl-row tl-truncation"; node.innerHTML = inner;
        entry = { type: "single", node, cls: node.className, inner };
        rows.set(key, entry);
      } else if (entry.inner !== inner) { entry.node.innerHTML = inner; entry.inner = inner; }
      order.push(entry.node);
    }
    for (const row of grouped) {
      if (row.kind === "single") {
        const key = "s:" + row.event.id;
        seen.add(key);
        let entry = rows.get(key);
        if (!entry || entry.type !== "single") { entry = buildSingleNode(row.event); rows.set(key, entry); }
        else updateSingleNode(entry, row.event);
        order.push(entry.node);
      } else {
        const occurrence = groupOccurrences.get(row.key) || 0;
        groupOccurrences.set(row.key, occurrence + 1);
        const identity = row.key + ":" + occurrence;
        const key = "g:" + identity;
        seen.add(key);
        const entry = reconcileGroupNode(rows.get(key), row, identity);
        rows.set(key, entry);
        order.push(entry.node);
      }
    }
    for (const [key, entry] of rows) {
      if (!seen.has(key)) { entry.node.remove(); rows.delete(key); }
    }
    // Enforce order (appendChild moves existing nodes — cheap, no HTML reparse).
    for (const node of order) tl.appendChild(node);
    state.timelineRenders++;
  } finally {
    restoreChatScroll(scrollSnapshot);
  }
}
function scheduleTimelineRender() {
  // Activity shares the token path's rAF cadence: a burst of events in one
  // frame costs one render instead of one render per event (#228).
  if (!state.pending) return;
  const timeline = state.pending.querySelector(".timeline");
  const button = state.pending.querySelector(".gen-toggle");
  if (button && button.getAttribute("aria-expanded") !== "true") {
    button.textContent = "Show activity (" + state.store.events.length + ")";
  }
  if (!timeline || timeline.hasAttribute("hidden")) {
    return;
  }
  if (state.activityRenderPending) return;
  state.activityRenderPending = true;
  requestAnimationFrame(() => {
    state.activityRenderPending = false;
    renderTimeline();
  });
}

/* ---------- status strip (#232) ----------
   A persistent, calm bar for AMBIENT state — connection, model, live cost,
   elapsed — fed only by channel:"status" events, tokens, and the receipt. It
   is never a timeline row (groupRows excludes channel:status). Dot states:
   idle · connecting · connected · active · error · cancelled. */
function prettyModel(raw) {
  let s = String(raw || "").trim().replace(/^account:/, "").replace(/^free:/, "");
  return (s === "" || s === "auto") ? "Auto" : s;
}
function stripSetState(kind) {
  const strip = $("#statusStrip");
  strip.className = "status-strip ss-" + kind;
  const dot = $("#ssDot");
  // Provider colour on the live/connected dot; semantic CSS colours otherwise.
  dot.style.background = ((kind === "connected" || kind === "active") && state.stripColor) ? state.stripColor : "";
}
function stripReset(sel) {
  const strip = $("#statusStrip");
  strip.removeAttribute("hidden");
  state.stripColor = PROVIDER_COLOR[sel.modelProvider] || "";
  stripSetState("connecting");
  $("#ssConn").textContent = "Connecting…";
  $("#ssModel").textContent = sel.model ? prettyModel(sel.model) : "";
  $("#ssCost").textContent = "";
  $("#ssTime").textContent = "00:00";
}
function stripHide() { $("#statusStrip").setAttribute("hidden", ""); }
function stripStreaming() {
  const strip = $("#statusStrip");
  if (strip.hasAttribute("hidden")) return;
  stripSetState("active");
  const conn = $("#ssConn");
  if (conn.textContent === "Connecting…") conn.textContent = "Streaming…";
}
function stripElapsed(ms) {
  const strip = $("#statusStrip");
  if (!strip.hasAttribute("hidden")) $("#ssTime").textContent = OPaiActivity.formatElapsed(ms);
}
// Ambient status events refine the strip; they never become timeline rows.
function applyStatusEvent(event) {
  const strip = $("#statusStrip");
  if (strip.hasAttribute("hidden")) return;
  const id = String(event.id || "");
  if (id.endsWith(":connect") || /^connected/i.test(event.title || "")) {
    stripSetState(state.streaming ? "active" : "connected");
    $("#ssConn").textContent = event.title || "Connected";
  } else if (id.endsWith(":model") || event.type === "model_selected") {
    const m = (event.metadata && event.metadata.model) || "";
    if (m) $("#ssModel").textContent = prettyModel(m);
  }
}
function stripFinalize(status, r) {
  const strip = $("#statusStrip");
  if (strip.hasAttribute("hidden")) return;
  $("#ssTime").textContent = OPaiActivity.formatElapsed(state.startTime ? Date.now() - state.startTime : 0);
  const rc = (r && r.receipt) || {};
  const cost = +rc.estimated_actual_usd;
  if (cost > 0) $("#ssCost").textContent = "$" + cost.toFixed(4) + " spent";
  else if (+rc.estimated_savings_usd > 0) $("#ssCost").textContent = "$" + (+rc.estimated_savings_usd).toFixed(4) + " saved";
  else $("#ssCost").textContent = ""; // never a fake $0 for a paid call
  const verdict = completionVerdict(r);
  if (verdict && verdict.verdict === "cancelled") { stripSetState("cancelled"); $("#ssConn").textContent = "Cancelled"; }
  // Round 5 finding 2: every non-completed verdict painted the strip red, so a
  // "Partial" run (answered, some evidence missing) looked identical to a hard
  // failure — the dot said one thing, the verdict card another. Partial/Blocked
  // are amber warnings; only Failed/Timed out are red.
  else if (verdict && (verdict.verdict === "partial" || verdict.verdict === "blocked")) { stripSetState("warning"); $("#ssConn").textContent = verdictLabel(verdict.verdict); }
  else if (verdict && verdict.verdict !== "completed") { stripSetState("error"); $("#ssConn").textContent = verdictLabel(verdict.verdict); }
  else if (verdict) { stripSetState("connected"); $("#ssConn").textContent = completionVerdictLabel(verdict); }
  // #380: Stop was accepted but teardown is not proven yet. Saying "Stopped"
  // here would be the same false claim the optimistic stop() used to make.
  else if (status === "cancel_requested") { stripSetState("cancelled"); $("#ssConn").textContent = "Stopping…"; }
  else if (status === "cancelled") { stripSetState("cancelled"); $("#ssConn").textContent = "Stopped"; }
  else if (ANSWERED.includes(status)) { stripSetState("connected"); $("#ssConn").textContent = "Done"; }
  else { stripSetState("error"); $("#ssConn").textContent = "Failed"; }
}

const ACTIVITY_STATE_BY_TYPE = {
  provider_checking: "authenticating", request_sending: "sending",
  waiting_first_token: "waiting", streaming: "streaming",
  // #402: the pipeline's evidence check before a terminal verdict.
  verifying: "verifying",
};
function applyActivityState(event) {
  const next = ACTIVITY_STATE_BY_TYPE[event.type];
  if (next) state.message = OPaiMessageState.transition(state.message, next);
  if ((event.channel || "feed") === "status") { applyStatusEvent(event); return; }
  updateInspectorLive(event && event.title);
}
function onActivity(json) {
  const d = JSON.parse(json);
  if (!OPaiMessageState.canApply(state.message, d.requestId)) return; // stale guard
  state.store.upsert(d.event);
  applyActivityState(d.event);
  scheduleTimelineRender();
}
// Batched activity path (#226): one signal carries an array of events. Ingest
// them in a single store pass, then render once — the same rAF cadence as the
// per-event path, but a whole burst costs one bridge crossing and one render.
function onActivityBatch(json) {
  const d = JSON.parse(json);
  if (!OPaiMessageState.canApply(state.message, d.requestId)) return; // stale guard
  const events = d.events || [];
  if (!events.length) return;
  state.store.ingestBatch(events);
  events.forEach(applyActivityState);
  scheduleTimelineRender();
}
function onToken(json) {
  const d = JSON.parse(json);
  if (!OPaiMessageState.canApply(state.message, d.requestId)) return; // stale guard
  state.message = OPaiMessageState.transition(state.message, "streaming");
  if (!state.streaming) { state.streaming = true; updateGenStage(); stripStreaming(); }
  state.streamedText += d.text;
  scheduleTokenRender();
}

function cancelTokenRender() {
  if (state.tokenRenderTimer != null) clearTimeout(state.tokenRenderTimer);
  if (state.tokenRenderFrame != null) cancelAnimationFrame(state.tokenRenderFrame);
  state.tokenRenderTimer = null;
  state.tokenRenderFrame = null;
  state.tokenRenderPending = false;
}

function flushTokenRender() {
  cancelTokenRender();
  const body = state.pending && state.pending.querySelector(".body.stream");
  if (!body || state.streamRenderedText === state.streamedText) return;
  withChatScrollPreserved(() => renderStreamingBody(body, state.streamedText));
  state.streamRenderedText = state.streamedText;
  state.lastStreamRenderAt = performance.now();
  state.streamRenders++;
}

function scheduleTokenRender() {
  if (!state.streamRenderedText) {
    flushTokenRender();
    return;
  }
  if (state.tokenRenderPending) return;
  state.tokenRenderPending = true;
  const elapsed = performance.now() - state.lastStreamRenderAt;
  const delay = Math.max(0, 32 - elapsed);
  state.tokenRenderTimer = setTimeout(() => {
    state.tokenRenderTimer = null;
    state.tokenRenderFrame = requestAnimationFrame(() => {
      state.tokenRenderFrame = null;
      state.tokenRenderPending = false;
      flushTokenRender();
    });
  }, delay);
}

function startTimer(sel) {
  stopTimer();
  state.timer = setInterval(() => updateGenStage(sel), 500);
  updateGenStage(sel);
}
function stopTimer() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }
function updateGenStage(sel) {
  sel = sel || state.lastSend || {};
  if (!state.pending) return;
  const elapsedMs = Date.now() - state.startTime;
  const sm = OPaiActivity.stageMessage(elapsedMs / 1000, { streaming: state.streaming, modelLabel: sel.modelLabel || sel.model });
  const stEl = state.pending.querySelector(".gen-stage");
  const tEl = state.pending.querySelector(".gen-time");
  const rEl = state.pending.querySelector(".gen-reassure");
  if (stEl) stEl.textContent = sm.stage;
  if (tEl) tEl.textContent = OPaiActivity.formatElapsed(elapsedMs);
  stripElapsed(elapsedMs);
  if (rEl) {
    rEl.innerHTML = (sm.reassurance ? esc(sm.reassurance) : "") + (sm.suggestFaster ? ` <a class="gen-switch">Switch to a faster model</a>` : "");
    rEl.classList.toggle("warn", sm.severity === "warning");
    const sw = rEl.querySelector(".gen-switch");
    if (sw) sw.onclick = () => openModelPicker();
  }
  updateInspectorLive(sm.stage);
}

// #380 / #295 invariant 9: pressing Stop is a *request*. The backend sets a
// cancel flag and the provider CLI dies whenever it next notices, so declaring
// "cancelled" here would claim a paid call had stopped while it was very
// possibly still running — and dropping the request id at the same moment made
// that unobservable (invariant 15: no hidden work). Acknowledge immediately,
// then wait for the backend to confirm the worker actually returned.
function stop() {
  if (!state.currentRequest || state.cancelling) return;
  const cancelling = state.currentRequest;
  state.cancelling = cancelling;
  bridge.cancel(cancelling);
  state.message = OPaiMessageState.transition(state.message, "cancel_requested");
  stopTimer();
  stripFinalize("cancel_requested", {});
  // Control returns to the user at once. Waiting for teardown before releasing
  // the composer would make Stop feel broken and would limit interaction for
  // something the user has no part in — the honesty belongs in the run state
  // and the strip, not in a frozen interface.
  setBusy(false);
  // Bounded: teardown that never reports back must not leave the run in limbo,
  // but it must not be reported as a clean stop either.
  state.cancelTimer = setTimeout(() => finishCancel(cancelling, "unconfirmed"), CANCEL_TEARDOWN_MS);
}

// How long to wait for teardown before saying so honestly.
const CANCEL_TEARDOWN_MS = 10000;

function onCancelReady(json) {
  let d = {};
  try { d = JSON.parse(json || "{}"); } catch (_e) { return; }
  if (!state.cancelling || d.requestId !== state.cancelling) return;
  const proven = d.teardown === "complete" || d.teardown === "not_running";
  finishCancel(state.cancelling, proven ? "complete" : "unconfirmed");
}

// `cancelledId` is carried explicitly because the user regains control the
// moment Stop is pressed: by the time teardown reports back they may already
// have started another run, and this must never clobber it.
function finishCancel(cancelledId, teardown) {
  if (state.cancelling !== cancelledId) return;
  if (state.cancelTimer) { clearTimeout(state.cancelTimer); state.cancelTimer = null; }
  state.cancelling = null;
  if (state.currentRequest === cancelledId) state.currentRequest = null;
  // Only finalize the message this cancel belongs to. A newer request owns the
  // composer now and its own reply will finalize it.
  if (!state.message || state.message.requestId !== cancelledId) return;
  state.message = OPaiMessageState.transition(state.message, "cancelled");
  state.store.cancelRunning(); renderTimeline();
  finalize("cancelled", {
    answer: state.streamedText || "",
    // Reported, not hidden: an unconfirmed teardown means OPai could not prove
    // the provider call stopped, and the user may still be paying for it.
    cancel_teardown: teardown,
  });
  flushQueued();
}
function retry() {
  if (!state.lastSend) return;
  const modelChanged = state.lastSend.model !== state.model.id;
  const payload = {
    ...state.lastSend,
    model: state.model.id,
    modelKind: state.model.kind,
    modelLabel: state.model.label,
    modelProvider: state.model.provider,
  };
  // Consent/limit overrides apply to the route the user reviewed. A newly
  // selected provider must pass its own gates instead of inheriting them.
  if (modelChanged) {
    delete payload.allowCloud;
    delete payload.allowLimit;
  }
  sendSelection(payload);
}

function openModelPicker() {
  // The picker lives in the Chat composer, but Ctrl+M and the command palette
  // are global. Move to its owning view first so the advertised shortcut
  // never opens an invisible popover behind Prompt Library or Settings.
  switchView("chat");
  // Failed-card and palette clicks originate outside the composer. Defer until
  // their click has finished bubbling, otherwise the composer's outside-click
  // listener closes the popover in the same event that opened it.
  setTimeout(() => {
    if (window.OPaiComposer && typeof window.OPaiComposer.openModel === "function") {
      window.OPaiComposer.openModel();
      return;
    }
    // Backward compatibility for an older composer bundle.
    const button = $("#modelBtn");
    if (button) button.click();
    else $("#modelSel").focus();
  }, 0);
}

function openSettingsPage(pageId = "overview") {
  const target = String(pageId || "overview").replace(/[^\w-]/g, "") || "overview";
  try { window.history.replaceState(null, "", `#settings/${target}`); } catch (_e) { /* best-effort deep link */ }
  switchView("settings");
}

function scrollBottom(force) {
  const sc = $("#chatScroll");
  if (force) state.followLatest = true;
  if (state.followLatest) sc.scrollTop = sc.scrollHeight;
  updateJumpLatest();
}
function isNearChatBottom(sc) {
  return sc.scrollHeight - sc.scrollTop - sc.clientHeight < 96;
}
function updateJumpLatest() {
  const sc = $("#chatScroll");
  const jump = $("#jumpLatest");
  if (!sc || !jump) return;
  jump.hidden = state.followLatest || sc.scrollHeight <= sc.clientHeight + 1;
}
function captureChatScroll() {
  const sc = $("#chatScroll");
  return sc ? { top: sc.scrollTop, follow: state.followLatest } : null;
}
function restoreChatScroll(snapshot) {
  if (!snapshot) return;
  const sc = $("#chatScroll");
  if (!sc) return;
  if (snapshot.follow) sc.scrollTop = sc.scrollHeight;
  else sc.scrollTop = snapshot.top;
  updateJumpLatest();
}
function withChatScrollPreserved(change) {
  const snapshot = captureChatScroll();
  try {
    return change();
  } finally {
    restoreChatScroll(snapshot);
  }
}
function stripStopNote(t) { return String(t || "").replace(/\n\n_\(stopped by you\)_\s*$/, ""); }

function activitySummaryHtml() {
  const n = state.store ? state.store.events.length : 0;
  if (!n) return "";
  const id = `activity-log-${++activityDisclosureSequence}`;
  pendingActivitySnapshot = {
    id,
    events: state.store.list().slice(),
    startTime: state.startTime,
    truncated: state.store.truncatedCount ? state.store.truncatedCount() : 0,
  };
  return `<button class="gen-toggle done" type="button" data-label="Activity (${n})" aria-controls="${id}" aria-expanded="false">Activity (${n})</button>` +
    `<div class="timeline done" id="${id}" role="log" aria-label="AI activity" hidden></div>`;
}
function wireActivitySummary(el) {
  const btn = el.querySelector(".gen-toggle.done");
  const pending = pendingActivitySnapshot;
  pendingActivitySnapshot = null;
  if (!btn) return;
  const controlledId = btn.getAttribute("aria-controls");
  const tl = controlledId ? el.querySelector(`#${CSS.escape(controlledId)}`) : el.querySelector(".timeline.done");
  const snapshot = pending && pending.id === controlledId ? pending : null;
  let hydrated = !snapshot;
  btn.onclick = () => {
    if (!tl) return;
    if (tl.hasAttribute("hidden")) {
      if (!hydrated) {
        withChatScrollPreserved(() => { tl.innerHTML = timelineRows(snapshot); });
        hydrated = true;
      }
      tl.removeAttribute("hidden");
      btn.setAttribute("aria-expanded", "true");
      btn.textContent = "Hide activity";
    } else {
      tl.setAttribute("hidden", "");
      btn.setAttribute("aria-expanded", "false");
      btn.textContent = btn.dataset.label;
    }
  };
}
// The measurement badge — honest about where the money number came from
// (#235). confidence comes from the receipt / cost_telemetry: "actual" =
// provider-reported dollars, "unknown" = subscription-style $0, else model math.
function receiptBadge(rc) {
  const c = String(rc.confidence || "").toLowerCase();
  if (c === "actual") return { cls: "measured", label: "Measured", title: "Real dollars reported by the provider" };
  if (c === "unknown") return { cls: "subscription", label: "Subscription", title: "Covered by a subscription — no per-call dollar amount" };
  return { cls: "estimated", label: "Estimated", title: "Estimated from token math, not a billed amount" };
}
function completionVerdict(r) {
  return OPaiRunResult.fromResult(r);
}
// The one user-facing label per verdict — mirrors opaihub.completion.VERDICT_LABELS
// (#396) so the GUI, CLI, and receipt summary never disagree ("Timed out", not
// "Timeout"). Kept in sync by a vocabulary-parity test.
const VERDICT_LABELS = {
  completed: "Completed", partial: "Partial", blocked: "Blocked",
  failed: "Failed", cancelled: "Cancelled", timeout: "Timed out",
  needs_attention: "Needs attention",
};
function verdictLabel(verdict) {
  const key = String(verdict || "").toLowerCase();
  return VERDICT_LABELS[key] || (key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()) || "Unknown");
}
// Verdicts a one-click Retry can actually help. A completed run has nothing to
// retry; a cancelled one is the user's own choice; a blocked one needs the user
// to resolve an approval/input first, so retry alone would just re-block.
const RETRYABLE_VERDICTS = new Set(["failed", "partial", "timeout"]);
function completionVerdictHtml(r) {
  const item = completionVerdict(r);
  if (!item) return "";
  const label = completionVerdictLabel(item);
  const glyph = item.verdict === "completed" ? "check" : item.verdict === "cancelled" ? "cancelled" : "warning";
  const next = item.nextAction ? `<div class="cv-next">Next: ${esc(item.nextAction)}</div>` : "";
  // Bug 8: pair the "Next: retry…" guidance with an actual button so the user
  // doesn't have to retype the prompt when a run fails.
  const retryBtn = RETRYABLE_VERDICTS.has(item.verdict) && state.lastSend
    ? `<div class="cv-actions"><button class="btn" data-a="retry">Retry</button></div>`
    : "";
  return `<section class="completion-verdict ${esc(item.verdict)}" role="status" aria-label="Completion verdict: ${esc(label)}">` +
    `<div class="cv-title">${uiIcon(glyph)} ${esc(label)}</div><div class="cv-reason">${esc(item.reason)}</div>${next}${retryBtn}</section>`;
}

// The verdict owns the user-facing outcome everywhere. Runtime phases describe
// internal progress and can legitimately end "completed" after a provider
// returned, even when OPai could not verify the user's objective. Rendering a
// runtime phase as the final status reintroduced the Round 6 contradiction:
// "Partial" above "Implement · Completed" below.
function completionVerdictLabel(item) {
  if (item && item.canonical) return item.label;
  return item && item.reasonCode === "answer_delivered"
    ? "Response received"
    : verdictLabel(item && item.verdict);
}

// Round 5 finding 2: one push turn showed a red "Failed" pill directly above the
// words "has been successfully pushed to the origin remote". Whichever was wrong,
// the two surfaces sent opposite messages and a user who glanced at only one drew
// the opposite conclusion. OPai cannot tell from prose which is right — so it
// refuses to let the claim read as settled, and says so where the claim is.
function unverifiedClaimHtml(r) {
  const item = completionVerdict(r);
  if (!item || !item.answerConflicts) return "";
  return `<div class="unverified-claim" role="note">${uiIcon("warning")} ` +
    `<span><strong>OPai could not verify this.</strong> The response below says the ` +
    `work succeeded, but this run ended as <em>${esc(completionVerdictLabel(item))}</em> ` +
    `and OPai found no evidence the action completed. Treat the claim as unconfirmed ` +
    `and check the result yourself before relying on it.</span></div>`;
}
function metaFooter(r, sel, durMs) {
  const rc = (r && r.receipt) || {};
  const badge = receiptBadge(rc);
  // Cost/savings line — the SAME honest text the flat footer used, so the
  // money-truth contract holds: a paid call shows spend and never "saved".
  const bits = [sel.modelLabel || "OPai", OPaiActivity.formatElapsed(durMs)];
  if (+rc.estimated_actual_usd) bits.push("$" + (+rc.estimated_actual_usd).toFixed(4) + " spent");
  if (+rc.estimated_savings_usd) bits.push("$" + (+rc.estimated_savings_usd).toFixed(4) + " saved");
  if (rc.paid_call_avoided) bits.push("paid call avoided");
  const verdict = completionVerdict(r);
  if (verdict) bits.unshift(`${verdict.verdict}: ${verdict.reason}`);
  return `<div class="receipt-card">` +
    `<div class="footer-note" role="button" tabindex="0" title="Copy this receipt" aria-label="Copy receipt">` +
      `<span class="rc-badge rc-${badge.cls}" title="${esc(badge.title)}">${esc(badge.label)}</span>` +
      `<span class="rc-bits">${esc(bits.join("   ·   "))}</span>` +
    `</div>` +
    `<button class="rc-ledger" type="button" aria-label="Open the savings summary">Summary ${uiIcon("arrowRight")}</button>` +
  `</div>`;
}

// The receipt is a claim — let the user take it with them, and open the savings
// summary. One click on the strip copies a clean plaintext receipt; the Summary
// button jumps to the aggregate savings dashboard (not an itemized ledger — that
// view lands with #390 — and not a copy).
function wireReceipt(el, sel, r) {
  const card = el.querySelector(".receipt-card");
  const strip = el.querySelector(".footer-note");
  if (!strip) return;
  const copy = () => {
    // #389: prefer the verdict-first summary rendered from the run record (one
    // source of truth, redacted server-side). Fall back to the footer scrape
    // only for surfaces without a record (e.g. the Build result card).
    const summary = r && typeof r.run_summary === "string" && r.run_summary.trim();
    if (summary) {
      copyText(summary);
      toast("Receipt summary copied");
      return;
    }
    const badge = strip.querySelector(".rc-badge");
    const bits = strip.querySelector(".rc-bits");
    const line = [badge && badge.textContent.trim(), bits && bits.textContent.trim()].filter(Boolean).join(" · ");
    copyText(`OPai receipt\nTask: ${(sel && sel.text) || "—"}\n${line || strip.textContent.trim()}`);
    toast("Receipt copied");
  };
  strip.onclick = copy;
  strip.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); copy(); } });
  const ledger = card && card.querySelector(".rc-ledger");
  if (ledger) ledger.onclick = (e) => { e.stopPropagation(); switchView("home"); };
}
// Defense-in-depth: never trust upstream redaction — scrub secret-shaped text
// before it can render in the details drawer (BUG-QA-002).
function redactSecrets(text) {
  return String(text || "")
    .replace(/\bsk-[A-Za-z0-9_-]{8,}/g, "[redacted]")
    .replace(/\b(token|secret|password|api[_-]?key|bearer)\s*[:=]\s*\S+/gi, "$1=[redacted]");
}

function providerName(provider) {
  // Named providers keep their exact brand casing; anything else is title-cased
  // so a live "Test" button never resets its label to a lowercase id like
  // "Test github" (Bug 5).
  const known = { claude: "Claude", codex: "Codex", copilot: "Copilot", github: "GitHub", gemini: "Gemini", kimi: "Kimi", groq: "Groq", openrouter: "OpenRouter" };
  const key = String(provider || "").toLowerCase();
  return known[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "Provider");
}

function connectionHealth(result) {
  if (result.cliPresent === false || (result.connection && result.connection.cliPresent === false)) return "not_installed";
  if (result.signedIn || result.connected || result.authStatus === "connected" || (result.connection && result.connection.authStatus === "connected")) return "verified";
  const authStatus = (result.connection && result.connection.authStatus) || result.authStatus || "";
  return ({ not_configured: "not_configured", unknown: "detected", misconfigured: "degraded", provider_unavailable: "degraded", invalid: "failed", expired: "failed", disconnected: "failed" })[authStatus] || "failed";
}

function connectionHealthLabel(value) {
  return ({ not_installed: "Not installed", not_configured: "Not configured", detected: "Detected", verified: "Verified", degraded: "Degraded", failed: "Failed" })[value] || String(value || "Unknown").replaceAll("_", " ");
}

// The one vocabulary for a provider's auth state, and the only place it
// becomes words. The backend emits exactly these six values
// (opaihub/accounts.py, connection_for_account).
//
// Three places used to write this element with wording of their own: a derived
// "not connected", a fallback "needs attention", and the raw enum with its
// underscores swapped for spaces. The diagnostic sentence comes from the
// payload while the status line was written by whichever of those ran last, so
// one card could read "Sign-in verified locally; provider acceptance is
// confirmed" directly above "Status: not connected".
//
// `expired` is the value that matters most and was the least visible. When a
// token expires the run fails with a 401 that normalises to AUTH_EXPIRED, and
// the card has to say so: "Not verified" is a state you wait out, "Sign-in
// expired" is one you act on.
const AUTH_STATUS_LABEL = {
  connected: "Connected",
  unknown: "Not verified",
  not_configured: "Not connected",
  misconfigured: "CLI unavailable",
  invalid: "Sign-in rejected",
  expired: "Sign-in expired",
};

function authStatusLabel(value) {
  return AUTH_STATUS_LABEL[String(value || "").trim()] || AUTH_STATUS_LABEL.unknown;
}

function updateDoctorCard(provider, result) {
  const card = document.querySelector(`[data-doctor-provider="${CSS.escape(String(provider || ""))}"]`);
  if (!card) return;
  const healthValue = connectionHealth(result);
  const signedIn = healthValue === "verified";
  const status = card.querySelector(`[data-account-status="${CSS.escape(String(provider || ""))}"]`);
  const health = card.querySelector("[data-doctor-health]");
  const diagnostic = card.querySelector("[data-doctor-diagnostic]");
  if (status) status.textContent = authStatusLabel(signedIn ? "connected" : (result.authStatus || result.status));
  if (health) { health.textContent = connectionHealthLabel(healthValue); health.className = `doctor-health ${healthValue}`; }
  if (diagnostic) diagnostic.textContent = (result.connection && result.connection.safeDiagnostic) || result.safeDiagnostic || result.message || diagnostic.textContent;
  // Bug 4: the check just ran, so "Last checked" must reflect it in place — a
  // fresh timestamp from the result when present, otherwise "Just now" — instead
  // of staying "Never checked" until the page is re-rendered.
  const checked = card.querySelector("[data-doctor-last-checked]");
  if (checked) {
    const at = Number(result.lastCheckedAt || (result.connection && result.connection.lastCheckedAt));
    checked.textContent = at ? new Date(at).toLocaleString() : "Just now";
  }
  refreshDoctorSummary();
}

// #238: a default changed in Settings shows up in the composer immediately —
// same state, same renderers the composer's own selects use.
function applyDefaults(key, value) {
  if (key === "default_model") {
    const m = (state.boot.models || []).find((x) => x.id === value);
    if (m) state.model = { id: m.id, label: m.label, advancedLabel: m.advanced_label, kind: m.kind, provider: m.provider };
  } else if (key === "default_mode") {
    const md = (state.boot.modes || []).find((x) => x.id === value);
    if (md) state.mode = md;
  } else if (key === "default_task_mode") {
    state.focus = value;
  } else if (key === "default_output_format") {
    state.format = value;
  }
  renderComposerSelects(); refreshInspector(); refreshStatus();
}

// #237: keep the Providers page's one-line health summary true after live
// checks change a card. Wording comes from the same helper the render uses.
function refreshDoctorSummary() {
  const summary = document.querySelector("[data-doctor-summary]");
  if (!summary || !window.OPaiSettings) return;
  const healths = Array.from(document.querySelectorAll(".doctor-card [data-doctor-health]"))
    .map((el) => el.className.split(/\s+/)[1] || "");
  const next = window.OPaiSettings.doctorSummary(healths);
  summary.classList.toggle("ok", next.attention === 0);
  summary.classList.toggle("warn", next.attention > 0);
  const text = summary.querySelector(".doctor-summary-text");
  if (text) text.textContent = next.text;
}

function startGuidedProviderLogin(provider, { button = null, retryPayload = null, retryRequestId = null } = {}) {
  if (!provider || !bridge.startProviderLogin) { toast("Guided sign-in is unavailable"); return; }
  const requestId = `login-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const originalLabel = button ? button.textContent : "";
  providerLoginRequests.set(requestId, { provider, button, originalLabel, retryPayload, retryRequestId });
  if (button) { button.disabled = true; button.textContent = "Waiting for sign-in…"; }
  try {
    bridge.startProviderLogin(provider, requestId);
  } catch (_e) {
    providerLoginRequests.delete(requestId);
    if (button) { button.disabled = false; button.textContent = originalLabel; }
    toast("Could not launch the sign-in window");
  }
}

function onProviderLoginReady(json) {
  let envelope = {};
  try { envelope = JSON.parse(json || "{}"); } catch (_e) { return; }
  const pending = providerLoginRequests.get(envelope.requestId);
  if (!pending) return;
  providerLoginRequests.delete(envelope.requestId);
  const result = envelope.result || {};
  if (pending.button && pending.button.isConnected) {
    pending.button.disabled = false;
    pending.button.textContent = pending.originalLabel;
  }
  updateDoctorCard(pending.provider, result);
  if (!result.signedIn) { toast(result.message || "Sign-in was not verified"); return; }
  toast(result.message || `${providerName(pending.provider)} sign-in verified`);
  if (pending.retryPayload && !state.busy && (!pending.retryRequestId || (state.message && state.message.requestId === pending.retryRequestId))) sendSelection(pending.retryPayload);
}

function onConnectionDoctorReady(json) {
  let envelope = {};
  try { envelope = JSON.parse(json || "{}"); } catch (_e) { return; }
  if (!envelope.requestId || envelope.requestId !== doctorRefreshRequestId) return;
  (envelope.entries || []).forEach((item) => {
    const card = document.querySelector(`[data-doctor-provider="${CSS.escape(String(item.providerId || ""))}"]`);
    const version = card && card.querySelector("[data-doctor-cli]");
    if (version && item.cliVersion) version.textContent = item.cliVersion;
  });
}

function renderErrorCard(el, status, r, sel) {
  const error = r && r.error && typeof r.error === "object" ? r.error : {};
  // Awaiting-input cards already provide the exact action that can unblock the
  // run. A generic retry only reproduces the same gate and makes the safest
  // path harder to recognize.
  const canRetry = ![
    "needs_model",
    "needs_free_confirmation",
    "needs_auto_confirmation",
    "needs_limit_confirmation",
  ].includes(status);
  const title = error.title || ERROR_TITLES[status] || "OPai could not complete this request.";
  const what = error.userMessage || (typeof (r && r.answer) === "string" && r.answer) || "Retry, or open Settings if the problem continues.";
  const raw = redactSecrets(
    error.technicalMessage ||
    (typeof (r && r.error) === "string" ? r.error : "") ||
    (r && r.raw_result ? JSON.stringify(r.raw_result) : "")
  );
  const actions = error.recoveryActions || ["retry", "open_settings", "show_details"];
  const loginProvider = error.provider || (sel && sel.modelProvider) || "";
  const offerLogin = ["AUTH_MISSING", "AUTH_INVALID", "AUTH_EXPIRED"].includes(String(error.code || "")) && !!loginProvider;
  // Free-tier consent card (replaces the old native confirm popup): confirm to
  // send to the provider's public API with the same explicit warning text.
  // F29: name the provider from the RESULT's model id ("free:groq:…"), not the
  // composer selection — a stale/mismatched selection must never label a Groq
  // consent card "Send to Gemini".
  const resultModelId = String((r && (r.model_id || (r.raw_result && r.raw_result.model_id))) || "");
  const freeFromResult = resultModelId.startsWith("free:") ? resultModelId.split(":")[1] : "";
  const freeProvider = freeFromResult
    ? freeFromResult.charAt(0).toUpperCase() + freeFromResult.slice(1)
    : String((sel && sel.modelLabel) || "the provider").split(" · ")[0];
  // Never a dead end: when the engine could name a model that can still run
  // this request, offer it as the primary action. "Switch model" alone made the
  // user diagnose a routing problem OPai had already solved — the whole point
  // of OPai is that having usage somewhere is enough to keep working.
  // Suppressed on awaiting-input cards (`canRetry` is the same test): those
  // already carry the exact action that unblocks them, and offering a different
  // model there would read as a way around a safety gate.
  const offer = canRetry && r && r.fallback_offer && typeof r.fallback_offer === "object"
    ? r.fallback_offer
    : null;
  const offerId = offer ? String(offer.id || "") : "";
  const offerLabel = offer ? String(offer.label || offerId) : "";
  const showOffer = !!offerId && offerId !== String((sel && sel.model) || "");
  // Route transparency. When OPai deliberately declines to reroute — an
  // irreversible request in the governed lane — the absence of a "Continue
  // with" button is a decision, not the dead end this release spent its time
  // removing. Say so, or it reads as the same old failure.
  const lane = r && r.message_contract && typeof r.message_contract === "object"
    ? r.message_contract
    : null;
  const heldLane = lane && lane.allowProviderFallback === false
    ? `OPai will not move this request to another model on its own — ${String(lane.reason || "it cannot be safely repeated")} Choose a model yourself to continue.`
    : "";
  // Keep the activity evidence reviewable after a failure while retaining the
  // structured provider recovery actions from the shared message contract.
  el.innerHTML = roleHeader(sel && sel.build ? "OPai Build" : "OPai", "var(--red)") + activitySummaryHtml() +
    `<div class="error-card" role="alert"><div class="ec-t">${esc(title)}</div><div class="ec-w">${esc(what)}</div>` +
    (heldLane ? `<div class="ec-w" data-lane-note>${esc(heldLane)}</div>` : "") +
    `<div class="ec-actions">` +
    (showOffer ? `<button class="btn primary" data-a="continue-with">Continue with ${esc(offerLabel)}</button>` : "") +
    (canRetry ? `<button class="btn" data-a="retry">Retry</button>` : "") +
    (actions.includes("repair_config") ? `<button class="btn primary" data-a="repair">Repair Codex config</button>` : "") +
    (offerLogin ? `<button class="btn primary" data-a="signin">Sign in to ${esc(providerName(loginProvider))}</button>` : "") +
    // A live re-check, not just a link to Settings: OPai may have said
    // "connected" from a cached/on-disk signal right before this exact call
    // 401'd — "Open Settings" alone showed nothing new. This runs the same
    // check right here and reports the truth, plus the concrete next step.
    (actions.includes("reconnect") ? `<button class="btn" data-a="reconnect">Test connection</button>` : "") +
    // A stale local session (detected but no longer valid server-side) can
    // pass every local check yet keep 401ing forever — "Retry" alone cannot
    // fix that. Disconnect forces a genuine sign-out via the provider's own
    // CLI so the next sign-in starts clean.
    (actions.includes("disconnect") ? `<button class="btn" data-a="disconnect">Disconnect account</button>` : "") +
    (status === "needs_free_confirmation" ? `<button class="btn primary" data-a="free">Send to ${esc(freeProvider)}</button>` : "") +
    (status === "needs_auto_confirmation" ? `<button class="btn primary" data-a="fallback">Confirm ${esc(r.fallbackModelLabel || "cloud fallback")}</button>` : "") +
    (status === "needs_limit_confirmation" ? `<button class="btn primary" data-a="limit">Continue past limit</button>` : "") +
    (actions.includes("open_settings") || actions.includes("reconnect") ? `<button class="btn" data-a="settings">Open Settings</button>` : "") +
    `<button class="btn" data-a="switch">Switch model</button>` +
    (raw ? `<button class="btn ghost" data-a="details">Show technical details</button><button class="btn ghost" data-a="copy">Copy details</button>` : "") + `</div>` +
    (raw ? `<details class="ec-details"><summary>Show details</summary><pre>${esc(raw.slice(0, 1500))}</pre></details>` : "") + `</div>`;
  wireActivitySummary(el);
  const continueWith = el.querySelector('[data-a="continue-with"]'); if (continueWith) continueWith.onclick = () => {
    // Make the switch visible before re-sending, so the composer never
    // disagrees with the model that is actually about to run. The saved
    // default is deliberately left alone: this recovers one request, it does
    // not silently rewrite the user's preferred model.
    const entry = (state.boot.models || []).find((x) => x.id === offerId);
    if (entry) state.model = { ...entry, advancedLabel: entry.advanced_label };
    else state.model = { id: offerId, label: offerLabel, kind: String(offer.kind || ""), provider: String(offer.provider || "") };
    renderComposerSelects(); refreshInspector(); refreshStatus();
    if (window.OPaiComposer) window.OPaiComposer.refresh();
    const payload = {
      ...(state.lastSend || sel || {}),
      model: state.model.id,
      modelKind: state.model.kind,
      modelLabel: state.model.label,
      modelProvider: state.model.provider,
    };
    // A consent the user gave for the previous route does not transfer. The new
    // model passes its own cloud/limit gates (PR #511) — this button changes
    // which model runs, never what it is allowed to do.
    delete payload.allowCloud;
    delete payload.allowLimit;
    toast(`Continuing with ${state.model.label}`);
    sendSelection(payload);
  };
  const retryButton = el.querySelector('[data-a="retry"]'); if (retryButton) retryButton.onclick = () => retry();
  const signIn = el.querySelector('[data-a="signin"]'); if (signIn) signIn.onclick = () => {
    const retryPayload = state.lastSend ? { ...state.lastSend } : (sel ? { ...sel } : null);
    startGuidedProviderLogin(loginProvider, { button: signIn, retryPayload, retryRequestId: state.lastFailedRequestId });
  };
  const repair = el.querySelector('[data-a="repair"]'); if (repair) repair.onclick = () => {
    // One-click Codex config repair (backs up first, removes only the invalid
    // line), then retry the message — no hunting through Settings.
    repair.disabled = true; repair.textContent = "Repairing…";
    const done = (res) => {
      let ok = false;
      try { ok = !!(JSON.parse(res) || {}).repaired; } catch (_e) { ok = false; }
      if (ok) { toast("Codex config repaired — retrying"); retry(); }
      else { repair.disabled = false; repair.textContent = "Repair Codex config"; toast("Repair failed — open Settings"); }
    };
    if (bridge.repairCodexConfig) { try { bridge.repairCodexConfig(done); } catch (_e) { done("{}"); } }
    else { done("{}"); }
  };
  const reconnect = el.querySelector('[data-a="reconnect"]'); if (reconnect) reconnect.onclick = () => {
    const provider = error.provider || (sel && sel.modelProvider) || "";
    if (!provider || !bridge.testProvider) { switchView("settings"); return; }
    reconnect.disabled = true; reconnect.textContent = "Testing…";
    bridge.testProvider(provider, (json2) => {
      let result = {}; try { result = JSON.parse(json2); } catch (_e) { /* keep {} */ }
      reconnect.disabled = false; reconnect.textContent = "Test connection";
      if (result.authStatus === "connected") { toast("Connection verified — Retry should work now"); return; }
      const hint = result.loginHint ? " " + result.loginHint : "";
      toast((result.safeDiagnostic || "Still not connected.") + hint);
    });
  };
  const disconnect = el.querySelector('[data-a="disconnect"]'); if (disconnect) disconnect.onclick = () => {
    const provider = error.provider || (sel && sel.modelProvider) || "";
    if (!provider || !bridge.disconnectAccount) { switchView("settings"); return; }
    disconnect.disabled = true; disconnect.textContent = "Disconnecting…";
    bridge.disconnectAccount(provider, (json2) => {
      let result = {}; try { result = JSON.parse(json2); } catch (_e) { /* keep {} */ }
      disconnect.disabled = false; disconnect.textContent = "Disconnect account";
      toast(result.message || (result.disconnected ? "Signed out." : "Could not sign out."));
    });
  };
  const free = el.querySelector('[data-a="free"]'); if (free) free.onclick = () => {
    // Remember consent so this card never appears for this free model again
    // (persisted per workspace by the bridge — the user asked for at most one
    // confirmation, ever). In-memory Set is updated synchronously; persistence
    // is fire-and-forget so a bridge blip can't block the send.
    const id = (state.lastSend && state.lastSend.model) || (sel && sel.model);
    if (id) {
      if (state.freeConsent) state.freeConsent.add(id);
      if (bridge.grantFreeConsent) {
        try { bridge.grantFreeConsent(id, () => {}); } catch (_e) { /* ignore */ }
      }
    }
    sendSelection(Object.assign({}, state.lastSend || {}, { allowCloud: true }));
  };
  const fallback = el.querySelector('[data-a="fallback"]'); if (fallback) fallback.onclick = () => {
    // Preserve the reviewed route. Re-sending Auto here would recompute a
    // fallback after consent and could differ from the configured provider the
    // user was shown on the confirmation card.
    const fallbackModelId = String(r.fallbackModelId || "").trim();
    if (!fallbackModelId) { switchView("settings"); return; }
    sendSelection(Object.assign({}, state.lastSend || {}, {
      model: fallbackModelId,
      allowCloud: true,
    }));
  };
  const limit = el.querySelector('[data-a="limit"]'); if (limit) limit.onclick = () => {
    sendSelection(Object.assign({}, state.lastSend || {}, { allowLimit: true }));
  };
  const settings = el.querySelector('[data-a="settings"]'); if (settings) settings.onclick = () => switchView("settings");
  el.querySelector('[data-a="switch"]').onclick = () => openModelPicker();
  const details = el.querySelector('[data-a="details"]'); if (details) details.onclick = () => {
    const panel = el.querySelector(".ec-details"); if (panel) panel.open = !panel.open;
  };
  const cp = el.querySelector('[data-a="copy"]'); if (cp) cp.onclick = () => { copyText(raw); toast("Details copied"); };
}

function finalize(status, r) {
  flushTokenRender();
  const scrollSnapshot = captureChatScroll();
  try {
    stopTimer();
    stripFinalize(status, r); // reflect the terminal state before we rebuild the bubble
    const el = state.pending;
    if (!el) return;
    state.pending = null;
    const sel = state.lastSend || {};
    const durMs = Date.now() - state.startTime;
    if (status === "cancelled") {
    el.innerHTML = roleHeader("Stopped", "var(--muted)") +
      `<div class="stopped-card"><div class="sc-t">Generation stopped by you.</div>` +
      (r && r.answer && stripStopNote(r.answer).trim() ? `<div class="body">${mdToHtml(stripStopNote(r.answer))}</div>` : "") +
      `<div class="sc-sub">You can edit the prompt, retry, or switch model.</div>` +
      `<div class="sc-actions"><button class="btn" data-a="retry">Retry</button><button class="btn ghost" data-a="edit">Edit prompt</button></div></div>`;
    el.querySelector('[data-a="retry"]').onclick = () => retry();
    el.querySelector('[data-a="edit"]').onclick = () => { switchView("chat"); setComposerDraft(sel.text || "", { focus: true }); };
    return;
  }
    if (!ANSWERED.includes(status)) {
    // #295 gate 3: this submission was an exact duplicate of a run already in
    // flight (a retry pressed mid-run, or a replayed send after a reconnect).
    // The message is NOT lost — the live run is answering it — so the pending
    // bubble is removed rather than turned into an error the user would try to
    // debug. Starting a second run instead would double the spend and race two
    // sets of edits over the same files.
    if (status === "duplicate_request") {
      el.remove();
      return;
    }
    // F9/F17: a policy-blocked command gets an inline approval card, not an
    // error dead-end — the user can approve the exact command once or deny it.
    if (status === "needs_command_approval") {
      renderCommandApprovalCard(el, r, sel);
      return;
    }
    // F26: file edits refused by the provider's permission gate get the same
    // treatment — an actionable "Allow edits once" card, never a dead end.
    if (status === "needs_edit_approval") {
      renderEditApprovalCard(el, r, sel);
      return;
    }
    state.lastFailedRequestId = state.message && state.message.requestId;
    renderErrorCard(el, status, r, sel); return;
  }
  // A malformed payload (answer that isn't a string) must never coerce into
  // "[object Object]" in the chat — treat it as a clean error (BUG-QA-003).
    const rawAnswer = r && r.answer;
    if (rawAnswer != null && typeof rawAnswer !== "string") {
    state.lastFailedRequestId = state.message && state.message.requestId;
    renderErrorCard(el, "empty", { answer: "The model returned an unexpected response shape." }, sel);
    return;
  }
    const isProvider = sel.modelKind === "account" || sel.modelKind === "free";
  const label = isProvider
    ? String(sel.modelLabel).replace(" · ", " ").replace(/\s+\(free tier\)$/, "")
    : "OPai";
  const color = isProvider ? (PROVIDER_COLOR[sel.modelProvider] || "var(--ink)") : "var(--muted)";
  const answer = (typeof rawAnswer === "string" && rawAnswer) || state.streamedText || "OPai didn't return a response for that one.";
  const headerHtml = roleHeader(label, color, { copy: true });
  let changesHtml = "";
  let supportHtml = "";
  const changed = (r && r.changed_files) || [];
  const flow = (r && r.workflow) || {};
  const review = flow.diff_review || {};
  const flowFiles = review.files || [];
  if (flowFiles.length) {
    changesHtml += changesetCardHtml(review, flow.phase, diffStatusMap(changed));
  } else if (changed.length) {
    changesHtml += filesCardHtml(changed);
  }
  if (r && (r.workflow || r.agent_policy)) supportHtml += workflowCardHtml(r);
  const planSteps = (r && r.plan && r.plan.steps) || [];
  if (planSteps.length) supportHtml += planCardHtml(planSteps);
  el.innerHTML = assistantPresentationHtml(
    headerHtml,
    answer,
    r && r.presentation,
    {
      result: r,
      changesHtml,
      supportHtml,
      warningsHtml: unverifiedClaimHtml(r),
      legacyWorkHtml: activitySummaryHtml(),
      legacyFinalHtml: completionVerdictHtml(r),
      extraHtml: metaFooter(r, sel, durMs),
      retryable: Boolean(state.lastSend),
    },
  );
  wireAnswerCopy(el, answer);
  wireActivitySummary(el);
  wireFilesCard(el);
  wireReceipt(el, sel, r);
  wirePlanCard(el, sel);
  wireChangesetCard(el);
  wireStructuredEvidence(el);
  enhanceCodeBlocks(el);
    const cvRetry = el.querySelector('.completion-verdict [data-a="retry"]');
    if (cvRetry) cvRetry.onclick = () => retry();
  } finally {
    restoreChatScroll(scrollSnapshot);
  }
}

// Diff evidence, rendered as GitHub-style numbered lines. `bounded preview`
// rows mark where the backend truncated a hunk (opaihub/diff_review.py).
function diffHunkLinesHtml(hunk) {
  let o = Number(hunk.old_start) || 0;
  let n = Number(hunk.new_start) || 0;
  const blankRow = (text) => `<div class="dline note"><span class="dl-num dl-old"></span><span class="dl-num dl-new"></span><span class="dl-sign"></span><span class="dl-text">${esc(text)}</span></div>`;
  const rows = (hunk.lines || []).map((raw) => {
    const line = String(raw);
    if (line.startsWith("\\")) return blankRow(line);
    const marker = line.slice(0, 1);
    const text = line.slice(1);
    let cls = "ctx", sign = " ", oldn = "", newn = "";
    if (marker === "+") { cls = "add"; sign = "+"; newn = n++; }
    else if (marker === "-") { cls = "del"; sign = "−"; oldn = o++; }
    else { oldn = o++; newn = n++; }
    return `<div class="dline ${cls}"><span class="dl-num dl-old">${oldn}</span><span class="dl-num dl-new">${newn}</span><span class="dl-sign">${sign}</span><span class="dl-text">${esc(text) || "&nbsp;"}</span></div>`;
  }).join("");
  return rows + (hunk.truncated ? blankRow("… bounded preview") : "");
}

function diffHunksHtml(file) {
  const hunks = file.hunks || [];
  if (!hunks.length && file.sensitive) return '<div class="diff-empty sensitive">Sensitive diff content is hidden.</div>';
  if (!hunks.length) return '<div class="diff-empty">No textual hunk available.</div>';
  return hunks.map((h) => `<div class="diff-hunk">
      <div class="diff-hunk-head">@@ -${esc(h.old_start)},${esc(h.old_count)} +${esc(h.new_start)},${esc(h.new_count)} @@${h.heading ? " " + esc(h.heading) : ""}</div>
      <div class="diff-hunk-body">${diffHunkLinesHtml(h)}</div>
    </div>`).join("");
}

// git status --short prefixes (also stripped by cleanPath for the flat chip
// list) tell us Modified/Added/Deleted/Renamed far more reliably than
// guessing from hunk shape alone.
function diffStatusMap(changedFiles) {
  const map = {};
  (changedFiles || []).forEach((raw) => {
    const line = String(raw);
    const code = line.slice(0, 2);
    const path = cleanPath(line);
    if (!path) return;
    if (code.includes("D")) map[path] = "D";
    else if (code === "??" || code.includes("A")) map[path] = "A";
    else if (code.includes("R")) map[path] = "R";
    else map[path] = "M";
  });
  return map;
}
function diffFileLetter(file, statusMap) {
  return (statusMap && statusMap[file.path]) || (file.untracked ? "A" : "M");
}

function diffPathHtml(path) {
  const p = String(path || "");
  const idx = p.lastIndexOf("/");
  if (idx < 0) return `<span class="df-name">${esc(p)}</span>`;
  return `<span class="df-dir">${esc(p.slice(0, idx + 1))}</span><span class="df-name">${esc(p.slice(idx + 1))}</span>`;
}

// Decisions here only record human sign-off (opaihub/diff_review.py never
// mutates source files) — a rejected file blocks merge, it doesn't "skip"
// an edit that already happened. Keep the copy honest about that.
function diffDecisionCopy(info) {
  if (info.decision === "approved") return "Approved — cleared for merge";
  if (info.decision === "rejected") return "Rejected — blocks merge until resolved";
  if (info.risky) return info.riskReasons ? `Touches ${info.riskReasons} — review carefully` : "Review carefully before approving";
  return "Awaiting review";
}
function diffDecisionButtonsHtml(decision) {
  if (decision === "pending") {
    return `<button type="button" class="btn ghost" data-diff-decision="rejected">Reject</button><button type="button" class="btn primary" data-diff-decision="approved">${uiIcon("check")}Approve</button>`;
  }
  return `<button type="button" class="btn ghost" data-diff-decision="pending">Undo</button>`;
}

function diffFileCardHtml(file, opts) {
  const letter = opts.letter;
  const hasHunks = !!(file.hunks && file.hunks.length);
  const flag = letter === "A" ? '<span class="df-flag df-flag-new">new file</span>'
    : letter === "D" ? '<span class="df-flag df-flag-del">deleted</span>' : "";
  const riskReasons = (file.risk_reasons || []).join(", ");
  const risks = (file.risk_reasons || []).map((reason) => `<span class="diff-risk">${esc(reason)}</span>`).join("");
  const decision = file.decision || "pending";
  const actionsRow = opts.actionable
    ? `<div class="df-decision-row" data-df-decision-row>
        <span class="df-decision-label" data-df-decision-label>${esc(diffDecisionCopy({ decision, risky: file.risky, riskReasons }))}</span>
        <span class="spacer"></span>
        <span class="df-decision-actions" data-df-decision-actions>${diffDecisionButtonsHtml(decision)}</span>
      </div>`
    : "";
  return `<details class="diff-file2" data-diff-index="${opts.index}" data-diff-path="${esc(file.path)}" data-diff-decision-state="${esc(decision)}" data-diff-risky="${file.risky ? "1" : "0"}" data-diff-risk-reasons="${esc(riskReasons)}" ${opts.open ? "open" : ""}>
    <summary class="diff-file-summary">
      <span class="diff-chevron" aria-hidden="true">${uiIcon("chevronRight")}</span>
      <span class="df-type df-type-${letter}">${letter}</span>
      <span class="df-path">${diffPathHtml(file.path)}</span>
      ${flag}${risks}
      <span class="spacer"></span>
      <span class="df-stat df-add">+${esc(file.additions || 0)}</span>
      <span class="df-stat df-del">−${esc(file.deletions || 0)}</span>
      <span class="df-actions">
        <button type="button" class="df-icon" data-df-copy-path title="Copy path" aria-label="Copy path for ${esc(file.path)}">${uiIcon("copy")}</button>
        ${hasHunks ? `<button type="button" class="df-icon" data-df-copy-diff title="Copy diff" aria-label="Copy diff for ${esc(file.path)}">${uiIcon("copyDiff")}</button>` : ""}
        <button type="button" class="df-icon" data-df-open title="Open in editor" aria-label="Open ${esc(file.path)}">${uiIcon("file")}</button>
      </span>
    </summary>
    <div class="diff-hunks">${diffHunksHtml(file)}</div>
    ${actionsRow}
  </details>`;
}

// Everything OPai knows about a code change lives here — whether it's already
// on disk and verified, or held (via "Manual") for review before it
// can ship. Both states reuse the same evidence and row markup; only the
// "reviewing_diff" phase gets approve/reject actions.
function changesetCardHtml(review, phase, statusMap) {
  const files = (review && review.files) || [];
  if (!files.length) return "";
  const summary = review.summary || {};
  const actionable = phase === "reviewing_diff";
  const pending = files.filter((f) => (f.decision || "pending") === "pending").length;
  const badge = actionable
    ? `<span class="cs-badge cs-badge-amber">${uiIcon("pending")}Proposed</span>`
    : `<span class="cs-badge cs-badge-green">${uiIcon("check")}Applied</span>`;
  const bulk = actionable
    ? `<span class="cs-bulk-wrap">
        <button type="button" class="btn ghost" data-diff-bulk="rejected" ${pending ? "" : "hidden"}>Reject all</button>
        <button type="button" class="btn primary" data-diff-bulk="approved" ${pending ? "" : "hidden"}>${uiIcon("check")}Approve all</button>
        <span class="cs-reviewed" data-cs-reviewed ${pending ? "hidden" : ""}>All reviewed</span>
      </span>`
    : "";
  const reviewNote = actionable ? `<span class="cs-review-note" data-cs-review-note>${pending} of ${files.length} pending review</span>` : "";
  const evidenceFlags = `${summary.truncated ? '<span class="cs-evidence-flag">Diff truncated</span>' : ""}` +
    `${summary.risky ? `<span class="cs-evidence-flag">${esc(summary.risky)} risky</span>` : ""}`;
  const filesHtml = files.map((file, index) => diffFileCardHtml(file, {
    actionable,
    index,
    open: state.responseDensity === "detailed" || (state.responseDensity === "balanced" && index === 0),
    letter: diffFileLetter(file, statusMap),
  })).join("");
  return `<section class="changeset-card ${actionable ? "changeset-proposed" : "changeset-applied"}" aria-label="Code changes" data-diff-actionable="${actionable ? "1" : "0"}">
    <div class="cs-head">
      ${badge}
      ${evidenceFlags}
      <span class="cs-count">${files.length} file${files.length === 1 ? "" : "s"} changed</span>
      <span class="cs-stats">+${esc(summary.additions || 0)} −${esc(summary.deletions || 0)}</span>
      ${reviewNote}
      <span class="spacer"></span>
      ${bulk}
    </div>
    ${filesHtml}
  </section>`;
}

function wireChangesetCard(el) {
  const card = el.querySelector(".changeset-card");
  if (!card) return;
  const files = Array.from(card.querySelectorAll(".diff-file2"));

  // Reconstructed from the same bounded evidence that's on screen — never a
  // second source of truth, so "Copy diff" can never show something the user
  // didn't already see.
  const patchText = (file) => {
    const path = file.dataset.diffPath;
    const hunks = Array.from(file.querySelectorAll(".diff-hunk"));
    if (!hunks.length) return path;
    const body = hunks.map((hunk) => {
      const head = hunk.querySelector(".diff-hunk-head").textContent.trim();
      const lines = Array.from(hunk.querySelectorAll(".dline:not(.note)")).map((row) => {
        const prefix = row.classList.contains("add") ? "+" : row.classList.contains("del") ? "-" : " ";
        return prefix + row.querySelector(".dl-text").textContent.replace(/ /g, "");
      });
      return [head, ...lines].join("\n");
    }).join("\n");
    return `--- a/${path}\n+++ b/${path}\n${body}`;
  };

  files.forEach((file) => {
    const path = file.dataset.diffPath;
    const copyPathBtn = file.querySelector("[data-df-copy-path]");
    if (copyPathBtn) copyPathBtn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); copyText(path); toast("Copied " + path); };
    const copyDiffBtn = file.querySelector("[data-df-copy-diff]");
    if (copyDiffBtn) copyDiffBtn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); copyText(patchText(file)); toast("Diff copied to clipboard"); };
    const openBtn = file.querySelector("[data-df-open]");
    if (openBtn) openBtn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); if (bridge.openPath) bridge.openPath(path); };
  });

  if (card.dataset.diffActionable !== "1" || !bridge.reviewDiff) return;

  const updateHeader = () => {
    const pendingCount = files.filter((f) => f.dataset.diffDecisionState === "pending").length;
    const note = card.querySelector("[data-cs-review-note]");
    if (note) note.textContent = pendingCount ? `${pendingCount} of ${files.length} pending review` : "All reviewed";
    card.querySelectorAll("[data-diff-bulk]").forEach((b) => { b.hidden = pendingCount === 0; });
    const reviewed = card.querySelector("[data-cs-reviewed]");
    if (reviewed) reviewed.hidden = pendingCount !== 0;
  };

  const setDecision = (file, decision, done) => {
    bridge.reviewDiff(file.dataset.diffPath, decision, (raw) => {
      let result = {};
      try { result = JSON.parse(raw || "{}"); } catch (_e) { result = {}; }
      if (!result.ok) { toast("Could not save the diff decision"); done(false); return; }
      file.dataset.diffDecisionState = decision;
      const label = file.querySelector("[data-df-decision-label]");
      if (label) {
        label.textContent = diffDecisionCopy({
          decision, risky: file.dataset.diffRisky === "1", riskReasons: file.dataset.diffRiskReasons || "",
        });
      }
      const actions = file.querySelector("[data-df-decision-actions]");
      if (actions) actions.innerHTML = diffDecisionButtonsHtml(decision);
      done(true);
    });
  };

  card.addEventListener("click", (e) => {
    const single = e.target.closest("[data-diff-decision]");
    if (single && card.contains(single)) {
      e.preventDefault(); e.stopPropagation();
      const file = single.closest(".diff-file2");
      const decision = single.dataset.diffDecision;
      single.disabled = true;
      setDecision(file, decision, (ok) => {
        updateHeader();
        if (ok) { refreshInspector(); toast(`Marked ${file.dataset.diffPath} ${decision}`); }
      });
      return;
    }
    const bulk = e.target.closest("[data-diff-bulk]");
    if (bulk && card.contains(bulk)) {
      e.preventDefault(); e.stopPropagation();
      const decision = bulk.dataset.diffBulk;
      const pendingFiles = files.filter((f) => f.dataset.diffDecisionState === "pending");
      if (!pendingFiles.length) return;
      bulk.disabled = true;
      let remaining = pendingFiles.length;
      pendingFiles.forEach((file) => setDecision(file, decision, () => {
        remaining -= 1;
        if (remaining === 0) {
          bulk.disabled = false;
          updateHeader();
          refreshInspector();
          toast(decision === "approved" ? "Approved all pending files" : "Rejected all pending files");
        }
      }));
    }
  });
}

function wireStructuredEvidence(el) {
  el.querySelectorAll("[data-copy-command]").forEach((button) => {
    button.onclick = () => {
      const command = button.closest(".verification-command");
      const code = command && command.querySelector("code");
      if (!code) return;
      copyText(code.textContent);
      toast("Command copied to clipboard");
    };
  });
}

function workflowCardHtml(result) {
  const flow = result.workflow || {};
  const policy = result.agent_policy || {};
  const mode = policy.label || policy.mode || flow.mode || "—";
  const pretty = (value) => String(value || "—").replaceAll("_", " ");
  const title = (value) => { const text = pretty(value); return text.charAt(0).toUpperCase() + text.slice(1); };
  const verdict = completionVerdict(result);
  const outcomeOverridesRuntime = verdict && verdict.verdict !== "completed";
  // A workflow's persisted phase remains useful internal provenance, but the
  // summary directly under the answer must say whether the objective was met.
  // The same verdict powers the pill and receipt, so these signals cannot drift.
  const displayedPhase = verdict ? completionVerdictLabel(verdict) : title(flow.phase);
  const displayedMessage = verdict && verdict.reason ? verdict.reason : flow.message;
  const displayedActions = verdict && verdict.nextAction
    ? [verdict.nextAction]
    : (flow.next_actions || []);
  const actions = displayedActions.map((item) => `<li>${esc(item)}</li>`).join("");
  const hasStructuredHistory = result.presentation && Array.isArray(result.presentation.activity) && result.presentation.activity.length;
  const history = hasStructuredHistory ? "" : (flow.history || []).slice(-5).map((item) => {
    // Do not leave a hidden contradictory "Completed" terminal state in the
    // expandable timeline when the outcome verdict is non-completed.
    const phase = outcomeOverridesRuntime && String(item.phase || "").toLowerCase() === "completed"
      ? displayedPhase
      : title(item.phase);
    const message = outcomeOverridesRuntime && String(item.phase || "").toLowerCase() === "completed"
      ? displayedMessage
      : item.message || "";
    return `<div class="wf-event"><span>${esc(phase)}</span><small>${esc(message)}</small></div>`;
  }
  ).join("");
  const provider = flow.provider || {};
  const cost = flow.cost || {};
  return `<div class="workflow-card">
    <div class="wf-head"><span>${esc(mode)}</span><span>${esc(displayedPhase)}</span></div>
    ${displayedMessage ? `<div class="wf-message">${esc(displayedMessage)}</div>` : ""}
    <div class="wf-row"><span>PR</span><strong>${esc(flow.pr_url || "not opened")}</strong></div>
    <div class="wf-row"><span>Merge</span><strong>${esc(pretty(flow.merge_status))}</strong></div>
    ${flow.issue_number ? `<div class="wf-row"><span>Issue</span><strong>#${esc(flow.issue_number)}</strong></div>` : ""}
    ${provider.model ? `<div class="wf-row"><span>Provider</span><strong>${esc(provider.model)}</strong></div>` : ""}
    ${cost.estimated_actual_usd != null ? `<div class="wf-row"><span>Cost</span><strong>$${esc(Number(cost.estimated_actual_usd).toFixed(4))}</strong></div>` : ""}
    ${actions ? `<div class="wf-subhead">Next actions</div><ul class="wf-actions">${actions}</ul>` : ""}
    ${history ? `<details class="wf-history"${state.responseDensity === "detailed" ? " open" : ""}><summary>Timeline · ${(flow.history || []).length} events</summary>${history}</details>` : ""}
  </div>`;
}

// The Plan Editor (#130): steps parsed from the REAL plan-mode answer become
// an editable checklist. Building sends the kept steps back through the real
// pipeline in Safe Auto — nothing here fakes execution.
function planCardHtml(steps) {
  const rows = steps.map((s, i) =>
    `<label class="plan-step"><input type="checkbox" checked data-step="${i}"><span>${esc(s)}</span></label>`
  ).join("");
  return `<div class="plan-card" role="group" aria-label="Plan steps">
    <div class="pc-head"><span class="pc-badge">Plan · ${steps.length} steps</span>
    <span class="pc-hint">Untick anything you don't want built</span></div>
    ${rows}
    <div class="pc-actions">
      <button class="btn primary" data-plan="build">Build this plan</button>
      <span class="pc-note">runs in Auto — edits gated by the usual approvals</span>
    </div></div>`;
}
function wirePlanCard(el, sel) {
  const card = el.querySelector(".plan-card");
  if (!card) return;
  const build = card.querySelector('[data-plan="build"]');
  const refresh = () => {
    build.disabled = card.querySelectorAll("input:checked").length === 0;
  };
  card.querySelectorAll("input[type=checkbox]").forEach((c) => (c.onchange = refresh));
  build.onclick = () => {
    const kept = Array.from(card.querySelectorAll("label.plan-step"))
      .filter((l) => l.querySelector("input").checked)
      .map((l) => l.querySelector("span").textContent.trim());
    if (!kept.length) return;
    build.disabled = true;
    // Reflect the mode switch honestly in the composer controls.
    const safe = (state.boot.modes || []).find((m) => m.id === "safe-auto");
    if (safe) { state.mode = safe; const ms = $("#modeSel"); if (ms) ms.value = "safe-auto"; }
    const text = "Implement this plan, in order. Stop and ask if a step becomes impossible:\n" +
      kept.map((s, i) => `${i + 1}. ${s}`).join("\n");
    // Through the normal composer path: user bubble, recents, live activity.
    setComposerDraft(text);
    send();
  };
}

// A changed file becomes a clickable chip that opens it in the OS file manager;
// the git status prefix (e.g. " M ") is stripped to a plain relative path.
function cleanPath(f) { return String(f).replace(/^[ \t]*[A-Z?!]{1,2}[ \t]+/, "").trim(); }
function filesCardHtml(changed) {
  const rows = changed.map((f) => {
    const p = cleanPath(f);
    return `<button class="file-chip" data-file="${esc(p)}"><span class="fc-ico">${uiIcon("file")}</span><span class="fc-name">${esc(f)}</span><span class="fc-open">Open</span></button>`;
  }).join("");
  return `<div class="files-card"><div class="fc-head"><div class="fc-t">${changed.length} file(s) changed</div>` +
    `<button class="btn ghost" data-openfolder="1">Open folder</button></div>${rows}</div>`;
}
function wireFilesCard(el) {
  el.querySelectorAll(".file-chip").forEach((b) => (b.onclick = () => { if (bridge.openPath) bridge.openPath(b.dataset.file); }));
  const of = el.querySelector("[data-openfolder]");
  if (of) of.onclick = () => { if (bridge.openPath) bridge.openPath(""); };
}

function onReply(json) {
  const d = JSON.parse(json);
  if (!OPaiMessageState.canApply(state.message, d.requestId)) return; // stale reply ignored
  const backendStatus = (d.result && d.result.status) || "failed";
  // #402: the completion verdict, when present, is the honest terminal truth —
  // hand it to the state machine so a partial/blocked/timeout run is not
  // collapsed into "failed".
  state.message = OPaiMessageState.transition(
    state.message,
    OPaiMessageState.fromBackendStatus(
      backendStatus,
      d.result && d.result.completion_verdict,
      d.result && d.result.run_result,
    ),
  );
  state.currentRequest = null;
  setBusy(false);
  finalize(backendStatus, d.result || {});
  maybeFlushQueued(d.result || {});
  // The backend archives the conversation as the turn finishes, so the sidebar
  // is re-read here rather than guessed at from the client's own state.
  refreshConversations();
  refreshStatus(); refreshInspector(); refreshWorkspaceBadge();
}

// The header's "N uncommitted" badge came from the boot payload and was never
// recomputed, so it kept showing the startup count after a run committed files
// (Round 2). Re-read the real git state whenever a turn ends. Best-effort: an
// older host without the slot, or a malformed reply, leaves the badge as-is
// rather than blanking a branch name we can no longer verify.
function refreshWorkspaceBadge() {
  if (!bridge || (!bridge.workspaceState && !bridge.requestWorkspace)) return;
  if (bridge.requestWorkspace && bridge.workspaceReady) {
    state.workspaceRequest = `workspace-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    bridge.requestWorkspace(state.workspaceRequest);
  } else {
    bridge.workspaceState((json) => {
      let ws = null;
      try { ws = JSON.parse(json); } catch (_e) { return; }
      applyWorkspaceRefresh(ws);
    });
  }
}
function applyWorkspaceRefresh(ws) {
  if (!ws || typeof ws !== "object" || typeof ws.root !== "string") return;
  state.boot.workspace = { ...(state.boot.workspace || {}), ...ws };
  renderWorkspace();
}
function onWorkspaceReady(json) {
  let d = {}; try { d = JSON.parse(json); } catch (_e) { return; }
  if (d.requestId !== state.workspaceRequest) return;
  applyWorkspaceRefresh(d.data);
}

function setBusy(on) {
  state.busy = on;
  document.body.classList.toggle("ai-working", on);
  const s = $("#send");
  s.textContent = on ? "Stop" : ((state.buildMode && state.buildApp) ? "Build" : "Send");
  s.classList.toggle("stop", on);
  s.setAttribute("aria-label", on ? "Stop generation" : "Send prompt");
  // Clearing the sent draft disables Send just before the request starts.
  // Re-evaluate availability in both directions so the same control becomes
  // an enabled Stop button while a request is active.
  updateComposerAvailability();
  if (window.OPaiComposer) window.OPaiComposer.refresh();
  updateInspectorLive(on ? "Preparing request…" : null);
}

/* ---------- dashboards ---------- */
function renderDashboard(section) {
  const page = $("#dashPage");
  renderViewState(page, {
    kind: "loading",
    title: "Loading dashboard",
    reason: "Waiting for locally prepared dashboard data.",
  });
  const paint = (json) => {
    let s = {};
    try { s = JSON.parse(json); } catch (_e) {
      renderViewState(page, {
        kind: "error",
        title: "Couldn't load this dashboard",
        reason: "OPai received an invalid local dashboard response.",
        action: "retry_dashboard",
        actionLabel: "Try again",
      }, () => renderDashboard(section));
      return;
    }
    if (!s || typeof s !== "object" || Array.isArray(s)) {
      renderViewState(page, {
        kind: "error",
        title: "Couldn't load this dashboard",
        reason: "OPai received an invalid local dashboard response.",
        action: "retry_dashboard",
        actionLabel: "Try again",
      }, () => renderDashboard(section));
      return;
    }
    if (s.error) {
      renderViewState(page, {
        kind: "error",
        title: "Couldn't load this dashboard",
        reason: safeStateReason(s.error, "Dashboard data is temporarily unavailable."),
        action: "retry_dashboard",
        actionLabel: "Try again",
      }, () => renderDashboard(section));
      return;
    }
    if (s.degraded) {
      renderViewState(page, {
        kind: "degraded",
        title: "Dashboard is temporarily unavailable",
        reason: safeStateReason(s.degraded, "Fresh dashboard data is temporarily unavailable."),
        action: "open_chat",
        actionLabel: "Open chat",
      }, () => switchView("chat"));
      return;
    }
    if (!s.hero && !(s.kpis || []).length && !(s.cards || []).length && !(s.actions || []).length) {
      renderViewState(page, {
        kind: "empty",
        title: s.title || "No dashboard data yet",
        reason: s.subtitle || "Run a task to give this dashboard something to show.",
        action: "open_chat",
        actionLabel: "Open chat",
      }, () => switchView("chat"));
      return;
    }
    let h = `<div class="page-title">${esc(s.title || section)}</div>`;
    if (s.subtitle) h += `<div class="page-sub">${esc(s.subtitle)}</div>`;
    if (s.hero) h += `<div class="hero"><div class="num" style="color:${sevColor(s.hero.severity)}">${esc(s.hero.headline)}</div><div class="cap">${esc(s.hero.caption || "")}</div></div>`;
    if (s.kpis && s.kpis.length) {
      h += `<div class="kpis">` + s.kpis.map((k) => `<div class="kpi"><div class="l">${esc(k.label)}</div><div class="v" style="color:${sevColor(k.severity)}">${esc(k.value)}</div>${k.description ? `<div class="d">${esc(k.description)}</div>` : ""}</div>`).join("") + `</div>`;
    }
    (s.cards || []).forEach((c) => {
      h += `<div class="card"><div class="ch"><div class="ct">${esc(c.title || "")}</div>${c.status ? `<span class="pill ${c.severity || "neutral"}">${esc(c.status)}</span>` : ""}</div>`;
      if (c.body) h += `<div class="cb">${esc(c.body)}</div>`;
      (c.items || []).forEach((it) => (h += `<div class="cb">• ${esc(it)}</div>`));
      if (c.metrics && c.metrics.length) {
        h += `<div class="card-metrics">` + c.metrics.map((m) => `<div class="card-metric"><span>${esc(m.label)}</span><strong style="color:${sevColor(m.severity)}">${esc(m.value)}</strong></div>`).join("") + `</div>`;
      }
      if (c.command) h += `<div class="cmd">${esc(c.command)}</div>`;
      if (c.footnote) h += `<div class="cf">${esc(c.footnote)}</div>`;
      h += `</div>`;
    });
    if (s.actions && s.actions.length) {
      h += `<div class="actions">` + s.actions.map((a) => `<button class="btn ${a.variant === "primary" ? "primary" : ""}" data-aid="${esc(a.id)}" data-cmd="${esc(a.command || "")}">${esc(a.label)}</button>`).join("") + `</div>`;
    }
    page.innerHTML = h;
    $$("#dashPage .btn").forEach((b) => (b.onclick = () => runAction(b.dataset.aid, b.dataset.cmd)));
  };
  // #146: prefer the async path — the heavy repo/ledger walk happens on a
  // worker thread and arrives via dashboardReady; stale responses are dropped.
  if (bridge.requestDashboard && bridge.dashboardReady) {
    state.dashRequest = `dash-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    state.dashPaint = paint;
    bridge.requestDashboard(section, state.dashRequest);
  } else {
    bridge.dashboard(section, paint);
  }
}
function onDashboardReady(json) {
  let d = {}; try { d = JSON.parse(json); } catch (_e) {
    if (state.dashPaint) state.dashPaint("");
    return;
  }
  if (!state.dashPaint || !d || typeof d !== "object" || Array.isArray(d) || d.requestId !== state.dashRequest) return; // stale
  if (!Object.prototype.hasOwnProperty.call(d, "data") || !d.data || typeof d.data !== "object" || Array.isArray(d.data)) {
    state.dashPaint("");
    return;
  }
  state.dashPaint(JSON.stringify(d.data));
}
function runAction(aid, cmd) {
  if (aid === "panic_toggle") { switchView("chat"); bridge.runTool("panic"); return; }
  if (aid === "safe_repair") { switchView("chat"); bridge.runTool("repair"); return; }
  if (aid === "cleanup_preview") { switchView("chat"); bridge.runTool("context_preview"); return; }
  if (aid === "generate_ignores") { switchView("chat"); bridge.runTool("ignores"); return; }
  if (aid === "benchmark_run") { switchView("chat"); bridge.runTool("benchmark_run"); return; }
  if (aid === "benchmark_gate") { switchView("chat"); bridge.runTool("benchmark"); return; }
  if (aid === "export_proof_json") { switchView("chat"); bridge.runTool("proof_json"); return; }
  if (aid === "export_proof_markdown") { switchView("chat"); bridge.runTool("proof_markdown"); return; }
  if (cmd) { copyText(cmd); toast("Copied: " + cmd); }
  else toast("Run it from your terminal.");
}
function sevColor(s) {
  return { success: "var(--green)", warning: "var(--amber)", danger: "var(--red)", accent: "var(--accent)", info: "var(--blue)", neutral: "var(--muted)" }[s] || "var(--ink)";
}

/* ---------- prompts ---------- */
function loadPrompts() {
  const q = $("#promptSearch").value, c = $("#promptCat").value || "";
  bridge.prompts(q, c, (json) => {
    const data = JSON.parse(json);
    if (!$("#promptCat").dataset.init) {
      $("#promptCat").innerHTML = `<option value="">All categories</option>` + data.categories.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
      $("#promptCat").dataset.init = "1";
    }
    const list = $("#promptList");
    if (!data.prompts.length) { list.innerHTML = `<div class="page-sub">No prompts match.</div>`; return; }
    list.innerHTML = data.prompts.map((p) => `
      <div class="card prompt-card"><div class="ch"><div class="ct">${esc(p.title)}</div><span class="pill info">${esc(p.category)}</span></div>
      <div class="cb">${esc(p.desc)}</div>
      <button class="btn use" data-id="${esc(p.id)}">Use prompt</button></div>`).join("");
    $$("#promptList .use").forEach((b) => (b.onclick = () => usePrompt(b.dataset.id)));
  });
}
function usePrompt(id) {
  bridge.usePrompt(id, (json) => {
    const p = JSON.parse(json);
    if (!p.id) return;
    if (p.mode) { state.focus = p.mode; bridge.savePref("default_task_mode", p.mode); }
    switchView("chat"); setComposerDraft(p.template, { focus: true }); refreshInspector();
  });
}

/* ---------- settings ---------- */
// Settings render, search, and section wiring live in settings.js (#236); this
// keeps only the data fetch (async on the worker thread, #146) and the shared
// dependency bundle the section registry needs.
function settingsCtx(d) {
  return {
    d, bridge, state, esc, toast, inlineConfirm, switchView,
    refresh: renderSettings, updateDoctorCard, providerName,
    startGuidedProviderLogin, connectionHealthLabel, authStatusLabel,
    renderComposerSelects,
    applyAppearance, applyDefaults, applyClearedHistory,
    replayTour: () => { if (window.OPaiOnboarding) window.OPaiOnboarding.replay(onboardingCtx()); },
    startDoctorRefresh() {
      if (bridge.refreshConnectionDoctor) {
        doctorRefreshRequestId = `doctor-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        bridge.refreshConnectionDoctor(doctorRefreshRequestId);
      }
    },
  };
}
function renderSettings() {
  const page = $("#settingsPage");
  renderViewState(page, {
    kind: "loading",
    title: "Loading settings",
    reason: "Checking local preferences and connections.",
  });
  const paint = (json) => {
    let d = {};
    try { d = JSON.parse(json); } catch (_e) {
      renderViewState(page, {
        kind: "error",
        title: "Couldn't load settings",
        reason: "OPai received an invalid local settings response.",
        action: "retry_settings",
        actionLabel: "Try again",
      }, renderSettings);
      return;
    }
    if (!d || typeof d !== "object" || Array.isArray(d)) {
      renderViewState(page, {
        kind: "error",
        title: "Couldn't load settings",
        reason: "OPai received an invalid local settings response.",
        action: "retry_settings",
        actionLabel: "Try again",
      }, renderSettings);
      return;
    }
    if (d.error) {
      renderViewState(page, {
        kind: "error",
        title: "Couldn't load settings",
        reason: safeStateReason(d.error, "Settings data is temporarily unavailable."),
        action: "retry_settings",
        actionLabel: "Try again",
      }, renderSettings);
      return;
    }
    if (d.degraded) {
      renderViewState(page, {
        kind: "degraded",
        title: "Settings are temporarily unavailable",
        reason: safeStateReason(d.degraded, "Fresh settings data is temporarily unavailable."),
        action: "retry_settings",
        actionLabel: "Try again",
      }, renderSettings);
      return;
    }
    window.OPaiSettings.render(page, settingsCtx(d));
  };
  // #146: prefer the async path — doctor/credential/usage aggregation happens
  // on a worker thread and arrives via settingsReady; stale responses dropped.
  if (bridge.requestSettings && bridge.settingsReady) {
    state.settingsRequest = `settings-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    state.settingsPaint = paint;
    bridge.requestSettings(state.settingsRequest);
  } else {
    bridge.settingsData(paint);
  }
}
function onSettingsReady(json) {
  let d = {}; try { d = JSON.parse(json); } catch (_e) {
    if (state.settingsPaint) state.settingsPaint("");
    return;
  }
  if (!state.settingsPaint || !d || typeof d !== "object" || Array.isArray(d) || d.requestId !== state.settingsRequest) return; // stale
  if (!Object.prototype.hasOwnProperty.call(d, "data") || !d.data || typeof d.data !== "object" || Array.isArray(d.data)) {
    state.settingsPaint("");
    return;
  }
  state.settingsPaint(JSON.stringify(d.data));
}

/* ---------- tools ---------- */
function onTool(json) {
  setBusy(false);
  const r = JSON.parse(json);
  if (r.needs_confirm) {
    renderApprovalCard(r);
    return;
  }
  appendCard(r.title || "Tool", r.text || "");
}

// What each mutating action actually touches — honest scope labels only
// (config-level toggles vs. anything unknown). No invented risk theater.
const APPROVAL_SCOPE = {
  panic: { risk: "Config change", scope: "Routing policy for this project (reversible)" },
  repair: { risk: "Config change", scope: "OPai client integration files (additive, no source deleted)" },
  ignores: { risk: "Config change", scope: "Supported AI ignore files (additive; user rules preserved)" },
  benchmark_run: { risk: "Local evidence write", scope: ".opaihub benchmark history (privacy-safe metadata; no raw prompts)" },
  proof_json: { risk: "Local file write", scope: ".opaihub/proof-bundle.json (redacted and locally signed)" },
  proof_markdown: { risk: "Local file write", scope: ".opaihub/proof-bundle.md (redacted and locally signed)" },
};

// Styled inline confirmation (#151) — the in-app replacement for native
// window.confirm(). Renders explicit consequences, is keyboard-operable
// (Enter confirms, Escape cancels, the confirm button is focused), and
// returns a Promise<boolean>. Used for Full Auto (in chat), and the Codex
// repair / account disconnect settings actions (in place).
function confirmMarkup({ title, body, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false }) {
  return `<div class="inline-confirm${danger ? " danger" : ""}" role="group" aria-label="${esc(title)}" tabindex="-1">
     <div class="ic-title">${esc(title)}</div>
     <div class="ic-body">${esc(body)}</div>
     <div class="ic-actions">
       <button class="btn primary" data-ic="ok">${esc(confirmLabel)}</button>
       <button class="btn" data-ic="cancel">${esc(cancelLabel)}</button>
     </div>
   </div>`;
}

function wireConfirm(scope) {
  return new Promise((resolve) => {
    const box = scope.querySelector(".inline-confirm");
    let settled = false;
    const finish = (val) => {
      if (settled) return;
      settled = true;
      box.querySelectorAll("button").forEach((b) => (b.disabled = true));
      box.removeEventListener("keydown", onKey);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
      else if (e.key === "Enter") { e.preventDefault(); finish(true); }
    };
    box.addEventListener("keydown", onKey);
    box.querySelector('[data-ic="ok"]').onclick = () => finish(true);
    box.querySelector('[data-ic="cancel"]').onclick = () => finish(false);
    const ok = box.querySelector('[data-ic="ok"]'); if (ok) ok.focus();
  });
}

function chatConfirm(opts) {
  switchView("chat");
  return wireConfirm(appendMsg(confirmMarkup(opts), "bot"));
}

function inlineConfirm(hostEl, opts) {
  const holder = document.createElement("div");
  holder.className = "inline-confirm-holder";
  holder.innerHTML = confirmMarkup(opts);
  hostEl.appendChild(holder);
  return wireConfirm(holder).then((ok) => { holder.remove(); return ok; });
}

// In-chat approval card — replaces the native confirm() with a proper gate:
// what's requested, why, the blast radius, and Approve once / Deny. Approve is
// the ONLY path to bridge.applyTool, so nothing can be applied silently.
function renderApprovalCard(r) {
  switchView("chat");
  const info = APPROVAL_SCOPE[r.apply] || { risk: "Mutating action", scope: "See details below" };
  const el = appendMsg(
    `<div class="approval-card" role="group" aria-label="Approval required">
       <div class="ap-head"><span class="ap-badge">Approval required</span><span class="ap-risk">${esc(info.risk)}</span></div>
       <div class="ap-title">${esc(r.title || "Action")}</div>
       <div class="ap-why">${esc(r.text || "This action changes state and needs your OK.")}</div>
       <div class="ap-scope"><span class="k">Affects</span><span class="v">${esc(info.scope)}</span></div>
       <div class="ap-actions">
         <button class="btn primary" data-ap="approve">Approve once</button>
         <button class="btn" data-ap="deny">Deny</button>
       </div>
     </div>`, "bot");
  const card = el.querySelector(".approval-card");
  const done = (note, cls) => {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    card.classList.add(cls);
    const state = document.createElement("div");
    state.className = "ap-state";
    state.textContent = note;
    card.appendChild(state);
  };
  el.querySelector('[data-ap="approve"]').onclick = () => {
    done("Approved — applying…", "approved");
    const finish = (j2) => {
      const a = JSON.parse(j2);
      const approvalState = card.querySelector(".ap-state");
      if (approvalState) approvalState.textContent = "Approved — applied.";
      card.classList.add("applied");
      appendCard(r.title, a.text);
      refreshStatus(); refreshInspector();
    };
    // #146: run the repair/panic subprocess on a worker thread; the window
    // stays responsive and the result arrives via toolApplied.
    if (bridge.applyToolAsync && bridge.toolApplied) {
      const rid = `tool-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      state.pendingToolApplies = state.pendingToolApplies || {};
      state.pendingToolApplies[rid] = finish;
      bridge.applyToolAsync(r.apply, rid);
    } else {
      bridge.applyTool(r.apply, finish);
    }
  };
  el.querySelector('[data-ap="deny"]').onclick = () => {
    done("Denied — nothing was changed.", "denied");
    appendCard(r.title, "Cancelled.");
  };
}
function appendCard(title, text) {
  switchView("chat");
  appendMsg(`<div class="tool-card"><div class="t">${esc(title)}</div><pre>${esc(text)}</pre></div>`, "bot");
}

// F9/F17: in-chat approval for a command hard-blocked by the run-mode policy
// (mirrors the needs_free_confirmation flow). The card shows the EXACT command
// the pipeline asked to run; Approve once re-sends the original message with
// allowCommand set to that exact string; Deny posts a cancellation and nothing
// is re-sent.
function renderCommandApprovalCard(el, r, sel) {
  const command = String((r && r.command) || "");
  const reason = modePresentationCopy((r && r.reason) || "The current run mode blocks this command.");
  el.innerHTML = roleHeader("OPai", "var(--amber)") + activitySummaryHtml() +
    `<div class="approval-card command-approval" role="group" aria-label="Command approval required">
       <div class="ap-head"><span class="ap-badge">Command blocked</span><span class="ap-risk">One-time approval</span></div>
       <div class="ap-title">Approve this command once?</div>
       <div class="ap-why">${esc(reason)}</div>
       <div class="ap-scope"><span class="k">Command</span><span class="v"><code>${esc(command)}</code></span></div>
       <div class="ap-actions">
         <button class="btn primary" data-ap="approve">Approve once</button>
         <button class="btn" data-ap="deny">Deny</button>
       </div>
     </div>`;
  wireActivitySummary(el);
  const card = el.querySelector(".approval-card");
  const done = (note, cls) => {
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    card.classList.add(cls);
    const outcome = document.createElement("div");
    outcome.className = "ap-state";
    outcome.textContent = note;
    card.appendChild(outcome);
  };
  el.querySelector('[data-ap="approve"]').onclick = () => {
    done("Approved — re-running with this command allowed…", "approved");
    send(Object.assign({}, state.lastSend || sel || {}, { allowCommand: command }));
  };
  el.querySelector('[data-ap="deny"]').onclick = () => {
    done("Denied — the command was not run.", "denied");
    if (bridge.cancel && state.message && state.message.requestId) {
      try { bridge.cancel(state.message.requestId); } catch (_e) { /* best-effort cancellation */ }
    }
  };
}

// F26: in-chat approval for file edits the provider's permission gate refused
// in Safe Auto. Mirrors the command-approval card: the card names the EXACT
// files; "Allow edits once" re-sends the original message with
// allowEditsOnce=true (Safe Auto keeps commands and destructive actions
// gated); Deny changes nothing.
function renderEditApprovalCard(el, r, sel) {
  const files = Array.isArray(r && r.edit_files) ? r.edit_files.map(String) : [];
  const listed = files.slice(0, 10);
  const more = files.length - listed.length;
  const rows = listed.map((f) => `<li><code>${esc(f)}</code></li>`).join("") +
    (more > 0 ? `<li>…and ${more} more</li>` : "");
  el.innerHTML = roleHeader("OPai", "var(--amber)") + activitySummaryHtml() +
    `<div class="approval-card edit-approval" role="group" aria-label="Edit approval required">
       <div class="ap-head"><span class="ap-badge">Edits blocked</span><span class="ap-risk">One-time approval</span></div>
       <div class="ap-title">Allow OPai to edit these files once?</div>
       <div class="ap-why">In Auto, OPai asks before changing files. Commands and destructive actions stay gated.</div>
       <div class="ap-scope"><span class="k">Files</span><span class="v"><ul class="ap-files">${rows || "<li>(paths unavailable)</li>"}</ul></span></div>
       <div class="ap-actions">
         <button class="btn primary" data-ap="approve">Allow edits once</button>
         <button class="btn" data-ap="deny">Deny</button>
       </div>
     </div>`;
  wireActivitySummary(el);
  const card = el.querySelector(".approval-card");
  const done = (note, cls) => {
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    card.classList.add(cls);
    const outcome = document.createElement("div");
    outcome.className = "ap-state";
    outcome.textContent = note;
    card.appendChild(outcome);
  };
  el.querySelector('[data-ap="approve"]').onclick = () => {
    done("Approved — re-running with edits allowed once…", "approved");
    send(Object.assign({}, state.lastSend || sel || {}, { allowEditsOnce: true }));
  };
  el.querySelector('[data-ap="deny"]').onclick = () => {
    done("Denied — no files were changed.", "denied");
    if (bridge.cancel && state.message && state.message.requestId) {
      try { bridge.cancel(state.message.requestId); } catch (_e) { /* best-effort cancellation */ }
    }
  };
}

/* ---------- palette + shortcuts ---------- */
function openPalette() {
  const ov = $("#palette"); ov.classList.add("open");
  const inp = $("#paletteInput"); inp.value = ""; renderPalette(""); inp.focus();
}
function renderPalette(q) {
  const list = $("#paletteList");
  const items = PALETTE.filter((c) => (c.label + " " + c.id).toLowerCase().includes(q.toLowerCase()));
  list.innerHTML = items.map((c, i) => `<div class="opt${i === 0 ? " sel" : ""}" data-id="${c.id}"><span>${esc(c.label)}</span><span class="hint">${esc(c.hint)}</span></div>`).join("");
  $$("#paletteList .opt").forEach((o) => (o.onclick = () => runCommand(o.dataset.id)));
}
function runCommand(id) {
  $("#palette").classList.remove("open");
  switch (id) {
    case "new_chat": startNewChat(); break;
    case "new_app": startNewApp(); break;
    case "focus_input": switchView("chat"); $("#input").focus(); break;
    case "stop": stop(); break;
    case "prompts": switchView("prompts"); break;
    case "inspector": togglePanel(); break;
    case "workspace": bridge.openWorkspace(); break;
    case "change_model": openModelPicker(); break;
    case "savings": switchView("home"); break;
    case "firewall": switchView("firewall"); break;
    case "settings": switchView("settings"); break;
    case "doctor": openSettingsPage("providers"); break;
    case "connect": switchView("chat"); bridge.runTool("connect"); break;
    case "shortcuts": toast("Ctrl+K palette · Ctrl+N new · Ctrl+L focus · Ctrl+P prompts · Ctrl+I panel · Ctrl+O folder · Ctrl+M model · Ctrl+B sidebar · Esc stop · ? shortcuts"); break;
  }
}
function togglePanel() {
  state.panel = !state.panel; applyPanel();
  // #246: the inspector is deferred at boot; load it the first time the panel
  // is opened (and refresh each open, matching pre-defer behaviour).
  if (state.panel) refreshInspector();
  bridge.savePref("show_control_panel", state.panel ? "true" : "false");
}
function applyPanel() {
  $("#app").classList.toggle("panel-hidden", !state.panel);
  $("#panelToggle").classList.toggle("on", state.panel);
  $("#panelToggle").setAttribute("aria-pressed", state.panel ? "true" : "false");
}

function isCompactShell() {
  return window.matchMedia("(max-width: 700px)").matches;
}
// True when focus is in a text field, so single-key shortcuts (like "?") don't
// steal characters the user is typing.
function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable === true;
}
function closeMobileSidebar() {
  $("#app").classList.remove("mobile-sidebar-open");
  if (isCompactShell()) $("#sidebarToggle").setAttribute("aria-expanded", "false");
}
function toggleSidebar() {
  const app = $("#app");
  if (isCompactShell()) {
    const open = !app.classList.contains("mobile-sidebar-open");
    app.classList.toggle("mobile-sidebar-open", open);
    $("#sidebarToggle").setAttribute("aria-expanded", open ? "true" : "false");
    return;
  }
  const hidden = !app.classList.contains("sidebar-hidden");
  app.classList.toggle("sidebar-hidden", hidden);
  $("#sidebarToggle").setAttribute("aria-expanded", hidden ? "false" : "true");
}

function wireWindowChrome() {
  $("#windowMinimize").onclick = () => bridge.minimizeWindow();
  $("#windowMaximize").onclick = () => bridge.toggleMaximizeWindow();
  $("#windowClose").onclick = () => bridge.closeWindow();

  const header = $("#appHeader");
  const interactive = "button, input, select, textarea, a, [role='menuitem']";
  header.addEventListener("mousedown", (event) => {
    if (event.button === 0 && !event.target.closest(interactive)) bridge.startWindowMove();
  });
  header.addEventListener("dblclick", (event) => {
    if (!event.target.closest(interactive)) bridge.toggleMaximizeWindow();
  });
  $$(".resize-zone").forEach((zone) => {
    zone.addEventListener("mousedown", (event) => {
      if (event.button === 0) bridge.startWindowResize(zone.dataset.edge);
    });
  });
}

/* ---------- misc ---------- */
let toastT;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 3200);
}
function autoSize() {
  const i = $("#input"); i.style.height = "auto"; i.style.height = Math.min(180, i.scrollHeight) + "px";
}
function setComposerDraft(value, options = {}) {
  const input = $("#input");
  if (!input) return;
  input.value = String(value == null ? "" : value);
  autoSize();
  updateComposerAvailability();
  if (options.focus) input.focus();
}

/* ---------- composer prompt history (shell-style Up/Down) ---------- */

// `state.boot.recents` is the list of prompts this workspace has sent, newest
// first — which is exactly a shell's history, and is what the sidebar used to
// show under the wrong name. Index -1 means "editing my own text".
function historyEntries() {
  return (state.boot && state.boot.recents) || [];
}

// Only take over the arrow keys when the caret is on the edge line, the way a
// terminal and every editor with history does. Inside a multi-line draft, Up
// must still move the caret — stealing it there would make the composer
// unusable for exactly the long prompts most worth recalling.
function caretOnFirstLine(input) {
  if (input.selectionStart !== input.selectionEnd) return false;
  return input.value.lastIndexOf("\n", Math.max(0, input.selectionStart - 1)) === -1;
}
function caretOnLastLine(input) {
  if (input.selectionStart !== input.selectionEnd) return false;
  return input.value.indexOf("\n", input.selectionStart) === -1;
}

function historyApply(input, index) {
  const list = historyEntries();
  state.history.index = index;
  const text = index < 0 ? state.history.draft : String(list[index] || "");
  setComposerDraft(text);
  // Caret to the end: the user is recalling a prompt to send or extend, not to
  // edit from the front.
  try { input.setSelectionRange(input.value.length, input.value.length); } catch (_e) { /* ignore */ }
}

function historyCancel() {
  const input = $("#input");
  if (!input) return;
  historyApply(input, -1);
}

function historyKey(e) {
  const input = $("#input");
  if (!input) return;
  const list = historyEntries();
  if (!list.length) return;
  const browsing = state.history.index >= 0;

  if (e.key === "ArrowUp") {
    if (!browsing && !caretOnFirstLine(input)) return;
    if (browsing && !caretOnFirstLine(input)) return;
    if (state.history.index + 1 >= list.length) { e.preventDefault(); return; }
    if (!browsing) state.history.draft = input.value;
    e.preventDefault();
    historyApply(input, state.history.index + 1);
    return;
  }

  // ArrowDown only does anything while browsing; otherwise it is ordinary
  // caret movement in the user's own draft.
  if (!browsing || !caretOnLastLine(input)) return;
  e.preventDefault();
  historyApply(input, state.history.index - 1);
}

// Any ordinary typing means the user has adopted the recalled text as their
// own draft, so Down should stop walking history back toward it.
function historyReset() {
  state.history.index = -1;
  state.history.draft = "";
}

function wire() {
  if (isCompactShell()) $("#sidebarToggle").setAttribute("aria-expanded", "false");
  const chatScroll = $("#chatScroll");
  chatScroll.addEventListener("scroll", () => {
    state.followLatest = isNearChatBottom(chatScroll);
    updateJumpLatest();
  }, { passive: true });
  $("#jumpLatest").onclick = () => {
    state.followLatest = true;
    scrollBottom(true);
  };
  $("#newChat").onclick = startNewChat;
  $("#newApp").onclick = startNewApp;
  $("#headerNewChat").onclick = startNewChat;
  $("#footSettings").onclick = () => switchView("settings");
  $("#headerSettings").onclick = () => switchView("settings");
  $("#sidebarToggle").onclick = toggleSidebar;
  $("#sidebarBackdrop").onclick = closeMobileSidebar;
  $("#send").onclick = () => (state.busy ? stop() : submitComposer());
  const buildToggle = $("#buildToggle");
  if (buildToggle) buildToggle.onclick = () => { state.buildMode = !state.buildMode; syncBuildMode(); };
  $("#panelToggle").onclick = togglePanel;
  $("#wsSwitch").onclick = (e) => { e.stopPropagation(); toggleWsMenu(); };
  $("#wsMenu").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", closeWsMenu);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeWsMenu(); });
  $("#input").addEventListener("input", () => {
    // Typing adopts the recalled prompt as the user's own draft.
    if (state.history.index >= 0) historyReset();
    autoSize(); updateComposerAvailability();
  });
  $("#input").addEventListener("keydown", (e) => {
    // Enter sends. While a request is active the text is queued rather than
    // discarded (#295) — still no second concurrent request.
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitComposer(); }
    else if (e.key === "ArrowUp" || e.key === "ArrowDown") historyKey(e);
    else if (e.key === "Escape" && state.history.index >= 0) { e.preventDefault(); historyCancel(); }
  });
  const contextPath = $("#contextPath");
  $("#addContext").onclick = () => {
    addContextHint(contextPath.value);
    if (normalizeContextHint(contextPath.value)) contextPath.value = "";
    contextPath.focus();
  };
  contextPath.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); $("#addContext").click(); }
  });
  const composer = $(".composer");
  composer.addEventListener("dragover", (e) => { e.preventDefault(); composer.classList.add("drag-over"); });
  composer.addEventListener("dragleave", () => composer.classList.remove("drag-over"));
  composer.addEventListener("drop", (e) => {
    e.preventDefault(); composer.classList.remove("drag-over");
    Array.from((e.dataTransfer && e.dataTransfer.files) || []).forEach((file) => addContextHint(file.name));
  });
  $("#promptSearch").addEventListener("input", loadPrompts);
  $("#promptCat").addEventListener("change", loadPrompts);
  $("#paletteInput").addEventListener("input", (e) => renderPalette(e.target.value));
  $("#paletteInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const sel = $("#paletteList .opt.sel") || $("#paletteList .opt"); if (sel) runCommand(sel.dataset.id); }
    if (e.key === "Escape") $("#palette").classList.remove("open");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const opts = $$("#paletteList .opt"); let i = opts.findIndex((o) => o.classList.contains("sel"));
      if (i < 0) i = 0; opts[i] && opts[i].classList.remove("sel");
      i = e.key === "ArrowDown" ? (i + 1) % opts.length : (i - 1 + opts.length) % opts.length;
      opts[i] && opts[i].classList.add("sel"); opts[i] && opts[i].scrollIntoView({ block: "nearest" });
    }
  });
  $("#palette").addEventListener("click", (e) => { if (e.target.id === "palette") $("#palette").classList.remove("open"); });
  wireWindowChrome();
  window.addEventListener("resize", () => {
    if (!isCompactShell()) {
      $("#app").classList.remove("mobile-sidebar-open");
      $("#sidebarToggle").setAttribute("aria-expanded", $("#app").classList.contains("sidebar-hidden") ? "false" : "true");
    }
  });
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-ext]"); if (a) { e.preventDefault(); bridge.openExternal(a.href); }
  });
  document.addEventListener("keydown", (e) => {
    const c = e.ctrlKey || e.metaKey;
    if (c && e.key === "k") { e.preventDefault(); openPalette(); }
    else if (c && e.key === "n") { e.preventDefault(); startNewChat(); }
    else if (c && e.key === "p") { e.preventDefault(); switchView("prompts"); }
    else if (c && e.key === "i") { e.preventDefault(); togglePanel(); }
    else if (c && e.key === "o") { e.preventDefault(); bridge.openWorkspace(); }
    else if (c && e.key === "l") { e.preventDefault(); switchView("chat"); $("#input").focus(); }
    else if (c && e.key === "m") { e.preventDefault(); openModelPicker(); }
    else if (c && e.key === "b") { e.preventDefault(); toggleSidebar(); }
    else if (e.key === "?" && !isTypingTarget(e.target)) { e.preventDefault(); runCommand("shortcuts"); }
    else if (e.key === "Escape" && state.busy) { e.preventDefault(); stop(); }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  wire();
  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.bridge;
    boot();
  });
});

// Test hook: lets the Playwright harness read state and drive send/stop without
// a real Qt bridge. Harmless in production (a read-only handle on internals).
if (typeof window !== "undefined") {
  window.__opai = {
    get state() { return state; },
    send: (x) => send(x),
    stop: () => stop(),
    // Pure-ish internals exposed for unit tests: the payload→state selection
    // sync (F16/F4) and the derived next-run agent mode preview (F21).
    applyBootSelection: (b) => applyBootSelection(b),
    derivedAgentMode: () => derivedAgentMode(),
    applyAppearance: (p) => applyAppearance(p),
    // Used by the redesigned composer's overflow menu (Keyboard shortcuts).
    runCommand: (id) => runCommand(id),
    // Context picker actions stay native so Chromium never receives arbitrary
    // host paths. The bridge returns only workspace-relative paths.
    pickContextFiles: (done) => {
      if (bridge && bridge.pickContextFiles) bridge.pickContextFiles(done);
      else if (done) done(JSON.stringify({ paths: [], rejected: 0 }));
    },
    pickContextFolder: (done) => {
      if (bridge && bridge.pickContextFolder) bridge.pickContextFolder(done);
      else if (done) done(JSON.stringify({ paths: [], rejected: 0 }));
    },
    // Images are picked with their own dialog because the context picker
    // only accepts paths inside the workspace -- right for source files,
    // wrong for a screenshot on the desktop. These are copied in instead.
    pickImages: (done) => {
      if (bridge && bridge.pickImages) bridge.pickImages(done);
      else if (done) done(JSON.stringify({ ok: true, images: [], rejected: 0 }));
    },
    addImageAttachment: (image) => {
      if (!image || !image.path) return;
      state.attachments[image.path] = {
        name: image.name || "Image",
        thumb: image.thumb || "",
      };
      addContextHint(image.path);
    },
    notify: (message) => toast(message),
    // Composer actions deep-link to the Settings page that owns the control.
    openSettings: (pageId) => openSettingsPage(pageId),
    // Settings' About page re-reports the update banner after a live check
    // or a completed update, so the shell-wide nudge never lags behind it.
    renderUpdateBanner: (update) => renderUpdateBanner(update),
    // Round 5 finding 2: the banner that stops the answer prose and the status
    // pill from telling the user opposite things.
    unverifiedClaimHtml: (r) => unverifiedClaimHtml(r),
  };
}
