/* Pure activity/cancellation logic shared by the browser UI and the Vitest
   suite. Framework-free UMD-lite: attaches to window.OPaiActivity in the
   browser and exports for Node/Vitest. Mirrors opai/activity.py so the two
   surfaces agree on the stale-guard and slow-model thresholds. */
(function (global) {
  "use strict";

  var TAKING_LONGER_S = 15;
  var STILL_WORKING_S = 45;

  // The anti-race primitive: only apply a signal if it belongs to the active
  // request. A cancelled/superseded request clears currentId, so its late
  // output is ignored — no stale response can overwrite the current message.
  function shouldApply(currentId, incomingId) {
    return !!currentId && currentId === incomingId;
  }

  function shortName(label) {
    return String(label || "the model").split(" · ")[0].split(" (")[0].trim();
  }

  function stageMessage(elapsedS, opts) {
    opts = opts || {};
    var short = shortName(opts.modelLabel);
    if (opts.streaming) {
      return { stage: "Streaming response", reassurance: "", suggestFaster: false, severity: "running" };
    }
    if (elapsedS < TAKING_LONGER_S) {
      return { stage: "Waiting for " + short, reassurance: "", suggestFaster: false, severity: "running" };
    }
    if (elapsedS < STILL_WORKING_S) {
      return {
        stage: "Waiting for " + short,
        reassurance: short + " is taking longer than usual — complex prompts can take a while.",
        suggestFaster: true,
        severity: "warning",
      };
    }
    return {
      stage: "Still working — " + short,
      reassurance: "Still working. You can keep waiting, stop, or switch to a faster model.",
      suggestFaster: true,
      severity: "warning",
    };
  }

  function formatElapsed(ms) {
    var s = Math.max(0, Math.floor((ms || 0) / 1000));
    var m = Math.floor(s / 60);
    s = s % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  // Minimal activity-event store: add/upsert by id, cancel running, clear.
  function createStore() {
    var events = [];
    return {
      events: events,
      add: function (ev) { events.push(ev); return ev; },
      upsert: function (ev) {
        for (var i = 0; i < events.length; i++) {
          if (events[i].id === ev.id) { events[i] = ev; return ev; }
        }
        events.push(ev);
        return ev;
      },
      cancelRunning: function () {
        events.forEach(function (e) {
          if (e.status === "running" || e.status === "pending") e.status = "cancelled";
        });
      },
      clear: function () { events.length = 0; },
      list: function () { return events.slice(); },
    };
  }

  // The CLI Mirror — the terminal twin of the current GUI selection. GUI/CLI
  // parity is a brand promise; this shows it, ready to copy. Mirrors
  // opai/brand.py::cli_mirror so both surfaces render the same command.
  function cliMirror(model, mode, task) {
    var short = String(model || "auto");
    if (short.indexOf("account:") === 0) short = short.slice(8);
    var parts = ["opai", "ask"];
    if (short) parts.push("--model", short);
    if (mode && mode !== "ask") parts.push("--mode", String(mode));
    var prompt = String(task || "").trim().replace(/"/g, "'");
    if (prompt.length > 60) prompt = prompt.slice(0, 57) + "...";
    parts.push('"' + (prompt || "<your task>") + '"');
    return parts.join(" ");
  }

  var api = {
    shouldApply: shouldApply,
    stageMessage: stageMessage,
    formatElapsed: formatElapsed,
    createStore: createStore,
    shortName: shortName,
    cliMirror: cliMirror,
    TAKING_LONGER_S: TAKING_LONGER_S,
    STILL_WORKING_S: STILL_WORKING_S,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.OPaiActivity = api;
})(typeof window !== "undefined" ? window : this);
