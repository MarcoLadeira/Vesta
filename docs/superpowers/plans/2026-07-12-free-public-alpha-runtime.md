# Free Public Alpha Runtime Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove executable paid-edition gates so every implemented OPai alpha capability is available for free, while retaining truthful diagnostics for planned work.

**Architecture:** Retain the existing edition module as a compatibility-facing availability API, but make the active launch state permanently `free`. Replace tier metadata with a one-entry Free Public Alpha catalog whose capability entries distinguish implemented from planned work. Remove the only CLI feature gate directly from savings export.

**Tech Stack:** Python 3.10+, unittest/pytest, YAML registries, argparse CLI, Ruff, package-data parity tests.

## Global Constraints

- The public alpha has no payment, licence, entitlement, invitation, or checkout gate.
- A planned capability may be reported as not implemented; it must never receive an upgrade hint.
- Do not add a cloud call, telemetry, or destructive migration.
- Keep the three checked-in catalog copies byte-equivalent after their path-specific headers, and keep the packaged data authoritative at runtime.
- Use test-first red/green cycles for all behavior changes.

---

### Task 1: Prove the current paid gate is a release contradiction

**Files:**
- Modify: `tests/test_editions.py`
- Modify: `tests/test_positioning_and_cli.py`

**Interfaces:**
- Consumes: `opaihub.editions.require_feature`, `opaihub.editions.set_edition`, and `opai.cli.main`.
- Produces: regression coverage for no-gate export and free-alpha availability.

- [ ] **Step 1: Replace tier-priced assertions with free-alpha contract tests**

  In `tests/test_editions.py`, replace the paid-tier tests with tests that
  require all implemented catalog features to be available, that require
  `require_feature(root, "savings_export")` to have `available is True` with
  no `upgrade_hint`, and that require a legacy `set_edition(root, "pro")` to
  report `status == "free_alpha"` while `current_edition(root) == "free"`.

- [ ] **Step 2: Add a CLI export regression test**

  In `tests/test_positioning_and_cli.py`, add a temporary-project test which
  calls:

  ```python
  code = main(["--project", str(root), "savings", "--export", str(target)])
  self.assertEqual(code, 0)
  self.assertTrue(target.exists())
  self.assertIn("opai-savings", target.read_text(encoding="utf-8"))
  ```

- [ ] **Step 3: Run the selected tests and verify the expected failures**

  Run:

  ```powershell
  python -m pytest tests/test_editions.py tests/test_positioning_and_cli.py -q
  ```

  Expected: the export test fails with `upgrade_required`, and paid-tier
  assertions fail after the desired free-alpha assertions replace them.

### Task 2: Convert the runtime policy from commercial tiers to availability

**Files:**
- Modify: `opaihub/editions.py`
- Modify: `opai/cli.py`
- Test: `tests/test_editions.py`
- Test: `tests/test_positioning_and_cli.py`

**Interfaces:**
- Consumes: the catalog returned by `load_editions(project_root)`.
- Produces: `current_edition(root) == "free"`; `require_feature` returns
  `{available: bool, availability: "free_alpha" | "not_implemented"}` and
  never returns `upgrade_hint`.

- [ ] **Step 1: Make the active launch state permanently free**

  In `opaihub/editions.py`, introduce `FREE_ALPHA_EDITION = "free"` and make
  `current_edition()` return that constant without reading `OPAI_EDITION` or
  project state. Keep legacy tier names only for a compatibility message.

- [ ] **Step 2: Make capability status implementation-based**

  Resolve a catalog feature by ID. Return `True` for unknown IDs and for
  entries whose `availability` is `implemented`; return `False` only when the
  entry explicitly states `planned`. Make `require_feature()` return
  `availability: "free_alpha"` for the former and `status:
  "not_implemented"` for the latter, with a safe implementation explanation
  and no price or upgrade text.

- [ ] **Step 3: Turn legacy tier selection into a harmless no-op**

  Make `set_edition()` return:

  ```python
  {
      "status": "free_alpha",
      "edition": "free",
      "requested_edition": str(name).lower(),
      "reason": "OPai public alpha has no paid editions or feature gates.",
  }
  ```

  Do not load or save project state. In `cmd_edition`, treat `free_alpha` as a
  successful exit without recording an edition-change audit event.

- [ ] **Step 4: Remove the actual savings-export gate**

  Delete the `require_feature(root, "savings_export")` branch from
  `cmd_savings`; write the local Markdown report immediately when `--export`
  is supplied. Change the option help from `Pro edition feature` to `Write a
  shareable local savings report`.

- [ ] **Step 5: Run the selected tests and verify them green**

  Run:

  ```powershell
  python -m pytest tests/test_editions.py tests/test_positioning_and_cli.py -q
  ```

  Expected: all selected tests pass and the export file is written in a default
  project without a tier selection.

### Task 3: Replace catalog and public copy without claiming planned work

**Files:**
- Modify: `opaihub/data/hub/editions.yaml`
- Modify: `hub/editions.yaml`
- Modify: `configs/editions.yaml`
- Modify: `hub/docs/PRICING_AND_EDITIONS.md`
- Modify: `opaihub/data/hub/docs/PRICING_AND_EDITIONS.md`
- Modify: `hub/docs/GOVERNANCE.md`
- Modify: `opaihub/data/hub/docs/GOVERNANCE.md`
- Modify: `README.md`
- Test: `tests/test_editions.py`

**Interfaces:**
- Consumes: the YAML catalog through `load_editions`.
- Produces: a one-tier `$0` Free Public Alpha catalog with implemented/planned
  availability and no `min_edition`, price ladder, or upgrade copy.

- [ ] **Step 1: Replace every catalog with the free-alpha schema**

  Give each catalog `schema_version: 2`, `launch: free-public-alpha`, one
  `free` entry with `price: 0`, and capability entries using
  `availability: implemented` or `availability: planned`. Do not include
  `min_edition`, `pro`, `team`, `enterprise`, or a nonzero price.

- [ ] **Step 2: Update stable documentation paths**

  Rework `PRICING_AND_EDITIONS.md` as a concise Free Public Alpha policy at
  the same path so legacy links resolve. Rework governance's tier table as
  free-alpha coverage, and change README's active editions/pricing link and
  command comment to availability language.

- [ ] **Step 3: Add configuration and copy assertions**

  In `tests/test_editions.py`, read all three catalog paths and assert each has
  `launch == "free-public-alpha"`, exactly the `free` edition, a zero price,
  no `min_edition` feature field, and no `upgrade_hint` from a supported
  feature. Add a public-copy test that fails if active `Pro / Team /
  Enterprise` selection language remains in README or the packaged pricing
  document.

- [ ] **Step 4: Run catalog, CLI, and registry verification**

  Run:

  ```powershell
  python -m pytest tests/test_editions.py tests/test_positioning_and_cli.py -q
  python -m opaihub validate
  git diff --check
  ```

  Expected: all commands exit 0; catalog data is parseable and public alpha
  copy contains no active paid-tier gate.

### Task 4: Broaden verification and commit the free-runtime boundary

**Files:**
- Verify: all files changed in Tasks 1–3

- [ ] **Step 1: Run behavior and package-data suites**

  Run:

  ```powershell
  python -m pytest tests/test_editions.py tests/test_positioning_and_cli.py tests/test_publish_readiness.py tests/test_governance.py tests/test_savings_honesty.py -q
  ```

- [ ] **Step 2: Run formatting, lint, and static validation**

  Run:

  ```powershell
  python -B -m ruff format --check .
  python -B -m ruff check --no-cache .
  python -B -m opaihub validate
  git diff --check
  ```

- [ ] **Step 3: Commit the isolated change**

  Run:

  ```powershell
  git add opaihub/editions.py opai/cli.py opaihub/data/hub/editions.yaml hub/editions.yaml configs/editions.yaml hub/docs/PRICING_AND_EDITIONS.md opaihub/data/hub/docs/PRICING_AND_EDITIONS.md hub/docs/GOVERNANCE.md opaihub/data/hub/docs/GOVERNANCE.md README.md tests/test_editions.py tests/test_positioning_and_cli.py
  git commit -m "fix(alpha): remove paid feature gates"
  ```

  Expected: the commit contains only the free public-alpha runtime boundary
  and its regression tests.
