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

  // Activity-event store v2 (#229): O(1) id-keyed upsert, batch ingest for
  // the bridge's activityBatch signal, and request-scoped clearing. Storage
  // keeps every raw event — grouping is presentation only (groupRows below).
  function createStore() {
    var events = [];
    var byId = Object.create(null); // id -> first index holding that id
    function upsertOne(ev) {
      var key = String(ev.id);
      var at = byId[key];
      if (at !== undefined) { events[at] = ev; return ev; }
      byId[key] = events.length;
      events.push(ev);
      return ev;
    }
    function reindex() {
      byId = Object.create(null);
      events.forEach(function (ev, i) {
        var key = String(ev.id);
        if (byId[key] === undefined) byId[key] = i;
      });
    }
    return {
      events: events,
      add: function (ev) {
        var key = String(ev.id);
        if (byId[key] === undefined) byId[key] = events.length;
        events.push(ev);
        return ev;
      },
      upsert: upsertOne,
      ingestBatch: function (list) {
        (list || []).forEach(upsertOne);
      },
      clearRequest: function (requestId) {
        for (var i = events.length - 1; i >= 0; i--) {
          if (events[i].requestId === requestId) events.splice(i, 1);
        }
        reindex();
      },
      cancelRunning: function () {
        events.forEach(function (e) {
          if (e.status === "running" || e.status === "pending") e.status = "cancelled";
        });
      },
      clear: function () { events.length = 0; byId = Object.create(null); },
      list: function () { return events.slice(); },
    };
  }

  // Worst-of status aggregation for grouped rows: a group is only as calm as
  // its most alarming child.
  var _SEVERITY = { error: 5, warning: 4, running: 3, pending: 2, cancelled: 1, success: 0 };
  function worstStatus(list) {
    var worst = "success";
    (list || []).forEach(function (ev) {
      if ((_SEVERITY[ev.status] || 0) > (_SEVERITY[worst] || 0)) worst = ev.status;
    });
    return worst;
  }

  // groupRows (#229): fold CONSECUTIVE feed-channel events sharing a `group`
  // key into one logical row. Pure presentation — callers keep every raw
  // event; expanding a group reveals the real children. Status-channel
  // events never render rows and never split a group (they're invisible
  // here). A single-member group renders as a plain row.
  function groupRows(list) {
    var runs = [];
    var open = null;
    (list || []).forEach(function (ev) {
      var channel = ev.channel || "feed";
      if (channel !== "feed") return; // status strip events: no timeline row
      var key = ev.group;
      if (key && open && open.key === key) {
        open.children.push(ev);
        return;
      }
      open = null;
      if (key) {
        open = { key: key, children: [ev] };
        runs.push(open);
      } else {
        runs.push({ key: null, children: [ev] });
      }
    });
    return runs.map(function (run) {
      if (!run.key || run.children.length === 1) {
        return { kind: "single", event: run.children[0] };
      }
      var base = String(run.children[0].title || "").split(":")[0].trim();
      return {
        kind: "group",
        key: run.key,
        status: worstStatus(run.children),
        label: (base || "Steps") + " ×" + run.children.length,
        children: run.children.slice(),
      };
    });
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
    groupRows: groupRows,
    worstStatus: worstStatus,
    shortName: shortName,
    cliMirror: cliMirror,
    TAKING_LONGER_S: TAKING_LONGER_S,
    STILL_WORKING_S: STILL_WORKING_S,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.OPaiActivity = api;
})(typeof window !== "undefined" ? window : this);
