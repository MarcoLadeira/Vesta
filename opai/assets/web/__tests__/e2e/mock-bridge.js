/* Playwright harness: stubs QWebChannel + qt and a controllable mock bridge so
   the real front-end (activity.js + app.js) runs in Chromium without Qt. The
   spec drives streaming/errors via window.__mock.* helpers. Injected with
   page.addInitScript before any page script runs. */
(function () {
  "use strict";
  function Sig() {
    var fns = [];
    return {
      connect: function (f) { fns.push(f); },
      emit: function () { var a = arguments; fns.forEach(function (f) { f.apply(null, a); }); },
    };
  }
  var boot = {
    workspace: { label: "demo", root: "/demo", name: "demo", branch: "main", file_count: 3, recents: [{ path: "/other/proj", label: "other/proj" }] },
    recents: ["summarize my changes"],
    models: [
      { id: "account:claude:opus", label: "Claude Opus · your account", kind: "account", provider: "claude", badge: "slower · highest · $$$" },
      { id: "auto", label: "Auto · cheapest", kind: "auto", badge: "" },
    ],
    selectedModel: "account:claude:opus",
    modes: [{ id: "ask", label: "Ask" }, { id: "safe-auto", label: "Safe Auto" }],
    navGroups: [{ group: "Control", items: [{ id: "home", label: "Home" }, { id: "agents", label: "Agents" }] }], taskModes: [{ id: "general", label: "General" }], outputFormats: [{ id: "normal", label: "Normal" }],
    prefs: { model: "account:claude:opus", mode: "ask", focus: "general", format: "normal", showPanel: true },
    accounts: [{ id: "claude", label: "Claude", connected: true }],
    status: { on: true, line: "Claude · Ask · $0.00 today · $0.00 saved" },
    inspector: { rows: [], budget: { pct: 0, text: "$0.00 today" }, permissions: [], privacy: [] },
    defaultView: "chat", initialTask: "", tools: [],
  };
  var bridge = {
    replyReady: Sig(), activity: Sig(), token: Sig(), toolReady: Sig(), workspaceChanged: Sig(),
    boot: function (cb) { cb(JSON.stringify(boot)); },
    inspector: function (s, cb) { cb(JSON.stringify(boot.inspector)); },
    statusLine: function (s, cb) { cb(JSON.stringify(boot.status)); },
    dashboard: function (id, cb) { cb(JSON.stringify(id === "home" ? {
      title: "Mission Control",
      subtitle: "Observed proxy sessions only. Direct unwrapped launches are not measurable yet.",
      kpis: [
        { label: "Capture health", value: "67%", severity: "warning" },
        { label: "Observed sessions", value: "3", severity: "neutral" },
      ],
      cards: [{ title: "Capture gap", body: "1 fail-open session was not accounted.", severity: "warning" }],
    } : id === "agents" ? {
      title: "Agent Readiness",
      subtitle: "Know which launches OPai can capture.",
      cards: [{
        title: "Codex", status: "ACTIVE", severity: "success",
        metrics: [
          { label: "Wrapper", value: "installed", severity: "success" },
          { label: "Capture", value: "selective proxy", severity: "success" },
        ],
      }],
    } : {})); },
    prompts: function (q, c, cb) { cb(JSON.stringify({ categories: [], prompts: [] })); },
    usePrompt: function (id, cb) { cb(JSON.stringify({})); },
    settingsData: function (cb) { cb(JSON.stringify({ prefs: {}, firewall: {}, permissions: [], accounts: [], about: {} })); },
    savePref: function () {},
    send: function (p) { var m = window.__mock; m.lastRequest = JSON.parse(p); m.sendCount++; },
    cancel: function (id) { var m = window.__mock; m.cancelCount++; m.cancelled.push(id); },
    runTool: function () {}, applyTool: function (n, cb) { cb("{}"); },
    openWorkspace: function () { window.__mock.openWorkspaceCount++; },
    switchWorkspace: function (p) { window.__mock.switched.push(p); },
    openPath: function (p) { window.__mock.opened.push(p); },
    recents: function (cb) { cb(JSON.stringify(boot.recents)); },
    saveRecent: function (t) { window.__mock.savedRecents.push(t); },
    openExternal: function () {},
  };
  window.qt = { webChannelTransport: {} };
  window.QWebChannel = function (transport, cb) { cb({ objects: { bridge: bridge } }); };
  window.__mock = {
    bridge: bridge, lastRequest: null, sendCount: 0, cancelCount: 0, cancelled: [],
    openWorkspaceCount: 0, switched: [], opened: [], savedRecents: [],
    reqId: function () { return window.__mock.lastRequest && window.__mock.lastRequest.requestId; },
    emitActivity: function (id, ev) { bridge.activity.emit(JSON.stringify({ requestId: id, event: ev })); },
    emitToken: function (id, t) { bridge.token.emit(JSON.stringify({ requestId: id, text: t })); },
    emitReply: function (id, result) { bridge.replyReady.emit(JSON.stringify({ requestId: id, result: result })); },
  };
})();
