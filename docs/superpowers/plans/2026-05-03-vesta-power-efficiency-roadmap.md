# Vesta Power, Efficiency, And Programming Quality Roadmap

Date: 2026-05-03

Goal: make Vesta feel like an automatic AI coding operating layer across every project: local-first, Superpowers-aware, tool-backed, cheap by default, and able to ship real apps with much less manual setup.

## Current Fix Baseline

This roadmap assumes the new activation layer exists:

- `vesta activate` attaches any project to Vesta.
- Vesta writes managed project instructions for Codex-compatible agents, Claude Code, and Copilot.
- Vesta ensures Superpowers skills are bridged into `~/.agents/skills/superpowers` when Superpowers is installed under `~/.codex/superpowers/skills`.
- Vesta wrappers call activation before launching Codex, Claude, or Copilot.
- `vesta route` collects local evidence before model escalation.

## Phase 1: Make Vesta Impossible To Forget

High-impact goal: every AI session launched through Vesta should automatically inherit the Vesta workflow.

- Add `vesta activate --repair` to re-check global skill files, shell wrappers, Superpowers bridge, project instructions, and MCP exports.
- Add `vesta activate --dry-run` so users can see what files Vesta would touch.
- Add `vesta status --project .` that shows project attached, global integrations active, Superpowers active, tools installed, MCP status, and AI-client instruction status in one compact view.
- Add project-local activation policy in `.vestahub/policy.yaml`:
  - write project instructions: true or false
  - write Copilot instructions: true or false
  - enable Superpowers bridge: true or false
  - cloud disabled by default
- Add `vesta uninstall --project` and `vesta uninstall --global` using managed-block removal only.
- Add shell-wrapper self-heal: if wrapper is old, `vesta doctor` should recommend or perform `vesta activate --repair`.

## Phase 2: Evidence Router Becomes The Brain

High-impact goal: Vesta should choose the cheapest useful action before any AI agent thinks deeply.

- Expand `vesta route` into a reusable evidence DAG:
  - project markers
  - language and framework detection
  - git status and diff
  - changed files
  - recent test failures
  - package manifests
  - lockfile state
  - known commands
  - enabled tools and MCP servers
  - cached summaries
- Add route classifications:
  - feature build
  - bug fix
  - failing test
  - code review
  - refactor
  - dependency update
  - security audit
  - release prepare
  - deploy prepare
  - docs update
  - research task
- Persist route decisions to `.vestahub/runs/`.
- Add `vesta why` to explain why Vesta chose local tools, cheap model, strong model, or confirmation.
- Add cache keys based on task text, git diff hash, file hashes, tool versions, prompt version, and project profile hash.
- Add route deduplication so multiple agents share the same evidence pack instead of re-scanning.

## Phase 3: Project Context Packs

High-impact goal: never waste tokens dumping whole repos.

- Add `.vestahub/context/code-map.json` with directory summaries and important symbols.
- Add `.vestahub/context/test-map.json` mapping source files to likely tests.
- Add `.vestahub/context/dependency-map.json` for manifests and risky packages.
- Add `.vestahub/context/architecture.md` generated from deterministic project scans first, then optionally improved by cheap/local models.
- Add per-file summary cache keyed by file hash.
- Add `vesta context pack --changed` that returns only changed files, adjacent tests, relevant docs, and prior summaries.
- Add `vesta context explain <path>` for local code map lookup before model use.
- Add SQLite FTS index as the default search backend.
- Add optional local embeddings only when a local model is already installed.

## Phase 4: Test Intelligence

High-impact goal: faster fixes and fewer wasted full test runs.

- Build test command detector for Python, Node, Go, Rust, .NET, Java, Playwright, and Cypress.
- Add test selector:
  - changed file to likely test
  - failing stack trace to test file
  - package/framework specific selectors
- Add `vesta test --changed`.
- Add `vesta test --last-failure`.
- Add flaky-test history under `.vestahub/testing/flaky.json`.
- Add failure summarizer that captures command, return code, compact stack, changed files, and likely owners.
- Use local model or cheap model for first-pass failure explanation only after deterministic extraction.

## Phase 5: Tool And MCP Marketplace

High-impact goal: Vesta can organize hundreds of tools without becoming messy.

- Add registry schema validation with duplicate detection and normalized categories.
- Add `vesta tool search <query>`.
- Add `vesta tool install <id> --dry-run`.
- Add `vesta tool health --all --parallel`.
- Add trust metadata:
  - source URL
  - license
  - maintainer
  - install risk
  - permission risk
  - network risk
  - last checked
- Add MCP export targets:
  - Codex
  - Claude Code
  - Cursor-compatible config
  - generic MCP JSON
- Add per-project MCP allowlists and forbidden path rules.
- Add MCP health checks with no cloud calls by default.

## Phase 6: Local Model Layer

High-impact goal: stronger AI feel with far less spend.

- Detect Ollama, LM Studio, llama.cpp, vLLM, and local OpenAI-compatible servers.
- Add `vesta models recommend --task <task>`.
- Add benchmark tasks:
  - summarize failure
  - classify route
  - explain small file
  - draft tests
- Use local models for:
  - context summaries
  - route classification
  - test failure first pass
  - documentation drafts
  - simple refactor suggestions
- Require explicit confirmation for:
  - model downloads
  - cloud model calls
  - expensive reasoning tiers
- Add model fallback policy:
  - deterministic
  - cache
  - local model
  - cheap cloud model if enabled
  - strong cloud model with confirmation
  - GPT-5.5 Max with explicit escalation

## Phase 7: Agent Orchestration

High-impact goal: agent power without agent chaos.

- Give every agent a budget, allowed tools, forbidden tools, stop conditions, and evidence inputs.
- Add agent run ledger:
  - task
  - evidence key
  - tools used
  - files touched
  - estimated cost
  - output artifacts
- Add parallel-safe task splitting:
  - disjoint file ownership
  - shared evidence pack
  - no duplicate scans
  - no destructive actions
- Add agent review gate:
  - implementation must run tests
  - review must cite diff lines
  - security agent must run scans before commentary
- Add `vesta workflow run app-create`:
  - scaffold
  - route
  - implement
  - test
  - review
  - docs
  - package
  - release notes

## Phase 8: One Prompt To App, Second Prompt To Ship

High-impact goal: make app creation feel magical while staying safe.

- Add project templates:
  - Python CLI
  - FastAPI API
  - React app
  - Next.js app
  - static site
  - full-stack app
  - browser automation tool
- Add `vesta app create "<idea>"`.
- Add spec extraction:
  - app type
  - target users
  - features
  - data model
  - tests
  - deployment target
- Add deterministic scaffold first.
- Add AI only for ambiguity, code generation, UX copy, or architecture gaps.
- Add `vesta app ship`:
  - full test gate
  - security gate
  - build gate
  - GitOps summary
  - release notes
  - rollback plan
  - deploy only after confirmation

## Phase 9: Cost And Quality Analytics

High-impact goal: prove Vesta saves money and improves coding.

- Add `.vestahub/analytics/usage.jsonl`.
- Track:
  - route tier
  - estimated tokens avoided
  - cached context hits
  - tools run
  - tests run
  - failures caught before AI
  - cloud calls blocked
  - user confirmations
- Add `vesta savings`.
- Add `vesta quality report`.
- Add weekly local-only report:
  - most useful tools
  - most expensive workflows
  - repeated failures
  - missing tests
  - slow checks
  - recommended automation

## Phase 10: Security And Release Hardening

High-impact goal: users trust Vesta with real codebases.

- Add managed-block uninstall for every file Vesta writes.
- Add explicit file-write manifest per activation.
- Add path boundary enforcement for recursive actions.
- Add secret redaction for every log and command result.
- Add optional pre-commit installation.
- Add GitHub Actions:
  - format
  - lint
  - tests
  - registry validation
  - package smoke install
  - Bandit
  - pip-audit
  - detect-secrets
  - markdownlint
  - pyright
- Add signed release artifacts later.
- Add clear pre-alpha warning and rollback docs.

## Highest-ROI Next Build Order

- Build `vesta status` and `vesta activate --repair`.
- Add managed-block uninstall.
- Persist route evidence packs under `.vestahub/runs/`.
- Add project context pack generation.
- Add test selector MVP.
- Add CI workflows and package smoke test in CI.
- Add local model detection and recommendations.
- Add `vesta app create` MVP with deterministic scaffolds.
- Add dashboard page for activation, tools, MCP, costs, and route history.
