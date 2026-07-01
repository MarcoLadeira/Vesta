/* Pure request/message state shared by the browser and Vitest. */
(function (global) {
  "use strict";

  var TERMINAL = { completed: true, failed: true, cancelled: true };
  var ALLOWED = {
    queued: ["preparing", "authenticating", "sending", "completed", "failed", "cancelled"],
    retrying: ["preparing", "authenticating", "sending", "failed", "cancelled"],
    preparing: ["authenticating", "sending", "completed", "failed", "cancelled"],
    authenticating: ["sending", "completed", "failed", "cancelled"],
    sending: ["waiting", "streaming", "completed", "failed", "cancelled"],
    waiting: ["streaming", "completed", "failed", "cancelled"],
    streaming: ["completed", "failed", "cancelled"],
    failed: ["retrying"],
    cancelled: ["retrying"],
    completed: [],
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

  function fromBackendStatus(status) {
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
