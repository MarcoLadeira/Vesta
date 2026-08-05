"""What each external tool can actually do at the version installed (#569).

The report's worked example is the whole motivation: `gh issue view` on GitHub
CLI 2.80 fails because classic Projects fields were removed upstream. OPai
discovers this the expensive way — a failed command, a confusing 400, a burnt
provider turn — and then usually retries the same broken call.

This registry front-runs that. Before invoking a capability OPai asks whether
the *installed* version still supports it, and if not it is handed an ordered
list of alternatives (REST API, the internal connector) to use instead.

Version comparison is a plain numeric tuple compare, not a full semver
implementation: tool versions here are dotted numerics, and pulling in a
dependency to parse pre-release tags would buy nothing. Anything unparseable is
treated as *unknown*, which never silently means "supported".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

REGISTRY_PATH = Path(__file__).resolve().parent / "data" / "tool_capabilities.yaml"


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    DEGRADED = "degraded"  # known-broken here; use an alternative
    UNKNOWN = "unknown"  # not in the registry, or version unparseable


# The FIRST dotted-numeric run only. Matching every digit in the string would
# swallow trailing build dates — "gh version 2.80.0 (2026-01-01)" would parse as
# (2, 80, 0, 2026) and compare wrongly against a broken_below bound.
_VERSION_RUN = re.compile(r"\d+(?:\.\d+)*")


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return a comparable version tuple, or None when it cannot be read.

    None is meaningful: an unreadable version must not be treated as new enough
    to satisfy a `broken_below` bound.
    """

    match = _VERSION_RUN.search(str(text or ""))
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split(".")[:4])


@dataclass(frozen=True)
class CapabilityVerdict:
    """Whether a capability may be used, and what to use instead if not."""

    tool: str
    capability: str
    state: CapabilityState
    reason: str = ""
    alternatives: tuple[str, ...] = ()
    detected_version: str = ""
    min_version: str = ""

    @property
    def usable(self) -> bool:
        """Only a positively-supported capability is usable.

        UNKNOWN is deliberately *not* usable-by-default here — callers decide
        whether to try it — but it is distinguished from DEGRADED so a missing
        registry entry never masquerades as a known breakage.
        """

        return self.state is CapabilityState.SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "capability": self.capability,
            "state": self.state.value,
            "reason": self.reason,
            "alternatives": list(self.alternatives),
            "detected_version": self.detected_version,
            "min_version": self.min_version,
        }


@dataclass(frozen=True)
class Capability:
    name: str
    status: CapabilityState
    reason: str = ""
    broken_below: str = ""
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolEntry:
    tool: str
    binary: str
    capabilities: Mapping[str, Capability] = field(default_factory=dict)


def _load_registry(path: Path) -> dict[str, ToolEntry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries: dict[str, ToolEntry] = {}
    for item in raw.get("tools") or []:
        capabilities: dict[str, Capability] = {}
        for capability in item.get("capabilities") or []:
            name = str(capability.get("name") or "")
            if not name:
                continue
            try:
                status = CapabilityState(str(capability.get("status") or "unknown"))
            except ValueError:
                status = CapabilityState.UNKNOWN
            capabilities[name] = Capability(
                name=name,
                status=status,
                reason=str(capability.get("reason") or ""),
                broken_below=str(capability.get("broken_below") or ""),
                alternatives=tuple(
                    str(alt) for alt in (capability.get("alternatives") or [])
                ),
            )
        tool = str(item.get("tool") or "")
        if tool:
            entries[tool] = ToolEntry(
                tool=tool,
                binary=str(item.get("binary") or tool),
                capabilities=capabilities,
            )
    return entries


@lru_cache(maxsize=1)
def _registry() -> dict[str, ToolEntry]:
    return _load_registry(REGISTRY_PATH)


def check_capability(
    tool: str,
    capability: str,
    *,
    detected_version: str = "",
    registry: Mapping[str, ToolEntry] | None = None,
) -> CapabilityVerdict:
    """Decide whether `tool.capability` is usable at `detected_version`."""

    entries = registry if registry is not None else _registry()
    entry = entries.get(tool)
    if entry is None or capability not in entry.capabilities:
        return CapabilityVerdict(
            tool=tool,
            capability=capability,
            state=CapabilityState.UNKNOWN,
            detected_version=detected_version,
        )

    known = entry.capabilities[capability]
    state = known.status
    if known.broken_below:
        installed = parse_version(detected_version)
        required = parse_version(known.broken_below)
        if installed is None:
            # An unknown version cannot prove it is new enough to be fixed.
            state = (
                CapabilityState.UNKNOWN
                if known.status is CapabilityState.SUPPORTED
                else known.status
            )
        elif required is not None and installed >= required:
            # Upstream fixed it at or above this version: no longer degraded.
            state = CapabilityState.SUPPORTED
        else:
            state = CapabilityState.DEGRADED

    return CapabilityVerdict(
        tool=tool,
        capability=capability,
        state=state,
        reason=known.reason if state is CapabilityState.DEGRADED else "",
        alternatives=known.alternatives if state is CapabilityState.DEGRADED else (),
        detected_version=detected_version,
        min_version=known.broken_below,
    )


def preferred_alternative(
    verdict: CapabilityVerdict, *, available: Sequence[str] = ()
) -> str:
    """The first listed alternative that is actually available, else "".

    Order in the registry is preference order, so the first available wins.
    """

    if verdict.state is not CapabilityState.DEGRADED:
        return ""
    if not available:
        return verdict.alternatives[0] if verdict.alternatives else ""
    for candidate in verdict.alternatives:
        if candidate in available:
            return candidate
    return ""
