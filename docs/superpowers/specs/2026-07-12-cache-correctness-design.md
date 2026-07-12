# Cache Correctness and Bounded Reuse Design

- **Date:** 2026-07-12
- **Status:** Approved under the standing alpha-release direction to resolve
  release-critical GitHub issues without introducing paid or cloud dependencies.
- **Issue:** [#287](https://github.com/MarcoLadeira/OPai/issues/287)

## Problem

OPai's result cache currently keys an answer using Git HEAD and dirty path
names. If a dirty `app.py` changes from one set of bytes to another at the same
path, the cache key does not change. Entries also do not expire. A cheap stale
answer is worse than a fresh answer, so reuse must be denied whenever OPai
cannot prove the relevant repository inputs are unchanged.

The shared fingerprint also drives evidence-cache reuse and the GUI's
in-memory repository-work cache. Fixing result cache only would leave two
callers with the same stale-state model.

## Options considered

1. **Disable all caching for any dirty repository.** Safe but discards useful
   repeat work on normal small edits. Rejected.
2. **Add hashing only inside result cache.** Fixes one caller but duplicates
   repository-state logic and leaves evidence/UI work susceptible to the same
   stale input. Rejected.
3. **Introduce one bounded, content-aware fingerprint assessment used by all
   cache callers.** The assessment proves cacheability, carries a digest for
   safe inputs, and reports a specific bypass reason for uncertain inputs.
   Selected.

## Architecture

### Fingerprint assessment

`opaihub.evidence_cache` gains a frozen `RepoFingerprint` value with:

```python
@dataclass(frozen=True)
class RepoFingerprint:
    digest: str
    cacheable: bool
    bypass_reason: str | None
    dirty_file_count: int
    dirty_bytes: int
```

`assess_repo_fingerprint(root, limits=DEFAULT_FINGERPRINT_LIMITS)` is the only
source of cacheability. The existing `repo_fingerprint(root)` remains as a
compatibility wrapper returning `assessment.digest` so external diagnostic
callers do not break.

For a clean Git worktree, the assessment uses the current two cheap Git calls:
status and HEAD. It reads no repository files. For a dirty Git worktree, it
adds deterministic SHA-256 digests for every changed tracked file and every
non-ignored untracked file, sorted by relative POSIX path. Git status remains
in the fingerprint so deletions, renames, modes, and index state are retained.
Only path names, sizes, and content digests enter the basis; raw file content
and prompts are never stored.

The default bounded limits are:

```python
FingerprintLimits(
    max_dirty_files=64,
    max_file_bytes=256 * 1024,
    max_total_bytes=1024 * 1024,
)
```

The `limits` argument makes the guard configurable and testable without an
environment-specific policy surface.

### Safe bypass rules

The assessment returns `cacheable=False` and a non-secret reason when it sees:

- no usable Git repository;
- an ignored input outside OPai's own `.opaihub`/`.opcoding` state directories;
- more dirty files or bytes than the configured bounds;
- a file above the per-file bound;
- a binary file, symlink, directory, unreadable file, or path outside the
  project root;
- a Git command failure or unparseable status.

OPai-owned state paths are excluded so writing an OPai cache cannot invalidate
itself. All other uncertainty is a bypass, never a best-effort hit.

### Result-cache envelope

`opaihub.result_cache` advances to schema version 2. A stored entry contains:

```json
{
  "schema_version": 2,
  "key": "digest",
  "model": "local-model-id",
  "created_at": "2026-07-12T00:00:00+00:00",
  "expires_at": "2026-07-12T01:00:00+00:00",
  "answer": "local-only answer"
}
```

The default lifetime is one hour. Reads reject missing/invalid schema,
malformed timestamps, expiry, and corrupt JSON. Writes use a same-directory
temporary file followed by `os.replace`, so concurrent readers see either a
complete old entry or a complete new entry, never partial JSON.

`lookup_with_meta()` exposes `hit`, `miss`, `expired`, `schema_mismatch`,
`corrupt`, and `bypass` outcomes plus age/reason. The existing `lookup()` stays
as a compatibility wrapper returning only an entry or `None`. `store()` skips
writes on a bypass and exposes metadata to callers that need it.

### Callers and observability

- `run_ask()` uses a task-only one-way hash for its public result ID, then calls
  `lookup_with_meta()` only for read-only requests. A cache bypass still runs
  the local model; a cache hit records an avoided model call.
- `ledger.record_cache_lookup()` records local-only cache evidence: cache kind,
  outcome, safe reason, age, and `avoided_model_call`. It uses the existing
  `cache_lookup` event type and does not affect spend or savings aggregation.
- `collect_evidence_cached()` skips both reads and writes when the assessment
  is uncacheable and returns `cache_bypassed` metadata.
- `intent_router._cached_repo_work()` skips its in-memory reuse for an
  uncacheable assessment rather than indexing an uncertain state.

## Error handling and compatibility

- Cached entries from schema 1 are ignored safely; no destructive migration is
  needed.
- Existing clean Git repositories retain cache reuse and do not hash files.
- Non-Git projects remain functional but bypass reuse because marker mtimes do
  not prove every input is unchanged.
- Existing callers of `repo_fingerprint()` and `result_cache.lookup()` retain
  their return types.
- Cache status never stores a raw prompt, file content, absolute file path, or
  credential.

## Test design

Tests are written before each production change:

- changing dirty bytes at the same tracked path changes a cache key;
- changing a safe untracked file changes a cache key;
- a clean Git worktree does not read source-file bytes while assessing state;
- each bounded uncertainty returns an explicit bypass and cannot read or write
  a result/evidence/UI-work cache;
- a result entry expires at the documented lifetime and invalid schema/corrupt
  data never returns an answer;
- atomic writer/read stress returns only complete JSON entries;
- a cache hit records one local `cache_lookup` event with avoided-call evidence;
- a miss/bypass records its outcome without creating false spend or savings;
- existing Ask/Plan, evidence-cache, GUI intent, and ledger contracts remain
  compatible.

## Acceptance criteria

- A different dirty-file byte sequence at the same path cannot reuse a result.
- Safe untracked inputs participate in the fingerprint.
- Oversized, binary, ignored, unreadable, unsafe, and limit-exceeding inputs
  bypass cache reuse explicitly.
- Result cache has a documented one-hour expiry and schema version.
- Clean Git repository fingerprint performance remains within the existing
  Git-status/HEAD fast path and has no source-file reads.
- Cache outcome evidence is local, privacy-safe, and separate from spend.
- All targeted tests, the canonical suite, static checks, and registry/CLI
  smoke checks pass.

## Rollback

The implementation is split into fingerprint, result-envelope, and caller
integration commits. No cache data is deleted or migrated; reverting any commit
only causes old entries to be ignored or a cache miss, never a corrupted answer
or paid call.
