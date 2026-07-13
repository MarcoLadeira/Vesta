"""Single source of truth for provider model ids, aliases, display names, and
capability hints (#170).

Before this module, account model lists lived as hardcoded tuples in
``opaihub/accounts.py`` (CLAUDE_MODELS / CODEX_MODELS / COPILOT_MODELS) with
display names duplicated in ``opai/provider_contract.py`` (_CLAUDE_DISPLAY …).
The two drifted, and the gap between them and what the CLIs actually accept
produced "model not found" failures that looked like auth problems.

Now both derive from the registry here, and there is one validation path
(:func:`validate`) that answers "does this provider still accept this model,
and if not, what is the safe fallback?". Kept dependency-free so every layer
(accounts, provider_contract, routing, doctor, the GUI Connection Doctor) can
import it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Capability hints, cheapest/fastest → most capable. "preview" = experimental.
CAPABILITIES = ("fast", "balanced", "best", "preview")


@dataclass(frozen=True)
class ModelSpec:
    """One provider model. ``id`` is what the provider CLI is given.

    ``display`` is the short picker label ("Sonnet 4.6", "Spark"); ``full`` is
    the longer name used on advanced/diagnostic surfaces ("GPT-5.3 Codex
    Spark"); ``aliases`` are alternate ids that resolve to ``id`` so friendly
    or dated names don't fail as "model not found".
    """

    id: str
    display: str
    full: str
    capability: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Ordering is significant — it is the model-picker order (kept identical to the
# previous accounts.py tuples so the picker does not visibly change).
_REGISTRY: dict[str, tuple[ModelSpec, ...]] = {
    "claude": (
        # Claude 5 family is the current default; Sonnet 4.6 stays reachable by
        # its canonical id/alias so existing selections never break (#307).
        ModelSpec(
            "sonnet-5",
            "Sonnet 5",
            "Sonnet 5",
            "balanced",
            ("claude-sonnet-5", "sonnet5"),
        ),
        ModelSpec("opus", "Opus 4.8", "Opus 4.8", "best", ("claude-opus", "opus-4.8")),
        ModelSpec("fable", "Fable 5", "Fable 5", "fast", ("claude-fable-5", "fable-5")),
        ModelSpec(
            "sonnet",
            "Sonnet 4.6",
            "Sonnet 4.6",
            "balanced",
            ("claude-sonnet", "sonnet-4.6"),
        ),
        ModelSpec(
            "haiku", "Haiku 4.5", "Haiku 4.5", "fast", ("claude-haiku", "haiku-4.5")
        ),
    ),
    "codex": (
        ModelSpec("gpt-5.6", "GPT-5.6", "GPT-5.6", "best"),
        ModelSpec("gpt-5.5", "GPT-5.5", "GPT-5.5", "best"),
        ModelSpec("gpt-5.4", "GPT-5.4", "GPT-5.4", "balanced"),
        ModelSpec(
            "gpt-5.4-mini", "GPT-5.4 Mini", "GPT-5.4 Mini", "fast", ("gpt-5.4mini",)
        ),
        ModelSpec(
            "gpt-5.3-codex-spark", "Spark", "GPT-5.3 Codex Spark", "preview", ("spark",)
        ),
    ),
    "copilot": (
        ModelSpec(
            "claude-sonnet-4.6", "Claude Sonnet", "Claude Sonnet 4.6", "balanced"
        ),
        ModelSpec("gpt-5.2", "GPT-5.2", "GPT-5.2", "best"),
        ModelSpec("claude-haiku-4.5", "Claude Haiku", "Claude Haiku 4.5", "fast"),
    ),
}


def providers() -> tuple[str, ...]:
    """All account providers the registry knows, in registration order."""
    return tuple(_REGISTRY)


def models_for(provider: str) -> tuple[ModelSpec, ...]:
    """Ordered model specs for a provider (empty for unknown providers)."""
    return _REGISTRY.get(str(provider or "").lower(), ())


def _index(provider: str) -> dict[str, ModelSpec]:
    """Lower-cased id/alias → spec. Canonical ids win over aliases on clash."""
    index: dict[str, ModelSpec] = {}
    for spec in models_for(provider):
        for alias in spec.aliases:
            index.setdefault(alias.lower(), spec)
    for spec in models_for(provider):  # ids second so they always win
        index[spec.id.lower()] = spec
    return index


def find(provider: str, model_id: str | None) -> ModelSpec | None:
    """The spec for a model id or alias, or None if the provider doesn't list it."""
    return _index(provider).get(str(model_id or "").lower())


def resolve_id(provider: str, model_id: str | None) -> str | None:
    """Canonical id for a model id/alias — e.g. ``claude-opus`` → ``opus`` — or None."""
    spec = find(provider, model_id)
    return spec.id if spec is not None else None


def display_map(provider: str) -> dict[str, str]:
    """``{canonical_id: short display}`` for a provider (picker labels)."""
    return {spec.id: spec.display for spec in models_for(provider)}


def default_model(provider: str) -> ModelSpec | None:
    """The safe fallback for a provider: a balanced model, else fast, else best,
    else the first listed. Used when a selected model is no longer accepted."""
    specs = models_for(provider)
    for capability in ("balanced", "fast", "best"):
        for spec in specs:
            if spec.capability == capability:
                return spec
    return specs[0] if specs else None


def validate(provider: str, model_id: str | None) -> dict[str, object]:
    """Answer whether a provider still accepts a model.

    Returns ``{valid, canonical, fallback, reason}``. ``canonical`` is the
    resolved id when valid; ``fallback`` is the safe default id to route to
    instead; ``reason`` is a human sentence when invalid (empty when valid).
    """
    prov = str(provider or "").lower()
    if prov not in _REGISTRY:
        return {
            "valid": False,
            "canonical": None,
            "fallback": None,
            "reason": f"Unknown provider '{provider}'.",
        }
    fallback = default_model(prov)
    fallback_id = fallback.id if fallback is not None else None
    spec = find(prov, model_id)
    if spec is None:
        return {
            "valid": False,
            "canonical": None,
            "fallback": fallback_id,
            "reason": (
                f"{prov} no longer lists '{model_id}'"
                + (f" — fall back to {fallback_id}." if fallback_id else ".")
            ),
        }
    return {"valid": True, "canonical": spec.id, "fallback": fallback_id, "reason": ""}


def catalog() -> dict[str, list[dict[str, str]]]:
    """The whole registry as plain data — for ``opai doctor`` and the GUI
    Connection Doctor to render the one true model list."""
    return {
        provider: [
            {"id": spec.id, "display": spec.display, "capability": spec.capability}
            for spec in specs
        ]
        for provider, specs in _REGISTRY.items()
    }
