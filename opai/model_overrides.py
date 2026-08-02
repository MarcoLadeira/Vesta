"""User-editable model list, layered over the built-in registry.

``opai/model_registry.py`` is a hardcoded table, so it goes stale the moment a
provider ships something new: a model exists, the user's CLI accepts it, and
OPai's picker does not offer it. Shipping a new OPai release is the only cure,
which is the wrong coupling — provider model names change far more often than
this app does.

This is the release-independent half. A JSON file the user owns adds models to
a provider, overrides a built-in entry (same id wins), or hides one that a
provider has withdrawn. Nothing here needs OPai to be updated, and nothing here
can be silently wrong in an invisible way: a malformed file is reported and
ignored, never partially applied.

**Why not auto-discover from the CLIs?** Because they cannot be asked. None of
`claude`, `codex` or `copilot` exposes a model-listing command — they take
``--model <id>`` and fail at request time on a bad one. Inventing model ids from
guesswork and presenting them as available would be worse than a stale list: a
wrong id fails mid-run, after the user has committed to the task. So the list
stays declarative, and the user gets a supported way to correct it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opai.model_registry import CAPABILITIES, ModelSpec

#: Bounds. This file is read on every model lookup; it is a convenience list,
#: not a database, and an unbounded one would be a way to slow the picker down.
MAX_MODELS_PER_PROVIDER = 40
MAX_ID_CHARS = 120
MAX_LABEL_CHARS = 80

SCHEMA_VERSION = 1


def overrides_path() -> Path:
    """Where the user's model list lives.

    Global rather than per-workspace on purpose: which models a provider offers
    is a property of the account, not of the repository you happen to be in.
    """
    env = os.environ.get("OPAI_MODEL_OVERRIDES")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".opai" / "models.json"


@dataclass(frozen=True)
class OverrideReport:
    """What loading produced, including why anything was refused.

    Errors are carried rather than raised: a broken overrides file must not
    stop OPai from listing the models it already knows about.
    """

    models: dict[str, tuple[ModelSpec, ...]]
    hidden: dict[str, frozenset[str]]
    errors: tuple[str, ...] = ()
    path: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def _clean(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _spec_from(
    entry: Any, *, provider: str, index: int
) -> tuple[ModelSpec | None, str]:
    """Build one spec, or explain precisely why the entry was refused."""
    where = f"{provider}[{index}]"
    if not isinstance(entry, dict):
        return None, f"{where}: expected an object, got {type(entry).__name__}"
    model_id = _clean(entry.get("id"), limit=MAX_ID_CHARS)
    if not model_id:
        return None, f"{where}: 'id' is required (the value passed to the CLI)"

    capability = _clean(entry.get("capability"), limit=20).lower() or "balanced"
    if capability not in CAPABILITIES:
        return None, (
            f"{where}: capability {capability!r} is not one of "
            f"{', '.join(CAPABILITIES)}"
        )

    display = _clean(entry.get("display"), limit=MAX_LABEL_CHARS) or model_id
    full = _clean(entry.get("full"), limit=MAX_LABEL_CHARS) or display

    raw_aliases = entry.get("aliases") or ()
    if isinstance(raw_aliases, str) or not isinstance(raw_aliases, (list, tuple)):
        return None, f"{where}: 'aliases' must be a list"
    aliases = tuple(
        alias
        for alias in (_clean(item, limit=MAX_ID_CHARS) for item in raw_aliases)
        if alias
    )
    return ModelSpec(model_id, display, full, capability, aliases), ""


def load_overrides(path: Path | None = None) -> OverrideReport:
    """Read the user's model list. Never raises; never partially applies.

    A file that cannot be parsed yields errors and *no* overrides, so OPai falls
    back to its built-in list whole rather than to some half-read version of the
    user's intent.
    """
    target = path or overrides_path()
    if not target.exists():
        return OverrideReport(models={}, hidden={}, path=target)

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return OverrideReport(
            models={},
            hidden={},
            errors=(f"{target}: could not be read ({exc})",),
            path=target,
        )
    if not isinstance(raw, dict):
        return OverrideReport(
            models={},
            hidden={},
            errors=(f"{target}: expected a JSON object at the top level",),
            path=target,
        )

    providers_raw = raw.get("providers")
    if providers_raw is None:
        providers_raw = {k: v for k, v in raw.items() if k != "schema_version"}
    if not isinstance(providers_raw, dict):
        return OverrideReport(
            models={},
            hidden={},
            errors=(f"{target}: 'providers' must be an object",),
            path=target,
        )

    models: dict[str, tuple[ModelSpec, ...]] = {}
    hidden: dict[str, frozenset[str]] = {}
    errors: list[str] = []

    for provider, value in providers_raw.items():
        name = _clean(provider, limit=40).lower()
        if not name:
            continue
        entries = value.get("models") if isinstance(value, dict) else value
        hide = value.get("hide") if isinstance(value, dict) else None
        if isinstance(hide, (list, tuple)):
            hidden[name] = frozenset(
                item.lower()
                for item in (_clean(h, limit=MAX_ID_CHARS) for h in hide)
                if item
            )
        if entries is None:
            continue
        if not isinstance(entries, (list, tuple)):
            errors.append(f"{name}: 'models' must be a list")
            continue
        if len(entries) > MAX_MODELS_PER_PROVIDER:
            errors.append(
                f"{name}: {len(entries)} models exceeds the "
                f"{MAX_MODELS_PER_PROVIDER} limit"
            )
            continue
        specs: list[ModelSpec] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            spec, problem = _spec_from(entry, provider=name, index=index)
            if spec is None:
                errors.append(problem)
                continue
            if spec.id.lower() in seen:
                errors.append(f"{name}: duplicate model id {spec.id!r}")
                continue
            seen.add(spec.id.lower())
            specs.append(spec)
        if specs:
            models[name] = tuple(specs)

    if errors:
        # All-or-nothing: applying the readable half of a file the user got
        # wrong would give them a model list matching neither their intent nor
        # the built-in default, and no clear way to reason about which.
        return OverrideReport(models={}, hidden={}, errors=tuple(errors), path=target)
    return OverrideReport(models=models, hidden=hidden, path=target)


def apply_overrides(
    provider: str,
    builtin: tuple[ModelSpec, ...],
    report: OverrideReport | None = None,
) -> tuple[ModelSpec, ...]:
    """Merge the user's list over ``builtin`` for one provider.

    Order is deliberate: user entries that *replace* a built-in keep the
    built-in's position, so correcting one model's id does not reshuffle the
    picker. Genuinely new models are appended, because a new entry the user
    added is not automatically more important than the defaults.
    """
    resolved = report or load_overrides()
    name = str(provider or "").lower()
    extra = resolved.models.get(name, ())
    hide = resolved.hidden.get(name, frozenset())

    by_id = {spec.id.lower(): spec for spec in extra}
    merged: list[ModelSpec] = []
    for spec in builtin:
        key = spec.id.lower()
        if key in hide:
            continue
        merged.append(by_id.pop(key, spec))
    for spec in extra:
        if spec.id.lower() in by_id and spec.id.lower() not in hide:
            merged.append(spec)
            by_id.pop(spec.id.lower(), None)
    return tuple(merged)


def save_overrides(
    providers: dict[str, list[dict[str, Any]]],
    *,
    hide: dict[str, list[str]] | None = None,
    path: Path | None = None,
) -> Path:
    """Write the overrides file, validating before it replaces anything."""
    target = path or overrides_path()
    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "providers": {}}
    for provider, entries in providers.items():
        block: dict[str, Any] = {"models": entries}
        if hide and hide.get(provider):
            block["hide"] = hide[provider]
        payload["providers"][provider] = block
    for provider, hidden in (hide or {}).items():
        payload["providers"].setdefault(provider, {})["hide"] = hidden

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # Validate what was actually written, not what we meant to write.
    check = load_overrides(temporary)
    if not check.ok:
        temporary.unlink(missing_ok=True)
        raise ValueError("; ".join(check.errors))
    temporary.replace(target)
    return target
