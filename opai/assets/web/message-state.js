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
  // #295: a run that handed control back to the user is waiting, not finished.
  // Keeping it out of TERMINAL is the point — canApply() refuses updates to a
  // terminal message, so recording an approval card as "blocked" made the very
  // run the user is about to resume unaddressable.
  var AWAITING = "awaiting_input";
  var LIVE = ["preparing", "authenticating", "sending", "verifying"];
  var ALLOWED = {
    queued: LIVE.concat(AWAITING, ENDS),
    // A retry re-enters the pipeline; it never jumps straight to "completed".
    retrying: LIVE.concat(AWAITING, RETRYABLE_ENDS),
    preparing: ["authenticating", "sending", "verifying", AWAITING].concat(ENDS),
    authenticating: ["sending", "verifying", AWAITING].concat(ENDS),
    sending: ["waiting", "streaming", "verifying", AWAITING].concat(ENDS),
    waiting: ["streaming", "verifying", AWAITING].concat(ENDS),
    streaming: ["verifying", AWAITING].concat(ENDS),
    // Answering resumes the work; it never skips ahead to verifying, and the
    // run can still end here if the user cancels or abandons the question.
    awaiting_input: ["preparing", "authenticating", "sending"].concat(ENDS),
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

  // #295: statuses whose run is not over — the user's answer re-sends the same
  // task plus one grant and the work continues. Mirrors
  // opaihub/run_state.AWAITING_INPUT_STATUSES; the Python guard test asserts
  // the two lists stay identical.
  var AWAITING_STATUS = {
    needs_command_approval: true,
    needs_edit_approval: true,
    needs_free_confirmation: true,
    needs_auto_confirmation: true,
    needs_limit_confirmation: true,
    needs_confirmation: true,
  };

  function fromBackendStatus(status, verdict) {
    // Checked before the verdict: an awaiting run has not reached a terminal,
    // so its verdict is provisional. Reading the verdict first is exactly what
    // recorded "shall I run this command?" as blocked.
    if (AWAITING_STATUS[status]) return AWAITING;
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
