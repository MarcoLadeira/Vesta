/* OPai Composer Redesign controller.
 *
 * Implements the three explored directions from the design doc
 * (docs/design/opai-prompt-composer-redesign.md) as one switchable card:
 *
 *   • "toolbar" — the quiet toolbar composer (the selected 1b direction, default)
 *   • "single"  — single line: input + send + one overflow menu (1a)
 *   • "command" — command bar: keyboard-first #file / /mode / @model tokens (1c)
 *
 * The direction is chosen in Settings › Appearance and persisted per workspace
 * (pref key "composer_style"). All three share one skeleton and one set of
 * popovers; they differ only in what is permanent versus revealed.
 *
 * This layer is presentation + information architecture ONLY. It never talks to
 * the model, routing, or permission engines directly — the popovers drive the
 * existing (now visually hidden) <select id="modeSel">, <select id="modelSel">,
 * and #contextPath/#addContext controls, so the send/abort/routing/approval
 * pipeline in app.js is completely unchanged.
 */
(function (global) {
  "use strict";

  var STYLES = ["toolbar", "single", "command"];
  var DEFAULT_STYLE = "toolbar";

  // The menu Claude Code presents, in its order and its words, rendered with
  // OPai's own rows. Four graded modes, numbered so they can be picked from
  // the keyboard, and Bypass sitting apart from the ladder rather than one
  // more step along it -- reaching it should be a decision, not a drift.
  var MODE_MENU = [
    { id: "safe-auto", label: "Auto", desc: "OPai handles permission decisions" },
    { id: "approve-edits", label: "Manual", desc: "Always ask before making changes" },
    { id: "auto-edits", label: "Accept edits", desc: "Automatically accept all file edits" },
    { id: "plan", label: "Plan", desc: "Create a plan before making changes" },
  ];
  // Bypass is a *switch*, not a fifth rung. Claude Code models the same
  // authority as a flag because it is orthogonal to the mode: it applies on top
  // of whichever mode you are in. As a row in this list, picking it replaced
  // the mode you were working in and turning it off could not give that mode
  // back -- so the menu could not express "Accept edits, without the asking".
  var BYPASS_SWITCH = {
    label: "Bypass permissions",
    on: "On — nothing will ask, including pushes",
    off: "Run everything, including pushes, without asking",
  };
  // Descriptions for every id, menu or not: `ask` is no longer offered but a
  // preferences file may still hold it, and a stored mode must stay nameable.
  var MODE_DESC = {
    ask: "Answer questions without changing files.",
    plan: "Create a plan before making changes",
    "approve-edits": "Always ask before making changes",
    "safe-auto": "OPai handles permission decisions",
    "auto-edits": "Automatically accept all file edits",
    "full-auto": "Run everything, including pushes, without asking",
  };
  // Dot colour: teal accent for calm modes, amber caution for the autonomous
  // ones, muted for plan-only. Never red — informative, not alarming.
  var MODE_DOT = { "full-auto": "caution", "auto-edits": "caution", plan: "muted" };

  function $(sel) { return document.querySelector(sel); }
  function state() { return (global.__opai && global.__opai.state) || {}; }
  function boot() { return state().boot || {}; }
  function icon(name) { return global.OPaiIcons ? global.OPaiIcons.icon(name) : ""; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  var els = {};
  var openPop = null; // id of the currently open popover, or null

  function shortModel(m) {
    if (!m) return "Auto";
    if (m.kind === "auto") return "Auto";
    if (m.kind === "local") return "Local";
    if (m.provider === "claude") return "Claude";
    if (m.provider === "codex" || m.provider === "openai") return "GPT";
    if (m.provider === "copilot") return "Copilot";
    var lbl = String(m.label || "").replace(/^OPai\s*·\s*/, "");
    return lbl.split(/[\s·]+/)[0] || "Model";
  }
  function routesLocal(m) {
    return !!m && (m.kind === "auto" || m.kind === "local" || m.kind === "free");
  }
  function modeLabelOf(mode) {
    if (typeof global.OPaiModePresentationLabel === "function") {
      return global.OPaiModePresentationLabel(mode);
    }
    return (mode && mode.label) || "Mode";
  }
  function dotVar(kind) {
    return kind === "caution" ? "var(--amber)" : kind === "muted" ? "var(--faint)" : "var(--accent)";
  }

  /* ---------- native drivers (drive the hidden real controls) ---------- */
  function setMode(id) {
    var sel = $("#modeSel");
    if (!sel || sel.value === id) { if (sel && sel.value === id) refresh(); return; }
    sel.value = id;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    refresh();
  }
  function setBypass(on) {
    // Authority only: the mode selector is deliberately untouched, so the mode
    // survives the toggle in both directions.
    var api = global.__opai || {};
    if (typeof api.setBypassPermissions === "function") {
      api.setBypassPermissions(on === true);
    } else {
      state().bypassPermissions = on === true;
    }
    refresh();
  }
  function setModel(id) {
    var sel = $("#modelSel");
    if (!sel) return;
    if (!Array.prototype.some.call(sel.options, function (option) { return option.value === id; })) {
      var model = (boot().models || []).find(function (item) { return item.id === id; });
      if (model) {
        var option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.label || model.id;
        sel.appendChild(option);
      }
    }
    sel.value = id;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    refresh();
  }
  function addPath(value) {
    var path = $("#contextPath");
    var add = $("#addContext");
    if (!path || !add) return;
    path.value = value;
    add.click();
  }
  function useRepo() {
    // Context hints are relative to the active workspace root. Using the Git
    // repository name is wrong for generated apps nested inside a parent repo.
    addPath("./");
  }

  /* ---------- popovers ---------- */
  function fitModePop() {
    var pop = els.modePop;
    if (!pop || pop.hidden) return;
    var header = document.getElementById('appHeader');
    var top = Math.max(0, header ? header.getBoundingClientRect().bottom : 0) + 8;
    pop.style.maxHeight = Math.max(80, Math.min(640, pop.getBoundingClientRect().bottom - top)) + 'px';
  }
  function closePopovers() {
    STYLES.forEach(function () {});
    ["ctxPop", "modePop", "modelPop", "morePop"].forEach(function (id) {
      var el = els[id];
      if (el) el.hidden = true;
    });
    ["ctxBtn", "modeBtn", "modelBtn", "moreBtn"].forEach(function (id) {
      var el = els[id];
      if (el) el.setAttribute("aria-expanded", "false");
    });
    els.composer && els.composer.classList.remove("pop-open");
    openPop = null;
  }
  function togglePop(popId, btnId, build) {
    var wasOpen = openPop === popId;
    closePopovers();
    if (wasOpen) return;
    build();
    var pop = els[popId];
    pop.hidden = false;
    var btn = els[btnId];
    if (btn) btn.setAttribute("aria-expanded", "true");
    els.composer && els.composer.classList.add("pop-open");
    openPop = popId;
    if (popId === 'modePop') fitModePop();
    var first = pop.querySelector("button, input, [tabindex]");
    if (first) { try { first.focus(); } catch (_e) { /* best effort */ } }
  }

  function menuRow(opts) {
    // opts: {title, desc, active, dot, disabled, meta}
    var dot = opts.dot ? '<span class="cpop-dot" style="background:' + opts.dot + '"></span>' : "";
    var meta = opts.meta ? '<span class="cpop-meta">' + esc(opts.meta) + "</span>" : "";
    var check = opts.active ? '<span class="cpop-check">' + icon("check") + "</span>" : "";
    var desc = opts.desc ? '<span class="cpop-desc">' + esc(opts.desc) + "</span>" : "";
    return (
      '<button type="button" role="' + (opts.role || "menuitem") + '"' +
      (opts.active != null ? ' aria-checked="' + (opts.active ? "true" : "false") + '"' : "") +
      (opts.disabled ? " disabled" : "") +
      ' class="cpop-row' + (opts.active ? " active" : "") + '">' +
      dot +
      '<span class="cpop-body"><span class="cpop-title">' + esc(opts.title) + "</span>" + desc + "</span>" +
      meta + check +
      "</button>"
    );
  }

  function buildContextPop() {
    var ws = boot().workspace || {};
    var repo = ws.label || ws.name || "this project";
    var pop = els.ctxPop;
    pop.innerHTML =
      '<div class="cpop-head">Add context</div>' +
      '<button type="button" role="menuitem" class="cpop-row" data-act="file"><span class="cpop-ico">' + icon("file") + '</span><span class="cpop-body"><span class="cpop-title">Attach files…</span></span></button>' +
      '<button type="button" role="menuitem" class="cpop-row" data-act="image"><span class="cpop-ico">' + icon("image") + '</span><span class="cpop-body"><span class="cpop-title">Attach images…</span><span class="cpop-desc">Or paste and drop them straight into the box</span></span></button>' +
      '<button type="button" role="menuitem" class="cpop-row" data-act="folder"><span class="cpop-ico">' + icon("folder") + '</span><span class="cpop-body"><span class="cpop-title">Add a folder…</span></span></button>' +
      '<button type="button" role="menuitem" class="cpop-row" data-act="repo"><span class="cpop-ico">' + icon("workspace") + '</span><span class="cpop-body"><span class="cpop-title">Use this repository</span></span><span class="cpop-meta" title="' + esc(repo) + '">' + esc(repo) + "</span></button>" +
      '<div class="cpop-sep"></div>' +
      '<div class="cpop-input"><span>›</span><input id="ctxPathDraft" placeholder="Type a path and press Enter" aria-label="Add a path" autocomplete="off" /></div>' +
      '<p class="cpop-note">OPai links to your files — it sends their location, not their contents.</p>';
    pop.querySelector('[data-act="repo"]').onclick = function () { useRepo(); closePopovers(); };
    pop.querySelector('[data-act="file"]').onclick = function () { pickContext("file"); };
    pop.querySelector('[data-act="image"]').onclick = function () { pickImages(); };
    pop.querySelector('[data-act="folder"]').onclick = function () { pickContext("folder"); };
    var draft = pop.querySelector("#ctxPathDraft");
    draft.onkeydown = function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        if (draft.value.trim()) { addPath(draft.value.trim()); draft.value = ""; }
        closePopovers();
      }
    };
    function focusDraft() { try { draft.focus(); } catch (_e) { /* ignore */ } }
    function pickImages() {
      var api = global.__opai || {};
      if (typeof api.pickImages !== "function") { closePopovers(); return; }
      api.pickImages(function (raw) {
        var result = {};
        try { result = JSON.parse(raw || "{}"); } catch (_e) { result = {}; }
        (result.images || []).forEach(function (image) {
          if (typeof api.addImageAttachment === "function") api.addImageAttachment(image);
        });
        if (result.rejected && typeof api.notify === "function") {
          api.notify(
            result.rejected === 1
              ? "One file was not an image OPai can send."
              : result.rejected + " files were not images OPai can send."
          );
        }
        closePopovers();
      });
    }
    function pickContext(kind) {
      var api = global.__opai || {};
      var pick = kind === "file" ? api.pickContextFiles : api.pickContextFolder;
      if (typeof pick !== "function") { focusDraft(); return; }
      pick(function (raw) {
        var result = {};
        try { result = JSON.parse(raw || "{}"); } catch (_e) { result = {}; }
        var paths = Array.isArray(result.paths) ? result.paths : [];
        paths.forEach(addPath);
        if (Number(result.rejected || 0) > 0 && typeof api.notify === "function") {
          api.notify("Only files and folders inside this workspace can be attached.");
        }
        closePopovers();
      });
    }
  }

  function buildModePop() {
    var st = state();
    var modes = boot().modes || [];
    var cur = (st.mode && st.mode.id) || "";
    var selectedModel = st.model || {};
    var editsUnavailable = selectedModel.repo_editing === false;
    var agentsUnavailable = boot().agentsRuntime && boot().agentsRuntime.supported === false;
    var pop = els.modePop;
    var limitsOpen = !!pop.querySelector('[data-agents-limits][open]');
    var offered = {};
    modes.forEach(function (m) { offered[m.id] = true; });
    var editRow = function (entry, index) {
      var editMode = entry.id !== "plan" && entry.id !== "ask";
      return menuRow({
        role: "menuitemradio",
        title: entry.label,
        desc: entry.desc,
        active: entry.id === cur,
        dot: dotVar(MODE_DOT[entry.id] || "accent"),
        meta: index == null ? "" : String(index + 1),
        disabled: editsUnavailable && editMode,
      }).replace('class="cpop-row', 'data-id="' + esc(entry.id) + '" class="cpop-row');
    };
    var rows = MODE_MENU.filter(function (entry) { return offered[entry.id]; });
    var bypassOn = st.bypassPermissions === true;
    pop.innerHTML =
      '<div class="cpop-head">Mode</div>' +
      rows.map(editRow).join("") +
      // Below the ladder and unnumbered, because it is not the next rung: it
      // is the decision to stop being asked at all. It is a checkbox rather
      // than a radio so the mode above stays selected while it is on.
      '<div class="cpop-sep" role="separator"></div>' +
      menuRow({
        role: "menuitemcheckbox",
        title: BYPASS_SWITCH.label,
        desc: bypassOn ? BYPASS_SWITCH.on : BYPASS_SWITCH.off,
        active: bypassOn,
        dot: dotVar("caution"),
        // Skipping the asking cannot grant an ability the CLI does not have:
        // when a provider offers no scoped edit controls, bypass buys nothing
        // and would only promise authority that will not materialise.
        disabled: editsUnavailable,
      }).replace('class="cpop-row', 'data-bypass="1" class="cpop-row cpop-row-switch') +
      '<div class="cpop-sep" role="separator"></div>' +
      menuRow({ role: "menuitemcheckbox", title: "Allow multiple agents mode", desc: agentsUnavailable ? boot().agentsRuntime.reason : "Coordinate independent assignments within your current permissions", active: st.multiAgentEnabled === true, disabled: agentsUnavailable }).replace('class="cpop-row', 'data-multi-agent="true" class="cpop-row') +
      (st.multiAgentEnabled ? menuRow({ role: "menuitemcheckbox", title: "Allow cloud providers for this objective", desc: "Sends code and context to cloud providers and may use paid or account quota. Applies to the next objective only.", active: st.agentsAllowCloud === true }).replace('class="cpop-row', 'data-agents-cloud="true" class="cpop-row') : "") +
      (st.multiAgentEnabled ? '<details class="cpop-agent-limits" data-agents-limits' + (limitsOpen ? ' open' : '') + '><summary>Team limits</summary><div class="cpop-agent-fields"><label>Concurrent agents<select data-agents-parallel aria-label="Concurrent agents">' + [1, 2, 3, 4].map(function (n) { return '<option value="' + n + '"' + (n === (st.agentsMaxParallel || 2) ? ' selected' : '') + '>' + n + (n === 1 ? ' · Sequential' : n === 2 ? ' · Default' : '') + '</option>'; }).join('') + '</select></label><label>Objective budget (USD)<input data-agents-budget aria-label="Objective budget in USD" type="text" inputmode="decimal" maxlength="100" placeholder="No cap" value="' + esc(st.agentsBudgetUsd || '') + '"></label></div><p class="cpop-note">Applies to each new objective in this workspace session. A dollar cap blocks providers that cannot enforce it.</p></details>' : '') +
      (editsUnavailable
        ? '<p class="cpop-note cpop-note-warn">Update this provider CLI to enable scoped edits. Plan remains available.</p>'
        : "");
    pop.querySelectorAll("[data-id]").forEach(function (row) {
      row.onclick = function () { setMode(row.dataset.id); closePopovers(); };
    });
    pop.querySelector("[data-multi-agent]").onclick = function (event) {
      event.stopPropagation();
      var api = global.__opai || {};
      if (api.setMultiAgentEnabled) api.setMultiAgentEnabled(!state().multiAgentEnabled);
      buildModePop();
      pop.querySelector("[data-multi-agent]").focus();
    };
    var cloudToggle = pop.querySelector("[data-agents-cloud]");
    if (cloudToggle) cloudToggle.onclick = function (event) {
      event.stopPropagation();
      var api = global.__opai || {};
      if (api.setAgentsAllowCloud) api.setAgentsAllowCloud(!state().agentsAllowCloud);
      buildModePop();
      pop.querySelector("[data-agents-cloud]").focus();
    };
    var parallel = pop.querySelector('[data-agents-parallel]');
    var budget = pop.querySelector('[data-agents-budget]');
    if (parallel) parallel.onchange = function () {
      var api = global.__opai || {};
      if (api.setAgentsRunSettings) api.setAgentsRunSettings({ maxParallel: Number(parallel.value) });
    };
    if (budget) budget.oninput = function () {
      var api = global.__opai || {};
      if (api.setAgentsRunSettings) api.setAgentsRunSettings({ budgetUsd: budget.value });
    };
    var limits = pop.querySelector('[data-agents-limits]');
    if (limits) limits.ontoggle = fitModePop;
    fitModePop();
    var bypassRow = pop.querySelector("[data-bypass]");
    if (bypassRow) {
      bypassRow.onclick = function () {
        if (editsUnavailable) return;
        // Toggles authority only. The selected mode is untouched, which is the
        // whole point of it being a switch: turning it off returns the user to
        // the mode they were already working in.
        setBypass(!bypassOn);
        buildModePop();
      };
    }
    // 1-4 pick a graded mode while the menu is open. Bypass has no number on
    // purpose -- a keystroke is exactly the kind of drift it should not have.
    pop.onkeydown = function (event) {
      if (event.target.matches('input, select, textarea') || event.target.isContentEditable) return;
      var index = "1234".indexOf(event.key);
      if (index < 0 || index >= rows.length) return;
      var target = rows[index];
      if (editsUnavailable && target.id !== "plan") return;
      event.preventDefault();
      setMode(target.id);
      closePopovers();
    };
  }

  // Local UI state for the picker (never persisted — see docs/design/
  // opai-model-picker-redesign.md §14 "State ownership"). Selection itself
  // still lives in the app's real store, driven via setModel().
  var pickerQuery = "";

  // A model is shown in the picker only when it is a real, configured provider
  // model (not the Auto card, not the routing group) — implementing the
  // redesign's core rule: unconfigured/placeholder providers never appear here,
  // only in Manage models. `available === false` means not configured / not
  // authenticated (a free key that isn't set, an expired account); those are
  // fully excluded. A configured model whose provider is currently failing
  // (`healthy === false`, e.g. a suspended free-tier account) is *shown but
  // disabled* with a reason, so it is never silently missing.
  function pickerModels() {
    return (boot().models || []).filter(function (m) {
      return m && m.kind !== "auto" && m.group !== "routing" &&
        (!global.OPaiModelVisibility || global.OPaiModelVisibility(m));
    });
  }
  function isConfigured(m) { return m.available !== false; }
  function isWorking(m) { return m.available !== false && m.healthy !== false; }
  function modelShortProvider(m) {
    if (m.provider) return m.provider.charAt(0).toUpperCase() + m.provider.slice(1);
    if (m.kind === "local") return "Local";
    return "";
  }
  function modelName(m) {
    // Strip the "OPai · " / "Provider · " prefix and the "(free tier)" suffix so
    // the flat row reads as a clean model name; the provider is its own column.
    return String(m.label || "")
      .replace(/^OPai\s*·\s*/, "")
      .replace(/\s*\(free tier\)\s*$/i, "")
      .trim() || (m.id || "Model");
  }
  // Exact remaining credit for a model's provider, when OPai knows it —
  // "€85.00 left" from a live balance, the user's manual entry, or an
  // observed refusal. Unknown balances show nothing (never a made-up number).
  var BALANCE_SYMBOLS = { USD: "$", EUR: "€", GBP: "£", CNY: "¥", JPY: "¥" };
  function balanceLabel(m) {
    var b = m && m.balance;
    if (!b || b.amount == null || isNaN(b.amount)) return "";
    var symbol = BALANCE_SYMBOLS[b.currency];
    var value = Number(b.amount).toFixed(2);
    return (symbol ? symbol + value : value + " " + (b.currency || "")) + " left";
  }
  // Out-of-credit models are removed from selection entirely; this builds the
  // one-line explanation of what was hidden and why (per provider, deduped).
  // A model that works for Ask and Plan but that OPai will refuse to hand
  // repository write access (Copilot's CLI today, because it cannot expose a
  // bounded edit-tool set). It stays fully selectable — read-only work is a
  // legitimate use — but the row says so up front instead of letting the user
  // pick it for an editing task and hit the refusal mid-run.
  function readOnlyNote(m) {
    return m.repo_editing === false ? "Ask & Plan only — can't edit files" : "";
  }

  function outOfCreditNote(models) {
    var names = [];
    models.forEach(function (m) {
      if (!m.out_of_credit) return;
      var b = m.balance || {};
      var name = b.displayName || modelShortProvider(m) || m.provider || "A provider";
      if (names.indexOf(name) < 0) names.push(name);
    });
    if (!names.length) return "";
    return (
      names.join(", ") +
      (names.length === 1 ? " is" : " are") +
      " hidden — out of credit. Top up or update the balance in Settings › Credits & Balance."
    );
  }

  function buildModelPop() {
    var st = state();
    var cur = (st.model && st.model.id) || "";
    var all = pickerModels();

    // Configured models split into working (selectable) and configured-but-
    // currently-failing (shown disabled, never hidden). Unconfigured models are
    // excluded entirely — they live only in Manage models.
    var configured = all.filter(isConfigured);
    var q = pickerQuery.trim().toLowerCase();
    function matches(m) {
      return !q || modelName(m).toLowerCase().indexOf(q) >= 0 ||
        modelShortProvider(m).toLowerCase().indexOf(q) >= 0;
    }
    var working = configured.filter(isWorking).filter(matches);
    var failing = configured.filter(function (m) { return !isWorking(m); }).filter(matches);

    var autoSelected = !cur || cur === "auto";
    // A previously chosen model that is no longer configured/working: fall the
    // display back to Auto and tell the user why — never a silent switch.
    var selectedModel = all.find(function (m) { return m.id === cur; });
    var selectedUnavailable = !!(cur && cur !== "auto" && (!selectedModel || !isWorking(selectedModel)));
    if (selectedUnavailable) autoSelected = true;

    var totalConfigured = configured.length;
    var showSearch = totalConfigured > 4;
    var emptyConfigured = totalConfigured === 0;

    var html = "";

    // Selection-unavailable / empty notes (calm amber).
    if (selectedUnavailable && selectedModel) {
      html += '<div class="cpop-note cpop-note-warn">' + esc(modelName(selectedModel)) +
        " is unavailable right now — using Auto for this chat.</div>";
    } else if (emptyConfigured) {
      html += '<div class="cpop-note">No models set up yet — Auto will use a local model if one is running.</div>';
    }
    // Out-of-credit models are removed (available=false), never shown as dead
    // rows — but their absence is always explained, never silent.
    var creditNote = outOfCreditNote(all);
    if (creditNote) {
      html += '<div class="cpop-note cpop-note-warn" data-credit-note>' + esc(creditNote) + "</div>";
    }

    // 1. Auto, first in the list and shaped like everything under it.
    //
    // It used to be a bordered, tinted card carrying a Recommended pill and a
    // sentence of explanation -- roughly the height of three model rows, above
    // a toggle, above a search box, before the first model appeared. The word
    // "Recommended" is the whole explanation, so it takes the slot every other
    // row uses for its provider and the rest goes.
    //
    // The routing toggle went with it. "Keep work on this machine" set the
    // model to Auto when it was off and did nothing at all when it was on --
    // clicking Auto did the same thing, one row above. A control that is a
    // duplicate half the time and a no-op the other half is not a preference,
    // it is furniture.
    html +=
      '<button type="button" role="menuitemradio" aria-checked="' + (autoSelected ? "true" : "false") +
      '" data-id="auto" class="cpop-row cpop-model cpop-auto' + (autoSelected ? " active" : "") + '">' +
      '<span class="cpop-body"><span class="cpop-title">Auto</span></span>' +
      '<span class="cpop-prov cpop-auto-tag">Recommended</span>' +
      (autoSelected ? '<span class="cpop-check">' + CHECK_SVG + "</span>" : "") +
      "</button>";

    // 3. Search — only once there are enough models to warrant it.
    if (showSearch) {
      html += '<div class="cpop-search"><input id="modelSearch" type="text" placeholder="Search models or providers" ' +
        'aria-label="Search models" value="' + esc(pickerQuery) + '"/></div>';
    }

    // 4. Bounded, scrollable list of configured models.
    if (!emptyConfigured) {
      html += '<div class="cpop-scroll">';
      if (!working.length && !failing.length && q) {
        html += '<div class="cpop-empty">No models match "' + esc(pickerQuery) + '"</div>';
      }
      working.concat(failing).forEach(function (m) {
        var disabled = !isWorking(m);
        var credit = balanceLabel(m);
        var readOnly = disabled ? "" : readOnlyNote(m);
        html +=
          '<button type="button" role="menuitemradio" aria-checked="' + (m.id === cur ? "true" : "false") +
          '" data-id="' + esc(m.id) + '" class="cpop-row cpop-model' + (m.id === cur && !selectedUnavailable ? " active" : "") + '"' +
          (disabled ? ' disabled aria-disabled="true" title="' + esc(m.health_reason || m.disabled_reason || "Unavailable") + '"' : "") +
          (readOnly ? ' title="' + esc(m.edit_blocked_reason || readOnly) + '"' : "") + ">" +
          '<span class="cpop-body"><span class="cpop-title">' + esc(modelName(m)) + "</span>" +
          (disabled ? '<span class="cpop-desc">' + esc(m.health_reason || "Currently unavailable") + "</span>" : "") +
          (readOnly ? '<span class="cpop-desc" data-read-only>' + esc(readOnly) + "</span>" : "") +
          "</span>" +
          (credit ? '<span class="cpop-balance" data-balance>' + esc(credit) + "</span>" : "") +
          '<span class="cpop-prov">' + esc(modelShortProvider(m)) + "</span>" +
          (m.id === cur && !selectedUnavailable ? '<span class="cpop-check">' + CHECK_SVG + "</span>" : "") +
          "</button>";
      });
      html += "</div>";
    }

    // 5. Footer — the single home for provider setup, kept out of selection.
    html += '<button type="button" role="menuitem" id="manageModels" class="cpop-row cpop-manage">Manage models</button>';

    els.modelPop.innerHTML = html;

    els.modelPop.querySelectorAll("[data-id]").forEach(function (row) {
      if (row.disabled) return;
      row.onclick = function () { pickerQuery = ""; setModel(row.dataset.id); closePopovers(); };
    });
    var search = els.modelPop.querySelector("#modelSearch");
    if (search) {
      search.oninput = function () { pickerQuery = search.value; var s = search.selectionStart; buildModelPop(); var again = els.modelPop.querySelector("#modelSearch"); if (again) { again.focus(); try { again.setSelectionRange(s, s); } catch (_e) { /* ignore */ } } };
    }
    var manage = els.modelPop.querySelector("#manageModels");
    if (manage) manage.onclick = function () {
      closePopovers();
      if (global.__opai && global.__opai.openSettings) global.__opai.openSettings("models");
    };
  }
  var CHECK_SVG = '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.5 3.5L13 5"/></svg>';

  function buildMorePop() {
    var st = state();
    var style = currentStyle();
    var pop = els.morePop;
    var rows = "";
    // In single-line mode the overflow owns everything, so context, model and
    // permissions all live here; otherwise they have their own toolbar buttons.
    if (style === "single") {
      rows += '<button type="button" role="menuitem" class="cpop-row" data-act="context"><span class="cpop-body"><span class="cpop-title">Add context…</span></span></button>';
    }
    rows +=
      '<button type="button" role="menuitem" class="cpop-row" data-act="model"><span class="cpop-body"><span class="cpop-title">Model &amp; routing</span></span><span class="cpop-meta">' + esc(shortModel(st.model)) + "</span></button>" +
      '<button type="button" role="menuitem" class="cpop-row" data-act="mode"><span class="cpop-body"><span class="cpop-title">Change permissions</span></span><span class="cpop-meta">' + esc(modeLabelOf(st.mode)) + "</span></button>" +
      '<button type="button" role="menuitem" class="cpop-row" data-act="shortcuts"><span class="cpop-body"><span class="cpop-title">Keyboard shortcuts</span></span><span class="cpop-kbd">?</span></button>' +
      '<div class="cpop-sep"></div>' +
      '<div class="cpop-scope-label">These settings apply to</div>' +
      '<div class="cpop-scope" role="group" aria-label="Settings scope">' +
      ["This repo", "Workspace", "Everywhere"].map(function (s, i) {
        return '<button type="button" data-scope="' + i + '" class="' + (i === scopeIdx ? "active" : "") + '">' + esc(s) + "</button>";
      }).join("") +
      "</div>";
    pop.innerHTML = '<div class="cpop-head">Composer settings</div>' + rows;
    var map = {
      context: function () { closePopovers(); openContext(); },
      model: function () { closePopovers(); openModel(); },
      mode: function () { closePopovers(); openMode(); },
      shortcuts: function () { closePopovers(); if (global.__opai && global.__opai.runCommand) global.__opai.runCommand("shortcuts"); },
    };
    pop.querySelectorAll("[data-act]").forEach(function (b) {
      var fn = map[b.dataset.act]; if (fn) b.onclick = fn;
    });
    pop.querySelectorAll("[data-scope]").forEach(function (b) {
      b.onclick = function () {
        scopeIdx = Number(b.dataset.scope) || 0;
        pop.querySelectorAll("[data-scope]").forEach(function (o) { o.classList.toggle("active", o === b); });
      };
    });
  }
  var scopeIdx = 0;

  function openContext() { togglePop("ctxPop", "ctxBtn", buildContextPop); }
  function openMode() { togglePop("modePop", "modeBtn", buildModePop); }
  function openModel() { togglePop("modelPop", "modelBtn", buildModelPop); }
  function openMore() { togglePop("morePop", "moreBtn", buildMorePop); }

  /* ---------- style ---------- */
  function currentStyle() {
    var s = els.composer ? els.composer.getAttribute("data-composer-style") : DEFAULT_STYLE;
    return STYLES.indexOf(s) >= 0 ? s : DEFAULT_STYLE;
  }
  function setStyle(style) {
    if (STYLES.indexOf(style) < 0) style = DEFAULT_STYLE;
    if (els.composer) els.composer.setAttribute("data-composer-style", style);
    if (els.tokens) els.tokens.hidden = style !== "command";
    closePopovers();
    refresh();
  }

  /* ---------- refresh (labels, summary, status) ---------- */
  function refresh() {
    if (!els.composer) return;
    var st = state();
    var mode = st.mode || {};
    var model = st.model || {};
    var teamButton = document.getElementById('teamModeBtn');
    if (teamButton) {
      teamButton.setAttribute('aria-pressed', String(st.multiAgentEnabled === true));
      teamButton.setAttribute('aria-label', st.multiAgentEnabled ? 'Toggle AI Team panel' : 'Enable AI Team');
      teamButton.setAttribute('aria-expanded', String(st.teamOpen === true && st.view === 'chat'));
      teamButton.disabled = boot().agentsRuntime && boot().agentsRuntime.supported === false;
      teamButton.title = teamButton.disabled ? boot().agentsRuntime.reason : 'Use an AI team for your objective';
    }

    // Mode button — visible text shows the value; aria-label carries purpose +
    // value so the menu button announces both to assistive tech.
    var mLabel = modeLabelOf(mode);
    // Bypass is authority the user cannot see from the mode name alone, and it
    // is the one state worth noticing without opening the menu. The mode keeps
    // its own name -- the switch is additive, so the pill says so.
    var bypassing = st.bypassPermissions === true;
    if (els.modeBtnLabel) {
      els.modeBtnLabel.textContent = mLabel + (bypassing ? " · Bypass" : "") + (st.multiAgentEnabled ? " · Agents" : "");
    }
    if (els.modeDot) {
      els.modeDot.style.background = dotVar(
        bypassing ? "caution" : MODE_DOT[mode.id] || "accent"
      );
    }
    if (els.modeBtn) {
      els.modeBtn.setAttribute(
        "aria-label",
        bypassing
          ? "Mode: " + mLabel + ", permissions bypassed" + (st.multiAgentEnabled ? ", multiple agents enabled" : "")
          : "Mode: " + mLabel + (st.multiAgentEnabled ? ", multiple agents enabled" : "")
      );
    }
    // Model button
    var mdLabel = shortModel(model);
    if (els.modelBtnLabel) els.modelBtnLabel.textContent = mdLabel;
    if (els.modelBtn) els.modelBtn.setAttribute("aria-label", "Model: " + mdLabel);

    // Status header driven by the existing run state (no new run logic).
    if (els.status) {
      if (st.busy) {
        els.status.hidden = false;
        els.status.innerHTML =
          '<span class="cstatus-ico">' + icon("running") + "</span>" +
          '<span class="cstatus-title">Working</span>' +
          '<span class="cstatus-esc">Esc to stop</span>';
      } else {
        els.status.hidden = true;
        els.status.innerHTML = "";
      }
    }
  }

  /* ---------- command-bar token accelerators ---------- */
  function wireCommandTokens() {
    var input = $("#input");
    if (!input) return;
    // Clicking a token chip opens the matching popover.
    if (els.tokens) {
      els.tokens.querySelectorAll("[data-token]").forEach(function (b) {
        b.onclick = function () {
          if (b.dataset.token === "@") openModel();
          else if (b.dataset.token === "/") openMode();
          else openContext();
        };
      });
    }
    // In command mode, a leading trigger char opens the popover (accelerator,
    // never a requirement — the same actions stay reachable by pointer).
    input.addEventListener("keyup", function (e) {
      if (currentStyle() !== "command") return;
      var v = input.value;
      if (v === "@" && e.key === "@") openModel();
      else if (v === "/" && e.key === "/") openMode();
      else if (v === "#" && e.key === "#") openContext();
    });
  }

  /* ---------- init ---------- */
  function cache() {
    ["composer", "composerStatus", "composerTokens",       "ctxBtn", "moreBtn", "modeBtn", "modelBtn", "modeBtnLabel", "modelBtnLabel", "modeDot",
      "ctxPop", "modePop", "modelPop", "morePop", "composerDrop"].forEach(function (id) {
      els[id] = document.getElementById(id);
    });
    els.status = els.composerStatus;
    els.tokens = els.composerTokens;
  }

  function init() {
    cache();
    if (!els.composer) return;
    els.ctxBtn && (els.ctxBtn.onclick = openContext);
    els.moreBtn && (els.moreBtn.onclick = openMore);
    els.modeBtn && (els.modeBtn.onclick = openMode);
    els.modelBtn && (els.modelBtn.onclick = openModel);
    var teamButton = document.getElementById('teamModeBtn');
    if (teamButton) teamButton.onclick = function () {
      if (!global.__opai) return;
      if (state().multiAgentEnabled && global.__opai.toggleAgentTeam) global.__opai.toggleAgentTeam();
      else global.__opai.setMultiAgentEnabled(true);
    };
    window.addEventListener('resize', fitModePop);

    // Outside click / Escape close (Escape only closes popovers; app.js owns
    // Escape-to-stop when a run is active and no popover is open).
    document.addEventListener("click", function (e) {
      if (!openPop) return;
      if (els.composer.contains(e.target)) return;
      closePopovers();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && openPop) { e.stopPropagation(); closePopovers(); }
      var ctrl = e.ctrlKey || e.metaKey;
      if (ctrl && e.key === "/") { e.preventDefault(); openContext(); }
    }, true);

    wireCommandTokens();

    // Apply the saved style once boot prefs are available; refresh continuously.
    var prefs = boot().prefs || {};
    setStyle(prefs.composerStyle || DEFAULT_STYLE);
    refresh();
  }

  // Run after app.js has wired and (ideally) booted. app.js also calls
  // OPaiComposer.refresh()/applyStyle() at the right lifecycle points.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.OPaiComposer = {
    init: init,
    refresh: refresh,
    setStyle: setStyle,
    currentStyle: currentStyle,
    applyBootStyle: function () { setStyle((boot().prefs || {}).composerStyle || DEFAULT_STYLE); },
    openModel: openModel,
    closePopovers: closePopovers,
  };
})(typeof window !== "undefined" ? window : this);
