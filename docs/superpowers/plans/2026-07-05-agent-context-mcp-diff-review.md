# OPai Context, MCP, and Diff Review Implementation Plan

**Goal:** Close #173–#175 with one local-first, structured agent-workbench slice.

### Task 1: semantic context

- [x] Write failing tests for deterministic local embeddings, ignored/secret files, no persisted source, provenance, stale chunks, and bounded ACI observations.
- [x] Implement the semantic index and ACI methods.

### Task 2: MCP runtime

- [x] Write failing tests for enabled/team-approved discovery, disabled/remote/write/destructive gates, cancellation, result redaction, and mock invocation.
- [x] Implement the injected MCP runtime and ACI methods without changing provider transports.

### Task 3: visual diff review

- [x] Write failing tests for file/hunk parsing, risk labels, untracked previews, path filtering, persisted decisions, and ship blockers.
- [x] Add workflow/pipeline integration and shared GUI payloads.
- [x] Add accessible web review navigation and decision controls with Playwright coverage.

### Task 4: publish

- [x] Run full Python, format/lint, Bandit, JS unit, and Playwright suites.
- [ ] Review the complete diff, secret scan, risky files, and branch freshness.
- [ ] Commit, push, open a PR closing #173, #174, and #175, monitor CI, and merge only when all gates pass.
