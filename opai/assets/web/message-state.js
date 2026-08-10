/* Pure request/message state shared by the browser and Vitest. */
(function (global) {
  "use strict";

  var lifecycle = global.OPaiLifecycle;
  if (!lifecycle) throw new Error("generated lifecycle contract must load first");

  var AWAITING = "awaiting_input";
  var CANCELLING = "cancel_requested";
  var PRESENTATION_ORDER = {
    queued: ["queued", "retrying"],
    preparing: ["preparing", "authenticating"],
    running: ["running", "sending", "waiting", "streaming"],
  };

  function knownCanonicalState(status) {
    var value = String(status || "").toLowerCase();
    if (lifecycle.stateIds.indexOf(value) !== -1) return value;
    return lifecycle.legacyMappings.states[value] || null;
  }

  function canonicalState(status) {
    return knownCanonicalState(status) || lifecycle.degradedInputs.unknown_state.state;
  }

  function isPresentationProgress(current, next, canonical) {
    var order = PRESENTATION_ORDER[canonical] || [];
    var fromIndex = order.indexOf(current);
    var toIndex = order.indexOf(next);
    return fromIndex !== -1 && toIndex !== -1 && toIndex >= fromIndex;
  }

  function beginRequest(requestId, options) {
    options = options || {};
    return {
      requestId: String(requestId || ""),
      retryOf: options.retryOf || null,
      status: options.retryOf ? "retrying" : "queued",
    };
  }

  // #295 alpha gate 7: "every attempted violation is rejected AND observable".
  // Refusing silently satisfied only the first half — the store knew something
  // had tried to walk an impossible path and said nothing, so the one class of
  // bug this model exists to catch left no trace on this surface either.
  // Bounded, with the true total kept separately so it survives truncation.
  var MAX_REFUSALS = 64;
  var refusals = [];
  var refusalCount = 0;

  // #612 AC6: a refusal must survive the window. The in-memory list above is
  // process-local, and nothing ever read it — so a browser-side illegal
  // transition was rejected correctly and then lost, while the Python half of
  // the same contract wrote a durable journal entry. Support could reconstruct
  // one surface's violations after a restart and not the other's.
  //
  // Injected rather than imported: this module is pure so Vitest can load it
  // with no bridge, and a renderer must never hard-depend on Qt being present.
  // Defaults to a no-op; app.js wires it to the bridge slot.
  var refusalSink = null;

  function setRefusalSink(sink) {
    refusalSink = typeof sink === "function" ? sink : null;
  }

  function publishRefusal(from, to) {
    if (!refusalSink) return;
    // A diagnostic must never be able to break the state machine it observes.
    try {
      refusalSink(from, to);
    } catch (err) {
      /* durable reporting is best-effort; the refusal itself already stands */
    }
  }

  function transition(message, nextStatus) {
    var current = String((message && message.status) || "queued");
    var next = String(nextStatus || current);
    var knownCurrent = knownCanonicalState(current);
    var knownNext = knownCanonicalState(next);
    var currentCanonical = knownCurrent || lifecycle.degradedInputs.unknown_state.state;
    var nextCanonical = knownNext || lifecycle.degradedInputs.unknown_state.state;
    if (!knownCurrent) {
      return Object.assign({}, message, { status: currentCanonical });
    }
    var allowed = (
      lifecycle.canTransition(currentCanonical, nextCanonical) ||
      (currentCanonical === nextCanonical && isPresentationProgress(current, next, currentCanonical))
    );
    if (!allowed) {
      refusalCount += 1;
      refusals.push({ from: current, to: next, at: Date.now() });
      if (refusals.length > MAX_REFUSALS) refusals.shift();
      // Canonical IDs only, never the raw presentation strings: this record is
      // read by diagnostics and must not become a place free text can land.
      publishRefusal(currentCanonical, nextCanonical);
      return message;
    }
    return Object.assign({}, message, { status: knownNext ? next : nextCanonical });
  }

  function illegalTransitions() {
    return { count: refusalCount, recent: refusals.slice() };
  }

  function resetIllegalTransitions() {
    refusalCount = 0;
    refusals.length = 0;
  }

  function canApply(message, incomingRequestId) {
    if (!message || lifecycle.isTerminal(canonicalState(message.status))) return false;
    // #380: a stopping run accepts no more content. The old stop() dropped the
    // request id to get this, which also made the teardown unobservable; the id
    // is now kept so the confirmation can be matched, and the stale-overwrite
    // protection lives here instead. Cancel confirmations do not come through
    // canApply — they are matched against state.cancelling directly.
    if (message.status === CANCELLING) return false;
    return !!message.requestId && message.requestId === incomingRequestId;
  }

  function fromBackendStatus(status, verdict) {
    // Checked before the verdict: an awaiting run has not reached a terminal,
    // so its verdict is provisional. Reading the verdict first is exactly what
    // recorded "shall I run this command?" as blocked.
    var mappedStatus = lifecycle.legacyMappings.statuses[String(status || "").toLowerCase()];
    if (mappedStatus === AWAITING) return AWAITING;
    // Prefer the authoritative completion verdict when the reply carries one so
    // a partial/blocked/timeout run is rendered honestly, never as "failed".
    var raw = verdict && typeof verdict === "object" ? verdict.verdict : verdict;
    var v = String(raw || "").toLowerCase();
    if (v) return lifecycle.isTerminal(v) ? v : lifecycle.degradedInputs.unknown_state.state;
    return mappedStatus || lifecycle.degradedInputs.unknown_status.state;
  }

  var api = {
    lifecycle: lifecycle,
    beginRequest: beginRequest,
    canApply: canApply,
    fromBackendStatus: fromBackendStatus,
    transition: transition,
    illegalTransitions: illegalTransitions,
    resetIllegalTransitions: resetIllegalTransitions,
    setRefusalSink: setRefusalSink,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.OPaiMessageState = api;
})(typeof window !== "undefined" ? window : globalThis);
