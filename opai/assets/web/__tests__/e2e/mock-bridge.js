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
    prefs: { model: "account:claude:opus", mode: "ask", focus: "general", format: "normal", showPanel: true },
    accounts: [{ id: "claude", label: "Claude", connected: true }],
    status: { on: true, line: "OPai · Ask · $0.00 today · $0.00 saved" },
    inspector: { rows: [], budget: { pct: 0, text: "$0.00 today" }, permissions: [], privacy: [] },
    defaultView: "chat", initialTask: "", tools: [],
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
  function respond(cb, value, delay) {
    if (delay) setTimeout(function () { cb(JSON.stringify(value)); }, delay);
    else cb(JSON.stringify(value));
  }
  var bridge = {
    replyReady: Sig(), activity: Sig(), token: Sig(), toolReady: Sig(), workspaceChanged: Sig(), modelsChanged: Sig(),
    boot: function (cb) { cb(JSON.stringify(boot)); },
    inspector: function (s, cb) { cb(JSON.stringify(boot.inspector)); },
    statusLine: function (s, cb) { cb(JSON.stringify(boot.status)); },
    dashboard: function (id, cb) {
      var error = scenario.dashboardErrors && scenario.dashboardErrors[id];
      respond(cb, error ? { error: error } : (dashboards[id] || {}), scenario.dashboardDelayMs);
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
    savePref: function (key, value) { window.__mock.savedPrefs.push([key, value]); },
    grantFreeConsent: function (modelId, cb) {
      window.__mock.freeConsentGrants.push(modelId);
      if (cb) cb(JSON.stringify({ ok: true, freeConsent: window.__mock.freeConsentGrants.slice() }));
    },
    send: function (p) { var m = window.__mock; m.lastRequest = JSON.parse(p); m.sendCount++; },
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
    openExternal: function (url) { window.__mock.externalUrls.push(url); },
  };
  window.qt = { webChannelTransport: {} };
  window.QWebChannel = function (transport, cb) { cb({ objects: { bridge: bridge } }); };
  window.__mock = {
    bridge: bridge, lastRequest: null, sendCount: 0, cancelCount: 0, cancelled: [],
    openWorkspaceCount: 0, switched: [], opened: [], savedRecents: [], savedPrefs: [],
    windowMoves: 0, windowResizes: [], windowMinimizes: 0,
    windowMaximizes: 0, windowCloses: 0,
    runTools: [], appliedTools: [], externalUrls: [], savedProviderKeys: [],
    deletedProviderKeys: [], providerTests: [], savedUsageLimits: [], codexRepairs: 0,
    freeConsentGrants: [],
    emitDiscoveredModels: function () {
      bridge.modelsChanged.emit(JSON.stringify({ models: scenario.discoveredModels || [] }));
    },
    reqId: function () { return window.__mock.lastRequest && window.__mock.lastRequest.requestId; },
    emitActivity: function (id, ev) { bridge.activity.emit(JSON.stringify({ requestId: id, event: ev })); },
    emitToken: function (id, t) { bridge.token.emit(JSON.stringify({ requestId: id, text: t })); },
    emitReply: function (id, result) { bridge.replyReady.emit(JSON.stringify({ requestId: id, result: result })); },
  };
})();
