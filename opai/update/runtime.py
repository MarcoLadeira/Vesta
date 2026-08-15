"""Composition of OPai's canonical run and lease truth for update safety."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from pathlib import Path
from typing import Callable, Iterable

from opaihub.generated_lifecycle import TERMINAL_STATE_IDS
from opaihub.owner_lease import describe as describe_lease


@dataclass(frozen=True)
class ActiveWorkStatus:
    safe_to_install: bool
    reasons: tuple[str, ...] = ()
    active_workspaces: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "safe_to_install": self.safe_to_install,
            "reasons": list(self.reasons),
            "active_workspaces": list(self.active_workspaces),
        }


def _read_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def probe_active_work(
    workspaces: Iterable[Path],
    *,
    now: float | None = None,
    active_session_root: Path | None = None,
    is_pid_alive: Callable[[int], bool] | None = None,
) -> ActiveWorkStatus:
    """Read existing run/thread contracts; never infer idleness from one surface.

    A stale persisted ``running`` value is not active unless its canonical owner
    lease is still live. Background runs use their generated lifecycle state,
    and the in-process provider registry covers work that has not reached disk.
    """

    stamp = time.time() if now is None else float(now)
    reasons: set[str] = set()
    active_roots: list[str] = []
    terminal = set(TERMINAL_STATE_IDS)
    for raw_root in workspaces:
        try:
            root = Path(raw_root).expanduser().resolve(strict=False)
        except OSError:
            continue
        root_active = False
        state_root = root / ".opaihub"
        thread = _read_object(state_root / "gui" / "thread.json")
        if str(thread.get("state") or "") == "running":
            owner = describe_lease(thread.get("lease"), now=stamp)
            if not owner["stale"]:
                reasons.add("active_run")
                root_active = True
        workflow = _read_object(state_root / "gui" / "workflow.json")
        phase = str(workflow.get("phase") or "idle")
        if phase in {"running", "cancelling", "verifying", "delivering"}:
            reasons.add(f"workflow_{phase}")
            root_active = True
        runs = state_root / "agent" / "background" / "runs"
        if runs.is_dir():
            for path in runs.glob("*.json"):
                run = _read_object(path)
                run_state = str(run.get("run_state") or "")
                if run_state and run_state not in terminal:
                    reasons.add(f"background_{run_state}")
                    root_active = True
                    break
        if root_active:
            active_roots.append(str(root))
    try:
        from opaihub.session_registry import (
            durable_active_count,
            durable_session_root,
            registry,
        )

        durable = durable_active_count(
            active_session_root or durable_session_root(),
            **({"is_pid_alive": is_pid_alive} if is_pid_alive is not None else {}),
        )
        if registry().active_count() or durable:
            reasons.add("owned_process")
    except (ImportError, RuntimeError):
        reasons.add("runtime_probe_failed")
    ordered_reasons = tuple(sorted(reasons))
    return ActiveWorkStatus(
        safe_to_install=not ordered_reasons,
        reasons=ordered_reasons,
        active_workspaces=tuple(dict.fromkeys(active_roots)),
    )
