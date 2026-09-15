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

Three provider tiers (F2, QA E2E 2026-07-17; paid-direct tier added #673):

- **Account providers** (``claude``/``codex``/``copilot``) are subscription
  CLIs. :func:`providers` and :func:`catalog` cover exactly this tier because
  the account pickers, doctor, and the derived ``accounts.py`` /
  ``provider_contract.py`` tables are account-only contracts.
- **Free API providers** (``kimi``/``gemini``/``groq``/``mistral``) are registered in
  ``_REGISTRY`` too — with honest capability metadata — so every picker-visible
  model resolves through :func:`find` / :func:`resolve_id` / :func:`validate`
  instead of being a registry blind spot. Their *operational* spec (API base,
  env key, setup hint, picker labels) lives in ``opaihub/free_models.py``;
  this module deliberately does not duplicate it.
  ``tests/test_free_model_registry.py`` pins the two modules together so the
  tiers cannot drift apart again.
- **Paid direct-API providers** (``deepseek``) are the same shape as the free
  tier — key-gated public APIs, not subscription CLIs — but cost real money
  per token. Operational spec lives in ``opaihub/paid_api_models.py`` and
  real pricing in ``opaihub/deepseek_pricing.py``; kept out of
  ``free_models.py`` so that module's ``$0``-is-actual contract stays true.
  ``tests/test_paid_model_registry.py`` pins this tier the same way.
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
        # Canonical ids are what the Claude CLI receives via ``--model``. Proven
        # Sonnet 4.6 stays the balanced default; the Claude 5 family uses the
        # FULL API ids the CLI accepts (short forms like "sonnet-5" are NOT valid
        # CLI aliases — that regressed real runs, #307), reachable via alias.
        # New models are opt-in, so an unavailable one never breaks the default.
        ModelSpec(
            "sonnet",
            "Sonnet 4.6",
            "Sonnet 4.6",
            "balanced",
            ("claude-sonnet", "sonnet-4.6"),
        ),
        ModelSpec(
            "opus",
            "Opus 4.8",
            "Opus 4.8",
            "best",
            ("claude-opus", "opus-4.8", "claude-opus-4-8"),
        ),
        ModelSpec(
            "claude-sonnet-5",
            "Sonnet 5",
            "Claude Sonnet 5",
            "best",
            ("sonnet-5", "sonnet5"),
        ),
        ModelSpec(
            "haiku",
            "Haiku 4.5",
            "Haiku 4.5",
            "fast",
            ("claude-haiku", "haiku-4.5", "claude-haiku-4-5"),
        ),
        ModelSpec(
            "claude-opus-5",
            "Opus 5",
            "Claude Opus 5",
            "best",
            ("opus-5", "opus5", "claude-opus-5-latest"),
        ),
        ModelSpec(
            "claude-fable-5", "Fable 5", "Claude Fable 5", "fast", ("fable", "fable-5")
        ),
    ),
    "codex": (
        ModelSpec("gpt-5.6-sol", "GPT-5.6 Sol", "GPT-5.6 Sol", "best", ("gpt-5.6",)),
        ModelSpec("gpt-5.6-terra", "GPT-5.6 Terra", "GPT-5.6 Terra", "balanced"),
        ModelSpec("gpt-5.6-luna", "GPT-5.6 Luna", "GPT-5.6 Luna", "fast"),
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
        # Copilot CLI 1.0.74 documents gpt-5.4 as its OpenAI model example and
        # rejects the old picker entry gpt-5.2 as unavailable.
        ModelSpec("gpt-5.4", "GPT-5.4", "GPT-5.4", "best"),
        ModelSpec("claude-haiku-4.5", "Claude Haiku", "Claude Haiku 4.5", "fast"),
    ),
    # Free API tier (F2): picker-visible free models are registered here too,
    # so find/resolve_id/validate answer for every model the GUI offers. The
    # ids MUST stay identical to opaihub/free_models.py FREE_MODEL_SPECS
    # model_id values — the consistency test enforces it. Capability hints
    # rank within the free tier (not against account models): the lite/small
    # models are "fast"; the 120B open model is the free tier's "balanced".
    #
    # Kimi (Moonshot) leads the free tier: K2.6 is "balanced" so it is the free
    # default (default_model returns the balanced pick, and it is first in
    # free_models.py so Auto selects it as the cheapest safe cloud fallback).
    "kimi": (
        ModelSpec(
            "kimi-k2.6",
            "Kimi K2.6",
            "Moonshot Kimi K2.6 (free tier)",
            "balanced",
        ),
        ModelSpec(
            "kimi-k2.6-turbo",
            "Kimi K2.6 Turbo",
            "Moonshot Kimi K2.6 Turbo (free tier)",
            "fast",
        ),
        ModelSpec(
            "kimi-k2",
            "Kimi K2",
            "Moonshot Kimi K2 (free tier)",
            "best",
        ),
        ModelSpec(
            "kimi-latest",
            "Kimi Latest",
            "Moonshot Kimi Latest (free tier)",
            "preview",
        ),
    ),
    "gemini": (
        ModelSpec(
            "gemini-3.1-flash-lite",
            "Gemini 3.1 Flash-Lite",
            "Google Gemini 3.1 Flash-Lite (free tier)",
            "fast",
        ),
    ),
    "groq": (
        ModelSpec(
            "openai/gpt-oss-120b",
            "GPT-OSS 120B",
            "OpenAI GPT-OSS 120B via Groq (free tier)",
            "balanced",
        ),
    ),
    "mistral": (
        ModelSpec(
            "mistral-small-latest",
            "Mistral Small",
            "Mistral Small (free tier)",
            "fast",
        ),
    ),
    # Paid direct-API tier (#673): key-gated like the free tier above, but
    # real per-token spend — see opaihub/paid_api_models.py and
    # opaihub/deepseek_pricing.py for the operational spec and cost. V4 Flash
    # is "fast" (default_model's first-choice capability tier) so it — not
    # the pricier Pro — is what a bare provider="deepseek" lookup prefers.
    "deepseek": (
        ModelSpec(
            "deepseek-v4-flash",
            "DeepSeek V4 Flash",
            "DeepSeek V4 Flash (paid, per-token)",
            "fast",
        ),
        ModelSpec(
            "deepseek-v4-pro",
            "DeepSeek V4 Pro",
            "DeepSeek V4 Pro (paid, per-token)",
            "best",
        ),
    ),
}

# Account providers are subscription CLIs; free providers are key-gated public
# API tiers at genuinely zero cost. Paid-direct providers are also key-gated
# public APIs, but real per-token spend — see paid_api_models.py. All three
# live in _REGISTRY, but the account pickers, doctor catalog, and the derived
# accounts/provider_contract tables are account-only contracts.
ACCOUNT_PROVIDERS = ("claude", "codex", "copilot")
FREE_PROVIDERS = ("kimi", "gemini", "groq", "mistral")
PAID_DIRECT_PROVIDERS = ("deepseek",)


def providers() -> tuple[str, ...]:
    """All account providers the registry knows, in registration order.

    Free API providers are registered too but are exposed separately via
    :func:`free_providers` — account surfaces must not suddenly list them.
    """
    return ACCOUNT_PROVIDERS


def free_providers() -> tuple[str, ...]:
    """Free API providers (``opaihub/free_models.py`` operational specs), in
    registration order."""
    return FREE_PROVIDERS


def paid_direct_providers() -> tuple[str, ...]:
    """Paid direct-API providers (``opaihub/paid_api_models.py`` operational
    specs), in registration order. Never conflated with :func:`free_providers`
    — every id here costs real money per token."""
    return PAID_DIRECT_PROVIDERS


def models_for(provider: str) -> tuple[ModelSpec, ...]:
    """Ordered model specs for a provider (empty for unknown providers).

    The built-in table is layered with the user's own list
    (``~/.opai/models.json``) so a model a provider ships after this release
    can be used without waiting for a Vesta update. Import is local and
    failure is swallowed on purpose: the registry is imported by nearly every
    layer, and a convenience file must never be able to break model lookup.
    """
    builtin = _REGISTRY.get(str(provider or "").lower(), ())
    try:
        from opai.model_overrides import apply_overrides

        return apply_overrides(provider, builtin)
    except Exception:  # noqa: BLE001 - overrides are additive, never load-bearing
        return builtin


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
    Covers both tiers — account providers and the free API providers
    registered under :data:`FREE_PROVIDERS`.
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
    """The account-provider registry as plain data — for ``vesta doctor`` and
    the GUI Connection Doctor to render the one true account model list.

    Free API providers are registered in ``_REGISTRY`` as well (F2) but are
    intentionally not part of this account catalog; their picker/diagnostic
    surface is ``opaihub/free_models.py``.
    """
    return {
        provider: [
            {"id": spec.id, "display": spec.display, "capability": spec.capability}
            for spec in models_for(provider)
        ]
        for provider in ACCOUNT_PROVIDERS
    }
