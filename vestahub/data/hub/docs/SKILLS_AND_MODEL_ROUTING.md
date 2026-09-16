# Vesta Skills And Model Routing

Vesta 0.2.0 alpha.1 has a small model-intelligence layer and a Vesta skill library. The goal is to make every coding session start with local evidence, choose the cheapest capable route, and expose repeatable workflows through native skill discovery.

## Commands

```sh
vesta skills list
vesta skills doctor
vesta models recommend "fix failing tests cheaply"
vesta hub models recommend "prepare a production security release"
```

`vesta skills doctor` verifies the skill registry and every `SKILL.md` path. `vesta models recommend` classifies a task, scores available model tiers, and reports whether confirmation is required before using a cloud or stronger model.

## Installed Skill Surface

The global Codex integration writes:

```text
~/.agents/skills/vesta/SKILL.md
~/.agents/skills/vesta/registry.yaml
~/.agents/skills/vesta/<skill-id>/SKILL.md
```

This makes Vesta project-neutral. The root skill tells the AI client to use Vesta, Superpowers, local evidence, safety rules, and cost routing. The 35 child skills cover routing, model selection, project onboarding, repo context, diffs, testing, debugging, implementation, review, refactor, security, dependency updates, release checks, MCP setup, local models, and tool installation.

## Model Tiers

```text
L0 deterministic local scripts
L1 local small model or cheap local summarizer
L2 cheap cloud coding model with confirmation
L3 strong frontier model with explicit confirmation
L4 GPT-5.5 Max only after cheaper routes fail or the task is high risk
```

Routing files live in:

```text
hub/model-intelligence/task_taxonomy.yaml
hub/model-intelligence/model_scorecards.yaml
hub/model-intelligence/routing_policy.yaml
```

The first implementation is intentionally simple: keyword/task classification plus weighted scoring. Phase 2 should add prompt fixtures, real eval scores, and per-project routing overrides.

## Optional Tool Shortlist

These tools are registered but disabled by default:

- RouteLLM: routing framework for cheaper/stronger model selection. Source: https://github.com/lm-sys/RouteLLM
- LiteLLM: OpenAI-compatible provider gateway with routing, fallbacks, and budget-oriented deployment patterns. Source: https://docs.litellm.ai/
- promptfoo: prompt and model eval CLI. Source: https://www.promptfoo.dev/docs/getting-started/
- Repomix: repository-to-AI-context packer. Source: https://repomix.com/guide/
- ast-grep: structural search and rewrite. Source: https://ast-grep.github.io/
- Semgrep: static analysis and security scanning. Source: https://semgrep.dev/docs/introduction
- Aider: terminal AI pair programmer. Source: https://aider.chat/docs/
- Continue: open-source AI coding assistant for IDE, CLI, and CI. Source: https://docs.continue.dev/index
- Ollama: local model runner. Source: https://docs.ollama.com/
- LM Studio: desktop local LLM runtime and local server. Source: https://lmstudio.ai/docs
- Context7 MCP: version-specific docs through MCP. Source: https://context7.com/docs

Vesta should recommend these tools only when the task benefits from them. Tool installation, network use, cloud APIs, file edits by external agents, model downloads, and spend-bearing evals require explicit user intent.

## Next Build Steps

1. Add an `vesta models eval` command that runs cached prompt fixtures across local model providers.
2. Add per-project `model_routing.yaml` overrides in `.vestahub/`.
3. Record anonymized local routing outcomes in `.vestahub/analytics/` without secrets.
4. Add a `tool enable --profile local-models` setup path for Ollama/LM Studio detection.
5. Add installer repair checks that verify both Vesta and Superpowers skills are discoverable after restart.
