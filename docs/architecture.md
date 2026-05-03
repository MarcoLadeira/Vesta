# OPcoding Architecture

## First-Third Scope

The MVP is a deterministic command spine:

```text
CLI -> scanner -> project profile -> context summary -> router -> local tools
```

The system deliberately avoids automatic cloud model calls. It prepares the exact evidence a coding agent needs: project profile, compressed context, git diff, test output, failure clues, budget route, and secret scan.

## Components

- `opcoding/cli.py`: command entrypoint.
- `opcoding/scanner.py`: detects stack, files, package managers, commands, docs, CI, git.
- `opcoding/context_manager.py`: writes `.opcoding/project.json` and `.opcoding/context.md`.
- `opcoding/cost.py`: routes tasks across L0-L4 and logs route decisions.
- `opcoding/gitops.py`: safe git summaries, commit messages, branch names, PR drafts.
- `opcoding/testing.py`: test command detection, execution logs, failure clues.
- `opcoding/reviewer.py`: local diff review heuristics and secret checks.
- `opcoding/doctor.py`: local tool readiness checks.

## Phase 2 Targets

- Model adapters with hard budget gates.
- MCP server manager with per-project profiles.
- Local memory server backed by SQLite.
- Project index server for symbol/file search.
- Agent runner that batches compatible agents.
- CI integration and pre-commit hooks.
