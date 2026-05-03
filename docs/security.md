# Security And Permissions

## Defaults

- File access is project-root scoped.
- Secret-like values are redacted in command logs.
- GitHub and external APIs are disabled by default.
- Shell commands should be allowlisted by workflow.
- Destructive git, deploy, publish, and production actions require explicit confirmation.

## GitOps Safety

The GitOps helpers can summarize, scan, suggest branch names, suggest commit messages, and draft PR text. They do not push, merge, rebase, delete branches, publish packages, or deploy.

## Logs

Logs are stored under `.opcoding/logs/` inside each project. They are intended for local debugging and should not be committed. Prompt text should be stored as metadata only once model adapters are added.
