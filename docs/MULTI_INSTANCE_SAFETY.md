# Multi-instance safety

OPai supports more than one desktop window, including windows for different
projects. Shared local state is deliberately protected rather than relying on
one GUI process: ledger updates and workflow state use interprocess
transactions, GUI preferences use a locked read-modify-write transaction, and
the global integration manifest is atomically replaced while preserving already
registered targets. Project workflow state remains scoped to that project.

This means opening a second OPai window does not create partially written JSON
or discard another window's preference, usage-limit, consent, or integration
target update. The newest global activation remains the displayed project root;
the manifest target set is cumulative.
