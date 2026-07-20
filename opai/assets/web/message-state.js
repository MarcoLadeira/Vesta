/* Pure request/message state shared by the browser and Vitest. */
(function (global) {
  "use strict";

  // #402: partial/blocked/timeout are honest terminal outcomes distinct from a
  // flat "failed", and "verifying" is the transient evidence-check state emitted
  // before a terminal verdict. A verdict that only some surfaces honor is a
  // verdict that fails under real use, so the message state machine carries the
  // same vocabulary as the completion verdict.
  var TERMINAL = {
    completed: true,
    partial: true,
    blocked: true,
    timeout: true,
    failed: true,
    cancelled: true,
  };
  // Terminal outcomes reachable from any live phase (verifying is pre-terminal,
  // so it is not itself an end state).
  var ENDS = ["completed", "partial", "blocked", "timeout", "failed", "cancelled"];
  var RETRYABLE_ENDS = ["partial", "blocked", "timeout", "failed", "cancelled"];
  var ALLOWED = {
    queued: ["preparing", "authenticating", "sending", "verifying"].concat(ENDS),
    // A retry re-enters the pipeline; it never jumps straight to "completed".
    retrying: ["preparing", "authenticating", "sending", "verifying"].concat(RETRYABLE_ENDS),
    preparing: ["authenticating", "sending", "verifying"].concat(ENDS),
    authenticating: ["sending", "verifying"].concat(ENDS),
    sending: ["waiting", "streaming", "verifying"].concat(ENDS),
    waiting: ["streaming", "verifying"].concat(ENDS),
    streaming: ["verifying"].concat(ENDS),
    verifying: ENDS.slice(),
    failed: ["retrying"],
    cancelled: ["retrying"],
    partial: ["retrying"],
    blocked: ["retrying"],
    timeout: ["retrying"],
    completed: [],
  };
  // The completion verdict (#378/#402) is authoritative: when a reply carries
  // one, the message state mirrors it exactly instead of collapsing every
  // non-answered outcome to "failed".
  var VERDICT_STATE = {
    completed: "completed",
    partial: "partial",
    blocked: "blocked",
    timeout: "timeout",
    failed: "failed",
    cancelled: "cancelled",
  };

  function beginRequest(requestId, options) {
    options = options || {};
    return {
      requestId: String(requestId || ""),
      retryOf: options.retryOf || null,
      status: options.retryOf ? "retrying" : "queued",
    };
  }

  function transition(message, nextStatus) {
    var current = String((message && message.status) || "queued");
    var next = String(nextStatus || current);
    if ((ALLOWED[current] || []).indexOf(next) === -1) return message;
    return Object.assign({}, message, { status: next });
  }

  function canApply(message, incomingRequestId) {
    if (!message || TERMINAL[message.status]) return false;
    return !!message.requestId && message.requestId === incomingRequestId;
  }

  function fromBackendStatus(status, verdict) {
    // Prefer the authoritative completion verdict when the reply carries one so
    // a partial/blocked/timeout run is rendered honestly, never as "failed".
    var raw = verdict && typeof verdict === "object" ? verdict.verdict : verdict;
    var v = String(raw || "").toLowerCase();
    if (VERDICT_STATE[v]) return VERDICT_STATE[v];
    if (["answered", "cache_hit", "answered_by_account", "answered_locally"].indexOf(status) !== -1) {
      return "completed";
    }
    if (status === "cancelled") return "cancelled";
    return "failed";
  }

  var api = {
    ALLOWED: ALLOWED,
    beginRequest: beginRequest,
    canApply: canApply,
    fromBackendStatus: fromBackendStatus,
    transition: transition,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.OPaiMessageState = api;
})(typeof window !== "undefined" ? window : this);
