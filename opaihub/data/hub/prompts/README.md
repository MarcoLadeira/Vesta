# OP AI Hub Prompt Library

Prompts are short, reusable, and designed to reduce tokens. Each prompt should:

- State the task and available local evidence.
- Ask for the smallest useful output.
- Prefer local/deterministic commands before AI reasoning.
- Avoid full-file rewrites unless explicitly needed.
- Include verification commands.
- Refuse to proceed with secrets or destructive actions.

Add new prompts as small Markdown files with placeholders like `{{task}}`, `{{context}}`, and `{{budget}}`.
