"""User-editable model list, layered over the built-in registry.

``opai/model_registry.py`` is a hardcoded table, so it goes stale the moment a
provider ships something new: a model exists, the user's CLI accepts it, and
Vesta's picker does not offer it. Shipping a new Vesta release is the only cure,
which is the wrong coupling — provider model names change far more often than
this app does.

This is the release-independent half. A JSON file the user owns adds models to
a provider, overrides a built-in entry (same id wins), or hides one that a
provider has withdrawn. Nothing here needs Vesta to be updated, and nothing here
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
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opai.model_registry import (
    ACCOUNT_PROVIDERS,
    CAPABILITIES,
    FREE_PROVIDERS,
    PAID_DIRECT_PROVIDERS,
    ModelSpec,
)
from opaihub.atomic_io import atomic_write_text, interprocess_transaction
from opaihub.boundary_errors import safe_detail

#: Bounds. This file is read on every model lookup; it is a convenience list,
#: not a database, and an unbounded one would be a way to slow the picker down.
MAX_MODELS_PER_PROVIDER = 40
MAX_ID_CHARS = 120
MAX_LABEL_CHARS = 80

SCHEMA_VERSION = 1

_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,39}$")
_PICKER_PROVIDERS = frozenset(
    ACCOUNT_PROVIDERS + FREE_PROVIDERS + PAID_DIRECT_PROVIDERS
)


def _safe_text(value: Any, *, limit: int, field: str) -> str:
    """Accept only bounded, single-line strings from the web bridge."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field} is required")
    if len(clean) > limit:
        raise ValueError(f"{field} exceeds the {limit} character limit")
    if any(ord(char) < 32 or ord(char) == 127 for char in clean):
        raise ValueError(f"{field} must not contain control characters")
    return clean


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
    stop Vesta from listing the models it already knows about.
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


#: Cache keyed by (path, mtime_ns, size). `models_for()` is called on every
#: model lookup — picker builds, routing decisions, validation — and re-reading
#: the file each time cost ~68us per call, which is real work to do thousands of
#: times for a file that changes when the user edits it and not otherwise.
#: Keying on mtime and size rather than time means an edit still takes effect
#: immediately: no staleness window to reason about, and nothing to invalidate.
_CACHE: dict[tuple[str, int, int], OverrideReport] = {}
_CACHE_MAX = 8

#: There is deliberately *no* cache for the absent-file case, even though it is
#: the common one. A synthetic benchmark made it look urgent — 0.2us to 57us per
#: call — but measuring a real operation showed `models_for()` is called 6 times,
#: for 0.13ms against a 724ms operation: 0.02%. Caching a missing file needs a
#: time-based TTL (there is no mtime to key on), and a staleness window where a
#: file the user just created is ignored is a bad trade for 0.13ms.


def clear_cache() -> None:
    """Drop cached state. For tests and for anything that writes the file."""
    _CACHE.clear()


def load_overrides(path: Path | None = None) -> OverrideReport:
    """Read the user's model list. Never raises; never partially applies.

    A file that cannot be parsed yields errors and *no* overrides, so Vesta falls
    back to its built-in list whole rather than to some half-read version of the
    user's intent.
    """
    target = path or overrides_path()

    try:
        stat = target.stat()
        key: tuple[str, int, int] | None = (
            str(target),
            stat.st_mtime_ns,
            stat.st_size,
        )
    except OSError:
        # Missing is the common case and is not an error; anything else that
        # cannot be stat'ed is treated the same way — no overrides.
        return OverrideReport(models={}, hidden={}, path=target)
    if key is not None and key in _CACHE:
        return _CACHE[key]

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return OverrideReport(
            models={},
            hidden={},
            errors=(f"{target}: could not be read ({safe_detail(exc)})",),
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
        report = OverrideReport(models={}, hidden={}, errors=tuple(errors), path=target)
    else:
        report = OverrideReport(models=models, hidden=hidden, path=target)
    if key is not None:
        if len(_CACHE) >= _CACHE_MAX:
            # Bounded: each edit produces a new key, so an unbounded map would
            # grow for the lifetime of the process.
            _CACHE.clear()
        _CACHE[key] = report
    return report


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

    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with interprocess_transaction(target, timeout_seconds=5.0):
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".validate",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(serialized)
            check = load_overrides(temporary)
            if not check.ok:
                raise ValueError("; ".join(check.errors))
            atomic_write_text(target, serialized, mode=0o600)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    clear_cache()
    return target


def save_override_payload(payload: Any, *, path: Path | None = None) -> Path:
    """Validate and replace the complete picker-editor document.

    This is intentionally stricter than the permissive file reader: the UI is
    an editor, not an import tool, so malformed browser data must never replace
    the user's last known-good global configuration.
    """
    if not isinstance(payload, dict):
        raise ValueError("model overrides must be a JSON object")
    if set(payload) - {"schema_version", "providers"}:
        raise ValueError("model overrides contain unsupported fields")
    version = payload.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version: {version!r}")
    raw_providers = payload.get("providers")
    if not isinstance(raw_providers, dict):
        raise ValueError("'providers' must be an object")

    providers: dict[str, list[dict[str, Any]]] = {}
    hidden: dict[str, list[str]] = {}
    for raw_provider, block in raw_providers.items():
        provider = _safe_text(raw_provider, limit=40, field="provider").lower()
        if not _PROVIDER_RE.fullmatch(provider):
            raise ValueError(f"provider {provider!r} contains unsafe characters")
        if provider not in _PICKER_PROVIDERS:
            raise ValueError(
                f"provider {provider!r} is not supported by this Vesta install"
            )
        if not isinstance(block, dict) or set(block) - {"models", "hide"}:
            raise ValueError(f"{provider}: expected models and/or hide")
        entries = block.get("models", [])
        if not isinstance(entries, list):
            raise ValueError(f"{provider}: 'models' must be a list")
        if entries and provider not in ACCOUNT_PROVIDERS:
            raise ValueError(
                f"{provider}: custom model IDs are supported only for account CLIs"
            )
        if len(entries) > MAX_MODELS_PER_PROVIDER:
            raise ValueError(f"{provider}: too many models")
        cleaned_entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) - {
                "id",
                "display",
                "full",
                "capability",
                "aliases",
            }:
                raise ValueError(f"{provider}[{index}]: unsupported model fields")
            model_id = _safe_text(entry.get("id"), limit=MAX_ID_CHARS, field="model id")
            key = model_id.lower()
            if key in seen:
                raise ValueError(f"{provider}: duplicate model id {model_id!r}")
            seen.add(key)
            cleaned: dict[str, Any] = {"id": model_id}
            for field, limit in (
                ("display", MAX_LABEL_CHARS),
                ("full", MAX_LABEL_CHARS),
            ):
                if field in entry:
                    cleaned[field] = _safe_text(entry[field], limit=limit, field=field)
            capability = entry.get("capability", "balanced")
            if (
                not isinstance(capability, str)
                or capability.lower() not in CAPABILITIES
            ):
                raise ValueError(f"{provider}[{index}]: invalid capability")
            cleaned["capability"] = capability.lower()
            aliases = entry.get("aliases", [])
            if not isinstance(aliases, list):
                raise ValueError(f"{provider}[{index}]: 'aliases' must be a list")
            if aliases:
                cleaned["aliases"] = [
                    _safe_text(alias, limit=MAX_ID_CHARS, field="alias")
                    for alias in aliases
                ]
            cleaned_entries.append(cleaned)
        raw_hidden = block.get("hide", [])
        if not isinstance(raw_hidden, list):
            raise ValueError(f"{provider}: 'hide' must be a list")
        providers[provider] = cleaned_entries
        if raw_hidden:
            hidden[provider] = [
                _safe_text(model_id, limit=MAX_ID_CHARS, field="hidden model id")
                for model_id in raw_hidden
            ]
    return save_overrides(providers, hide=hidden, path=path)


def overrides_report_payload(report: OverrideReport | None = None) -> dict[str, Any]:
    """Return the secret-free, user-facing representation of global overrides."""
    resolved = report or load_overrides()
    return {
        "global": True,
        # Never reveal the account's home directory to the renderer or logs.
        "path": "~/.opai/models.json",
        "providers": {
            provider: {
                "models": [
                    {
                        "id": spec.id,
                        "display": spec.display,
                        "full": spec.full,
                        "capability": spec.capability,
                        "aliases": list(spec.aliases),
                    }
                    for spec in specs
                ]
            }
            for provider, specs in sorted(resolved.models.items())
        },
        "hidden": {
            provider: sorted(model_ids)
            for provider, model_ids in sorted(resolved.hidden.items())
        },
        "errors": list(resolved.errors),
    }
