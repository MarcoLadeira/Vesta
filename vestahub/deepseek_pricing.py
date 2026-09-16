"""Real, sourced per-token pricing for DeepSeek's direct API (#673 A6).

DeepSeek is Vesta's first *paid* direct-API provider. Every existing direct
provider (kimi/gemini/groq/mistral) is genuinely free, so ``$0`` is the
actual cost and no pricing table has ever needed to exist. Reporting
DeepSeek at the same ``$0`` would be a false receipt, and reporting
``None``/unknown would violate the never-show-unknown-as-zero rule the
report and #673 both name — so this module exists to make a real number
available at the one call site that needs it
(:func:`vestahub.local_runner.PaidAPIRunner.cost_usd`).

Prices below are sourced from the official pricing page, fetched at
implementation time (see ``SOURCE``/``OBSERVED_AT``) — not invented, and not
assumed stable. DeepSeek's own docs note an upcoming peak/off-peak pricing
policy; this snapshot is off-peak/standard pricing only. Treat ``EXPIRY`` as
a hard boundary: past it, this module answers ``measurement="stale"``
instead of silently keeping an old number current, mirroring how
``vestahub/provider_catalog.py`` treats an expired price snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
OBSERVED_AT = "2026-08-05T00:00:00Z"
# One week, matching the review cadence provider_catalog.py's existing
# entries use (their own expiry gap is 7 days) — a short enough window that
# stale-but-unnoticed pricing cannot drift far before this module says so.
EXPIRY = "2026-08-12T00:00:00Z"


@dataclass(frozen=True)
class PerTokenPrice:
    """USD per single token (the pricing page publishes per-1M; this module
    divides once here so every call site multiplies, never divides)."""

    input_cache_hit: float
    input_cache_miss: float
    output: float


# $ per 1,000,000 tokens, from SOURCE at OBSERVED_AT, converted to per-token.
_PER_MILLION = {
    "deepseek-v4-flash": {"cache_hit": 0.0028, "cache_miss": 0.14, "output": 0.28},
    "deepseek-v4-pro": {"cache_hit": 0.003625, "cache_miss": 0.435, "output": 0.87},
}

PRICES: dict[str, PerTokenPrice] = {
    model_id: PerTokenPrice(
        input_cache_hit=values["cache_hit"] / 1_000_000,
        input_cache_miss=values["cache_miss"] / 1_000_000,
        output=values["output"] / 1_000_000,
    )
    for model_id, values in _PER_MILLION.items()
}


def is_expired(*, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    expiry = datetime.fromisoformat(EXPIRY.replace("Z", "+00:00"))
    return moment >= expiry


def estimate_cost_usd(
    model_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_hit_tokens: int | None = None,
) -> tuple[float | None, str]:
    """Real dollar cost for one call, and its measurement classification.

    Returns ``(cost_usd, measurement)``. ``cost_usd`` is ``None`` only for a
    genuinely unknown ``model_id`` — never for an expired snapshot. A real
    paid call still happened whether or not this module's data is current;
    recording nothing (or falling back to an unrelated tier rate, which the
    only caller with no "unknown" slot — ``vestahub.ledger.record_model_call``
    — would otherwise do) would silently understate real spend. Staleness is
    instead a distinct, honest label: ``"estimated_stale"``.

    ``measurement`` is one of ``provider_catalog.py``'s pricing-schema
    values, plus one Vesta-local addition:

    - ``"derived"`` when the provider told us the real cache-hit/miss split
      (the exact rates each token was actually billed at).
    - ``"estimated"`` when only a total input-token count is known: every
      input token is priced at the cache-*miss* (higher) rate, so this can
      only ever over-state cost, never hide spend by assuming an unproven
      cache hit.
    - ``"estimated_stale"``: as above, but ``EXPIRY`` has passed — the
      number is still real arithmetic against the last known price, flagged
      so a human knows this table is due for a refresh.
    """

    price = PRICES.get(model_id)
    if price is None:
        return None, "unavailable"

    output_cost = max(0, int(output_tokens or 0)) * price.output

    total_input = max(0, int(input_tokens or 0))
    if cache_hit_tokens is not None:
        hit = max(0, min(int(cache_hit_tokens), total_input))
        miss = total_input - hit
        input_cost = hit * price.input_cache_hit + miss * price.input_cache_miss
        measurement = "derived"
    else:
        # Worst case, deliberately: never understate a real paid call.
        input_cost = total_input * price.input_cache_miss
        measurement = "estimated"

    if is_expired():
        measurement = "estimated_stale"

    return round(input_cost + output_cost, 8), measurement
