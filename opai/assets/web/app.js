/* OPai web UI front-end. Renders JSON the Python bridge provides; never computes
   anything sensitive itself. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) =>
  String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const PROVIDER_COLOR = { claude: "#e0937a", codex: "#6cc1e8", auto: "#98a2b0", local: "#34d399" };

const PALETTE = [
  { id: "new_chat", label: "New chat", hint: "Ctrl+N" },
  { id: "focus_input", label: "Focus prompt", hint: "Ctrl+L" },
  { id: "prompts", label: "Open prompt library", hint: "Ctrl+P" },
  { id: "inspector", label: "Toggle control panel", hint: "Ctrl+I" },
  { id: "workspace", label: "Open project folder", hint: "Ctrl+O" },
  { id: "change_model", label: "Change model", hint: "Ctrl+M" },
  { id: "savings", label: "Show savings", hint: "" },
  { id: "firewall", label: "Cost firewall", hint: "" },
  { id: "settings", label: "Open settings", hint: "" },
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
  expandedGroups: new Set(),
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

/* ---------- boot ---------- */
function boot() {
  bridge.boot((json) => {
    state.boot = JSON.parse(json);
    const b = state.boot;
    state.accounts = b.accounts || [];
    state.panel = b.prefs.showPanel !== false;
    state.focus = b.prefs.focus || "general";
    state.format = b.prefs.format || "normal";
    // One-time consent per free-tier model id: after the first "Send to X"
    // click the card never appears again for that provider (persisted per
    // workspace by grantFreeConsent). Fresh install → empty Set.
    state.freeConsent = new Set(b.prefs.freeConsent || []);
    const m = (b.models || []).find((x) => x.id === b.selectedModel) || b.models[0];
    if (m) state.model = { id: m.id, label: m.label, advancedLabel: m.advanced_label, kind: m.kind, provider: m.provider };
    const md = (b.modes || []).find((x) => x.id === b.prefs.mode) || b.modes[0];
    if (md) state.mode = md;
    applyBrand(b.brand);
    renderSidebar(); renderWorkspace(); renderComposerSelects(); renderInspector();
    renderStatus(b.status); renderAccount(); applyPanel();
    renderEmptyChips();
    switchView("chat");
    if (b.initialTask) { $("#input").value = b.initialTask; }
  });
  bridge.replyReady.connect(onReply);
  bridge.activity.connect(onActivity);
  if (bridge.activityBatch) bridge.activityBatch.connect(onActivityBatch);
  bridge.token.connect(onToken);
  bridge.toolReady.connect(onTool);
  bridge.workspaceChanged.connect((json) => { state.boot = JSON.parse(json); rebootFromState(); toast("Workspace switched"); });
  if (bridge.modelsChanged) bridge.modelsChanged.connect((json) => {
    const catalog = JSON.parse(json);
    if (catalog.models) { state.boot.models = catalog.models; renderComposerSelects(); }
  });
  if (bridge.providerLoginReady) bridge.providerLoginReady.connect(onProviderLoginReady);
  if (bridge.connectionDoctorReady) bridge.connectionDoctorReady.connect(onConnectionDoctorReady);
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
  renderSidebar(); renderWorkspace(); renderComposerSelects(); renderInspector();
  renderStatus(b.status); renderAccount(); renderEmptyChips();
  clearChat(); switchView("chat");
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
      toggle.className = "nav-group-toggle" + (open ? " open" : "");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.innerHTML = `<span>${esc(g.group)}</span><span class="ngt-chev">${open ? "▾" : "▸"}</span>`;
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
  lab.className = "nav-group-label"; lab.textContent = "Recents";
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
    rec.innerHTML = `<div class="recent" style="color:var(--faint);cursor:default">Your chats appear here.</div>`;
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
    if (bridge.clearRecents) bridge.clearRecents(() => {});
    state.boot.recents = [];
    renderRecents();
  };
  rec.appendChild(clear);
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
    `<span class="m-ico">📁</span><div class="m-body"><div class="m-name">${esc(w.label)}</div><div class="sub">${esc(w.root)}</div></div><span class="m-check">✓</span>`,
    () => bridge.openPath(""), "current",
  );
  item(`<span class="m-ico">📂</span><div class="m-body"><div class="m-name">Open another folder…</div></div>`, () => bridge.openWorkspace());
  const recents = w.recents || [];
  if (recents.length) {
    sep();
    label("Recent projects");
    recents.forEach((r) => {
      item(
        `<span class="m-ico">📁</span><div class="m-body"><div class="m-name">${esc(r.label)}</div><div class="sub">${esc(r.path)}</div></div>`,
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
      chatConfirm({
        title: "Pin Full Auto?",
        body: "Full Auto lets OPai edit files and run commands without asking first. It stays on until you unpin it. Push, deploy, and destructive actions still ask for confirmation.",
        confirmLabel: "Pin Full Auto",
        cancelLabel: "Keep current mode",
        danger: true,
      }).then((ok) => {
        if (!ok || !bridge.pinFullAuto) return;
        bridge.pinFullAuto((res) => {
          try { const d = JSON.parse(res); state.boot.prefs.fullAutoPinned = !!d.full_auto_pinned; } catch (e) {}
        });
        state.mode = state.boot.modes.find((m) => m.id === "full-auto") || state.mode;
        modeSel.value = "full-auto";
        refreshInspector(); refreshStatus();
      });
      return;
    }
    // Leaving Full Auto unpins it so the durable default falls back to safe.
    if (state.mode.id === "full-auto" && bridge.unpinFullAuto) {
      bridge.unpinFullAuto(() => {});
      state.boot.prefs.fullAutoPinned = false;
    }
    state.mode = state.boot.modes.find((m) => m.id === modeSel.value) || state.mode;
    bridge.savePref("default_mode", state.mode.id); refreshInspector(); refreshStatus();
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
  const grouped = {};
  allModels.forEach((m) => {
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
  allModels.filter((m) => m.group && !knownGroups.has(m.group)).forEach((m) => {
    const o = document.createElement("option"); o.value = m.id; o.textContent = m.label; o.title = m.advanced_label || m.badge || "";
    if (m.available === false) { o.disabled = true; o.title = m.disabled_reason || "Not available"; }
    if (m.id === state.model.id) o.selected = true; modelSel.appendChild(o);
  });
  modelSel.onchange = () => {
    const m = state.boot.models.find((x) => x.id === modelSel.value);
    if (m) state.model = { id: m.id, label: m.label, advancedLabel: m.advanced_label, kind: m.kind, provider: m.provider };
    setProviderDot(); bridge.savePref("default_model", state.model.id); refreshInspector(); refreshStatus();
  };
  setProviderDot();
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
function refreshStatus() { bridge.statusLine(JSON.stringify(selPayload()), (json) => renderStatus(JSON.parse(json))); }

function renderInspector(data) {
  data = data || state.boot.inspector;
  const ins = $("#inspector");
  const focusOpts = (state.boot.taskModes || []).map((m) => `<option value="${m.id}"${m.id === state.focus ? " selected" : ""}>${esc(m.label)}</option>`).join("");
  const fmtOpts = (state.boot.outputFormats || []).map((f) => `<option value="${f.id}"${f.id === state.format ? " selected" : ""}>${esc(f.label)}</option>`).join("");
  const rows = (data.rows || []).map((r) => `<div class="insp-row"><span class="k">${esc(r.label)}</span><span class="v">${esc(r.value)}</span></div>`).join("");
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
function startNewChat() {
  if (state.busy) stop();
  clearChat();
  switchView("chat");
  $("#input").focus();
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
const ICON = { pending: "◌", running: "◐", success: "✓", warning: "!", error: "✗", cancelled: "⊘" };
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
  startTimer(sel);
  setBusy(true);
  bridge.send(JSON.stringify({
    requestId, text, model: sel.model, mode: sel.mode, focus: sel.focus,
    format: sel.format, allowCloud: sel.allowCloud === true, allowLimit: sel.allowLimit === true,
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
  return `<span class="tl-ic">${ICON[e.status] || "•"}</span>` +
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
    `<span class="tl-ic">${ICON[row.status] || "•"}</span>` +
    `<span class="tl-caret">${expanded ? "▾" : "▸"}</span>` +
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

const ACTIVITY_STATE_BY_TYPE = {
  provider_checking: "authenticating", request_sending: "sending",
  waiting_first_token: "waiting", streaming: "streaming",
};
function applyActivityState(event) {
  const next = ACTIVITY_STATE_BY_TYPE[event.type];
  if (next) state.message = OPaiMessageState.transition(state.message, next);
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
  if (!state.streaming) { state.streaming = true; updateGenStage(); }
  state.streamedText += d.text;
  if (!state.tokenRenderPending) {
    state.tokenRenderPending = true;
    requestAnimationFrame(() => {
      state.tokenRenderPending = false;
      const body = state.pending && state.pending.querySelector(".body.stream");
      if (body) { body.textContent = state.streamedText; scrollBottom(); }
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
function metaFooter(r, sel, durMs) {
  const bits = [sel.modelLabel || "OPai", OPaiActivity.formatElapsed(durMs)];
  const rc = (r && r.receipt) || {};
  if (+rc.estimated_actual_usd) bits.push("$" + (+rc.estimated_actual_usd).toFixed(4));
  if (+rc.estimated_savings_usd) bits.push("$" + (+rc.estimated_savings_usd).toFixed(4) + " saved");
  if (rc.paid_call_avoided) bits.push("paid call avoided");
  return `<div class="footer-note" role="button" tabindex="0" title="Copy this receipt" aria-label="Copy receipt">${esc(bits.join("   ·   "))}</div>`;
}

// The receipt strip is a claim — let the user take it with them. One click
// copies a plaintext receipt (task + the same honest numbers shown).
function wireReceipt(el, sel) {
  const strip = el.querySelector(".footer-note");
  if (!strip) return;
  const copy = () => {
    copyText(`OPai receipt\nTask: ${(sel && sel.text) || "—"}\n${strip.textContent.trim()}`);
    toast("Receipt copied");
  };
  strip.onclick = copy;
  strip.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); copy(); } });
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
  const freeProvider = String((sel && sel.modelLabel) || "the provider").split(" · ")[0];
  // Keep the activity evidence reviewable after a failure while retaining the
  // structured provider recovery actions from the shared message contract.
  el.innerHTML = roleHeader("OPai", "var(--red)") + activitySummaryHtml() +
    `<div class="error-card"><div class="ec-t">${esc(title)}</div><div class="ec-w">${esc(what)}</div>` +
    `<div class="ec-actions"><button class="btn" data-a="retry">Retry</button>` +
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
  el.querySelector('[data-a="retry"]').onclick = () => retry();
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
    send(Object.assign({}, state.lastSend || {}, { allowCloud: true }));
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
  let html = roleHeader(label, color) + activitySummaryHtml() + `<div class="body">${mdToHtml(answer)}</div>`;
  const changed = (r && r.changed_files) || [];
  if (changed.length) html += filesCardHtml(changed);
  if (r && (r.workflow || r.agent_policy)) html += workflowCardHtml(r);
  const planSteps = (r && r.plan && r.plan.steps) || [];
  if (planSteps.length) html += planCardHtml(planSteps);
  html += metaFooter(r, sel, durMs);
  el.innerHTML = html;
  wireActivitySummary(el);
  wireFilesCard(el);
  wireReceipt(el, sel);
  wirePlanCard(el, sel);
  wireDiffReview(el);
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
    <div class="diff-review-head"><div><strong>Review changes</strong><small><span data-diff-counts>${esc(summary.files || files.length)} files · ${esc(summary.pending || 0)} pending${summary.risky ? ` · ${esc(summary.risky)} risky` : ""}</span> · Tests: ${esc(String(testsStatus || "not run").replaceAll("_", " "))}</small></div><div class="diff-nav"><button class="btn ghost" data-diff-nav="prev" aria-label="Previous changed file">←</button><span data-diff-position>1 / ${files.length}</span><button class="btn ghost" data-diff-nav="next" aria-label="Next changed file">→</button></div></div>
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
    return `<button class="file-chip" data-file="${esc(p)}"><span class="fc-ico">📄</span><span class="fc-name">${esc(f)}</span><span class="fc-open">Open ↗</span></button>`;
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
  state.message = OPaiMessageState.transition(state.message, OPaiMessageState.fromBackendStatus(backendStatus));
  state.currentRequest = null;
  setBusy(false);
  finalize(backendStatus, d.result || {});
  refreshStatus(); refreshInspector();
}

function setBusy(on) {
  state.busy = on;
  document.body.classList.toggle("ai-working", on);
  const s = $("#send");
  s.textContent = on ? "Stop" : "Send";
  s.classList.toggle("stop", on);
  s.setAttribute("aria-label", on ? "Stop generation" : "Send prompt");
  updateInspectorLive(on ? "Preparing request…" : null);
}

/* ---------- dashboards ---------- */
function renderDashboard(section) {
  const page = $("#dashPage"); page.innerHTML = `<div class="page-sub">Loading…</div>`;
  bridge.dashboard(section, (json) => {
    const s = JSON.parse(json);
    if (s.error) { page.innerHTML = `<div class="page-sub">Couldn't load: ${esc(s.error)}</div>`; return; }
    let h = `<div class="page-title">${esc(s.title || section)}</div>`;
    if (s.subtitle) h += `<div class="page-sub">${esc(s.subtitle)}</div>`;
    if (s.hero) h += `<div class="hero"><div class="num" style="color:${sevColor(s.hero.severity)}">${esc(s.hero.headline)}</div><div class="cap">${esc(s.hero.caption || "")}</div></div>`;
    if (s.kpis && s.kpis.length) {
      h += `<div class="kpis">` + s.kpis.map((k) => `<div class="kpi"><div class="l">${esc(k.label)}</div><div class="v" style="color:${sevColor(k.severity)}">${esc(k.value)}</div></div>`).join("") + `</div>`;
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
  });
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
function renderSettings() {
  const page = $("#settingsPage"); page.innerHTML = `<div class="page-sub">Loading…</div>`;
  bridge.settingsData((json) => {
    const d = JSON.parse(json);
    const modeLabels = { ask: "Ask", plan: "Plan", "safe-auto": "Safe Auto", "approve-edits": "Approve Edits", "full-auto": "Full Auto" };
    const row = (k, v) => `<div class="set-row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`;
    let h = `<div class="page-title">Settings</div><div class="page-sub">Project: ${esc(state.boot.workspace.root)}</div>`;
    // One compact source of provider truth. Production supplies the normalized
    // doctor payload; the account fallback keeps sparse/older payloads safe.
    const doctorItems = Array.isArray(d.connectionDoctor) ? d.connectionDoctor : (d.accounts || []).map((a) => ({
      providerId: a.id, displayName: a.label || a.id, kind: "account",
      health: a.connected ? "verified" : (a.cli_present === false ? "not_installed" : "not_configured"),
      authStatus: a.connected ? "connected" : "not connected", credentialSourceLabel: "Subscription sign-in",
      cliInstalled: a.cli_present !== false, cliVersion: "", safeDiagnostic: a.connected ? "Sign-in detected; test the connection to verify it." : "No provider sign-in was detected.",
      detected: !!a.authenticated || !!a.connected, loginSupported: true, recoveryActions: a.connected ? ["test_connection", "disconnect"] : ["sign_in"],
    }));
    const checkedLabel = (value) => value ? new Date(Number(value)).toLocaleString() : "Never checked";
    h += `<section class="connection-doctor" role="region" aria-label="Connection Doctor"><div class="set-head">Connection Doctor</div><div class="set-note">Accounts and API provider health in one place. Credential values and files are never read or displayed.</div><div class="doctor-grid">`;
    doctorItems.forEach((item) => {
      const id = item.providerId || "provider", isAccount = item.kind === "account";
      const actions = item.recoveryActions || [];
      const envNames = item.envOverridesRemoved || [];
      h += `<article class="doctor-card" data-doctor-provider="${esc(id)}"${isAccount ? ` data-account-row="${esc(id)}"` : ""}>` +
        `<div class="doctor-head"><div><strong>${esc(item.displayName || id)}</strong><span class="doctor-kind">${isAccount ? "account CLI" : "API"}</span></div><span class="doctor-health ${esc(item.health)}" data-doctor-health>${esc(connectionHealthLabel(item.health))}</span></div>` +
        `<div class="doctor-meta"><span>Credential <b>${esc(item.credentialSourceLabel || "Not configured")}</b></span>` +
        (isAccount ? `<span>CLI <b data-doctor-cli>${esc(item.cliInstalled === false ? "not installed" : (item.cliVersion || "installed"))}</b></span>` : "") +
        (item.credentialEnvironmentName ? `<span>Variable <b>${esc(item.credentialEnvironmentName)}</b></span>` : "") +
        `<span>Last checked <b>${esc(checkedLabel(item.lastCheckedAt))}</b></span></div>` +
        `<div class="doctor-diagnostic" data-doctor-diagnostic>${esc(item.safeDiagnostic || "No diagnostic available.")}</div>` +
        (envNames.length ? `<div class="doctor-env">Ignored inherited overrides: ${envNames.map(esc).join(", ")}</div>` : "") +
        (item.lastError ? `<div class="doctor-error">${esc(item.lastErrorCode || "Connection error")}: ${esc(item.lastError)}</div>` : "") +
        (isAccount ? `<div class="doctor-status">Status: <span data-account-status="${esc(id)}">${esc(item.authStatus || "unknown")}</span></div>` : "") +
        `<div class="doctor-actions">` +
        (isAccount && item.cliInstalled !== false ? `<button class="btn ghost" data-test-account="${esc(id)}">Test ${esc(item.displayName || id)}</button>` : "") +
        (isAccount && item.loginSupported !== false && item.health !== "verified" ? `<button class="btn primary" data-login-account="${esc(id)}">Sign in to ${esc(item.displayName || id)}</button>` : "") +
        (isAccount && (item.detected || actions.includes("disconnect")) ? `<button class="btn ghost" data-disconnect-account="${esc(id)}" data-account-label="${esc(item.displayName || id)}">Disconnect</button>` : "") +
        (id === "codex" && d.codexConfig && d.codexConfig.repairable ? `<button class="btn" id="repairCodex">Repair Codex config</button>` : "") +
        (!isAccount && actions.includes("test_connection") ? `<button class="btn ghost" data-doctor-test-provider="${esc(id)}">Test ${esc(item.displayName || id)}</button>` : "") +
        `</div></article>`;
    });
    h += `</div></section>`;
    h += `<div class="actions"><button class="btn primary" id="setConnect">Connect accounts</button></div>`;
    if (d.codexConfig && d.codexConfig.repairable && !doctorItems.some((item) => item.providerId === "codex")) {
      h += `<div class="config-repair"><div><strong>Codex configuration needs repair</strong><div class="set-note">${esc(d.codexConfig.message || "Invalid Codex configuration")}</div></div><button class="btn" id="repairCodex">Repair Codex config</button></div>`;
    }
    const providerNames = { gemini: "Gemini", groq: "Groq", mistral: "Mistral" };
    h += `<div class="set-head">Free model connections</div>`;
    (d.credentials || []).forEach((credential) => {
      const provider = credential.provider || "provider";
      const label = providerNames[provider] || provider;
      const status = credential.configured ? `Connected securely · ${credential.source}` : (credential.keychainAvailable ? "Not connected" : "OS keychain unavailable — use environment setup");
      h += `<div class="provider-key-card" data-provider="${esc(provider)}"><div class="provider-key-head"><span>${esc(label)}</span><span class="provider-key-status">${esc(status)}</span></div>` +
        `<div class="provider-key-form"><input type="password" autocomplete="off" spellcheck="false" aria-label="${esc(label)} API key" placeholder="Paste API key">` +
        `<button class="btn" data-save-provider="${esc(provider)}">${credential.configured ? "Replace" : "Connect"} ${esc(label)}</button>` +
        (credential.configured ? `<button class="btn ghost" data-test-provider="${esc(provider)}">Test ${esc(label)}</button>` : "") +
        (credential.source === "keychain" ? `<button class="btn ghost" data-delete-provider="${esc(provider)}">Remove</button>` : "") + `</div></div>`;
    });
    h += `<div class="set-note">Keys are stored only in the operating-system credential store. Environment variables override keychain values.</div>`;
    h += `<div class="set-head">Model usage limits</div>`;
    const modelsById = Object.fromEntries((d.models || []).map((model) => [model.id, model]));
    const fmtUsage = (value) => Number(value || 0).toLocaleString();
    (d.usage || []).forEach((usage) => {
      const model = modelsById[usage.modelId] || { label: usage.modelId };
      const bounded = usage.limit != null;
      const summary = bounded ? `${fmtUsage(usage.used)} / ${fmtUsage(usage.limit)} ${esc(usage.metric)}` : `${fmtUsage(usage.used)} ${esc(usage.metric)} used · no limit set`;
      const pct = bounded ? Math.max(0, Math.min(100, +usage.percent || 0)) : 0;
      h += `<div class="usage-card" data-model-id="${esc(usage.modelId)}"><div class="usage-head"><span>${esc(model.label || usage.modelId)}</span><span>${summary}</span></div>` +
        `<div class="usage-track" role="progressbar" aria-label="${esc(model.label || usage.modelId)} usage" aria-valuemin="0" aria-valuemax="100"${bounded ? ` aria-valuenow="${pct}"` : ""}><span style="width:${pct}%"></span></div>` +
        `<div class="usage-meta">${esc(usage.source === "provider" ? "Provider reported" : "OPai tracked")} · ${esc(usage.window || "month")} · ${esc(usage.confidence || "unknown")}</div>` +
        `<div class="usage-limit-form"><input type="number" min="1" step="1" aria-label="Soft ${esc(String(usage.metric || "tokens").replace(/s$/, ""))} limit" value="${bounded ? esc(usage.limit) : ""}" placeholder="Set limit">` +
        `<button class="btn ghost" data-save-limit="${esc(usage.modelId)}" data-metric="${esc(usage.metric || "tokens")}" data-window="${esc(usage.window || "month")}">Save limit</button></div></div>`;
    });
    h += `<div class="set-head">Defaults</div>`;
    h += row("Default model", d.prefs.default_model || "auto");
    h += row("Default run mode", modeLabels[d.prefs.default_mode] || d.prefs.default_mode);
    h += row("Task focus", state.focus);
    h += row("Output format", state.format);
    h += `<div class="set-note">Change model and run mode from the composer; task focus and output format live in the Inspector. All persist automatically.</div>`;
    h += `<div class="set-head">Cost firewall</div>`;
    h += row("Profile", d.firewall.profile || "—");
    h += row("Panic mode", d.firewall.panic ? "ON (local-only)" : "off");
    h += row("Spent today", "$" + (+d.firewall.spent_today || 0).toFixed(2));
    h += row("Cloud gate", d.firewall.cloud_gate ? "confirm" : "open");
    h += `<div class="actions"><button class="btn" id="setPanic">${d.firewall.panic ? "Disable panic" : "Enable panic"}</button></div>`;
    h += `<div class="set-head">Tool permissions · ${esc(modeLabels[d.prefs.default_mode] || d.prefs.default_mode)}</div>`;
    (d.permissions || []).forEach((p) => (h += `<div class="perm"><span class="k">${esc(p.label)}</span><span class="s ${p.state}">${esc(p.state)}</span></div>`));
    h += `<div class="set-head">Privacy</div>`;
    ["No telemetry — nothing leaves your machine.", "No secrets stored; chat history is redacted, kept per workspace on this machine, and can be cleared from the sidebar.", "Local-first routing; cloud only on confirmation."].forEach((t) => (h += `<div class="cb">• ${esc(t)}</div>`));
    if (d.about && d.about.version) { h += `<div class="set-head">About</div>` + row("Version", d.about.version) + row("Release stage", d.about.release_stage || "—"); }
    page.innerHTML = h;
    const pb = $("#setPanic"); if (pb) pb.onclick = () => { switchView("chat"); bridge.runTool("panic"); };
    const cb = $("#setConnect"); if (cb) cb.onclick = () => { switchView("chat"); bridge.runTool("connect"); };
    const repair = $("#repairCodex"); if (repair) repair.onclick = () => {
      const host = repair.closest(".config-repair") || repair.parentElement;
      repair.disabled = true;
      inlineConfirm(host, {
        title: "Repair Codex config?",
        body: "OPai will create a backup, then remove only the invalid service_tier = \"default\" line from your Codex config.",
        confirmLabel: "Repair config",
      }).then((ok) => {
        repair.disabled = false;
        if (!ok) return;
        bridge.repairCodexConfig((json2) => {
          const result = JSON.parse(json2);
          if (result.repaired) {
            host.innerHTML = `<div><strong>Codex config repaired</strong><div class="set-note">Invalid tier removed; backup created.</div></div>`;
          } else toast(result.error || "Could not repair Codex config");
        });
      });
    };
    page.querySelectorAll("[data-save-provider]").forEach((button) => {
      button.onclick = () => {
        const card = button.closest(".provider-key-card"), input = card.querySelector("input");
        const secret = input.value.trim(); if (!secret) return;
        bridge.saveProviderKey(button.dataset.saveProvider, secret, (json2) => {
          input.value = "";
          const result = JSON.parse(json2), status = card.querySelector(".provider-key-status");
          status.textContent = result.configured ? "Connected securely · " + result.source : (result.error || "Not connected");
          if (result.configured && bridge.refreshModels) bridge.refreshModels((modelsJson) => {
            const refreshed = JSON.parse(modelsJson); if (refreshed.models) { state.boot.models = refreshed.models; renderComposerSelects(); }
          });
        });
      };
    });
    page.querySelectorAll("[data-delete-provider]").forEach((button) => {
      button.onclick = () => bridge.deleteProviderKey(button.dataset.deleteProvider, () => renderSettings());
    });
    page.querySelectorAll("[data-test-provider]").forEach((button) => {
      button.onclick = () => {
        const card = button.closest(".provider-key-card"), status = card.querySelector(".provider-key-status");
        status.textContent = "Testing connection…"; button.disabled = true;
        bridge.testProvider(button.dataset.testProvider, (json2) => {
          const result = JSON.parse(json2); button.disabled = false;
          const detail = result.error && (result.error.userMessage || result.error);
          status.textContent = result.connected ? "Connection verified" : (detail || "Connection failed");
        });
      };
    });
    page.querySelectorAll("[data-doctor-test-provider]").forEach((button) => {
      button.onclick = () => {
        const id = button.dataset.doctorTestProvider;
        button.disabled = true; button.textContent = "Testing…";
        bridge.testProvider(id, (json2) => {
          let result = {}; try { result = JSON.parse(json2); } catch (_e) { /* keep {} */ }
          button.disabled = false; button.textContent = `Test ${providerName(id)}`;
          updateDoctorCard(id, result);
          toast(result.connected ? "Connection verified" : ((result.error && result.error.userMessage) || "Connection failed"));
        });
      };
    });
    page.querySelectorAll("[data-login-account]").forEach((button) => {
      button.onclick = () => startGuidedProviderLogin(button.dataset.loginAccount, { button });
    });
    // Account (Claude/Codex/Copilot) connections: unlike the free-tier
    // buttons above, this hits the SAME live check OPai runs before a real
    // send (test_account_connection, force=True — bypasses the 5-minute
    // cache), so the row updates to what the CLI actually reports right now
    // instead of the on-disk "sign-in detected" presence check.
    page.querySelectorAll("[data-test-account]").forEach((button) => {
      button.onclick = () => {
        const id = button.dataset.testAccount;
        const status = page.querySelector(`[data-account-status="${id}"]`);
        button.disabled = true; button.textContent = "Testing…";
        if (status) status.textContent = "testing…";
        bridge.testProvider(id, (json2) => {
          let result = {}; try { result = JSON.parse(json2); } catch (_e) { /* keep {} */ }
          button.disabled = false; button.textContent = "Test connection";
          const live = result.authStatus === "connected";
          if (status) status.textContent = live ? "connected" : (result.authStatus || "needs attention");
          updateDoctorCard(id, result);
          if (live) { toast("Connection verified"); return; }
          const hint = result.loginHint ? " " + result.loginHint : "";
          toast((result.safeDiagnostic || "Connection check failed.") + hint);
        });
      };
    });
    // Disconnect runs the provider's OWN sign-out command (claude "auth
    // logout", codex "logout") — never touches credential files directly.
    // This is the real fix for a session that Test connection can't detect
    // as broken until a real request actually fails against it: force a
    // clean sign-out so the next `claude`/`codex` run starts a fresh login.
    page.querySelectorAll("[data-disconnect-account]").forEach((button) => {
      button.onclick = () => {
        const id = button.dataset.disconnectAccount;
        const label = button.dataset.accountLabel || id;
        const host = button.closest(".doctor-card") || button.closest("[data-account-row]") || button.parentElement;
        button.disabled = true;
        inlineConfirm(host, {
          title: `Sign out of ${label}?`,
          body: "OPai runs the provider's own sign-out so the next run starts a fresh login. You'll need to sign in again to use it.",
          confirmLabel: "Sign out",
          danger: true,
        }).then((ok) => {
        if (!ok) { button.disabled = false; return; }
        button.textContent = "Disconnecting…";
        bridge.disconnectAccount(id, (json2) => {
          let result = {}; try { result = JSON.parse(json2); } catch (_e) { /* keep {} */ }
          button.textContent = "Disconnect";
          toast(result.message || (result.disconnected ? "Signed out." : "Could not sign out."));
          if (result.disconnected) {
            const status = page.querySelector(`[data-account-status="${id}"]`);
            const dot = page.querySelector(`[data-account-row="${id}"] .prov-dot`);
            if (status) status.textContent = "not connected";
            if (dot) dot.classList.remove("on");
            const health = button.closest(".doctor-card") && button.closest(".doctor-card").querySelector("[data-doctor-health]");
            if (health) { health.textContent = "Not configured"; health.className = "doctor-health not_configured"; }
            const testBtn = page.querySelector(`[data-test-account="${id}"]`);
            if (testBtn) testBtn.disabled = true;
            button.disabled = true; // nothing left here to disconnect again
          } else {
            button.disabled = false;
          }
        });
        });
      };
    });
    page.querySelectorAll("[data-save-limit]").forEach((button) => {
      button.onclick = () => {
        const input = button.closest(".usage-limit-form").querySelector("input"), value = input.value.trim();
        if (!value || +value <= 0) return;
        bridge.saveUsageLimit(button.dataset.saveLimit, button.dataset.metric, value, button.dataset.window, (json2) => {
          const result = JSON.parse(json2); toast(result.ok ? "Usage limit saved" : (result.error || "Could not save limit"));
        });
      };
    });
    if (bridge.refreshConnectionDoctor) {
      doctorRefreshRequestId = `doctor-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      bridge.refreshConnectionDoctor(doctorRefreshRequestId);
    }
  });
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
    bridge.applyTool(r.apply, (j2) => {
      const a = JSON.parse(j2);
      appendCard(r.title, a.text);
      refreshStatus(); refreshInspector();
    });
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
    case "focus_input": switchView("chat"); $("#input").focus(); break;
    case "prompts": switchView("prompts"); break;
    case "inspector": togglePanel(); break;
    case "workspace": bridge.openWorkspace(); break;
    case "change_model": $("#modelSel").focus(); break;
    case "savings": switchView("home"); break;
    case "firewall": switchView("firewall"); break;
    case "settings": switchView("settings"); break;
    case "connect": switchView("chat"); bridge.runTool("connect"); break;
    case "shortcuts": toast("Ctrl+K palette · Ctrl+N new · Ctrl+P prompts · Ctrl+I inspector · Ctrl+O folder · Ctrl+L focus"); break;
  }
}
function togglePanel() {
  state.panel = !state.panel; applyPanel();
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
  $("#headerNewChat").onclick = startNewChat;
  $("#footSettings").onclick = () => switchView("settings");
  $("#headerSettings").onclick = () => switchView("settings");
  $("#sidebarToggle").onclick = toggleSidebar;
  $("#sidebarBackdrop").onclick = closeMobileSidebar;
  $("#send").onclick = () => (state.busy ? stop() : send());
  $("#panelToggle").onclick = togglePanel;
  $("#wsSwitch").onclick = (e) => { e.stopPropagation(); toggleWsMenu(); };
  $("#wsMenu").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", closeWsMenu);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeWsMenu(); });
  $("#input").addEventListener("input", autoSize);
  $("#input").addEventListener("keydown", (e) => {
    // Enter sends; while a request is active it is ignored (no duplicate/queue).
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!state.busy) send(); }
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
  window.__opai = { get state() { return state; }, send: (x) => send(x), stop: () => stop() };
}
