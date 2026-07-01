"""Classify agent CLI invocations before an OPai shell wrapper launches them.

Only provider forms that OPai can reproduce without changing their observable
contract are proxied. Interactive, stdin-driven, structured-output, and unknown
forms return ``PASSTHROUGH_EXIT`` so the shell wrapper can directly execute the
real CLI with the original argv and TTY.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

PASSTHROUGH_EXIT = 125
SUPPORTED_AGENTS = {"claude", "codex", "copilot"}


@dataclass(frozen=True)
class InvocationPlan:
    agent: str
    kind: str
    argv: tuple[str, ...]
    reason: str
    prompt: str = ""
    model: str | None = None
    mode: str = "ask"


def _passthrough(agent: str, argv: Sequence[str], reason: str) -> InvocationPlan:
    return InvocationPlan(
        agent=agent,
        kind="passthrough",
        argv=tuple(str(item) for item in argv),
        reason=reason,
    )


def _option_value(argv: Sequence[str], index: int) -> tuple[str | None, int]:
    if index + 1 >= len(argv):
        return None, index + 1
    return str(argv[index + 1]), index + 2


def _classify_claude(agent: str, argv: Sequence[str]) -> InvocationPlan:
    print_mode = False
    model: str | None = None
    mode = "ask"
    positionals: list[str] = []
    index = 0
    while index < len(argv):
        token = str(argv[index])
        if token in {"-p", "--print"}:
            print_mode = True
            index += 1
        elif token in {"--model"}:
            model, index = _option_value(argv, index)
            if not model:
                return _passthrough(agent, argv, "missing_option_value")
        elif token.startswith("--model="):
            model = token.partition("=")[2] or None
            if model is None:
                return _passthrough(agent, argv, "missing_option_value")
            index += 1
        elif token == "--permission-mode":  # nosec B105 - CLI option, not a secret
            permission_mode, index = _option_value(argv, index)
            if permission_mode == "plan":
                mode = "plan"
            elif permission_mode == "default":
                mode = "ask"
            else:
                return _passthrough(agent, argv, "unsupported_permission_mode")
        elif token in {"--output-format"}:
            output_format, index = _option_value(argv, index)
            if output_format != "text":
                return _passthrough(agent, argv, "unsupported_option")
        elif token == "--output-format=text":  # nosec B105 - CLI option
            index += 1
        elif token.startswith("-"):
            return _passthrough(agent, argv, "unsupported_option")
        else:
            positionals.append(token)
            index += 1
    if not print_mode:
        return _passthrough(agent, argv, "interactive_or_management")
    if len(positionals) != 1 or not positionals[0].strip():
        return _passthrough(agent, argv, "ambiguous_prompt")
    return InvocationPlan(
        agent=agent,
        kind="proxy",
        argv=tuple(str(item) for item in argv),
        reason="canonical_print",
        prompt=positionals[0],
        model=model,
        mode=mode,
    )


def _classify_codex(agent: str, argv: Sequence[str]) -> InvocationPlan:
    if not argv or str(argv[0]) not in {"exec", "e"}:
        return _passthrough(agent, argv, "interactive_or_management")
    model: str | None = None
    positionals: list[str] = []
    index = 1
    while index < len(argv):
        token = str(argv[index])
        if token in {"-m", "--model"}:
            model, index = _option_value(argv, index)
            if not model:
                return _passthrough(agent, argv, "missing_option_value")
        elif token.startswith("--model="):
            model = token.partition("=")[2] or None
            if model is None:
                return _passthrough(agent, argv, "missing_option_value")
            index += 1
        elif token.startswith("-"):
            return _passthrough(agent, argv, "unsupported_option")
        else:
            positionals.append(token)
            index += 1
    if len(positionals) != 1 or positionals[0] == "-" or not positionals[0].strip():
        return _passthrough(agent, argv, "ambiguous_prompt")
    return InvocationPlan(
        agent=agent,
        kind="proxy",
        argv=tuple(str(item) for item in argv),
        reason="canonical_exec",
        prompt=positionals[0],
        model=model,
        mode="safe-auto",
    )


def _classify_copilot(agent: str, argv: Sequence[str]) -> InvocationPlan:
    prompt: str | None = None
    model: str | None = None
    index = 0
    while index < len(argv):
        token = str(argv[index])
        if token in {"-p", "--prompt"}:
            prompt, index = _option_value(argv, index)
            if not prompt:
                return _passthrough(agent, argv, "missing_option_value")
        elif token.startswith("--prompt="):
            prompt = token.partition("=")[2] or None
            if prompt is None:
                return _passthrough(agent, argv, "missing_option_value")
            index += 1
        elif token in {"--model"}:
            model, index = _option_value(argv, index)
            if not model:
                return _passthrough(agent, argv, "missing_option_value")
        elif token.startswith("--model="):
            model = token.partition("=")[2] or None
            if model is None:
                return _passthrough(agent, argv, "missing_option_value")
            index += 1
        elif token in {"-s", "--silent", "--no-ask-user", "--no-color"}:
            index += 1
        else:
            return _passthrough(
                agent,
                argv,
                "unsupported_option" if token.startswith("-") else "management_command",
            )
    if not prompt or not prompt.strip():
        return _passthrough(agent, argv, "interactive_or_management")
    return InvocationPlan(
        agent=agent,
        kind="proxy",
        argv=tuple(str(item) for item in argv),
        reason="canonical_prompt",
        prompt=prompt,
        model=model,
        mode="ask",
    )


def classify_invocation(agent: str, argv: Sequence[str]) -> InvocationPlan:
    """Return a conservative proxy/passthrough plan without side effects."""
    normalized = (agent or "").strip().lower()
    args = tuple(str(item) for item in argv)
    if normalized not in SUPPORTED_AGENTS:
        return _passthrough(normalized, args, "unsupported_agent")
    if normalized == "claude":
        return _classify_claude(normalized, args)
    if normalized == "codex":
        return _classify_codex(normalized, args)
    return _classify_copilot(normalized, args)


def _record_passthrough(root: Path, plan: InvocationPlan, reason: str) -> None:
    from .ledger import record_capture_session

    record_capture_session(
        root,
        "agent wrapper passthrough",
        capture_id=uuid4().hex,
        agent=plan.agent,
        mode="passthrough",
        outcome="passthrough",
        captured=False,
        paid=False,
        spend_accounted=False,
        model="",
        reason_code=reason,
        source="wrapper",
    )


def _passthrough_result(
    plan: InvocationPlan, reason: str | None = None
) -> dict[str, Any]:
    return {
        "status": "passthrough",
        "launch_kind": "passthrough",
        "reason": reason or plan.reason,
        "exit_code": PASSTHROUGH_EXIT,
    }


def launch_agent(
    project_root: Path,
    agent: str,
    argv: Sequence[str],
    *,
    runner: Any = None,
) -> dict[str, Any]:
    """Proxy a canonical one-shot call or ask the wrapper to execute it raw."""
    root = project_root.expanduser().resolve()
    plan = classify_invocation(agent, argv)
    if plan.kind != "proxy":
        _record_passthrough(root, plan, plan.reason)
        return _passthrough_result(plan)

    run = runner
    if run is None:
        from .accounts import runner_for_account

        run = runner_for_account(plan.agent, model=plan.model)
    try:
        available = run is not None and bool(run.available())
    except Exception:  # noqa: BLE001 - wrapper must fail open
        available = False
    if not available:
        _record_passthrough(root, plan, "account_runner_unavailable")
        return _passthrough_result(plan, "account_runner_unavailable")

    from .proxy import proxy_run

    result = proxy_run(
        root,
        plan.prompt,
        agent=plan.agent,
        model=plan.model,
        mode=plan.mode,
        runner=run,
    )
    result["launch_kind"] = "proxy"
    result["invocation_reason"] = plan.reason
    return result
