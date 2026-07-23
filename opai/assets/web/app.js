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
  tlNodes: null, activityRenderPending: false, timelineRenders: 0,
  expandedGroups: new Set(), stripColor: "",
  resumePending: false,
  contextHints: [],
};
const providerLoginRequests = new Map();
let doctorRefreshRequestId = null;

/* ---------- markdown (escape-first, safe) ---------- */
function mdToHtml(src) {
  let s = esc(src);
  const fences = [];
  s = s.replace(/```([\s\S]*?)```/g, (_m, code) => {
    fences.push(code.replace(/^\n/, ""));
    return `FENCE${fences.length - 1}`;
  });
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/^######\s+(.*)$/gm, "<h3>$1</h3>")
       .replace(/^#####\s+(.*)$/gm, "<h3>$1</h3>")
       .replace(/^####\s+(.*)$/gm, "<h3>$1</h3>")
       .replace(/^###\s+(.*)$/gm, "<h3>$1</h3>")
       .replace(/^##\s+(.*)$/gm, "<h2>$1</h2>")
       .replace(/^#\s+(.*)$/gm, "<h2>$1</h2>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
       .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" data-ext="1">$1</a>');
  // lists
  s = s.replace(/(?:^|\n)((?:\s*[-*]\s+.*(?:\n|$))+)/g, (block) => {
    const items = block.trim().split(/\n/).map((l) => l.replace(/^\s*[-*]\s+/, "")).map((t) => `<li>${t}</li>`).join("");
    return `\n<ul>${items}</ul>`;
  });
  s = s.replace(/(?:^|\n)((?:\s*\d+\.\s+.*(?:\n|$))+)/g, (block) => {
    const items = block.trim().split(/\n/).map((l) => l.replace(/^\s*\d+\.\s+/, "")).map((t) => `<li>${t}</li>`).join("");
    return `\n<ol>${items}</ol>`;
  });
  s = s.split(/\n{2,}/).map((p) => (/^\s*<(h\d|ul|ol|pre)/.test(p) ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`)).join("");
  s = s.replace(/FENCE(\d+)/g, (_m, i) => `<pre><code>${fences[+i]}</code></pre>`);
  return s;
}

// Progressive, safe markdown for the streaming answer (#233). mdToHtml is
// escape-first, so rendering partial text is XSS-safe; an unclosed fence or
// inline marker simply shows literally until it completes, then snaps to
// formatted — no broken HTML, no flash of injected markup.
function renderStreamingBody(body, text) {
  body.classList.add("streaming");
  body.innerHTML = mdToHtml(text);
  enhanceCodeBlocks(body);
}
// Every code block gets a copy button (#233). Idempotent so it survives the
// per-frame re-render during streaming and the final render.
function enhanceCodeBlocks(root) {
  root.querySelectorAll("pre").forEach((pre) => {
    if (pre.classList.contains("has-copy")) return;
    const code = pre.querySelector("code");
    if (!code) return;
    pre.classList.add("has-copy");
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
    pre.appendChild(btn);
  });
}

/* ---------- boot ---------- */
// Appearance (#241): density scales spacing via a root class; reduced motion
// overrides the OS media query via a root attribute ("system" removes the
// attribute so the media query governs). Applied at boot and live on change.
function applyAppearance(prefs) {
  const p = prefs || {};
  const root = document.documentElement;
  root.classList.toggle("density-compact", (p.density || "comfortable") === "compact");
  const motion = p.reducedMotion === "on" || p.reducedMotion === "off" ? p.reducedMotion : "system";
  if (motion === "system") delete root.dataset.motion;
  else root.dataset.motion = motion;
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
      const input = $("#input");
      input.value = text;
      if (typeof autoSize === "function") autoSize();
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
  if (m) state.model = { id: m.id, label: m.label, advancedLabel: m.advanced_label, kind: m.kind, provider: m.provider };
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
    syncBuildMode();
    // Composer Redesign: apply the saved direction (toolbar / single / command).
    if (window.OPaiComposer) window.OPaiComposer.applyBootStyle();
    switchView("chat");
    if (b.initialTask) { $("#input").value = b.initialTask; autoSize(); }
    renderResumeChoice();
    // F16: if this workspace requests Full Auto but has no pin, surface the
    // acknowledgement even though no dropdown change event fired.
    maybeOfferFullAutoPin();
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
  bridge.replyReady.connect(onReply);
  if (bridge.buildReady) bridge.buildReady.connect(onBuildReply);
  bridge.activity.connect(onActivity);
  if (bridge.activityBatch) bridge.activityBatch.connect(onActivityBatch);
  bridge.token.connect(onToken);
  bridge.toolReady.connect(onTool);
  bridge.workspaceChanged.connect((json) => {
    state.boot = JSON.parse(json);
    rebootFromState();
    toast("Workspace switched");
    // F16: the new workspace may request Full Auto without a pin — the ack
    // must be offered even though no dropdown change event fired.
    maybeOfferFullAutoPin();
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
  if (bridge.toolApplied) bridge.toolApplied.connect((json) => {
    let d = {}; try { d = JSON.parse(json); } catch (_e) { return; }
    const pending = (state.pendingToolApplies || {})[d.requestId];
    if (!pending) return;
    delete state.pendingToolApplies[d.requestId];
    pending(JSON.stringify(d.data || {}));
  });
  if (bridge.discoverModels) setTimeout(() => bridge.discoverModels(), 0);
}

// One brand voice, one source: copy comes from opai/brand.py via the boot
// payload, so the GUI, Qt fallback, and CLI never drift apart.
function applyBrand(brand) {
  if (!brand) return;
  state.brand = brand;
  const h1 = $("#empty h1");
  if (h1 && brand.emptyTitle) h1.textContent = brand.emptyTitle;
  const hint = $("#empty .hint");
  if (hint && brand.emptyHint) hint.innerHTML = brand.emptyHint.replace(/Ctrl\+K/, "<kbd>Ctrl</kbd>+<kbd>K</kbd>");
  if (brand.composerPlaceholder) $("#input").placeholder = brand.composerPlaceholder;
  const eyebrow = $("#emptyEyebrow");
  if (eyebrow && brand.tagline) eyebrow.textContent = brand.name + " · " + brand.tagline;
}

function rebootFromState() {
  const b = state.boot;
  state.accounts = b.accounts || [];
  // F16/F4: re-apply the fresh payload's selection — without this the composer
  // kept the PREVIOUS workspace's mode while the header showed the new one.
  applyBootSelection(b);
  renderSidebar(); renderWorkspace(); renderComposerSelects(); renderComposerContext(); renderInspector();
  renderStatus(b.status); renderAccount(); renderEmptyChips();
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
  if (state.buildMode && state.buildApp && text && !text.startsWith("/")) {
    sendBuild(text);
    return;
  }
  send();
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
  const list = state.boot.recents || [];
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
  list.forEach((text) => {
    const b = document.createElement("button");
    b.className = "recent";
    b.textContent = text.length > 34 ? text.slice(0, 33) + "…" : text;
    b.title = text;
    b.onclick = () => { switchView("chat"); $("#input").value = text; autoSize(); $("#input").focus(); };
    rec.appendChild(b);
  });
  // Privacy control (#145): history is per-workspace and deletable in one click.
  const clear = document.createElement("button");
  clear.className = "recent";
  clear.id = "clearRecents";
  clear.style.color = "var(--faint)";
  clear.textContent = "Clear history";
  clear.title = "Delete this workspace's stored chat history";
  clear.onclick = () => {
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
      state.boot.recents = Array.isArray(response) ? response : (response.recents || []);
      state.boot.resume = (!Array.isArray(response) && response.resume)
        ? response.resume : { available: false, requires_choice: false };
      setResumeGate(false);
      clearChat();
      renderRecents();
    });
  };
  rec.appendChild(clear);
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
    // Full Auto edits files and runs commands without asking, so it is only
    // ever the effective mode when explicitly pinned (#137). Selecting it
    // asks for acknowledgement, then pins via the dedicated bridge slot — a
    // plain savePref for full-auto is deliberately downgraded server-side.
    if (modeSel.value === "full-auto") {
      // Revert the selector until the styled card is confirmed (#151).
      modeSel.value = state.mode.id;
      offerFullAutoPinAck();
      return;
    }
    // Leaving Full Auto unpins it so the durable default falls back to safe.
    if (state.mode.id === "full-auto" && bridge.unpinFullAuto) {
      bridge.unpinFullAuto(() => { maybeOfferFullAutoPin(); });
      state.boot.prefs.fullAutoPinned = false;
    }
    state.mode = state.boot.modes.find((m) => m.id === modeSel.value) || state.mode;
    bridge.savePref("default_mode", state.mode.id);
    // Keep the local autonomy snapshot coherent: an explicit non-Full-Auto
    // choice becomes the requested mode for this workspace, so the pin ack is
    // not re-offered for a mode the user just deliberately left.
    if (state.boot.autonomy) {
      state.boot.autonomy.requested_mode = state.mode.id;
      state.boot.autonomy.effective_mode = state.mode.id;
      state.boot.autonomy.downgraded = false;
    }
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
    if (m) state.model = { id: m.id, label: m.label, advancedLabel: m.advanced_label, kind: m.kind, provider: m.provider };
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
    "approve-edits": "Asks before commands",
    "full-auto": "Edits and runs commands",
  }[modeId] || "Uses your selected autonomy";
}

function costPosture() {
  if (state.model.kind === "local" || state.model.kind === "free") return "No provider spend";
  if (state.model.kind === "auto") return "Routes local first";
  return "May spend within your limits";
}

function composerBlockReason() {
  if (state.resumePending) return "Choose how to continue this saved session before sending.";
  if (state.model.kind === "account") {
    const account = (state.accounts || []).find((item) => item.id === state.model.provider);
    if (!account || !(account.connected || account.authenticated)) {
      return `Connect ${state.model.provider ? providerName(state.model.provider) : "this provider"} before sending.`;
    }
  }
  if (!$("#input").value.trim()) return "Write a prompt before sending.";
  return "";
}

function renderComposerContext() {
  const root = $("#composerContext");
  if (!root || !state.boot) return;
  const modeLabel = state.mode.label || "Selected mode";
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
  if (state.busy) { send.disabled = false; reason.innerHTML = ""; return; }
  send.disabled = Boolean(blocked);
  send.setAttribute("aria-label", (state.buildMode && state.buildApp) ? "Start build" : "Send prompt");
  if (!blocked) { reason.innerHTML = ""; send.removeAttribute("aria-describedby"); return; }
  const action = !state.resumePending && state.model.kind === "account"
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
  root.innerHTML = state.contextHints.map((path, index) =>
    `<span class="context-hint">@${esc(path)}<button class="context-remove" type="button" aria-label="Remove ${esc(path)}" data-context-index="${index}">${uiIcon("close")}</button></span>`
  ).join("");
  root.querySelectorAll("[data-context-index]").forEach((button) => {
    button.onclick = () => { state.contextHints.splice(Number(button.dataset.contextIndex), 1); renderContextHints(); };
  });
}

// The Full Auto acknowledgement (#137/#151), extracted so it can be offered
// from the composer dropdown AND proactively after a boot/workspace switch —
// the stale-dropdown bug (F16) made this ack unreachable when the select
// already displayed Full Auto.
function offerFullAutoPinAck() {
  state.fullAutoAckOpen = true;
  chatConfirm({
    title: "Pin Full Auto?",
    body: "Full Auto lets OPai edit files and run commands without asking first. It stays on until you unpin it. Push, deploy, and destructive actions still ask for confirmation.",
    confirmLabel: "Pin Full Auto",
    cancelLabel: "Keep current mode",
    danger: true,
  }).then((ok) => {
    state.fullAutoAckOpen = false;
    if (!ok) {
      // Declined: if Full Auto was being shown optimistically (e.g. carried
      // over from another workspace), fall back to the mode the engine
      // actually resolved for THIS workspace and paint it honestly.
      if (state.mode.id === "full-auto") {
        const eff = ((state.boot && state.boot.autonomy) || {}).effective_mode || "safe-auto";
        state.mode = (state.boot.modes || []).find((m) => m.id === eff) || state.mode;
      }
      renderComposerSelects(); refreshInspector(); refreshStatus();
      return;
    }
    if (!bridge.pinFullAuto) return;
    bridge.pinFullAuto((res) => {
      try {
        const d = JSON.parse(res);
        state.boot.prefs.fullAutoPinned = !!d.full_auto_pinned;
        if (state.boot.autonomy) {
          state.boot.autonomy.effective_mode = d.effective_mode || state.boot.autonomy.effective_mode;
          state.boot.autonomy.full_auto_pinned = !!d.full_auto_pinned;
          state.boot.autonomy.downgraded = !d.full_auto_pinned && state.boot.autonomy.requested_mode === "full-auto";
        }
      } catch (e) {}
      maybeOfferFullAutoPin();
    });
    state.mode = state.boot.modes.find((m) => m.id === "full-auto") || state.mode;
    const modeSel = $("#modeSel");
    if (modeSel) modeSel.value = "full-auto";
    refreshInspector(); refreshStatus();
  });
}

// Offer the pin ack whenever the CURRENT workspace requests Full Auto but has
// no pin for it — regardless of whether a dropdown change event fired (F16).
function maybeOfferFullAutoPin() {
  if (state.fullAutoAckOpen) return;
  const prefs = (state.boot && state.boot.prefs) || {};
  const autonomy = (state.boot && state.boot.autonomy) || {};
  if (prefs.fullAutoPinned) return;
  if (autonomy.requested_mode !== "full-auto" && state.mode.id !== "full-auto") return;
  offerFullAutoPinAck();
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
function refreshInspector() { bridge.inspector(JSON.stringify(selPayload()), (json) => renderInspector(JSON.parse(json))); }
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
  $("#statusLine").innerHTML = esc(st.line).replace(/^([^·]+)/, "<b>$1</b>");
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
  const brandBody = (state.brand && state.brand.emptyBody) || "";
  $("#emptySub").textContent = connected
    ? brandBody || "Your AI connection is ready · OPai picks the cheapest safe path."
    : "Connect your Claude, Codex, or Copilot account in Settings, then just type.";
  $("#chips").innerHTML = chips.map((c) => `<button class="chip" data-p="${esc(c[1])}">${esc(c[0])}</button>`).join("");
  $$("#chips .chip").forEach((b) => (b.onclick = () => { $("#input").value = b.dataset.p; send(); }));
}
function clearChat() {
  const t = $("#thread");
  t.querySelectorAll(".msg").forEach((m) => m.remove());
  $("#empty").style.display = "";
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
  const recovery = checkpoint.recovery_actions || [];
  return `<div class="resume-summary" role="status">
    <div class="rs-title">Work restored</div>
    ${checkpoint.id ? `<div class="rs-row">Checkpoint ${esc(checkpoint.id)} · ${esc(checkpoint.completion_state || "saved")}</div>` : ""}
    ${flow.message ? `<div class="rs-row">${esc(flow.message)}</div>` : ""}
    ${steps.length ? `<div class="rs-label">Plan</div><ul>${steps.map((step) => `<li>${esc(step)}</li>`).join("")}</ul>` : ""}
    ${changed.length ? `<div class="rs-row">Changed files: ${esc(changed.join(", "))}</div>` : ""}
    ${recovery.length ? `<div class="rs-row">Next: ${esc(recovery[0])}</div>` : ""}
  </div>`;
}
function restoreSession(resume) {
  clearChat();
  for (const message of ((resume.thread || {}).messages || [])) {
    if (message.role === "user") {
      appendMsg(`<div class="bubble">${esc(message.text || "")}</div>`, "user");
    } else if (message.role === "assistant") {
      const el = appendMsg(
        roleHeader("OPai", "var(--accent)") + `<div class="body">${mdToHtml(message.text || "")}</div>`,
        "bot",
      );
      enhanceCodeBlocks(el);
    }
  }
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
  card.querySelector('[data-a="cancel"]').onclick = () => el.remove();
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
function sendBuild(text) {
  if (state.busy) return;
  text = (text || $("#input").value).trim();
  if (!text) return;
  $("#input").value = ""; autoSize();
  appendMsg(`<div class="bubble">${esc(text)}</div>`, "user");
  const sel = {
    text, model: state.model.id, modelKind: state.model.kind,
    modelLabel: state.model.label, modelProvider: state.model.provider, build: true,
  };
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
  bridge.build(JSON.stringify({ requestId, text, model: sel.model, strict: false }));
}

function onBuildReply(json) {
  const d = JSON.parse(json);
  if (!OPaiMessageState.canApply(state.message, d.requestId)) return; // stale reply ignored
  const r = d.result || {};
  state.message = OPaiMessageState.transition(state.message, r.ok ? "answered" : "failed");
  state.currentRequest = null;
  setBusy(false);
  finalizeBuild(r);
  refreshStatus(); refreshInspector();
}

function finalizeBuild(r) {
  stopTimer();
  const kind = r.ok ? "answered" : (r.status === "rolled_back" ? "cancelled" : "error");
  stripFinalize(kind, r);
  const el = state.pending;
  if (!el) return;
  state.pending = null;
  const sel = state.lastSend || {};
  const durMs = Date.now() - state.startTime;
  el.innerHTML = roleHeader("OPai Build", "var(--accent)") + activitySummaryHtml() +
    buildResultHtml(r) + (r.receipt ? metaFooter({ receipt: r.receipt }, sel, durMs) : "");
  wireActivitySummary(el);
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
  const sc = $("#chatScroll");
  const follow = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 96;
  const d = document.createElement("div");
  d.className = "msg " + (cls || "");
  d.innerHTML = html;
  $("#thread").appendChild(d);
  if (follow) sc.scrollTop = sc.scrollHeight;
  return d;
}
function roleHeader(label, color) {
  const av = `<span class="av" style="background:${color};color:#06160f">${esc((label[0] || "O"))}</span>`;
  return `<div class="role" style="color:${color}">${av}${esc(label)}</div>`;
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
  // Slash commands run local OPai tools ("/panic", "/savings", "/connect") —
  // they must NEVER be sent to a paid model as a prompt.
  if (!retryOf && text.startsWith("/")) {
    $("#input").value = ""; autoSize();
    const name = text.slice(1).trim().split(/\s+/)[0].toLowerCase();
    if (name) {
      appendMsg(`<div class="bubble">${esc(text)}</div>`, "user");
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
  if (!retryOf) { $("#input").value = ""; autoSize(); }
  state.lastSend = sel;
  if (!retryOf) {
    appendMsg(`<div class="bubble">${esc(text)}</div>`, "user");
    if (bridge.saveRecent) bridge.saveRecent(text);
    state.boot.recents = [text].concat((state.boot.recents || []).filter((r) => r !== text)).slice(0, 12);
    renderRecents();
  }
  const requestId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "r" + Date.now() + Math.random();
  state.currentRequest = requestId;
  state.message = OPaiMessageState.beginRequest(requestId, { retryOf: retryOf ? state.lastFailedRequestId : null });
  state.message = OPaiMessageState.transition(state.message, "preparing");
  state.store = OPaiActivity.createStore();
  state.streaming = false;
  state.streamedText = "";
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
       <button class="gen-toggle" aria-expanded="false">Show activity</button>
       <div class="timeline" role="log" aria-label="AI activity" hidden></div>
       <div class="body stream"></div>
     </div>`, "bot");
  state.pending = el;
  el.querySelector(".gen-stop").onclick = stop;
  el.querySelector(".gen-toggle").onclick = () => {
    const tl = el.querySelector(".timeline"), btn = el.querySelector(".gen-toggle");
    if (tl.hasAttribute("hidden")) { tl.removeAttribute("hidden"); btn.textContent = "Hide activity"; btn.setAttribute("aria-expanded", "true"); }
    else { tl.setAttribute("hidden", ""); btn.textContent = "Show activity (" + state.store.events.length + ")"; btn.setAttribute("aria-expanded", "false"); }
  };
}

function tlRowInner(e) {
  // Each row carries its real offset from the start of the run (the events
  // have true epoch timestamps). No timestamp -> no label, never invented.
  let ts = "";
  if (typeof e.timestamp === "number" && state.startTime && e.timestamp >= state.startTime) {
    ts = `<span class="tl-ts">+${((e.timestamp - state.startTime) / 1000).toFixed(1)}s</span>`;
  }
  return `<span class="tl-ic">${uiIcon(ICON[e.status] || "pending")}</span>` +
    `<span class="tl-t">${esc(e.title)}</span>${e.detail ? `<span class="tl-d">${esc(e.detail)}</span>` : ""}${ts}`;
}
function timelineRows() {
  // Flat archive: every raw event, all detail visible. Used by the frozen
  // post-completion block so nothing is ever hidden after the fact.
  return state.store.list().map((e) => `<div class="tl-row ${e.status}">${tlRowInner(e)}</div>`).join("");
}
// A group is auto-expanded when any child errored (surface the failure), else
// it honors the user's toggle.
function groupExpanded(row) {
  if (row.children.some((c) => c.status === "error")) return true;
  return state.expandedGroups.has(row.key);
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
function reconcileGroupNode(entry, row) {
  const expanded = groupExpanded(row);
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
      if (state.expandedGroups.has(row.key)) state.expandedGroups.delete(row.key);
      else state.expandedGroups.add(row.key);
      renderTimeline(); // immediate, not rAF — a click deserves a live response
    };
  }
  entry.node.className = "tl-row tl-group " + row.status;
  entry.header.innerHTML = groupHeaderInner(row, expanded);
  entry.header.querySelector(".tl-group-toggle").onclick = () => {
    if (state.expandedGroups.has(row.key)) state.expandedGroups.delete(row.key);
    else state.expandedGroups.add(row.key);
    renderTimeline();
  };
  if (expanded) entry.kids.removeAttribute("hidden"); else entry.kids.setAttribute("hidden", "");
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
  const grouped = OPaiActivity.groupRows(state.store.list());
  if (tl) {
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
    // Honest truncation marker (#248, #400): when the store dropped the oldest
    // events to stay bounded, say so plainly — and don't promise a full record
    // the UI can't actually show (no itemized ledger view exists yet, #390).
    const truncated = state.store.truncatedCount ? state.store.truncatedCount() : 0;
    if (truncated > 0) {
      const key = "truncation";
      seen.add(key);
      const inner = `<span class="tl-ic">${uiIcon("more")}</span>` +
        `<span class="tl-t">${truncated.toLocaleString()} earlier steps hidden</span>` +
        `<span class="tl-d">dropped to stay fast</span>`;
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
        const key = "g:" + row.key;
        seen.add(key);
        const entry = reconcileGroupNode(rows.get(key), row);
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
  }
  const btn = state.pending.querySelector(".gen-toggle");
  if (btn && btn.getAttribute("aria-expanded") !== "true") btn.textContent = "Show activity (" + grouped.length + ")";
}
function scheduleTimelineRender() {
  // Activity shares the token path's rAF cadence: a burst of events in one
  // frame costs one render instead of one render per event (#228).
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
  else if (verdict && verdict.verdict !== "completed") { stripSetState("error"); $("#ssConn").textContent = verdictLabel(verdict.verdict); }
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
  if (!state.tokenRenderPending) {
    state.tokenRenderPending = true;
    requestAnimationFrame(() => {
      state.tokenRenderPending = false;
      const body = state.pending && state.pending.querySelector(".body.stream");
      if (body) { renderStreamingBody(body, state.streamedText); scrollBottom(); }
    });
  }
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
    if (sw) sw.onclick = () => { $("#modelSel").focus(); };
  }
  updateInspectorLive(sm.stage);
}

function stop() {
  if (!state.currentRequest) return;
  bridge.cancel(state.currentRequest);
  state.currentRequest = null; // drop id → any late signal is ignored
  state.message = OPaiMessageState.transition(state.message, "cancelled");
  state.store.cancelRunning(); renderTimeline();
  stopTimer();
  finalize("cancelled", { answer: state.streamedText || "" });
  setBusy(false);
}
function retry() { send(state.lastSend); }

function scrollBottom(force) {
  const sc = $("#chatScroll");
  if (force || sc.scrollHeight - sc.scrollTop - sc.clientHeight < 96) sc.scrollTop = sc.scrollHeight;
}
function stripStopNote(t) { return String(t || "").replace(/\n\n_\(stopped by you\)_\s*$/, ""); }

function activitySummaryHtml() {
  const n = state.store ? state.store.events.length : 0;
  if (!n) return "";
  return `<button class="gen-toggle done" data-label="Activity (${n})">Activity (${n})</button>` +
    `<div class="timeline done" hidden>${timelineRows()}</div>`;
}
function wireActivitySummary(el) {
  const btn = el.querySelector(".gen-toggle.done");
  if (!btn) return;
  btn.onclick = () => {
    const tl = el.querySelector(".timeline.done");
    if (tl.hasAttribute("hidden")) { tl.removeAttribute("hidden"); btn.textContent = "Hide activity"; }
    else { tl.setAttribute("hidden", ""); btn.textContent = btn.dataset.label; }
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
  const raw = r && r.completion_verdict;
  if (!raw || typeof raw !== "object") return null;
  const verdict = String(raw.verdict || "").toLowerCase();
  return verdict ? { verdict, reason: String(raw.reason || ""), nextAction: String(raw.next_action || "") } : null;
}
// The one user-facing label per verdict — mirrors opaihub.completion.VERDICT_LABELS
// (#396) so the GUI, CLI, and receipt summary never disagree ("Timed out", not
// "Timeout"). Kept in sync by a vocabulary-parity test.
const VERDICT_LABELS = {
  completed: "Completed", partial: "Partial", blocked: "Blocked",
  failed: "Failed", cancelled: "Cancelled", timeout: "Timed out",
};
function verdictLabel(verdict) {
  const key = String(verdict || "").toLowerCase();
  return VERDICT_LABELS[key] || (key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()) || "Unknown");
}
function completionVerdictHtml(r) {
  const item = completionVerdict(r);
  if (!item) return "";
  const label = verdictLabel(item.verdict);
  const glyph = item.verdict === "completed" ? "check" : item.verdict === "cancelled" ? "cancelled" : "warning";
  const next = item.nextAction ? `<div class="cv-next">Next: ${esc(item.nextAction)}</div>` : "";
  return `<section class="completion-verdict ${esc(item.verdict)}" role="status" aria-label="Completion verdict: ${esc(label)}">` +
    `<div class="cv-title">${uiIcon(glyph)} ${esc(label)}</div><div class="cv-reason">${esc(item.reason)}</div>${next}</section>`;
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
  return ({ claude: "Claude", codex: "Codex", copilot: "Copilot" })[provider] || String(provider || "Provider");
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

function updateDoctorCard(provider, result) {
  const card = document.querySelector(`[data-doctor-provider="${CSS.escape(String(provider || ""))}"]`);
  if (!card) return;
  const healthValue = connectionHealth(result);
  const signedIn = healthValue === "verified";
  const status = card.querySelector(`[data-account-status="${CSS.escape(String(provider || ""))}"]`);
  const health = card.querySelector("[data-doctor-health]");
  const diagnostic = card.querySelector("[data-doctor-diagnostic]");
  if (status) status.textContent = signedIn ? "connected" : (result.authStatus || result.status || "needs attention").replaceAll("_", " ");
  if (health) { health.textContent = connectionHealthLabel(healthValue); health.className = `doctor-health ${healthValue}`; }
  if (diagnostic) diagnostic.textContent = (result.connection && result.connection.safeDiagnostic) || result.safeDiagnostic || result.message || diagnostic.textContent;
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
  if (pending.retryPayload && !state.busy && (!pending.retryRequestId || (state.message && state.message.requestId === pending.retryRequestId))) send(pending.retryPayload);
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
  // Retrying Auto with no eligible provider only reproduces the same setup
  // card. Keep recovery concrete: choose a model or configure one first.
  const canRetry = status !== "needs_model";
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
  // Keep the activity evidence reviewable after a failure while retaining the
  // structured provider recovery actions from the shared message contract.
  el.innerHTML = roleHeader("OPai", "var(--red)") + activitySummaryHtml() +
    `<div class="error-card" role="alert"><div class="ec-t">${esc(title)}</div><div class="ec-w">${esc(what)}</div>` +
    `<div class="ec-actions">` +
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
    send(Object.assign({}, state.lastSend || {}, { allowCloud: true }));
  };
  const fallback = el.querySelector('[data-a="fallback"]'); if (fallback) fallback.onclick = () => {
    // Preserve the reviewed route. Re-sending Auto here would recompute a
    // fallback after consent and could differ from the configured provider the
    // user was shown on the confirmation card.
    const fallbackModelId = String(r.fallbackModelId || "").trim();
    if (!fallbackModelId) { switchView("settings"); return; }
    send(Object.assign({}, state.lastSend || {}, {
      model: fallbackModelId,
      allowCloud: true,
    }));
  };
  const limit = el.querySelector('[data-a="limit"]'); if (limit) limit.onclick = () => {
    send(Object.assign({}, state.lastSend || {}, { allowLimit: true }));
  };
  const settings = el.querySelector('[data-a="settings"]'); if (settings) settings.onclick = () => switchView("settings");
  el.querySelector('[data-a="switch"]').onclick = () => { $("#modelSel").focus(); };
  const details = el.querySelector('[data-a="details"]'); if (details) details.onclick = () => {
    const panel = el.querySelector(".ec-details"); if (panel) panel.open = !panel.open;
  };
  const cp = el.querySelector('[data-a="copy"]'); if (cp) cp.onclick = () => { copyText(raw); toast("Details copied"); };
}

function finalize(status, r) {
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
    el.querySelector('[data-a="edit"]').onclick = () => { $("#input").value = sel.text || ""; switchView("chat"); $("#input").focus(); };
    return;
  }
  if (!ANSWERED.includes(status)) {
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
  if (rawAnswer != null && typeof rawAnswer !== "string" && !state.streamedText) {
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
  let html = roleHeader(label, color) + activitySummaryHtml() + completionVerdictHtml(r) + `<div class="body">${mdToHtml(answer)}</div>`;
  const changed = (r && r.changed_files) || [];
  if (changed.length) html += filesCardHtml(changed);
  if (r && (r.workflow || r.agent_policy)) html += workflowCardHtml(r);
  const planSteps = (r && r.plan && r.plan.steps) || [];
  if (planSteps.length) html += planCardHtml(planSteps);
  html += metaFooter(r, sel, durMs);
  el.innerHTML = html;
  wireActivitySummary(el);
  wireFilesCard(el);
  wireReceipt(el, sel, r);
  wirePlanCard(el, sel);
  wireDiffReview(el);
  enhanceCodeBlocks(el);
}

function diffReviewHtml(review, testsStatus) {
  const files = (review && review.files) || [];
  if (!files.length) return "";
  const summary = review.summary || {};
  const panels = files.map((file, index) => {
    const risks = (file.risk_reasons || []).map((reason) => `<span class="diff-risk">${esc(reason)}</span>`).join("");
    const hunks = (file.hunks || []).map((hunk) => {
      const lines = (hunk.lines || []).map((line) => `<code>${esc(line)}</code>`).join("");
      return `<section class="diff-hunk"><div class="diff-hunk-head">@@ -${esc(hunk.old_start)},${esc(hunk.old_count)} +${esc(hunk.new_start)},${esc(hunk.new_count)} @@ ${esc(hunk.heading || "")}</div><pre>${lines}${hunk.truncated ? "<code>… bounded preview</code>" : ""}</pre></section>`;
    }).join("");
    return `<article class="diff-file" data-diff-index="${index}" data-diff-path="${esc(file.path)}" ${index ? "hidden" : ""}>
      <div class="diff-file-head"><strong>${esc(file.path)}</strong><span class="diff-decision">${esc(file.decision || "pending")}</span></div>
      <div class="diff-stats"><span>+${esc(file.additions || 0)}</span><span>−${esc(file.deletions || 0)}</span>${file.untracked ? "<span>untracked</span>" : ""}${risks}</div>
      ${hunks || '<div class="diff-empty">No textual hunk available.</div>'}
      <div class="diff-actions"><button class="btn ghost" data-diff-decision="rejected">Reject</button><button class="btn primary" data-diff-decision="approved">Approve</button></div>
    </article>`;
  }).join("");
  return `<section class="diff-review" aria-label="Changed-file review">
    <div class="diff-review-head"><div><strong>Review changes</strong><small><span data-diff-counts>${esc(summary.files || files.length)} files · ${esc(summary.pending || 0)} pending${summary.risky ? ` · ${esc(summary.risky)} risky` : ""}</span> · Tests: ${esc(String(testsStatus || "not run").replaceAll("_", " "))}</small></div><div class="diff-nav"><button class="btn ghost" data-diff-nav="prev" aria-label="Previous changed file">${uiIcon("arrowLeft")}</button><span data-diff-position>1 / ${files.length}</span><button class="btn ghost" data-diff-nav="next" aria-label="Next changed file">${uiIcon("arrowRight")}</button></div></div>
    ${panels}
  </section>`;
}

function wireDiffReview(el) {
  const review = el.querySelector(".diff-review");
  if (!review) return;
  const files = Array.from(review.querySelectorAll(".diff-file"));
  let active = 0;
  const show = (index) => {
    active = (index + files.length) % files.length;
    files.forEach((file, item) => { file.hidden = item !== active; });
    review.querySelector("[data-diff-position]").textContent = `${active + 1} / ${files.length}`;
  };
  review.querySelector('[data-diff-nav="prev"]').onclick = () => show(active - 1);
  review.querySelector('[data-diff-nav="next"]').onclick = () => show(active + 1);
  review.querySelectorAll("[data-diff-decision]").forEach((button) => {
    button.onclick = () => {
      const file = button.closest(".diff-file");
      const decision = button.dataset.diffDecision;
      if (!bridge.reviewDiff) return;
      button.disabled = true;
      bridge.reviewDiff(file.dataset.diffPath, decision, (raw) => {
        button.disabled = false;
        let result = {};
        try { result = JSON.parse(raw || "{}"); } catch (_) { result = {}; }
        if (!result.ok) { toast("Could not save the diff decision"); return; }
        file.querySelector(".diff-decision").textContent = decision;
        const pending = files.filter((item) => item.querySelector(".diff-decision").textContent === "pending").length;
        const approved = files.filter((item) => item.querySelector(".diff-decision").textContent === "approved").length;
        const rejected = files.filter((item) => item.querySelector(".diff-decision").textContent === "rejected").length;
        review.querySelector("[data-diff-counts]").textContent = `${files.length} files · ${pending} pending · ${approved} approved · ${rejected} rejected`;
        refreshInspector();
        toast(`Marked ${file.dataset.diffPath} ${decision}`);
      });
    };
  });
}

function workflowCardHtml(result) {
  const flow = result.workflow || {};
  const policy = result.agent_policy || {};
  const mode = policy.label || policy.mode || flow.mode || "—";
  const pretty = (value) => String(value || "—").replaceAll("_", " ");
  const title = (value) => { const text = pretty(value); return text.charAt(0).toUpperCase() + text.slice(1); };
  const blockerItems = [...(flow.blockers || [])];
  if (flow.blocker && !blockerItems.includes(flow.blocker)) blockerItems.push(flow.blocker);
  const blockers = blockerItems.map((item) => `<div class="wf-blocker">${esc(item)}</div>`).join("");
  const actions = (flow.next_actions || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const history = (flow.history || []).slice(-5).map((item) =>
    `<div class="wf-event"><span>${esc(title(item.phase))}</span><small>${esc(item.message || "")}</small></div>`
  ).join("");
  const provider = flow.provider || {};
  const cost = flow.cost || {};
  const gates = flow.safety_gates || {};
  const failedGates = gates.failed || [];
  return `<div class="workflow-card">
    <div class="wf-head"><span>${esc(mode)}</span><span>${esc(title(flow.phase))}</span></div>
    ${flow.message ? `<div class="wf-message">${esc(flow.message)}</div>` : ""}
    <div class="wf-row"><span>Tests</span><strong>${esc(pretty(flow.tests_status))}</strong></div>
    <div class="wf-row"><span>PR</span><strong>${esc(flow.pr_url || "not opened")}</strong></div>
    <div class="wf-row"><span>Merge</span><strong>${esc(pretty(flow.merge_status))}</strong></div>
    ${flow.issue_number ? `<div class="wf-row"><span>Issue</span><strong>#${esc(flow.issue_number)}</strong></div>` : ""}
    ${provider.model ? `<div class="wf-row"><span>Provider</span><strong>${esc(provider.model)}</strong></div>` : ""}
    ${cost.estimated_actual_usd != null ? `<div class="wf-row"><span>Cost</span><strong>$${esc(Number(cost.estimated_actual_usd).toFixed(4))}</strong></div>` : ""}
    ${failedGates.length ? `<div class="wf-row"><span>Failed gates</span><strong>${esc(failedGates.join(", "))}</strong></div>` : ""}
    ${blockers}
    ${actions ? `<div class="wf-subhead">Next actions</div><ul class="wf-actions">${actions}</ul>` : ""}
    ${history ? `<details class="wf-history"><summary>Timeline · ${(flow.history || []).length} events</summary>${history}</details>` : ""}
    ${diffReviewHtml(flow.diff_review, flow.tests_status)}
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
      <span class="pc-note">runs in Safe Auto — edits gated by the usual approvals</span>
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
    $("#input").value = text; autoSize();
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
    OPaiMessageState.fromBackendStatus(backendStatus, d.result && d.result.completion_verdict),
  );
  state.currentRequest = null;
  setBusy(false);
  finalize(backendStatus, d.result || {});
  refreshStatus(); refreshInspector();
}

function setBusy(on) {
  state.busy = on;
  document.body.classList.toggle("ai-working", on);
  const s = $("#send");
  s.textContent = on ? "Stop" : ((state.buildMode && state.buildApp) ? "Build" : "Send");
  s.classList.toggle("stop", on);
  s.setAttribute("aria-label", on ? "Stop generation" : "Send prompt");
  if (!on) updateComposerAvailability();
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
    switchView("chat"); $("#input").value = p.template; autoSize(); $("#input").focus(); refreshInspector();
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
    startGuidedProviderLogin, connectionHealthLabel, renderComposerSelects,
    applyAppearance, applyDefaults,
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
  const reason = String((r && r.reason) || "The current run mode blocks this command.");
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
       <div class="ap-why">Safe Auto asks before changing files. Commands and destructive actions stay gated.</div>
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
    case "change_model": $("#modelSel").focus(); break;
    case "savings": switchView("home"); break;
    case "firewall": switchView("firewall"); break;
    case "settings": switchView("settings"); break;
    case "doctor": switchView("settings"); break;
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

function wire() {
  if (isCompactShell()) $("#sidebarToggle").setAttribute("aria-expanded", "false");
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
  $("#input").addEventListener("input", () => { autoSize(); updateComposerAvailability(); });
  $("#input").addEventListener("keydown", (e) => {
    // Enter sends; while a request is active it is ignored (no duplicate/queue).
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!state.busy) submitComposer(); }
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
    else if (c && e.key === "m") { e.preventDefault(); $("#modelSel").focus(); }
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
    // Used by the redesigned composer's overflow menu (Keyboard shortcuts).
    runCommand: (id) => runCommand(id),
    // The model picker's "Manage models" action opens the providers settings —
    // the single real home for connecting/reconnecting a provider, kept out of
    // the selection list itself.
    openSettings: () => switchView("settings"),
  };
}
