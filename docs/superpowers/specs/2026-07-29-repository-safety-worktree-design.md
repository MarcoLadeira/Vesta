# Repository safety and worktree isolation design

**Issues:** #521 (parent), #536 (repository identity), #537 (worktree isolation)  
**Status:** Approved for planning

## Goal

Make every OPai repository mutation depend on one fresh, inspectable repository
identity and one evidence-backed dirty-worktree decision.  Risky, conflicting,
or concurrent work must occur through a durable, recoverable worktree lease.
The system must preserve unrelated user work even when Git state changes,
processes crash, or an external actor changes the repository.

## Scope and boundaries

This change implements #536 and #537 together as the foundation for #521.  It
will guard the existing OPai mutation surfaces: provider file patches/writes,
branch/commit/push actions, parallel-agent worktree creation, and the GUI task
pipeline that grants those actions.  GUI and CLI will render the same structured
repository-safety payload.

The change does not create a new authority model, verification verdict, or
general-purpose receipt schema.  It accepts injected authority and evidence
decisions from #526 and #522, and emits a redacted structured result that #528
can later include in canonical receipts.  Absence, ambiguity, or failure of an
upstream decision fails closed for a mutation.

## Chosen architecture

The implementation will introduce a single repository-safety service and leave
`repo_context` as a compatibility projection for existing read-only consumers.
Replacing every Git helper at once would expand the release risk; leaving the
current path/branch helper intact would not meet the stale-state, identity, or
lease requirements.

### Repository identity (#536)

`RepositoryIdentity` is captured with read-only Git and filesystem probes.  It
contains the selected path, canonical worktree root, Git directory, common Git
directory, filesystem identity when available, canonical remote map, default
branch, active branch or detached state, HEAD SHA, requested base SHA, and a
status/index fingerprint.  Remote URLs are normalized and redacted before they
enter durable state or a surface payload.

`RepositoryHandle` wraps that immutable snapshot with a schema version,
captured time, maximum permitted age (five minutes by default), requested task
and run correlation IDs, and a stable identity digest.  It is persisted through
the shared atomic, interprocess transaction utility.  A path string alone is
never treated as identity.

`revalidate_handle()` recaptures the identity immediately before each mutation.
It returns a typed decision rather than a boolean.  Deletion or replacement,
path/junction substitution, nested-repository boundary changes, worktree
relocation, changed Git/common directories, remote rewrite, branch or HEAD
movement, index/status change, expiry, and probe failure are explicit stale or
blocked reasons.  Detached HEAD is represented explicitly and cannot be used
for branch-dependent mutation until isolation has selected a branch.

Dirty state is parsed using `git status --porcelain=v2 -z` with untracked and
ignored files enabled.  The parser records staged, unstaged, untracked, ignored
and conflicted paths separately and does not rely on line splitting or display
quoting.  A classifier receives the planned scope and returns a versioned
assessment with affected paths, overlap evidence, rule identifier, confidence,
and one of these outcomes:

- `clean` / `proceed`
- `compatible` / `proceed_carefully`
- `unrelated` / `isolate`
- `overlapping` / `block`
- `unknown` or `unsafe` / `block`

Unknown scope or malformed status never authorizes a write.  The mutation gate
calls handle revalidation and classification after the caller has described the
specific planned paths, then immediately invokes the fixed operation only when
the decision permits it.

### Worktree leases (#537)

`WorktreeLease` is a versioned durable record under OPai state.  It contains a
lease ID, owner process, task/run IDs, repository identity digest, worktree
path and filesystem identity, branch, requested and resolved base SHA,
creation/heartbeat/expiry times, lifecycle state, and redacted evidence.  The
lifecycle is `planned`, `creating`, `active`, `needs_review`, `completed`,
`cleaning`, `cleanup_failed`, or `released`.

`WorktreeManager.create()` first runs the repository mutation gate, checks the
resolved base revision, branch/path collisions, configured quota and available
disk space, then writes `creating` before invoking `git worktree add`.  It
reconciles Git's worktree registry and filesystem before marking the lease
active.  Per-repository lease transactions prevent two tasks from claiming the
same branch or path.

`reconcile()` treats Git's registry and the filesystem as authoritative over a
stale lease file.  Interrupted creation, missing paths, unknown ownership,
changed base, user edits, unpushed commits, or changed branch state become
explicit recovery states.  Recovery offers resume, inspect, or cleanup; it
does not delete automatically.

`preview_apply()` compares the recorded base, source branch, and current target
using read-only Git queries.  It reports target movement and potentially
overlapping paths deterministically.  Applying changes requires a fresh target
handle, a safe preview, and an injected authority grant; otherwise it is
blocked.  `cleanup()` only removes a reconciled, OPai-owned, pristine worktree;
user-modified, unpushed, unknown, or inconsistent worktrees are preserved and
marked `needs_review`.  Branch deletion is never implicit.

### Surface and compatibility integration

`RepoContext` will remain available, populated from the canonical identity and
assessment rather than separate ad-hoc probes.  Existing GUI workspace and
task-packet paths will render the same redacted identity, handle freshness, and
safety decision exposed by a new CLI repository inspection/recovery command.
`RepositoryToolExecutor` will retain the initial task handle but must call the
gate just before file writes, patches, branch creation, commits, and pushes.
`parallel_agents` will delegate worktree creation and reconciliation to the
lease manager while preserving its assignment API for compatibility.

## Safety properties

- No mutation continues with a missing, expired, stale, ambiguous, or
  mismatched repository handle.
- Read-only probes do not modify the index, worktree, hooks, submodules, or
  generated files.
- Unknown dirty-state input and unknown worktree ownership fail closed.
- A crash may leave a recoverable lease or worktree, but cannot cause automatic
  deletion or mixing of user work.
- Persistent state is versioned, redacted, atomically written, and locked via
  the existing shared persistence primitive.

## Test and QA strategy

Focused hermetic suites will cover identity capture/revalidation, null-delimited
status parsing, classification, guard enforcement, worktree leases, and surface
parity.  The fixture matrix includes nested repositories, worktrees, symlinks
when supported, detached HEAD, no/multiple remotes, remote rewrites, path
replacement, staged/untracked/conflicted states, unusual Unicode filenames,
large dirty sets, concurrent lease claims, branch collisions, interrupted
creation/cleanup, target divergence, and preserved user-edited worktrees.

Property tests will fuzz status records and path scope overlap.  A no-write
fixture will hash the worktree/index and assert that inspection paths issue only
the declared read-only Git commands.  The final QA step runs the complete
repository test suite once, plus formatting and lint checks; it does not repeat
already-passing full-suite runs during component development.

## Migration and compatibility

Existing active-repository state and parallel-assignment records remain
readable.  Older records that lack a verifiable identity or lease ownership are
reported as degraded/needs-review, never upgraded to safe automatically.  The
new structured payload is additive for callers that still consume legacy
`path`, `branch`, `remote`, and `dirty_paths` fields.
