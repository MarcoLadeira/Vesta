# Benchmark Market Research Notes

Research date: 2026-06-20.

## Market signal

The coding-agent market is converging on expensive agent power, team admin, and
enterprise controls:

- Cursor sells individual and team plans with cloud agents, usage analytics,
  team privacy mode, SSO, access controls, and audit logs.
- Codex is bundled into ChatGPT plans and supports cloud, CLI, IDE, and API-key
  usage paths.
- Devin sells individual, team, and enterprise tiers with cloud agents, admin
  analytics, integrations, SSO, enterprise controls, and dedicated deployment.
- Claude Code cost analysis keeps pointing at the same drivers OPai targets:
  context size, model choice, and multi-agent/auto-accept multipliers.

## Benchmark signal

The credible proof stack is now layered:

- `promptfoo` is the practical CI/eval layer for comparing coding agents with
  assertions, traces, and cost checks.
- SWE-bench Verified is the public correctness scoreboard for GitHub issue
  resolution.
- SWE-bench Pro is the harder long-horizon target because it emphasizes
  realistic, cross-file software engineering tasks.
- Terminal-Bench measures long-horizon terminal autonomy with realistic tasks.
- Aider Polyglot measures cross-language file editing and test repair.

## OPai wedge

Most tools sell stronger agents. OPai should sell the control plane around all
agents:

- Reduce wasted context before a paid model sees it.
- Route deterministic/local work before cloud escalation.
- Gate risky workflows with human approval and audit evidence.
- Prove savings and safety with local, privacy-safe benchmark history.
- Export to external harnesses only when the user opts in.

The strongest next product move is a benchmark proof loop:

```sh
opai benchmark run --suite local --mode both
opai benchmark run --suite max --mode both
opai benchmark gate --min-context-reduction 10 --require-risk-blocks
opai benchmark compare --format markdown
opai benchmark export --harness promptfoo
```

This makes OPai defensible even when underlying coding agents improve, because
the product promise is not "we are the smartest agent"; it is "we make every
agent cheaper, smaller, safer, and measurable."

## Max-suite stance

The `max` suite is a local readiness suite, not a public leaderboard submission.
It deliberately maps tasks to SWE-bench Pro, Terminal-Bench, Aider Polyglot,
promptfoo, and OPai governance signals so OPai can measure the control-plane
behaviors that top coding-agent benchmarks increasingly care about: smaller
context, fewer paid calls, deterministic evidence first, bounded autonomy, and
auditable risk gates.

## Sources

- Cursor pricing: https://cursor.com/pricing
- OpenAI Codex pricing: https://developers.openai.com/codex/pricing
- Devin pricing: https://devin.ai/pricing/
- promptfoo coding-agent eval guide: https://www.promptfoo.dev/docs/guides/evaluate-coding-agents/
- SWE-bench: https://www.swebench.com/
- SWE-Bench Pro: https://labs.scale.com/leaderboard/swe_bench_pro_public
- Terminal-Bench: https://www.tbench.ai/
- Aider leaderboards: https://aider.chat/docs/leaderboards/
