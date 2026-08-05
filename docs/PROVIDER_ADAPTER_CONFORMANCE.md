# Provider Adapter Conformance

<!-- Generated from the immutable catalog `opaihub/data/provider_catalog/v1.json` by `opaihub.provider_catalog.render_coverage_matrix()`. Do not edit by hand. -->

Catalog version: `v1`
Protocol version: `1`

## Coverage matrix

| Provider | Stream | Cancel | Usage | Tools | Structured output | Smoke |
| --- | --- | --- | --- | --- | --- | --- |
| claude | supported | supported | supported | supported | partial | partial |
| codex | supported | supported | supported | supported | partial | partial |
| copilot | supported | supported | supported | supported | partial | partial |
| kimi | unsupported | supported | supported | supported | partial | partial |
| gemini | unsupported | supported | supported | supported | partial | partial |
| groq | unsupported | supported | supported | supported | partial | partial |
| mistral | unsupported | supported | supported | supported | partial | partial |
| deepseek | supported | supported | supported | supported | partial | partial |
| ollama | unsupported | supported | supported | unsupported | unsupported | partial |
| openai-compatible | unsupported | supported | supported | unsupported | unsupported | partial |

## Generated provenance and compatibility

This document is the byte-for-byte output of `render_coverage_matrix()` for the pinned catalog v1. Change the catalog snapshot and its replay fixture before regenerating this document.

The Stream, Cancel, Tools, and Structured output cells are the exact `streaming`, `cancellation`, `tool_calling`, and `structured_output` statuses in each catalog record. Usage is `supported` because protocol v1 validates typed usage observations for every adapter; it is not a pricing, cost, completion, authority, or verification claim. Smoke is `partial` because every provider has deterministic offline conformance coverage, while credentialed sandbox smoke remains explicitly opt-in and may be skipped. These two protocol/harness values are common to every catalog record, not inferred from provider or model names.

To migrate, consumers must retain the announced catalog and protocol versions and regenerate this evidence from the matching snapshot. An unknown or incompatible version, capability, readiness, or SLO proof is reported as degraded with an actionable upgrade or reconfiguration path; there is no silent fallback to a weaker adapter contract.
