Diff summary:
{{diff}}

Return a safe git summary, risky files, secret-scan requirement, commit message, and PR outline.
The current request authorizes normal branch creation, commit, push, PR creation, and a requested merge after checks pass. Do not ask again for those steps. Always require confirmation for force-push, branch or file deletion, destructive reset/clean, secret exposure, production changes, or an unrequested merge.
