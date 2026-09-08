/* Playwright harness: stubs QWebChannel + qt and a controllable mock bridge so
   the real front-end (activity.js + app.js) runs in Chromium without Qt. The
   spec drives streaming/errors via window.__mock.* helpers. Injected with
   page.addInitScript before any page script runs. */
(function () {
  "use strict";
  function merge(base, override) {
    if (!override || typeof override !== "object" || Array.isArray(override)) {
      return override === undefined ? base : override;
    }
    var result = Object.assign({}, base);
    Object.keys(override).forEach(function (key) {
      var value = override[key];
      result[key] = value && typeof value === "object" && !Array.isArray(value)
        ? merge(base && base[key] ? base[key] : {}, value)
        : value;
    });
    return result;
  }
  function Sig() {
    var fns = [];
    return {
      connect: function (f) { fns.push(f); },
      emit: function () { var a = arguments; fns.forEach(function (f) { f.apply(null, a); }); },
    };
  }
  var defaultBoot = {
    workspace: { label: "demo", root: "/demo", name: "demo", branch: "main", file_count: 3, recents: [{ path: "/other/proj", label: "other/proj" }] },
    recents: ["summarize my changes"],
    conversations: [
      { id: "conv-1", title: "summarize my changes", message_count: 2, updated_at: "2026-08-02" },
    ],
    update: {
      operation: { state: "up_to_date", candidate: null, safe_diagnostic: null },
      policy: {
        discovery_enabled: true, automatic_downloads: false,
        automatic_install_on_quit: false, channel: "stable", owner: "opai",
      },
      installed: { version: "0.2.1a1", build_id: "test-build", install_type: "portable" },
    },
    brand: {
      name: "OPai",
      tagline: "Every step visible. Every dollar accounted.",
      emptyTitle: "Better. Faster. Cheaper.",
      composerPlaceholder: "Tell OPai what to build, fix, or explain…",
    },
    models: [
      { id: "account:claude:opus", label: "OPai · Powerful mode", advanced_label: "Claude Opus 4.8 via Anthropic account connector", kind: "account", provider: "claude", badge: "slower · highest · $$$" },
      { id: "auto", label: "OPai · Auto mode", advanced_label: "Automatic local-first routing", kind: "auto", badge: "" },
    ],
    selectedModel: "account:claude:opus",
    modes: [{ id: "ask", label: "Ask" }, { id: "safe-auto", label: "Safe Auto" }],
    navGroups: [{ group: "Control", items: [{ id: "home", label: "Home" }, { id: "agents", label: "Agents" }] }], taskModes: [{ id: "general", label: "General" }], outputFormats: [{ id: "normal", label: "Normal" }],
    prefs: { model: "account:claude:opus", mode: "ask", focus: "general", format: "normal", showPanel: true, onboardingSeen: true },
    accounts: [{ id: "claude", label: "Claude", connected: true }],
    connections: [],
    modelOverrides: { global: true, path: "~/.opai/models.json", providers: {}, hidden: {}, errors: [] },
    status: { on: true, line: "OPai · Ask · $0.00 today · $0.00 saved" },
    inspector: { rows: [], budget: { pct: 0, text: "$0.00 today" }, permissions: [], privacy: [] },
    defaultView: "chat", initialTask: "", tools: [],
    resume: { available: false, requires_choice: false, thread: {}, workflow: {}, checkpoint: {} },
  };
  var scenario = window.__OPAI_TEST_SCENARIO__ || {};
  var boot = merge(defaultBoot, scenario.boot || {});
  var defaultDashboards = {
    home: {
      title: "Mission Control",
      subtitle: "Observed proxy sessions only. Direct unwrapped launches are not measurable yet.",
      kpis: [
        { label: "Capture health", value: "67%", severity: "warning" },
        { label: "Observed sessions", value: "3", severity: "neutral" },
      ],
      cards: [{ title: "Capture gap", body: "1 fail-open session was not accounted.", severity: "warning" }],
    },
    agents: {
      title: "Agent Readiness",
      subtitle: "Know which launches OPai can capture.",
      cards: [{
        title: "Codex", status: "ACTIVE", severity: "success",
        metrics: [
          { label: "Wrapper", value: "installed", severity: "success" },
          { label: "Capture", value: "selective proxy", severity: "success" },
        ],
      }],
    },
  };
  var dashboards = merge(defaultDashboards, scenario.dashboards || {});
  var promptData = scenario.prompts || [];
  var settings = scenario.settings || { prefs: {}, firewall: {}, permissions: [], accounts: [], about: {} };
  var updateState = boot.update;
  // GitHub connect/consent state (#300): stateful so connect/toggle change what
  // subsequent settingsData / githubStatus report — the real flow.
  var githubState = scenario.github || {
    connected: false, allow_push: false, ready_for_push: false,
    readiness_reason: "no_token_and_consent_off", login: "", token_source: "",
    hint: "Connect a token, then enable pushes.",
  };
  settings.github = githubState;
  function respond(cb, value, delay) {
    if (delay) setTimeout(function () { cb(JSON.stringify(value)); }, delay);
    else cb(JSON.stringify(value));
  }
  var bridge = {
    replyReady: Sig(), buildReady: Sig(), activity: Sig(), activityBatch: Sig(), token: Sig(), toolReady: Sig(), cancelReady: Sig(), workspaceChanged: Sig(), modelsChanged: Sig(), providerLoginReady: Sig(), connectionDoctorReady: Sig(), updateReady: Sig(),
    dashboardReady: Sig(), objectiveReady: Sig(), objectiveControlReady: Sig(), settingsReady: Sig(), toolApplied: Sig(), statusReady: Sig(),
    workspaceReady: Sig(), inspectorReady: Sig(),
    boot: function (cb) { cb(JSON.stringify(boot)); },
    // Round 2: the header's "N uncommitted" badge came from the boot payload
    // and was never recomputed, so it stayed stale after a run committed. The
    // app now re-reads the workspace when a turn ends; a scenario can supply
    // the post-run state via scenario.workspaceAfterRun.
    workspaceState: function (cb) {
      window.__mock.workspaceStateCalls += 1;
      var after = scenario.workspaceAfterRun;
      if (after) boot.workspace = merge(boot.workspace || {}, after);
      cb(JSON.stringify(boot.workspace));
    },
    inspector: function (s, cb) { cb(JSON.stringify(boot.inspector)); },
    requestWorkspace: function (requestId) {
      window.__mock.workspaceRequests.push(requestId);
      var after = scenario.workspaceAfterRun;
      if (after) boot.workspace = merge(boot.workspace || {}, after);
      var response = scenario.workspaceResponsePartial && after
        ? merge({}, after)
        : merge({}, boot.workspace || {});
      setTimeout(function () {
        bridge.workspaceReady.emit(JSON.stringify({ requestId: requestId, data: response }));
      }, scenario.workspaceDelayMs || 0);
    },
    requestInspector: function (s, requestId) {
      window.__mock.inspectorRequests.push(requestId);
      setTimeout(function () {
        bridge.inspectorReady.emit(JSON.stringify({ requestId: requestId, data: boot.inspector }));
      }, scenario.inspectorDelayMs || 0);
    },
    statusLine: function (s, cb) { cb(JSON.stringify(boot.status)); },
    requestStatus: function (sel, requestId) {
      window.__mock.statusRequests.push(requestId);
      setTimeout(function () {
        bridge.statusReady.emit(JSON.stringify({ requestId: requestId, data: boot.status }));
      }, scenario.statusDelayMs || 0);
    },
    dashboard: function (id, cb) {
      var error = scenario.dashboardErrors && scenario.dashboardErrors[id];
      respond(cb, error ? { error: error } : (dashboards[id] || {}), scenario.dashboardDelayMs);
    },
    // #146: async data path — the production bridge computes these on a worker
    // thread and delivers via signals; the mock mirrors that timing contract.
    requestDashboard: function (id, requestId) {
      window.__mock.dashboardRequests.push({ sectionId: id, requestId: requestId });
      var error = scenario.dashboardErrors && scenario.dashboardErrors[id];
      var data = error ? { error: error } : (dashboards[id] || {});
      setTimeout(function () {
        bridge.dashboardReady.emit(JSON.stringify({ requestId: requestId, sectionId: id, data: data }));
      }, scenario.dashboardDelayMs || 0);
    },
    requestSettings: function (requestId) {
      window.__mock.settingsRequests.push(requestId);
      setTimeout(function () {
        bridge.settingsReady.emit(JSON.stringify({ requestId: requestId, data: settings }));
      }, scenario.settingsDelayMs || 0);
    },
    applyToolAsync: function (name, requestId) {
      window.__mock.appliedTools.push(name);
      var response = (scenario.applyToolResponses && scenario.applyToolResponses[name]) || { text: "Applied safely." };
      setTimeout(function () {
        bridge.toolApplied.emit(JSON.stringify({ requestId: requestId, data: response }));
      }, scenario.applyToolDelayMs || 0);
    },
    prompts: function (q, c, cb) {
      var query = String(q || "").toLowerCase().trim();
      var items = promptData.filter(function (p) {
        var hay = [p.title, p.desc, p.category, (p.tags || []).join(" ")].join(" ").toLowerCase();
        return (!c || p.category === c) && (!query || query.split(/\s+/).every(function (token) { return hay.indexOf(token) >= 0; }));
      });
      var categories = Array.from(new Set(promptData.map(function (p) { return p.category; })));
      respond(cb, { categories: categories, prompts: items }, scenario.promptDelayMs);
    },
    usePrompt: function (id, cb) { cb(JSON.stringify(promptData.find(function (p) { return p.id === id; }) || {})); },
    settingsData: function (cb) { respond(cb, settings, scenario.settingsDelayMs); },
    saveProviderKey: function (provider, key, cb) {
      window.__mock.savedProviderKeys.push([provider, key]);
      cb(JSON.stringify({ provider: provider, configured: true, source: "keychain", keychainAvailable: true }));
    },
    deleteProviderKey: function (provider, cb) {
      window.__mock.deletedProviderKeys.push(provider);
      cb(JSON.stringify({ provider: provider, configured: false, source: null, keychainAvailable: true }));
    },
    githubStatus: function (cb) { cb(JSON.stringify(githubState)); },
    githubConnect: function (token, cb) {
      window.__mock.githubConnects.push(token);
      var res = scenario.githubConnectResponse;
      if (res && res.connected === false) { cb(JSON.stringify(res)); return; }
      githubState.connected = true;
      githubState.login = (res && res.login) || "octocat";
      githubState.ready_for_push = !!githubState.allow_push;
      githubState.readiness_reason = githubState.allow_push ? "ready" : "consent_off";
      githubState.hint = githubState.allow_push ? "" : "A token is connected. Enable pushes/PRs.";
      cb(JSON.stringify({ connected: true, login: githubState.login, ready_for_push: githubState.ready_for_push, readiness_reason: githubState.readiness_reason }));
    },
    githubSetPush: function (state, cb) {
      var on = String(state) === "on";
      window.__mock.githubPushToggles.push(on);
      githubState.allow_push = on;
      githubState.ready_for_push = on && githubState.connected;
      githubState.readiness_reason = githubState.ready_for_push ? "ready" : (githubState.connected ? "consent_off" : "no_token");
      cb(JSON.stringify({ allow_push: on, connected: githubState.connected, ready: githubState.ready_for_push, reason: githubState.readiness_reason, next_step: "" }));
    },
    githubDisconnect: function (cb) {
      window.__mock.githubDisconnects++;
      githubState.connected = false; githubState.allow_push = false; githubState.ready_for_push = false;
      githubState.login = ""; githubState.readiness_reason = "no_token_and_consent_off";
      cb(JSON.stringify({ disconnected: true }));
    },
    testProvider: function (provider, cb) {
      window.__mock.providerTests.push(provider);
      cb(JSON.stringify((scenario.providerTestResponses && scenario.providerTestResponses[provider]) || { provider: provider, connected: true }));
    },
    saveUsageLimit: function (model, metric, limit, windowName, cb) {
      window.__mock.savedUsageLimits.push([model, metric, +limit, windowName]);
      cb(JSON.stringify({ ok: true }));
    },
    refreshUsage: function (cb) {
      window.__mock.usageRefreshes++;
      var usage = scenario.refreshedUsage || (settings && settings.providerUsage) || [];
      cb(JSON.stringify({ ok: true, usage: usage }));
    },
    refreshModels: function (cb) {
      cb(JSON.stringify({
        models: boot.models,
        accounts: boot.accounts,
        connections: boot.connections || [],
        modelOverrides: boot.modelOverrides,
      }));
    },
    saveModelOverrides: function (payload, cb) {
      var parsed = {};
      try { parsed = JSON.parse(payload); } catch (_e) {
        cb(JSON.stringify({ ok: false, error: "Invalid model picker data." }));
        return;
      }
      window.__mock.savedModelOverrides.push(parsed);
      var providers = {};
      var hidden = {};
      Object.keys(parsed.providers || {}).forEach(function (provider) {
        var block = parsed.providers[provider] || {};
        providers[provider] = { models: (block.models || []).map(function (entry) { return Object.assign({}, entry); }) };
        if ((block.hide || []).length) hidden[provider] = block.hide.slice();
      });
      var report = { global: true, path: "~/.opai/models.json", providers: providers, hidden: hidden, errors: [] };
      var baseModels = (boot.models || []).filter(function (model) { return !model.__mockCustom; });
      var models = baseModels.slice();
      Object.keys(providers).forEach(function (provider) {
        var template = baseModels.find(function (model) { return model.provider === provider && model.kind === "account"; });
        if (!template) return;
        providers[provider].models.forEach(function (entry) {
          if (models.some(function (model) { return model.provider === provider && model.model === entry.id; })) return;
          models.push(Object.assign({}, template, {
            id: "account:" + provider + ":" + entry.id,
            model: entry.id,
            label: provider.charAt(0).toUpperCase() + provider.slice(1) + " · " + (entry.display || entry.id),
            __mockCustom: true,
          }));
        });
      });
      boot.models = models;
      boot.modelOverrides = report;
      settings.modelOverrides = report;
      var catalog = { models: models, accounts: boot.accounts, connections: boot.connections || [], modelOverrides: report };
      setTimeout(function () {
        cb(JSON.stringify({ ok: true, modelOverrides: report, catalog: catalog }));
      }, 0);
    },
    discoverModels: function () {
      window.__mock.modelDiscoveries++;
      if (scenario.discoveredModels && !scenario.deferDiscovery) setTimeout(function () {
        bridge.modelsChanged.emit(JSON.stringify({
          models: scenario.discoveredModels,
          accounts: scenario.discoveredAccounts || boot.accounts,
          connections: scenario.discoveredConnections || boot.connections,
        }));
      }, scenario.discoveryDelayMs || 0);
    },
    repairCodexConfig: function (cb) {
      window.__mock.codexRepairs++;
      cb(JSON.stringify({ repaired: true, backupPath: "/tmp/config.toml.bak" }));
    },
    markInteractive: function () { window.__mock.interactiveMarks++; },
    updateStatus: function (cb) { cb(JSON.stringify(updateState)); },
    checkForUpdates: function (force, cb) {
      window.__mock.updateChecks.push(!!force);
      updateState = scenario.updateCheckResponse || updateState;
      boot.update = updateState;
      setTimeout(function () {
        bridge.updateReady.emit(JSON.stringify(updateState));
        if (cb) cb(JSON.stringify(updateState));
      }, scenario.updateDelayMs || 0);
    },
    updateAction: function (action) {
      window.__mock.updateActions.push(action);
      var canned = scenario.updateActionResponses && scenario.updateActionResponses[action];
      if (canned) updateState = canned;
      else {
        var operation = merge((updateState && updateState.operation) || {}, {});
        var transitions = {
          download: "ready_to_install", install_now: "restarting",
          when_idle: "waiting_for_idle", on_quit: "install_on_quit",
          later: "deferred", retry: "downloading", rollback: "rolled_back",
        };
        if (action === "check") operation.state = "checking";
        else if (action === "resume") operation.state = operation.artifact_staged ? "ready_to_install" : "available";
        else operation.state = transitions[action] || operation.state;
        if (action === "download") operation.artifact_staged = true;
        updateState = merge(updateState || {}, { ok: true, operation: operation });
      }
      boot.update = updateState;
      setTimeout(function () { bridge.updateReady.emit(JSON.stringify(updateState)); }, scenario.updateDelayMs || 0);
    },
    setUpdatePolicy: function (key, value, cb) {
      var on = String(value) === "true";
      window.__mock.updatePolicies.push([key, on]);
      var policy = merge((updateState && updateState.policy) || {}, {});
      policy[key] = on;
      updateState = merge(updateState || {}, { ok: true, policy: policy });
      boot.update = updateState;
      if (settings.about) settings.about.update = updateState;
      if (cb) cb(JSON.stringify(updateState));
    },
    disconnectAccount: function (provider, cb) {
      window.__mock.disconnects.push(provider);
      const response = (scenario.disconnectResponses && scenario.disconnectResponses[provider])
        || { provider: provider, disconnected: true, message: "Signed out." };
      cb(JSON.stringify(response));
    },
    startProviderLogin: function (provider, requestId) {
      window.__mock.providerLogins.push({ provider: provider, requestId: requestId });
      const response = scenario.loginResponses && scenario.loginResponses[provider];
      if (response && !scenario.deferProviderLogin) setTimeout(function () {
        bridge.providerLoginReady.emit(JSON.stringify({ requestId: requestId, provider: provider, result: response }));
      }, scenario.loginDelayMs || 0);
    },
    refreshConnectionDoctor: function (requestId) {
      if (scenario.refreshedDoctorEntries) setTimeout(function () {
        bridge.connectionDoctorReady.emit(JSON.stringify({ requestId: requestId, entries: scenario.refreshedDoctorEntries }));
      }, scenario.doctorDelayMs || 0);
    },
    savePref: function (key, value) { window.__mock.savedPrefs.push([key, value]); },
    controlObjective: function (raw) { window.__mock.objectiveControls.push(JSON.parse(raw)); },
    // OPai Build (#276): scaffold an app under the workspace, zero tokens.
    scaffoldApp: function (payload, cb) {
      var parsed = {};
      try { parsed = JSON.parse(payload); } catch (_e) { /* keep {} */ }
      window.__mock.scaffolded.push(parsed);
      cb(JSON.stringify(scenario.scaffoldResponse || {
        ok: true, name: "demo-app", kind: "web", root: "/ws/demo-app",
        files: ["README.md", "app.js", "index.html", "styles.css"],
        entrypoint: "index.html", preview_cmd: "python -m http.server 8000",
        boilerplate_tokens_avoided: 1018, next_steps: [],
      }));
    },
    pickContextFiles: function (cb) {
      window.__mock.contextFilePicks++;
      cb(JSON.stringify({ paths: scenario.contextPickedFiles || ["index.html", "app.js"], rejected: [] }));
    },
    attachImage: function (data, name, cb) {
      window.__mock.attachedImages.push({ name: name, bytes: String(data || "").length });
      var reply = scenario.attachImageReply;
      if (reply) { cb(JSON.stringify(reply)); return; }
      var index = window.__mock.attachedImages.length;
      cb(JSON.stringify({
        ok: true,
        path: ".opaihub/attachments/shot-" + index + ".png",
        name: name || "Pasted image.png",
        bytes: 128,
      }));
    },
    pickImages: function (cb) {
      window.__mock.imagePicks++;
      cb(JSON.stringify(scenario.pickedImages || {
        ok: true,
        images: [{ path: ".opaihub/attachments/picked.png", name: "picked.png", bytes: 64 }],
        rejected: 0,
      }));
    },
    pickContextFolder: function (cb) {
      window.__mock.contextFolderPicks++;
      cb(JSON.stringify({ paths: scenario.contextPickedFolders || ["src/"], rejected: [] }));
    },
    appReceipt: function (cb) {
      cb(JSON.stringify(scenario.appReceipt || { ok: false, status: "not_an_app" }));
    },
    pinFullAuto: function (cb) {
      window.__mock.fullAutoPins++;
      var res = scenario.pinFullAutoResult || { effective_mode: "full-auto", full_auto_pinned: true };
      boot.prefs.fullAutoPinned = !!res.full_auto_pinned;
      if (cb) cb(JSON.stringify(res));
    },
    unpinFullAuto: function (cb) {
      window.__mock.fullAutoUnpins++;
      var res = scenario.unpinFullAutoResult || { effective_mode: "safe-auto", full_auto_pinned: false };
      boot.prefs.fullAutoPinned = !!res.full_auto_pinned;
      if (cb) cb(JSON.stringify(res));
    },
    grantFreeConsent: function (modelId, cb) {
      window.__mock.freeConsentGrants.push(modelId);
      if (cb) cb(JSON.stringify({ ok: true, freeConsent: window.__mock.freeConsentGrants.slice() }));
    },
    send: function (p) {
      var m = window.__mock; m.lastRequest = JSON.parse(p); m.sendCount++;
      // F9/F17 scenario: the pipeline hard-blocks a command and asks for a
      // one-time approval. Until the front-end re-sends with allowCommand set
      // to the exact string, every send gets a needs_command_approval reply;
      // the approved re-send gets the configured (or a default) answer.
      var gate = scenario.commandApproval;
      if (gate && gate.command) {
        var req = m.lastRequest;
        if (req.allowCommand === gate.command) {
          setTimeout(function () {
            bridge.replyReady.emit(JSON.stringify({
              requestId: req.requestId,
              result: gate.approvedResult || { status: "answered", answer: gate.approvedAnswer || "Ran with the approved command.", receipt: {} },
            }));
          }, gate.delayMs || 0);
        } else {
          setTimeout(function () {
            bridge.replyReady.emit(JSON.stringify({
              requestId: req.requestId,
              result: {
                status: "needs_command_approval",
                command: gate.command,
                reason: gate.reason || "The current run mode blocks this command.",
              },
            }));
          }, gate.delayMs || 0);
        }
      }
      // F26 scenario: Safe Auto refused file edits; until the front-end
      // re-sends with allowEditsOnce=true, every send gets a
      // needs_edit_approval reply carrying the exact file paths.
      var editGate = scenario.editApproval;
      if (editGate && editGate.files) {
        var ereq = m.lastRequest;
        if (ereq.allowEditsOnce === true) {
          setTimeout(function () {
            bridge.replyReady.emit(JSON.stringify({
              requestId: ereq.requestId,
              result: editGate.approvedResult || { status: "answered", answer: editGate.approvedAnswer || "Edits applied.", changed_files: editGate.files, receipt: {} },
            }));
          }, editGate.delayMs || 0);
        } else {
          setTimeout(function () {
            bridge.replyReady.emit(JSON.stringify({
              requestId: ereq.requestId,
              result: {
                status: "needs_edit_approval",
                edit_files: editGate.files,
                edit_approval: { files: editGate.files },
                answer: "OPai needs your approval to edit these files.",
              },
            }));
          }, editGate.delayMs || 0);
        }
      }
    },
    build: function (p) {
      var m = window.__mock;
      m.lastBuild = JSON.parse(p);
      m.buildCount++;
      var result = scenario.buildCloudGate && !m.lastBuild.allowCloud ? {
        ok: false,
        status: "needs_auto_confirmation",
        answer: "Confirm the named cloud model.",
        fallbackModelId: "free:gemini:gemini-3.1-flash-lite-preview",
        fallbackModelLabel: "Gemini · 3.1 Flash-Lite (free tier)",
        completion_verdict: { verdict: "blocked", reasonCode: "approval_required" },
      } : scenario.buildResult || {
        ok: true, status: "applied",
        applied: [{ path: "styles.css", action: "updated", added: 2, removed: 1 }],
        rejected: [], verify: { ok: true, passed: 3, failed: 0 },
        context: { files: ["styles.css"], saved_pct: 60 },
        receipt: { estimated_actual_usd: 0.0021, confidence: "actual" },
        preview_cmd: "python -m http.server 8000",
      };
      setTimeout(function () {
        bridge.buildReady.emit(JSON.stringify({ requestId: m.lastBuild.requestId, result: result }));
      }, scenario.buildDelayMs || 0);
    },
    // #380: the real bridge only sets a cancel flag here; teardown is confirmed
    // later via cancelReady. Tests drive that explicitly with confirmCancel().
    cancel: function (id) { var m = window.__mock; m.cancelCount++; m.cancelled.push(id); },
    runTool: function (name) {
      window.__mock.runTools.push(name);
      var response = scenario.toolResponses && scenario.toolResponses[name];
      if (response) setTimeout(function () { bridge.toolReady.emit(JSON.stringify(response)); }, 0);
    },
    applyTool: function (name, cb) {
      window.__mock.appliedTools.push(name);
      var response = scenario.applyToolResponses && scenario.applyToolResponses[name];
      cb(JSON.stringify(response || { text: "Applied safely." }));
    },
    reviewDiff: function (path, decision, cb) {
      window.__mock.diffDecisions.push([path, decision]);
      cb(JSON.stringify({ ok: true, workflow: { diff_review: {} } }));
    },
    openWorkspace: function () { window.__mock.openWorkspaceCount++; },
    switchWorkspace: function (p) {
      window.__mock.switched.push(p);
      // Mirror the real bridge: switching workspace re-boots server-side and
      // emits workspaceChanged with the NEW workspace's payload. The scenario
      // can override the new workspace's prefs.mode / fullAutoPinned /
      // autonomy via scenario.workspaceSwitch.boot (F16/F4 regression hook).
      var ws = scenario.workspaceSwitch || {};
      var wsBoot = ws.boot || {};
      var next = merge(boot, wsBoot);
      var explicitWs = wsBoot.workspace || {};
      next.workspace = merge(next.workspace || {}, {
        root: explicitWs.root || p,
        label: explicitWs.label || ws.label || String(p).split(/[\\/]/).filter(Boolean).pop() || String(p),
      });
      boot = next;
      setTimeout(function () {
        bridge.workspaceChanged.emit(JSON.stringify(next));
      }, ws.delayMs || 0);
    },
    openPath: function (p) { window.__mock.opened.push(p); },
    startWindowMove: function () { window.__mock.windowMoves++; },
    startWindowResize: function (edge) { window.__mock.windowResizes.push(edge); },
    minimizeWindow: function () { window.__mock.windowMinimizes++; },
    toggleMaximizeWindow: function () { window.__mock.windowMaximizes++; },
    closeWindow: function () { window.__mock.windowCloses++; },
    recents: function (cb) { cb(JSON.stringify(boot.recents)); },
    listConversations: function (cb) {
      window.__mock.conversationLists++;
      if (cb) cb(JSON.stringify({ ok: true, conversations: boot.conversations || [] }));
    },
    loadConversation: function (id, cb) {
      window.__mock.openedConversations.push(id);
      var canned = (scenario.conversationTranscripts || {})[id];
      if (!canned) { if (cb) cb(JSON.stringify({ ok: false, error: "That chat is no longer available." })); return; }
      if (cb) cb(JSON.stringify({ ok: true, conversation: canned }));
    },
    saveRecent: function (t) {
      window.__mock.savedRecents.push(t);
      // Mirror the backend: begin_thread_turn archives the chat as the turn
      // starts, so the sidebar has an entry before any answer arrives.
      boot.conversations = [{ id: "live-" + window.__mock.savedRecents.length, title: t, message_count: 1, updated_at: "2026-08-02" }]
        .concat(boot.conversations || []);
    },
    clearRecents: function (cb) {
      window.__mock.clearedRecents++;
      if (scenario.clearRecentsResult) {
        boot = merge(boot, scenario.clearRecentsResult);
        if (cb) cb(JSON.stringify(boot));
        return;
      }
      boot.recents = [];
      // Mirror clear_recents, which deletes saved conversations as well.
      boot.conversations = [];
      boot.resume = { available: false, requires_choice: false, thread: {}, workflow: {}, checkpoint: {} };
      if (cb) cb(JSON.stringify(Object.assign({}, boot, { ok: true })));
    },
    resumeSession: function (cb) {
      window.__mock.resumedSessions++;
      if (cb) cb(JSON.stringify({ activated: true }));
    },
    clearSession: function (cb) {
      window.__mock.clearedSessions++;
      var result = scenario.clearSessionResult;
      if (!result) {
        boot.resume = { available: false, requires_choice: false, thread: {}, workflow: {}, checkpoint: {} };
        result = Object.assign({}, boot, { ok: true });
      }
      // Simulate a slow/contended real bridge: hold the callback so a test can
      // assert the resume card is dismissed synchronously on click (#416).
      if (scenario.deferClearSession) {
        window.__mock.flushClearSession = function () { if (cb) cb(JSON.stringify(result)); };
        return;
      }
      if (cb) cb(JSON.stringify(result));
    },
    copyText: function (t) { window.__mock.copiedTexts.push(t); },
    openExternal: function (url) { window.__mock.externalUrls.push(url); },
  };
  window.qt = { webChannelTransport: {} };
  window.QWebChannel = function (transport, cb) { cb({ objects: { bridge: bridge } }); };
  window.__mock = {
    bridge: bridge, lastRequest: null, sendCount: 0, lastBuild: null, buildCount: 0, cancelCount: 0, cancelled: [],
    confirmCancel: function (id, teardown) {
      bridge.cancelReady.emit(JSON.stringify({ requestId: id, teardown: teardown || "complete" }));
    },
    openWorkspaceCount: 0, switched: [], opened: [], savedRecents: [], savedPrefs: [],
    clearedRecents: 0, resumedSessions: 0, clearedSessions: 0,
    conversationLists: 0, openedConversations: [],
    copiedTexts: [], contextFilePicks: 0, contextFolderPicks: 0,
    fullAutoPins: 0, fullAutoUnpins: 0,
    attachedImages: [], imagePicks: 0,
    windowMoves: 0, windowResizes: [], windowMinimizes: 0,
    windowMaximizes: 0, windowCloses: 0,
    runTools: [], appliedTools: [], externalUrls: [], savedProviderKeys: [], scaffolded: [],
    deletedProviderKeys: [], providerTests: [], savedUsageLimits: [], codexRepairs: 0,
    freeConsentGrants: [], disconnects: [], diffDecisions: [], providerLogins: [],
    githubConnects: [], githubPushToggles: [], githubDisconnects: 0,
    dashboardRequests: [], objectiveControls: [], settingsRequests: [], statusRequests: [],
    workspaceRequests: [], inspectorRequests: [],
    updateChecks: [], updateActions: [], updatePolicies: [], interactiveMarks: 0, usageRefreshes: 0,
    modelDiscoveries: 0, savedModelOverrides: [],
    workspaceStateCalls: 0,
    emitDiscoveredModels: function () {
      bridge.modelsChanged.emit(JSON.stringify({
        models: scenario.discoveredModels || [],
        accounts: scenario.discoveredAccounts || boot.accounts,
        connections: scenario.discoveredConnections || boot.connections,
      }));
    },
    // Drive a workspace switch through the same slot the UI uses; the mock
    // emits workspaceChanged with the (possibly scenario-overridden) payload.
    switchWorkspace: function (path) { bridge.switchWorkspace(path); },
    // Merge a partial override into the mock's live boot payload (e.g. to make
    // the inspector report a fresh authoritative Agent mode after a run).
    updateBoot: function (override) { boot = merge(boot, override || {}); },
    reqId: function () { return window.__mock.lastRequest && window.__mock.lastRequest.requestId; },
    emitActivity: function (id, ev) { bridge.activity.emit(JSON.stringify({ requestId: id, event: ev })); },
    // Schema v2 batch path (#226/#230): one signal carrying an event array,
    // the wire shape the real bridge's activityBatch QTimer flush will use.
    emitActivityBatch: function (id, list) { bridge.activityBatch.emit(JSON.stringify({ requestId: id, events: list })); },
    emitToken: function (id, t, blockStart) {
      const payload = { requestId: id, text: t };
      if (blockStart) payload.blockStart = true;
      bridge.token.emit(JSON.stringify(payload));
    },
    emitReply: function (id, result) { bridge.replyReady.emit(JSON.stringify({ requestId: id, result: result })); },
    emitObjective: function (payload) { bridge.objectiveReady.emit(JSON.stringify(payload)); },
    emitObjectiveControl: function (payload) { bridge.objectiveControlReady.emit(JSON.stringify(payload)); },
    emitDashboard: function (payload) { bridge.dashboardReady.emit(JSON.stringify(payload)); },
    emitProviderLogin: function (id, result) { bridge.providerLoginReady.emit(JSON.stringify({ requestId: id, provider: result.provider, result: result })); },
  };
})();
