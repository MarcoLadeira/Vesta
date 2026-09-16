/* Canonical RunResult -> browser presentation adapter (#618). */
(function (global) {
  "use strict";

  var lifecycle = global.VestaLifecycle;
  if (!lifecycle) throw new Error("generated lifecycle contract must load first");

  function degraded() {
    var state = lifecycle.degradedInputs.unknown_state.state;
    var spec = lifecycle.stateSpecs[state];
    return {
      verdict: state,
      state: state,
      label: spec.label,
      category: spec.presentation_category,
      reason: "Canonical run result was invalid and needs manual review.",
      automaticRetry: false,
      retryReason: "manual_review",
      reasonCode: "",
      nextAction: "",
      answerConflicts: false,
      canonical: true,
    };
  }

  function fromRunResult(raw, completionVerdict) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return degraded();
    var acceptedVersions = [lifecycle.schemaVersion].concat(lifecycle.acceptedLegacySchemaVersions || []);
    if (acceptedVersions.indexOf(raw.schema_version) === -1) return degraded();

    var requiredMappings = [
      "identity", "lifecycle", "provider", "recovery", "verification",
      "delivery", "economics", "authority", "diagnostics", "presentation",
      "compatibility",
    ];
    if (requiredMappings.some(function (field) {
      return !raw[field] || typeof raw[field] !== "object" || Array.isArray(raw[field]);
    })) return degraded();

    var lifecycleValue = raw.lifecycle;
    var presentation = raw.presentation;
    var recovery = raw.recovery;

    var state = String(lifecycleValue.state || "").toLowerCase();
    var spec = lifecycle.stateSpecs[state];
    var reason = String(lifecycleValue.reason_detail || "").trim();
    if (!spec || !spec.terminal || !reason ||
        lifecycleValue.reason !== "terminal_resolution" ||
        lifecycleValue.reconciliation !== "reconciled" ||
        !String(lifecycleValue.final_transition_at || "").trim() ||
        presentation.label !== spec.label ||
        presentation.category !== spec.presentation_category ||
        typeof recovery.automatic_retry !== "boolean" ||
        typeof raw.authority.mutating !== "boolean" ||
        !Array.isArray(raw.diagnostics.record_refs) ||
        raw.compatibility.automatic_retry !== recovery.automatic_retry) return degraded();

    var supplement = completionVerdict && typeof completionVerdict === "object"
      ? completionVerdict
      : {};
    return {
      verdict: state,
      state: state,
      label: spec.label,
      category: spec.presentation_category,
      reason: reason,
      automaticRetry: recovery.automatic_retry,
      retryReason: String(recovery.reason || ""),
      reasonCode: String(supplement.reason_code || "").toLowerCase(),
      nextAction: String(supplement.next_action || ""),
      answerConflicts: supplement.answer_conflicts === true,
      canonical: true,
    };
  }

  function fromLegacyVerdict(raw) {
    if (!raw || typeof raw !== "object") return null;
    var state = String(raw.verdict || "").toLowerCase();
    if (!state) return null;
    if (!lifecycle.isTerminal(state)) return degraded();
    var spec = lifecycle.stateSpecs[state];
    return {
      verdict: state,
      state: state,
      label: spec.label,
      category: spec.presentation_category,
      reason: String(raw.reason || ""),
      automaticRetry: false,
      retryReason: "",
      reasonCode: String(raw.reason_code || "").toLowerCase(),
      nextAction: String(raw.next_action || ""),
      answerConflicts: raw.answer_conflicts === true,
      canonical: false,
    };
  }

  function fromResult(result) {
    if (!result || typeof result !== "object") return null;
    if (Object.prototype.hasOwnProperty.call(result, "run_result")) {
      return fromRunResult(result.run_result, result.completion_verdict);
    }
    return fromLegacyVerdict(result.completion_verdict);
  }

  var api = {
    fromLegacyVerdict: fromLegacyVerdict,
    fromResult: fromResult,
    fromRunResult: fromRunResult,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.VestaRunResult = api;
})(typeof window !== "undefined" ? window : globalThis);
