# Adding Agents

Agents live in `hub/registry/agents.yaml`. Each agent should have one bounded job, a clear stop condition, and a local-first cost policy.

Required fields:

- `id`
- `name`
- `purpose`
- `inputs`
- `outputs`
- `tools_allowed`
- `tools_forbidden`
- `cost_policy`
- `permission_policy`
- `escalation_policy`
- `default_model_tier`
- `max_context_policy`
- `run_triggers`
- `stop_conditions`

Start from `hub/templates/vesta-agent.yaml`, then validate:

```sh
vesta hub validate --registry agents
```

Good Vesta agents avoid vague authority. They gather local evidence, produce small artifacts, and stop before paid models or risky tools unless a policy explicitly allows escalation.
