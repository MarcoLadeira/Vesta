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
    brand: {
      name: "OPai",
      tagline: "Every step visible. Every dollar accounted.",
      emptyTitle: "Build more. Burn less.",
      emptyBody: "Tell OPai the goal. It plans, routes to the cheapest capable model, shows every step, and hands you the receipt.",
      emptyHint: "Press Ctrl+K for commands",
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
    replyReady: Sig(), buildReady: Sig(), activity: Sig(), activityBatch: Sig(), token: Sig(), toolReady: Sig(), workspaceChanged: Sig(), modelsChanged: Sig(), providerLoginReady: Sig(), connectionDoctorReady: Sig(),
    dashboardReady: Sig(), settingsReady: Sig(), toolApplied: Sig(), statusReady: Sig(),
    boot: function (cb) { cb(JSON.stringify(boot)); },
    inspector: function (s, cb) { cb(JSON.stringify(boot.inspector)); },
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
    refreshModels: function (cb) { cb(JSON.stringify({ models: boot.models })); },
    discoverModels: function () {
      if (scenario.discoveredModels && !scenario.deferDiscovery) setTimeout(function () {
        bridge.modelsChanged.emit(JSON.stringify({ models: scenario.discoveredModels }));
      }, scenario.discoveryDelayMs || 0);
    },
    repairCodexConfig: function (cb) {
      window.__mock.codexRepairs++;
      cb(JSON.stringify({ repaired: true, backupPath: "/tmp/config.toml.bak" }));
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
    appReceipt: function (cb) {
      cb(JSON.stringify(scenario.appReceipt || { ok: false, status: "not_an_app" }));
    },
    pinFullAuto: function (cb) { window.__mock.fullAutoPins++; if (cb) cb(JSON.stringify({ effective_mode: "full-auto", full_auto_pinned: true })); },
    unpinFullAuto: function (cb) { window.__mock.fullAutoUnpins++; if (cb) cb(JSON.stringify({ effective_mode: "safe-auto", full_auto_pinned: false })); },
    grantFreeConsent: function (modelId, cb) {
      window.__mock.freeConsentGrants.push(modelId);
      if (cb) cb(JSON.stringify({ ok: true, freeConsent: window.__mock.freeConsentGrants.slice() }));
    },
    send: function (p) { var m = window.__mock; m.lastRequest = JSON.parse(p); m.sendCount++; },
    build: function (p) {
      var m = window.__mock;
      m.lastBuild = JSON.parse(p);
      m.buildCount++;
      var result = scenario.buildResult || {
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
    switchWorkspace: function (p) { window.__mock.switched.push(p); },
    openPath: function (p) { window.__mock.opened.push(p); },
    startWindowMove: function () { window.__mock.windowMoves++; },
    startWindowResize: function (edge) { window.__mock.windowResizes.push(edge); },
    minimizeWindow: function () { window.__mock.windowMinimizes++; },
    toggleMaximizeWindow: function () { window.__mock.windowMaximizes++; },
    closeWindow: function () { window.__mock.windowCloses++; },
    recents: function (cb) { cb(JSON.stringify(boot.recents)); },
    saveRecent: function (t) { window.__mock.savedRecents.push(t); },
    clearRecents: function (cb) {
      window.__mock.clearedRecents++;
      if (scenario.clearRecentsResult) {
        if (cb) cb(JSON.stringify(scenario.clearRecentsResult));
        return;
      }
      boot.recents = [];
      boot.resume = { available: false, requires_choice: false, thread: {}, workflow: {}, checkpoint: {} };
      if (cb) cb(JSON.stringify(Object.assign({}, boot, { ok: true })));
    },
    resumeSession: function (cb) {
      window.__mock.resumedSessions++;
      if (cb) cb(JSON.stringify({ activated: true }));
    },
    clearSession: function (cb) {
      window.__mock.clearedSessions++;
      if (scenario.clearSessionResult) {
        if (cb) cb(JSON.stringify(scenario.clearSessionResult));
        return;
      }
      boot.resume = { available: false, requires_choice: false, thread: {}, workflow: {}, checkpoint: {} };
      if (cb) cb(JSON.stringify(Object.assign({}, boot, { ok: true })));
    },
    copyText: function (t) { window.__mock.copiedTexts.push(t); },
    openExternal: function (url) { window.__mock.externalUrls.push(url); },
  };
  window.qt = { webChannelTransport: {} };
  window.QWebChannel = function (transport, cb) { cb({ objects: { bridge: bridge } }); };
  window.__mock = {
    bridge: bridge, lastRequest: null, sendCount: 0, lastBuild: null, buildCount: 0, cancelCount: 0, cancelled: [],
    openWorkspaceCount: 0, switched: [], opened: [], savedRecents: [], savedPrefs: [],
    clearedRecents: 0, resumedSessions: 0, clearedSessions: 0,
    copiedTexts: [],
    fullAutoPins: 0, fullAutoUnpins: 0,
    windowMoves: 0, windowResizes: [], windowMinimizes: 0,
    windowMaximizes: 0, windowCloses: 0,
    runTools: [], appliedTools: [], externalUrls: [], savedProviderKeys: [], scaffolded: [],
    deletedProviderKeys: [], providerTests: [], savedUsageLimits: [], codexRepairs: 0,
    freeConsentGrants: [], disconnects: [], diffDecisions: [], providerLogins: [],
    githubConnects: [], githubPushToggles: [], githubDisconnects: 0,
    dashboardRequests: [], settingsRequests: [], statusRequests: [],
    emitDiscoveredModels: function () {
      bridge.modelsChanged.emit(JSON.stringify({ models: scenario.discoveredModels || [] }));
    },
    reqId: function () { return window.__mock.lastRequest && window.__mock.lastRequest.requestId; },
    emitActivity: function (id, ev) { bridge.activity.emit(JSON.stringify({ requestId: id, event: ev })); },
    // Schema v2 batch path (#226/#230): one signal carrying an event array,
    // the wire shape the real bridge's activityBatch QTimer flush will use.
    emitActivityBatch: function (id, list) { bridge.activityBatch.emit(JSON.stringify({ requestId: id, events: list })); },
    emitToken: function (id, t) { bridge.token.emit(JSON.stringify({ requestId: id, text: t })); },
    emitReply: function (id, result) { bridge.replyReady.emit(JSON.stringify({ requestId: id, result: result })); },
    emitProviderLogin: function (id, result) { bridge.providerLoginReady.emit(JSON.stringify({ requestId: id, provider: result.provider, result: result })); },
  };
})();
