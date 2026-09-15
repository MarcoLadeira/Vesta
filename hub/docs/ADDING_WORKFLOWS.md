# Adding Workflows

Workflows live in `hub/registry/workflows.yaml`. They combine agents and tools into repeatable local-first paths.

Start from `hub/templates/opai-workflow.yaml`.

Rules:

- Use deterministic checks before model reasoning.
- Keep output artifacts under `.opaihub/` or `.opcoding/`.
- Add safe command mappings in `opaihub/workflow_runner.py` only for commands that are read-only or explicitly low risk.
- Require confirmation for destructive Git, deployment, publish, or cloud actions.

Validate after editing:

```sh
vesta hub validate --registry workflows
```
