# Vesta Local Context, MCP Runtime, and Diff Review Design

## Scope

Implement issues #173, #174, and #175 as one extension of the Vesta-owned coding-agent runtime introduced by #172. Providers remain executors; Vesta owns context provenance, tool authorization, observations, review decisions, and persisted workflow truth.

## Local semantic context

`vestahub.semantic_index` builds deterministic, repository-scoped hash embeddings from bounded code chunks. It indexes text-like tracked/unignored files, skips generated/secret-bearing paths and oversized/binary files, and persists only vectors, hashes, and line provenance under `.vestahub/agent/semantic-index.json`—never source text. Query results re-read the current bounded slice, redact it, verify the content hash, and return score/model/path/line provenance. `AgentComputerInterface` exposes index and search as structured observations.

## MCP runtime boundary

`vestahub.mcp_runtime` consumes existing effective MCP registry/team-policy state and injected clients. Discovery exposes tools only from enabled, team-approved servers. Every descriptor carries read/write/destructive/remote posture. Invocation validates the discovered tool, cancellation, remote consent, and write authorization before calling the client; results and errors are recursively redacted and output-bounded. The adapter never launches arbitrary MCP commands or reads credential values. ACI wraps discovery/invocation as observations and the workflow ledger can record them without granting MCP ownership of runtime phases.

## Visual diff review

`vestahub.diff_review` parses bounded unified diff evidence into files and hunks with additions/deletions, old/new ranges, risk markers, and redacted preview lines. It filters to files attributed to the current provider response/new task changes, adds safe previews for untracked files, and persists only explicit `approved`/`rejected`/`pending` decisions. Rejecting a file blocks ship state; approving marks review evidence but never commits, reverts, or merges.

The web cockpit renders an accessible expandable review card with previous/next navigation, risky-file labels, hunk previews, and approve/reject controls wired to a narrow bridge method. The desktop surface shows the review summary. Workflow state carries the same structured review payload on both surfaces.

## Safety invariants

- No cloud embedding dependency or paid provider by default.
- No source text in the semantic index, and no prompt text, MCP credentials, or raw secrets in persisted workflow/ledger state.
- MCP writes, destructive tools, and remote tools fail closed without current authorization.
- Path traversal, ignored/generated directories, binary files, and secret-bearing filenames are excluded.
- Diff review decisions do not mutate the working tree.
- Output sizes, chunk counts, tool counts, and snippets are bounded and deterministic.

## Verification

Unit tests cover indexing, provenance, ignore rules, stale indexes, query bounds, MCP approval/cancellation/redaction, diff parsing/risk/decisions, and workflow payloads. Playwright covers accessible visual review and decision navigation. Full Python, Ruff format/lint, Bandit, JavaScript unit, Playwright, registry validation, and CI must pass before merge.
