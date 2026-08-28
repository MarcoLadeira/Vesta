"""OPai desktop app — web-rendered UI (Chromium via QtWebEngine).

The window is a single ``QWebEngineView`` rendering a hand-built HTML/CSS/JS
front-end (``opai/assets/web/``). All of OPai's logic stays in Python and is
exposed to the page over a thin ``QWebChannel`` bridge — the front-end never
computes anything sensitive, it just renders JSON the bridge hands it.

Why web rendering: Qt's QSS/text engine can't match the polish of Cursor/Claude
(no real font smoothing, weak shadows/blur, no transitions). Chromium gives real
CSS, ``@font-face`` Inter with antialiasing, depth, and animation — at zero new
dependency (QtWebEngine ships with PySide6 here). ``gui_desktop.py`` (the Qt
version) remains as a fallback when QtWebEngine isn't available.

The bridge reuses the exact same Qt-free data modules the Qt UI used
(``gui_nav``, ``gui_modes``, ``gui_permissions``, ``gui_prompts``,
``gui_workspace``, ``gui_view_model``, ``gui_controls``), so there is one source
of truth for both surfaces.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from opai import app_state as A
from opai.asset_identity import asset_manifest
from opai.activity_batch import FLUSH_INTERVAL_MS, ActivityBatcher
from opai.gui_controls import (
    header_status,
    live_agent_mode_row,
    model_badge,
    session_inspector,
)
from opai.gui_lifecycle import drain_workers, signal_cancels, start_tracked_worker
from opai.gui_modes import (
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_TASK_MODE,
    describe_controls,
    output_format,
    output_formats,
    task_modes,
    task_summary,
)
from opai.gui_nav import DEFAULT_VIEW, group_collapsed, nav_groups
from opai.gui_permissions import permission_summary, permissions_for
from opai.gui_prompts import categories_present, filter_prompts, find_prompt
from opai.gui_view_model import build_view_model
from opai.gui_workspace import (
    add_recent_workspace,
    is_valid_workspace,
    load_recent_workspaces,
    resolve_gui_workspace,
    workspace_label,
)


_LOG = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent / "assets" / "web"

# Name of the generated, cache-busted copy of index.html the GUI actually loads.
# Kept next to index.html so every relative asset path, the CSP, and the qrc
# web-channel script resolve identically to index.html itself.
_RUNTIME_INDEX = ".runtime-index.html"

_ASSET_REF = re.compile(r'(href|src)="([^"]+)"')


def asset_build_identity(asset_dir: Path = WEB_DIR) -> dict[str, Any]:
    """Identify the exact UI assets served by this Python host.

    The package version alone cannot distinguish a stale installed GUI from a
    source checkout carrying newer web assets. Hashing the production asset set
    gives support, QA, and users a stable value they can compare directly.
    """

    root = asset_dir.expanduser().resolve()
    if root == WEB_DIR.resolve():
        manifest = asset_manifest(root.parent)
        source_root = Path(__file__).resolve().parents[1]
        return {
            "applicationVersion": manifest["application_version"],
            "assetFingerprint": manifest["fingerprint_sha256"],
            "assetCount": manifest["asset_count"],
            "schemaVersion": manifest["schema_version"],
            "runtimeSource": (
                "source_checkout"
                if (source_root / ".git").exists()
                else "installed_package"
            ),
        }
    candidates = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and "__tests__" not in path.relative_to(root).parts
            and path.name != _RUNTIME_INDEX
            and path.suffix.lower() in {".css", ".html", ".js", ".svg"}
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    digest = hashlib.sha256()
    for path in candidates:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    source_root = Path(__file__).resolve().parents[1]
    return {
        "assetFingerprint": digest.hexdigest(),
        "assetCount": len(candidates),
        "runtimeSource": (
            "source_checkout"
            if (source_root / ".git").exists()
            else "installed_package"
        ),
    }


def _runtime_index_url(web_dir: Path) -> "Any":
    """Write a per-launch, cache-busted copy of index.html and return its file
    URL. QtWebEngine's resource cache can serve a stale styles.css/app.js/icon
    across restarts even after the file on disk changes; appending a fresh
    ``?v=<launch>`` to every local sub-resource makes each launch request a URL
    the cache has never seen, so the current on-disk assets always render.

    Falls back to plain index.html if the package dir is not writable (e.g. a
    read-only wheel install) — behaviour then matches the pre-change loader.

    Raises ``FileNotFoundError`` when the packaged asset itself is missing
    (#365): a corrupt/partial install must not open a silent blank window,
    which is what happened when the cache-busting write's own ``OSError``
    handler also swallowed "the source file was never there."
    """
    from PySide6.QtCore import QUrl  # local import: Qt only present in the GUI

    index = web_dir / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            f"packaged web asset missing: {index} "
            "(reinstall OPai or rebuild the web bundle)"
        )
    try:
        ver = str(int(time.time() * 1000))

        def _bust(m: "re.Match[str]") -> str:
            attr, url = m.group(1), m.group(2)
            if url.startswith(("http:", "https:", "qrc:", "data:", "//", "#")):
                return m.group(0)
            sep = "&" if "?" in url else "?"
            return f'{attr}="{url}{sep}v={ver}"'

        html = _ASSET_REF.sub(_bust, index.read_text(encoding="utf-8"))
        out = web_dir / _RUNTIME_INDEX
        out.write_text(html, encoding="utf-8")
        return QUrl.fromLocalFile(str(out))
    except OSError:
        return QUrl.fromLocalFile(str(index))


# Startup instrumentation (#246). t0 is import time — the closest proxy to GUI
# process start. Off unless OPAI_STARTUP_TRACE is set, in which case boot marks
# its stages and the front-end flushes an "interactive" mark after first paint.
from opaihub.startup_trace import StartupTrace, trace_enabled, trace_path  # noqa: E402
from opaihub.boundary_errors import safe_detail  # noqa: E402

_STARTUP = StartupTrace(enabled=trace_enabled())


class _SessionPersistenceEpoch:
    """Serialize session clears with late worker persistence callbacks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._value = 0

    def capture(self) -> int:
        with self._lock:
            return self._value

    def run_if_current(self, token: int, action: Any) -> bool:
        with self._lock:
            if token != self._value:
                return False
            action()
            return True

    def invalidate_on_success(self, action: Any) -> dict[str, Any]:
        with self._lock:
            result = action()
            if result.get("ok"):
                self._value += 1
            return result

    def invalidate(self) -> None:
        with self._lock:
            self._value += 1


def web_available() -> bool:
    """True when QtWebEngine is importable (it ships with PySide6 here).

    ``find_spec`` raises ``ModuleNotFoundError`` for a submodule when the parent
    package (PySide6) is absent — e.g. on CI without the desktop extra — so we
    treat any import failure as "not available".
    """
    try:
        return (
            importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
            and importlib.util.find_spec("PySide6.QtWebChannel") is not None
        )
    except (ModuleNotFoundError, ValueError):
        return False


def resolve_openable(root: Path, target: str) -> Path | None:
    """Resolve ``target`` to an absolute path that is safe to open in the OS.

    Allows the workspace root itself or any existing path under it (a changed
    file, a subfolder). Anything outside the project, or that doesn't exist,
    returns ``None`` — so the front-end can never ask the OS to open an
    arbitrary path.
    """
    try:
        base = root.expanduser().resolve()
        raw = Path(target or "")
        candidate = (raw if raw.is_absolute() else base / raw).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not candidate.exists():
        return None
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def context_picker_payload(root: Path, selected: list[str]) -> dict[str, Any]:
    """Return safe, workspace-relative context paths from a native picker.

    The browser must never receive an arbitrary absolute path selected by the
    host.  Existing files and folders inside the active workspace are reduced
    to portable relative paths; everything else is counted as rejected.
    """
    try:
        workspace = root.expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return {"paths": [], "rejected": len(selected)}

    paths: list[str] = []
    rejected = 0
    for raw in selected:
        try:
            target = Path(raw).expanduser().resolve()
            relative = target.relative_to(workspace)
        except (OSError, ValueError, RuntimeError):
            rejected += 1
            continue
        if not target.exists() or relative == Path("."):
            rejected += 1
            continue
        value = relative.as_posix()
        if target.is_dir():
            value = value.rstrip("/") + "/"
        if value not in paths:
            paths.append(value)
    return {"paths": paths, "rejected": rejected}


# --------------------------------------------------------------------------- #
# Overview cache: the status hot path must not rescan an unchanged repo (#146)
# --------------------------------------------------------------------------- #
# statusLine() runs after every message and calls overview(), which rebuilds the
# cockpit (integration walk + local-model discovery), re-summarizes the audit
# log, and re-reads the ledger. On a long-lived ledger that is a per-message
# freeze. We memoize the whole composition on a cheap state-file fingerprint:
# unchanged state serves the cache (coalescing repeated refreshes), a real
# ledger/audit/budget change recomputes exactly once (the ledger's own mtime is
# the signal a completed turn changed things), and a short TTL ceiling self-heals
# against inputs we don't fingerprint.
_OVERVIEW_CACHE: dict[str, tuple[tuple, float, dict[str, Any]]] = {}
_OVERVIEW_CACHE_LOCK = threading.Lock()
_OVERVIEW_TTL_S = 2.0


def _state_fingerprint(root: Path) -> tuple:
    """(path, size, mtime_ns) for the files overview() actually reads.

    Cheap: three stat() calls, no directory walk. A missing file contributes a
    stable sentinel so its later creation still changes the fingerprint.
    """
    from opaihub.state import state_dir

    # Each public path helper validates and resolves ``.opaihub`` independently.
    # That is appropriate at write boundaries, but this hot path owns all three
    # fixed children and can perform the same safety check once. On Windows,
    # avoiding six repeated ``_getfinalpathname`` calls is materially faster.
    state = state_dir(root)
    parts: list[tuple] = []
    for path in (
        state / "ledger" / "usage.jsonl",
        state / "audit" / "audit.jsonl",
        state / "budget.json",
    ):
        try:
            stat = path.stat()
            parts.append((str(path), int(stat.st_size), int(stat.st_mtime_ns)))
        except OSError:
            parts.append((str(path), -1, -1))
    return tuple(parts)


def cached_overview(
    root: Path, *, now: Any = None, ttl: float = _OVERVIEW_TTL_S
) -> dict[str, Any]:
    """``app_state.overview(root)`` memoized on the state-file fingerprint (#146)."""
    clock = (now or time.monotonic)()
    key = str(root)
    fingerprint = _state_fingerprint(root)
    with _OVERVIEW_CACHE_LOCK:
        cached = _OVERVIEW_CACHE.get(key)
        if cached is not None:
            cached_fp, cached_at, value = cached
            if ttl > 0 and cached_fp == fingerprint and (clock - cached_at) < ttl:
                return value
    value = A.overview(root)  # heavy: cockpit + audit + ledger
    with _OVERVIEW_CACHE_LOCK:
        _OVERVIEW_CACHE[key] = (fingerprint, clock, value)
    return value


def clear_overview_cache() -> None:
    """Drop every memoized overview (test isolation / explicit refresh)."""
    with _OVERVIEW_CACHE_LOCK:
        _OVERVIEW_CACHE.clear()


# --------------------------------------------------------------------------- #
# Bridge payload builders (pure-ish; reuse the Qt-free data layer)
# --------------------------------------------------------------------------- #
def _models(root: Path, *, discover_local: bool = True) -> dict[str, Any]:
    data = A.available_models(root, discover_local=discover_local)
    models = []
    for opt in data["models"]:
        models.append({**opt, "badge": model_badge(opt)})
    return {
        "models": models,
        "accounts": data.get("accounts", []),
        "connections": data.get("connections", []),
        **{
            key: data[key]
            for key in (
                "providerCatalogVersion",
                "providerProtocolVersion",
                "providerContracts",
            )
            if key in data
        },
    }


def _status(root: Path, model_label: str, mode_label: str) -> dict[str, Any]:
    try:
        o = cached_overview(root)  # #146: no full-ledger re-read per message
        ins = A.inspector_state(root, mode="safe-auto")
        spent = ins["budget"]["spent_today"]
        saved = o["savings"]["estimated_savings_usd"]
        on = bool(o.get("on"))
    except Exception:  # noqa: BLE001
        spent, saved, on = 0.0, 0.0, False
    return {
        "on": on,
        "line": header_status(model_label, mode_label, spent, saved=saved),
        "spent": spent,
        "saved": saved,
    }


def _workspace(root: Path) -> dict[str, Any]:
    from opaihub.repo_context import repository_safety_surface, save_active_repo

    context, repository_safety, worktree_leases = repository_safety_surface(root)
    save_active_repo(root, context)
    try:
        ws = A.workspace_summary(context.path)
    except Exception:  # noqa: BLE001
        ws = {"name": root.name, "branch": "", "file_count": 0}
    from opaihub.build_loop import load_app_manifest

    build_manifest = load_app_manifest(root)
    return {
        "label": workspace_label(root),
        "name": ws["name"],
        "root": str(root.expanduser().resolve()),
        "repo_root": str(context.path),
        "branch": context.branch or ws.get("branch", ""),
        "remote": context.remote,
        "dirty": bool(context.dirty_paths),
        "dirty_paths": list(context.dirty_paths),
        "repository_safety": repository_safety,
        "worktree_leases": worktree_leases,
        "file_count": ws.get("file_count", 0),
        # OPai Build (#276): when the workspace is a scaffolded app, the GUI
        # offers Build mode — chat edits it with cheap, verified targeted diffs.
        "build_app": build_manifest is not None,
        "build_app_name": (build_manifest or {}).get("name")
        if build_manifest
        else None,
        "recents": [
            {"path": p, "label": workspace_label(p)}
            for p in load_recent_workspaces()
            if p != str(root)
        ],
    }


def _workspace_refresh(root: Path) -> dict[str, Any]:
    """Refresh only live badge facts after a turn, using one Git status probe.

    The full boot payload retains the canonical repository-safety snapshot.
    Post-turn refreshes are passive display updates, so rebuilding content
    fingerprints, remotes, and receipts here only duplicated mutation-safety
    work across roughly a dozen Git subprocesses.
    """

    from opaihub.repo_context import load_active_repo
    from opaihub.repository_safety import (
        RepositoryProbeError,
        capture_workspace_status,
    )

    selected = root.expanduser().resolve()
    context = load_active_repo(selected)
    if context is None or not context.is_git:
        return _workspace(selected)
    try:
        branch, dirty_state = capture_workspace_status(context.path)
    except RepositoryProbeError:
        return _workspace(selected)
    try:
        summary = A.workspace_summary(context.path)
    except Exception:  # noqa: BLE001 - preserve the last rendered metadata
        summary = {}
    return {
        "root": str(selected),
        "repo_root": str(context.path),
        "name": summary.get("name", context.path.name),
        "branch": branch,
        "dirty": bool(dirty_state.changed_paths),
        "dirty_paths": list(dirty_state.changed_paths),
        **({"file_count": summary["file_count"]} if "file_count" in summary else {}),
    }


def _github_row_value(readiness: dict[str, Any]) -> str:
    """A concise, honest push-readiness line for the inspector (#300)."""
    if readiness.get("ready"):
        return "Ready to push & open PRs"
    connect_message = "Connect a token in Settings"
    return {
        "no_token": connect_message,
        "consent_off": "Enable pushes in Settings",
        "no_token_and_consent_off": connect_message,
    }.get(str(readiness.get("reason") or ""), "Not ready to push")


def _inspector(root: Path, sel: dict[str, Any]) -> dict[str, Any]:
    from opaihub.workflow_state import load_workflow_state

    model_label = (
        sel.get("model_advanced_label") or sel.get("model_label") or "Automatic routing"
    )
    model_kind = sel.get("model_kind") or "auto"
    run_mode = sel.get("mode") or "safe-auto"
    run_mode_label = sel.get("mode_label") or "Safe Auto"
    focus = sel.get("focus") or DEFAULT_TASK_MODE
    fmt = sel.get("format") or DEFAULT_OUTPUT_FORMAT
    try:
        ins = A.inspector_state(root, mode=run_mode)
    except Exception:  # noqa: BLE001
        ins = {}
    accounts = sel.get("accounts") or []
    connected = any(a.get("connected") for a in accounts)
    data = session_inspector(
        model_label=model_label,
        model_kind=model_kind,
        run_mode_label=run_mode_label,
        task_summary=task_summary(focus, fmt),
        inspector=ins,
        permission_summary=permission_summary(run_mode),
        connected=connected,
    )
    prefs = None
    try:
        from opaihub.gui_preferences import load_gui_preferences

        prefs = load_gui_preferences(root)
    except Exception:  # noqa: BLE001
        prefs = {}
    data["permissions"] = permissions_for(
        run_mode, safe_auto=(prefs or {}).get("safe_auto")
    )
    workflow = load_workflow_state(root)
    # F21: the persisted "Agent mode" row is the *last completed* run and goes
    # stale; the live preview (F20 single source of truth) shows what the next
    # run would do with the current run mode + focus. They are separate fields
    # on purpose — one is history, one is a preview.
    controls = describe_controls(run_mode, focus)
    rows_to_add: list[dict[str, Any]] = [
        {"label": "Agent mode", "value": workflow.mode.title()},
        live_agent_mode_row(run_mode, focus),
        {"label": "Workflow", "value": workflow.phase.replace("_", " ").title()},
        {"label": "Tests", "value": workflow.tests_status.replace("_", " ").title()},
    ]
    data["agent_mode_preview"] = controls["agent_mode_preview"]
    data["controls"] = controls
    # Push readiness matters only when this run could actually push (#300): in an
    # edit-capable mode, tell the user up front whether a PR is even possible.
    if run_mode in {"safe-auto", "full-auto"}:
        try:
            from opaihub.github_connector import github_readiness

            rows_to_add.append(
                {"label": "GitHub", "value": _github_row_value(github_readiness())}
            )
        except Exception:  # noqa: BLE001 - readiness must never break the inspector
            rows_to_add.append({"label": "GitHub", "value": "Readiness unavailable"})
    rows_to_add.append(
        {
            "label": "PR / merge",
            "value": workflow.pr_url or workflow.merge_status.replace("_", " ").title(),
        }
    )
    data.setdefault("rows", []).extend(rows_to_add)
    if workflow.blocker:
        data["rows"].append({"label": "Blocker", "value": workflow.blocker})
    review_summary = workflow.diff_review.get("summary", {})
    if review_summary:
        data["rows"].append(
            {
                "label": "Diff review",
                "value": (
                    f"{review_summary.get('approved', 0)} approved · "
                    f"{review_summary.get('pending', 0)} pending · "
                    f"{review_summary.get('rejected', 0)} rejected"
                ),
            }
        )
    if workflow.next_actions:
        data["rows"].append({"label": "Next action", "value": workflow.next_actions[0]})
    return data


def _resume_payload(root: Path) -> dict[str, Any]:
    """Build a whitelisted crash-safe resume offer for one workspace."""

    from opai.gui_recents import load_thread
    from opaihub.owner_lease import describe as describe_lease
    from opaihub.checkpoints import list_run_checkpoints
    from opaihub.workflow_state import load_workflow_state

    # Boot is deliberately read-only. A pending checkpoint can still belong to
    # another live OPai window or CLI run; without an owner lease, process death
    # cannot be inferred safely. Preserve it verbatim and let the user make the
    # explicit resume/start-fresh choice.
    thread = load_thread(root)
    if not thread:
        return {
            "available": False,
            "requires_choice": False,
            "thread": {},
            "workflow": {},
            "checkpoint": {},
        }

    workflow = load_workflow_state(root)
    task_id = str(thread.get("task_id") or workflow.task_id or "")
    linked_ids = {
        value
        for value in (
            str(thread.get("checkpoint_id") or ""),
            workflow.checkpoint_id,
        )
        if value
    }
    checkpoints = [
        checkpoint
        for checkpoint in list_run_checkpoints(root)
        if (task_id and checkpoint.task_id == task_id)
        or checkpoint.checkpoint_id in linked_ids
    ]
    by_id = {item.checkpoint_id: item for item in checkpoints}
    thread_checkpoint_id = str(thread.get("checkpoint_id") or "")
    if str(thread.get("state") or "") in {"running", "interrupted"}:
        # begin_thread_turn is persisted before the pipeline creates its new
        # checkpoint. If the process dies in that window, the thread still
        # links the prior completed turn while workflow state links the newer
        # pending run. Prefer that newer execution evidence without mutating it.
        preferred_ids = (workflow.checkpoint_id, thread_checkpoint_id)
    else:
        preferred_ids = (thread_checkpoint_id, workflow.checkpoint_id)
    checkpoint = next(
        (by_id[item] for item in preferred_ids if item and item in by_id),
        checkpoints[-1] if checkpoints else None,
    )
    checkpoint_payload = (
        {
            "id": checkpoint.checkpoint_id,
            "completion_state": checkpoint.completion_state,
            "mode": checkpoint.mode,
            "model": checkpoint.model,
            "changed_files": list(checkpoint.result_changed_files),
            "changed_during_run": list(checkpoint.changed_during_run),
            "recovery_actions": list(checkpoint.recovery_actions),
            "created_at": checkpoint.created_at,
            "finalized_at": checkpoint.finalized_at,
        }
        if checkpoint is not None
        else {}
    )
    return {
        "available": True,
        "requires_choice": True,
        "thread": thread,
        "workflow": workflow.to_dict(),
        "checkpoint": checkpoint_payload,
        # #295 invariant 4: whether the process that owned this run is
        # still alive. Boot stays read-only — this reports what the lease
        # says and mutates nothing, so the resume/start-fresh choice is
        # still the user's. It just stops OPai having to present a run
        # abandoned days ago and one a sibling window is actively working
        # on as the same indistinguishable thing.
        "owner": describe_lease(thread.get("lease")),
    }


def boot_payload(root: Path, *, initial_task: str | None = None) -> dict[str, Any]:
    """Everything the front-end needs to render the whole shell in one call."""
    from opaihub.gui_preferences import MODES, load_gui_preferences
    from opaihub.workflow_state import load_workflow_state

    from opaihub.autonomy import MODE_LABELS, resolve_startup_mode

    _STARTUP.mark("boot:start")
    root = root.expanduser().resolve()
    prefs = load_gui_preferences(root)
    # Central autonomy decision (#137): boot into the effective mode, which is
    # Full Auto only when it is explicitly pinned.
    autonomy = resolve_startup_mode(prefs)
    mode = autonomy.effective_mode
    focus = str(prefs.get("default_task_mode") or DEFAULT_TASK_MODE)
    fmt = str(prefs.get("default_output_format") or DEFAULT_OUTPUT_FORMAT)
    _STARTUP.mark("boot:prefs")
    # _workspace() (git probe) and _models() (account/keyring lookups) only
    # need `root` — not each other's output — so they run side by side. Each
    # is a few hundred ms of subprocess/keyring latency; run in series they
    # used to stack into a boot the user felt as a stall.
    with ThreadPoolExecutor(max_workers=2) as boot_pool:
        workspace_future = boot_pool.submit(_workspace, root)
        models = _models(root, discover_local=False)
        _STARTUP.mark("boot:models")
        workspace_payload = workspace_future.result()
    mode_labels = MODE_LABELS
    sel_model = next(
        (m for m in models["models"] if m["id"] == prefs.get("default_model")),
        models["models"][0]
        if models["models"]
        else {"id": "auto", "label": "OPai · Auto mode", "kind": "auto"},
    )
    sel = {
        "model_label": sel_model.get("label", "OPai · Auto mode"),
        "model_advanced_label": sel_model.get(
            "advanced_label", sel_model.get("label", "Automatic routing")
        ),
        "model_kind": sel_model.get("kind", "auto"),
        "mode": mode,
        "mode_label": mode_labels.get(mode, mode),
        "focus": focus,
        "format": fmt,
        "accounts": models["accounts"],
    }
    workflow = load_workflow_state(root)
    _STARTUP.mark("boot:workflow")
    payload = {
        "workspace": workspace_payload,
        "workflow": workflow.to_dict(),
        "resume": _resume_payload(root),
        "models": models["models"],
        "selectedModel": sel_model.get("id", "auto"),
        "modes": [{"id": item, "label": mode_labels.get(item, item)} for item in MODES],
        "navGroups": [
            {"group": group, "items": items, "collapsed": group_collapsed(group)}
            for group, items in nav_groups()
        ],
        "taskModes": task_modes(),
        "outputFormats": output_formats(),
        "prefs": {
            "model": prefs.get("default_model", "auto"),
            "mode": mode,
            "focus": focus,
            "format": fmt,
            "showPanel": bool(prefs.get("show_control_panel", True)),
            # Appearance (#241): applied to the document root at boot.
            "density": str(prefs.get("density") or "comfortable"),
            "reducedMotion": str(prefs.get("reduced_motion") or "system"),
            # Activity copy: lets the activity rail be drag-selected/copied for
            # debugging. On by default; applied to the document root at boot.
            "activityCopy": str(prefs.get("activity_copy") or "on"),
            # Composer layout (Composer Redesign): one of the three directions
            # — "toolbar" (quiet toolbar, default), "single" (single line),
            # "command" (command bar). Applied to the composer at boot and live.
            "composerStyle": str(prefs.get("composer_style") or "toolbar"),
            # First-run onboarding (#250): show the tour until it is seen.
            "onboardingSeen": bool(prefs.get("onboarding_seen", False)),
            # Full Auto pin state (#137) so the UI can show danger styling and
            # an unpin action, and never silently present unpinned Full Auto.
            "fullAutoPinned": autonomy.full_auto_pinned,
            # Free-model ids the user already consented to (asked once, never
            # again). Front-end skips the consent card for anything in this list.
            "freeConsent": list(prefs.get("free_consent") or []),
        },
        "autonomy": autonomy.to_dict(),
        # F20/F21: one live, shared reading of Run mode + Task focus + the
        # agent mode the next run would fall back to (describe_controls is the
        # single source of truth; the classic GUI reads the same helper).
        "controls": describe_controls(mode, focus),
        "accounts": models["accounts"],
        "connections": models["connections"],
        **{
            key: models[key]
            for key in (
                "providerCatalogVersion",
                "providerProtocolVersion",
                "providerContracts",
            )
            if key in models
        },
        "status": _status(root, sel["model_label"], sel["mode_label"]),
        # Startup deferral (#246): the session inspector is non-critical — the
        # panel is hidden by default (show_control_panel) — and it re-reads the
        # budget/permissions/workflow every boot. Defer it: the front-end fetches
        # it via the inspector() slot when the panel is actually shown, so cold
        # boot skips this work for the common first-run case.
        "inspector": None,
        "defaultView": DEFAULT_VIEW,
        "initialTask": initial_task or "",
        "recents": _recents(root),
        # The sidebar's real chat history. `recents` above stays the prompt
        # list, which is what the composer's up-arrow history reads.
        "conversations": _conversations(root),
        "brand": _brand(),
        "build": asset_build_identity(),
        "tools": [
            {"id": tool["id"], "label": tool["label"], "desc": tool["desc"]}
            for tool in A.TOOLS
        ],
        # Cache-only (no network at boot): lets the shell show an "update
        # available" nudge immediately, without the user opening Settings.
        # A live (TTL-guarded) check happens when the About page renders.
        "update": _cached_update_check(root),
    }
    _STARTUP.mark("boot:done")
    return payload


def start_fresh_payload(root: Path) -> dict[str, Any]:
    """Explicitly forget the active chat/workflow, preserving audit evidence."""

    from opai.gui_recents import clear_thread
    from opaihub.workflow_state import clear_workflow_state

    resolved = root.expanduser().resolve()
    try:
        # Keep the thread until last: if either deletion fails, boot can still
        # offer the saved conversation instead of presenting a false empty UI.
        clear_workflow_state(resolved)
        clear_thread(resolved)
    except (OSError, RuntimeError, ValueError):
        return _clear_failure_payload(resolved, target="saved session")
    payload = boot_payload(resolved)
    payload["ok"] = True
    return payload


def _clear_failure_payload(root: Path, *, target: str) -> dict[str, Any]:
    try:
        payload = boot_payload(root)
    except (OSError, RuntimeError, ValueError):
        # The frontend retains its current resume card when no replacement
        # payload is available (for example, an unsafe state-dir symlink).
        payload = {}
    payload["ok"] = False
    payload["error"] = {
        "code": "SESSION_CLEAR_FAILED",
        "title": "Saved work was not cleared",
        "userMessage": f"OPai could not clear the {target}.",
        "recoveryActions": [
            "Close other OPai windows using this workspace and try again.",
            "Check that the workspace files are writable.",
        ],
    }
    return payload


def clear_history_payload(root: Path) -> dict[str, Any]:
    """Clear prior history without destroying the current conversation."""

    from opai.gui_recents import clear_recents, load_thread
    from opaihub.build_loop import scrub_build_log_requests

    resolved = root.expanduser().resolve()
    try:
        current = load_thread(resolved)
        scrub_build_log_requests(resolved)
        clear_recents(
            resolved,
            preserve_conversation_id=str(current.get("conversation_id") or ""),
        )
    except (OSError, RuntimeError, ValueError):
        return _clear_failure_payload(resolved, target="saved history")
    payload = boot_payload(resolved)
    payload["ok"] = True
    return payload


def _beat_lease(root: Path, request_id: str) -> None:
    """Restamp this run's supervisor lease. Never breaks the run it describes."""

    from opai.gui_recents import refresh_thread_lease

    try:
        refresh_thread_lease(root, request_id=request_id)
    except (OSError, TypeError, ValueError):
        pass


def _persist_turn_start(root: Path, request_id: str, text: str, mode: str) -> None:
    """Best-effort durability must never prevent the actual user request."""

    from opai.gui_recents import begin_thread_turn

    try:
        begin_thread_turn(root, request_id=request_id, text=text, mode=mode)
    except (OSError, TypeError, ValueError):
        pass


def _persist_turn_result(
    root: Path,
    request_id: str,
    result: dict[str, Any],
    *,
    mode: str,
    build: bool = False,
) -> None:
    """Persist the user-visible outcome, excluding provider/tool internals."""

    from opai.gui_recents import finish_thread_turn, thread_status_for_result

    status = str(result.get("status") or "failed")
    if build:
        applied = [
            str(item.get("path") or "")
            for item in result.get("applied") or []
            if isinstance(item, dict) and item.get("path")
        ]
        if status == "applied":
            answer = "Applied and verified " + str(len(applied)) + " change(s)."
            if applied:
                answer += " " + ", ".join(applied[:20])
        elif status == "verification_failed":
            answer = (
                f"Verification failed after applying {len(applied)} change(s); "
                "the changes were preserved for review or rollback."
            )
            if applied:
                answer += " " + ", ".join(applied[:20])
        elif status == "rolled_back":
            answer = "Verification failed; the edit was rolled back."
        elif status in {"partial_rollback", "rollback_failed"}:
            remaining = [
                str(path)
                for path in result.get("remaining_changed_files") or []
                if str(path).strip()
            ]
            applied = remaining
            answer = "Verification failed; automatic rollback was incomplete."
            if remaining:
                answer += " Still changed: " + ", ".join(remaining[:20])
        elif status == "no_edits":
            answer = str(result.get("answer") or "No file changes were needed.")
        else:
            error = result.get("error")
            answer = (
                str(error.get("userMessage") or error.get("title") or "")
                if isinstance(error, dict)
                else str(error or "")
            ) or str(result.get("answer") or status)
        changed_files: Any = applied
    else:
        error = result.get("error")
        answer = str(result.get("answer") or "")
        if not answer and isinstance(error, dict):
            answer = str(error.get("userMessage") or error.get("title") or "")
        answer = answer or status
        changed_files = result.get("changed_files") or ()

    workflow = (
        result.get("workflow") if isinstance(result.get("workflow"), dict) else {}
    )
    plan_payload = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    plan_steps = plan_payload.get("steps") or workflow.get("plan_steps") or ()
    plan = [
        {"step": str(step), "status": "pending"}
        for step in plan_steps
        if str(step).strip()
    ]
    thread_status = thread_status_for_result(
        status, result.get("completion_verdict"), result.get("run_result")
    )
    try:
        finish_thread_turn(
            root,
            request_id=request_id,
            answer=answer,
            status=thread_status,
            task_id=str(workflow.get("task_id") or request_id),
            # Build is a UI execution channel, not the agent intent reported by
            # the nested chat pipeline. Preserve it so a blocked cloud handoff
            # resumes through bridge.build instead of silently becoming Chat.
            mode=str(mode if build else (workflow.get("mode") or mode)),
            checkpoint_id=str(result.get("checkpoint_id") or ""),
            plan=plan,
            changed_files=changed_files,
        )
    except (OSError, TypeError, ValueError):
        pass


def scaffold_app_payload(root: Path, payload_json: str) -> dict[str, Any]:
    """Scaffold an app under the workspace root for the GUI "New app" flow
    (#276). Qt-free so the contract is unit-tested; the Bridge slot is a thin
    wrapper. Zero tokens: scaffolding is deterministic.
    """
    from opaihub.app_scaffold import scaffold_app

    try:
        payload = json.loads(payload_json or "{}")
    except ValueError:
        payload = {}
    description = str(payload.get("description") or "").strip()
    if not description:
        return {"ok": False, "error": "Describe the app you want to create."}
    try:
        result = scaffold_app(
            root,
            description,
            name=(str(payload.get("name") or "").strip() or None),
            kind=str(payload.get("kind") or "auto"),
        )
    except (ValueError, FileExistsError, OSError) as exc:
        return {"ok": False, "error": safe_detail(exc)}
    return {"ok": True, **result.to_dict()}


def app_receipt_payload(root: Path) -> dict[str, Any]:
    """The current workspace's OPai Build receipt, or an honest not-an-app."""
    from opaihub.build_loop import app_receipt

    return app_receipt(root)


def dashboard_section_payload(root: Path, section_id: str) -> dict[str, Any]:
    """One dashboard section's view model — the heavy repo/ledger walk (#146).

    Qt-free so both the legacy synchronous slot and the async worker path share
    one source of truth (and the tests can exercise it without Chromium).
    """
    try:
        vm = build_view_model(root)
        section = next((s for s in vm["sections"] if s.get("id") == section_id), None)
    except Exception as exc:  # noqa: BLE001
        return {"error": safe_detail(exc)}
    return section or {"error": "not found"}


def apply_tool_payload(root: Path, name: str) -> dict[str, Any]:
    """Run a confirmed mutating tool (repair/panic) — subprocess work (#146)."""
    result = A.run_tool(root, name)
    apply = result.get("apply")
    if apply:
        applied = A.apply_tool(root, apply)
        return {"text": applied.get("text", "done")}
    return {"text": "Nothing to apply."}


def outcomes_payload(root: Path) -> dict[str, Any]:
    """Task-outcome metrics for the cockpit (#288).

    Returns exactly what ``opai outcomes`` prints, so the GUI and CLI are
    provably in parity: cost per completed task and duplicate-call avoidance,
    reconciled to the authoritative model_call ledger.
    """
    from opaihub.ledger import summarize_outcomes

    return summarize_outcomes(root)


def github_status_payload() -> dict[str, Any]:
    """Local-only GitHub connection + push readiness for the Settings UI (#300)."""
    from opaihub.github_connector import github_status

    return github_status()


def github_connect_payload(token: str, *, http: Any = None) -> dict[str, Any]:
    """Validate and store a user-supplied GitHub PAT, then report readiness.

    The token value never appears in the result (the connector guarantees this);
    the GUI only ever receives the connection verdict. ``http`` is injectable so
    tests never touch the network.
    """
    from opaihub.github_connector import connect_github, github_readiness

    result = connect_github(token) if http is None else connect_github(token, http=http)
    result.pop("token", None)  # defensive: never echo a secret to the page
    if result.get("connected"):
        # Fold in the push-readiness truth so the UI can update in one round-trip.
        readiness = github_readiness()
        result["ready_for_push"] = readiness["ready"]
        result["readiness_reason"] = readiness["reason"]
        result["next_step"] = readiness["next_step"]
    return result


def github_set_push_payload(enabled: bool) -> dict[str, Any]:
    """Toggle the persisted allow-push consent and report honest readiness (#300)."""
    from opaihub.github_connector import set_push_allowed

    return set_push_allowed(bool(enabled))


def github_disconnect_payload() -> dict[str, Any]:
    """Remove the stored GitHub token and revoke push consent (#300)."""
    from opaihub.github_connector import disconnect_github

    return disconnect_github()


def settings_payload(root: Path) -> dict[str, Any]:
    """Return the complete, secret-free Settings/Connections payload."""

    from opaihub.autonomy import MODE_LABELS
    from opaihub.gui_preferences import MODES, load_gui_preferences
    from opaihub.ledger import EVENT_MODEL_CALL, read_events

    prefs = load_gui_preferences(root)
    mode_labels = MODE_LABELS
    ledger_events = read_events(root)
    try:
        firewall = A.cost_firewall(root, events=ledger_events)
    except Exception:  # noqa: BLE001
        firewall = {}
    try:
        overview = cached_overview(root)
    except Exception:  # noqa: BLE001
        overview = {}
    models = _models(root, discover_local=False)
    from opaihub.accounts import codex_config_issue, provider_connection_doctor
    from opaihub.credentials import credential_statuses
    from opaihub.github_connector import github_status
    from opaihub.provider_capabilities import all_provider_profiles
    from opaihub.usage import build_usage_snapshots

    credentials = credential_statuses()
    return {
        "prefs": prefs,
        "firewall": {
            "profile": firewall.get("profile"),
            "panic": firewall.get("panic"),
            "spent_today": (firewall.get("spent") or {}).get("today_usd", 0),
            "cloud_gate": bool(firewall.get("require_confirmation_for_cloud")),
            # Budgets (#238): caps and remaining straight from budget_status —
            # the ledger-backed single source of truth, no derived duplicates.
            "caps": firewall.get("caps") or {},
            "spent_month": (firewall.get("spent") or {}).get("month_usd", 0),
            "remaining": firewall.get("remaining") or {},
            "local_first": firewall.get("local_first") or "",
        },
        "permissions": permissions_for(
            str(prefs.get("default_mode") or "safe-auto"),
            safe_auto=prefs.get("safe_auto"),
        ),
        # Per-mode comparison (#239): what each run mode allows, derived from the
        # same permission rules — not re-invented copy. Highlighted against the
        # active mode in the Permissions & Safety page.
        "modePermissions": [
            {
                "id": mode_id,
                "label": mode_labels.get(mode_id, mode_id),
                "summary": permission_summary(mode_id),
                "active": mode_id == str(prefs.get("default_mode") or "safe-auto"),
            }
            for mode_id in MODES
        ],
        # Privacy stance (#239): the honest, factual data posture — sourced from
        # the ledger/audit modules, stated plainly, not marketing copy.
        "privacy": {
            "prompts_stored": False,
            "statements": [
                "No telemetry — nothing leaves your machine.",
                "Raw prompts are never stored; the local ledger keeps one-way "
                "task hashes and counts only.",
                "Saved chat is redacted and kept per workspace on this machine; "
                "clear it any time below or from the sidebar.",
                "Local-first routing; a cloud model is used only after you confirm it.",
            ],
        },
        "accounts": models["accounts"],
        "connections": models["connections"],
        "models": models["models"],
        "usage": build_usage_snapshots(
            root,
            models["models"],
            limits=prefs.get("usage_limits") or {},
            events=ledger_events,
        ),
        "credentials": credentials,
        "connectionDoctor": provider_connection_doctor(
            accounts=models["accounts"],
            connections=models["connections"],
            credentials=credentials,
            include_cli_versions=False,
            include_history=True,
        ),
        "codexConfig": codex_config_issue(),
        # One capability truth for the picker, settings, doctor, and router (#168).
        "providerProfiles": all_provider_profiles(),
        # Per-provider credit/balance for the Credits & Balance page. Cache
        # only — no network in the payload build; the page triggers a live
        # (TTL-guarded) refresh through the refreshBalances slot after render.
        "providerBalances": provider_balances_payload(root, models, probe=False),
        # Per-provider *account usage* (rate/allowance windows) for the Model
        # Usage page. Cache only — reads the local ledger's observed quota, no
        # network in the payload build; the page triggers a live (TTL-guarded)
        # header probe through the refreshUsage slot after render.
        "providerUsage": provider_usage_payload(
            root,
            models,
            events=[
                event
                for event in ledger_events
                if event.get("event_type") == EVENT_MODEL_CALL
            ],
            probe=False,
        ),
        # GitHub connection + push readiness for the Settings connect flow (#300).
        "github": github_status(),
        "about": {
            "version": overview.get("version"),
            "release_stage": overview.get("release_stage"),
            "release_identity": overview.get("release_identity"),
            "compatibility": overview.get("compatibility"),
            "build": asset_build_identity(),
            # Cache only — no network in the payload build; the About page
            # triggers a live (TTL-guarded) check through checkForUpdates
            # after render, same pattern as providerBalances above.
            "update": _cached_update_check(root),
        },
    }


def _cached_update_check(root: Path) -> dict[str, Any]:
    """Read canonical app-wide updater state without touching the network."""
    from opai.update.factory import create_update_service

    try:
        return create_update_service(workspaces=[root]).status()
    except Exception:  # noqa: BLE001 - the About page must never fail to render
        return {
            "schema_version": 1,
            "operation": {
                "state": "unavailable",
                "error_category": "updater_unavailable",
                "safe_diagnostic": "Update status is unavailable right now.",
            },
        }


def provider_balances_payload(
    root: Path,
    models: dict[str, Any] | None = None,
    *,
    probe: bool = False,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Balance snapshots for every AI tool the user could route to.

    Accounts (Claude/Codex/Copilot) come from the live connection list; free
    API providers from the credential store. Each entry carries whether the
    tool is configured so the UI can separate "no credit" from "not set up".
    """
    from opaihub.credentials import CredentialStore
    from opaihub.free_models import FREE_MODEL_SPECS
    from opaihub.provider_balance import balance_overview

    if models is None:
        models = _models(root, discover_local=False)
    providers: list[dict[str, Any]] = []
    for connection in models.get("connections") or []:
        provider = str(connection.get("providerId") or "")
        if provider:
            providers.append(
                {
                    "provider": provider,
                    "kind": "account",
                    "configured": str(connection.get("authStatus") or "")
                    in {"connected", "unknown"},
                }
            )
    store = CredentialStore()
    seen_free: set[str] = set()
    for spec in FREE_MODEL_SPECS:
        provider = str(spec.get("provider") or "")
        if not provider or provider in seen_free:
            continue
        seen_free.add(provider)
        try:
            configured = bool(store.get(provider))
        except Exception:  # noqa: BLE001 - keychain trouble must not break Settings
            configured = False
        providers.append(
            {"provider": provider, "kind": "free", "configured": configured}
        )
    # #673: paid direct-API providers (DeepSeek) get the same balance-card
    # treatment as free ones — balance_overview/balance_snapshot are already
    # generic (locally-tracked amount, "unknown" until any is recorded), so
    # this needs no DeepSeek-specific knowledge, unlike the rate-limit-window
    # data _usage_providers below would need and does not yet have.
    from opaihub.paid_api_models import PAID_MODEL_SPECS

    seen_paid: set[str] = set()
    for spec in PAID_MODEL_SPECS:
        provider = str(spec.get("provider") or "")
        if not provider or provider in seen_paid:
            continue
        seen_paid.add(provider)
        try:
            configured = bool(store.get(provider))
        except Exception:  # noqa: BLE001 - keychain trouble must not break Settings
            configured = False
        providers.append(
            {"provider": provider, "kind": "paid", "configured": configured}
        )
    return balance_overview(root, providers, probe=probe, force=force)


def _usage_providers(root: Path, models: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The connected/known providers to show on the Model Usage page.

    Account providers come from the live connection list; free providers from
    the credential store — the same assembly used for balances, so the two
    pages can never disagree about what is connected."""
    from opaihub.credentials import CredentialStore
    from opaihub.free_models import FREE_MODEL_SPECS
    from opaihub.provider_usage import USAGE_MODELS

    if models is None:
        models = _models(root, discover_local=False)
    providers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for connection in models.get("connections") or []:
        provider = str(connection.get("providerId") or "")
        if provider in USAGE_MODELS and provider not in seen:
            seen.add(provider)
            providers.append(
                {
                    "provider": provider,
                    "configured": str(connection.get("authStatus") or "")
                    in {"connected", "unknown"},
                }
            )
    store = CredentialStore()
    for spec in FREE_MODEL_SPECS:
        provider = str(spec.get("provider") or "")
        if provider not in USAGE_MODELS or provider in seen:
            continue
        seen.add(provider)
        try:
            configured = bool(store.get(provider))
        except Exception:  # noqa: BLE001 - keychain trouble must not break Settings
            configured = False
        providers.append({"provider": provider, "configured": configured})
    return providers


def provider_usage_payload(
    root: Path,
    models: dict[str, Any] | None = None,
    *,
    events: list[dict[str, Any]] | None = None,
    probe: bool = False,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Per-provider account-usage snapshots for the Model Usage page."""
    from opaihub.provider_usage import usage_overview

    return usage_overview(
        root,
        _usage_providers(root, models),
        events=events,
        probe=probe,
        force=force,
    )


def _recents(root: Path) -> list[str]:
    from opai.gui_recents import load_recents

    return load_recents(root)


def _conversations(root: Path) -> list[dict[str, Any]]:
    """Saved chats for the sidebar. Never fails a boot over history."""
    from opai.gui_recents import list_conversations

    try:
        return list_conversations(root)
    except (OSError, ValueError):
        return []


def _brand() -> dict[str, str]:
    from opai.brand import boot_brand

    return boot_brand()


def _write_artifact_smoke_result(path: Path, payload: dict[str, Any]) -> None:
    """Atomically persist an artifact-only GUI smoke result for its parent runner."""
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _run_gui(
    project_root: Path,
    *,
    initial_task: str | None = None,
    artifact_smoke_result: Path | None = None,
    artifact_smoke_timeout_seconds: int = 30,
):
    if artifact_smoke_result is not None and artifact_smoke_timeout_seconds <= 0:
        raise ValueError("artifact smoke timeout must be positive")
    if not web_available():
        raise RuntimeError("QtWebEngine is not available")
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    QtCore.qInstallMessageHandler(lambda *_a: None)
    root = resolve_gui_workspace(project_root)
    from opaihub.gui_pipeline import handle_gui_message
    from opaihub.gui_preferences import save_gui_preferences

    class Worker(QtCore.QThread):
        done = QtCore.Signal(str)

        def __init__(self, fn) -> None:
            super().__init__()
            self._fn = fn

        def run(self) -> None:
            try:
                result = self._fn()
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "answer": safe_detail(exc)}
            self.done.emit(json.dumps(result, default=str))

    class Bridge(QtCore.QObject):
        replyReady = QtCore.Signal(str)
        buildReady = QtCore.Signal(str)
        activity = QtCore.Signal(str)
        activityBatch = QtCore.Signal(str)
        token = QtCore.Signal(str)
        toolReady = QtCore.Signal(str)
        # #380: teardown actually finished for a cancelled request.
        cancelReady = QtCore.Signal(str)
        workspaceChanged = QtCore.Signal(str)
        modelsChanged = QtCore.Signal(str)
        providerLoginReady = QtCore.Signal(str)
        connectionDoctorReady = QtCore.Signal(str)
        # Async data delivery (#146): heavy payloads leave the GUI thread.
        dashboardReady = QtCore.Signal(str)
        settingsReady = QtCore.Signal(str)
        toolApplied = QtCore.Signal(str)
        statusReady = QtCore.Signal(str)
        workspaceReady = QtCore.Signal(str)
        inspectorReady = QtCore.Signal(str)
        updateReady = QtCore.Signal(str)

        def __init__(self, window) -> None:
            super().__init__()
            self.window = window
            self.root = root
            self._workers: list[Any] = []
            self._cancels: dict[str, threading.Event] = {}
            # Requests whose Stop was accepted but whose teardown is unproven.
            self._cancelling: set[str] = set()
            self._resume_context_active = False
            self._session_epoch = _SessionPersistenceEpoch()
            from opai.update.factory import create_update_service

            self._update_service = create_update_service(workspaces=[self.root])
            self._update_started = False
            self._update_maintenance_running = False

        def shutdown(self) -> dict[str, int]:
            """Stop everything before the window dies (#140).

            Signals every pending cancel (the runners kill their CLI children),
            then waits — bounded — for worker threads so Qt never destroys a
            live QThread. Stragglers stay referenced rather than destroyed.
            """
            signalled = signal_cancels(self._cancels)
            self._cancels.clear()
            stragglers = drain_workers(self._workers)
            self._workers = list(stragglers)
            return {"cancelled": signalled, "still_running": len(stragglers)}

        # ---- synchronous data slots ---------------------------------- #
        @QtCore.Slot(result=str)
        def boot(self) -> str:
            return json.dumps(boot_payload(self.root, initial_task=initial_task))

        @QtCore.Slot()
        def markInteractive(self) -> None:
            # Startup instrumentation (#246): the front-end calls this once, after
            # the chat view has first painted. Records the cold-start-to-
            # interactive span and flushes the trace — only when explicitly
            # enabled, and only to the local state dir.
            if _STARTUP.enabled:
                _STARTUP.mark("interactive")
                with contextlib.suppress(Exception):
                    _STARTUP.write(trace_path(self.root))
            if self._update_started:
                return
            self._update_started = True

            def discover_after_interactive() -> dict[str, object]:
                from opai.update.models import UpdateState

                service = self._update_service
                try:
                    service.maintain()
                    current = service.store.load_operation()
                    health = service.startup_health(
                        Path(__file__).resolve().parent / "assets" / "web"
                    )
                    if current.state is UpdateState.RESTARTING:
                        current = service.confirm_health(
                            current.operation_id,
                            running=service.installed,
                            interactive=True,
                            **health,
                        )
                        if current.state is UpdateState.ROLLBACK_PENDING:
                            service.rollback(current.operation_id)
                    elif current.state is UpdateState.ROLLING_BACK:
                        service.confirm_recovery(
                            current.operation_id,
                            running=service.installed,
                            interactive=True,
                            **health,
                        )
                    service.maintain()
                except Exception:  # noqa: BLE001 - public state stays sanitized
                    _LOG.debug("Updater launch maintenance failed", exc_info=True)
                return service.status()

            self._start_update_worker(discover_after_interactive)

        @QtCore.Slot(result=str)
        def resumeSession(self) -> str:
            """Activate persisted context for this window after explicit consent."""

            from opai.gui_recents import resume_execution_context

            context = resume_execution_context(self.root)
            self._resume_context_active = bool(context)
            return json.dumps({"activated": self._resume_context_active})

        @QtCore.Slot(result=str)
        def workspaceState(self) -> str:
            """Re-read branch + uncommitted paths for the header badge.

            The workspace block ships inside the boot payload, which is built
            once per window. The "N uncommitted" badge next to the branch name
            therefore froze at its startup value and kept showing a stale count
            after a run committed files (Round 2). This recomputes it from git
            on demand; the front end calls it whenever a turn finishes.
            """
            return json.dumps(_workspace(self.root))

        @QtCore.Slot(str, result=str)
        def inspector(self, sel_json: str) -> str:
            try:
                sel = json.loads(sel_json)
            except ValueError:
                sel = {}
            return json.dumps(_inspector(self.root, sel))

        @QtCore.Slot(str, result=str)
        def statusLine(self, sel_json: str) -> str:
            try:
                sel = json.loads(sel_json)
            except ValueError:
                sel = {}
            return json.dumps(
                _status(
                    self.root,
                    sel.get("model_label", "Auto"),
                    sel.get("mode_label", "Safe Auto"),
                )
            )

        @QtCore.Slot(str, result=str)
        def dashboard(self, section_id: str) -> str:
            # Legacy synchronous path; requestDashboard is the responsive one.
            return json.dumps(dashboard_section_payload(self.root, section_id))

        def _spawn_data_worker(
            self, fn, signal, request_id: str, extra: dict[str, Any] | None = None
        ) -> None:
            """Compute a payload on a worker thread and deliver it via ``signal``
            as ``{requestId, ...extra, data}`` — the GUI thread never does repo
            walks, ledger reads, or subprocess runs for data views (#146). The
            front-end drops stale responses by requestId."""

            def compute() -> dict[str, Any]:
                try:
                    data = fn()
                except Exception as exc:  # noqa: BLE001
                    data = {"error": safe_detail(exc)}
                return {
                    "requestId": str(request_id or ""),
                    **(extra or {}),
                    "data": data,
                }

            worker = Worker(compute)
            worker.done.connect(signal.emit)
            start_tracked_worker(self._workers, worker)

        @QtCore.Slot(str, str)
        def requestDashboard(self, section_id: str, request_id: str) -> None:
            root, section = self.root, str(section_id or "")
            self._spawn_data_worker(
                lambda: dashboard_section_payload(root, section),
                self.dashboardReady,
                request_id,
                extra={"sectionId": section},
            )

        @QtCore.Slot(str)
        def requestWorkspace(self, request_id: str) -> None:
            root = self.root
            self._spawn_data_worker(
                lambda: _workspace_refresh(root), self.workspaceReady, request_id
            )

        @QtCore.Slot(str, str)
        def requestInspector(self, sel_json: str, request_id: str) -> None:
            try:
                sel = json.loads(sel_json)
            except ValueError:
                sel = {}
            root = self.root
            self._spawn_data_worker(
                lambda: _inspector(root, sel), self.inspectorReady, request_id
            )

        @QtCore.Slot(str, str)
        def reportIllegalTransition(self, from_state: str, to_state: str) -> None:
            """Persist a browser-side refused transition (#612 AC6).

            The renderer already refuses the edge and keeps a bounded list, but
            that list is process-local and nothing read it: a browser-side
            violation vanished on reload while the Python half of the same
            contract wrote a durable journal entry. Support could reconstruct
            one surface's violations after a restart and not the other's.

            Both states are projected onto the canonical vocabulary by
            ``record_illegal_transition``'s own writer, and the source is a
            closed label, so no free text from the page can reach the journal.
            Best-effort: a diagnostic must never break the window it observes.
            """
            from opaihub.lifecycle_diagnostics import record_illegal_transition

            with contextlib.suppress(Exception):
                record_illegal_transition(
                    self.root, str(from_state), str(to_state), "browser"
                )

        @QtCore.Slot(str)
        def requestSettings(self, request_id: str) -> None:
            root = self.root
            self._spawn_data_worker(
                lambda: settings_payload(root), self.settingsReady, request_id
            )

        @QtCore.Slot(str, str)
        def requestStatus(self, sel_json: str, request_id: str) -> None:
            try:
                sel = json.loads(sel_json)
            except ValueError:
                sel = {}
            root = self.root
            model_label = sel.get("model_label", "Auto")
            mode_label = sel.get("mode_label", "Safe Auto")
            # #146: statusLine runs after every message; keep the (now cached)
            # overview read off the GUI thread so the one post-turn recompute
            # never stalls the window. Stale refreshes are dropped by requestId.
            self._spawn_data_worker(
                lambda: _status(root, model_label, mode_label),
                self.statusReady,
                request_id,
            )

        @QtCore.Slot(str, str)
        def applyToolAsync(self, name: str, request_id: str) -> None:
            root, tool = self.root, str(name or "")
            self._spawn_data_worker(
                lambda: apply_tool_payload(root, tool),
                self.toolApplied,
                request_id,
            )

        @QtCore.Slot(str, str, result=str)
        def prompts(self, query: str, category: str) -> str:
            return json.dumps(
                {
                    "categories": categories_present(),
                    "prompts": filter_prompts(query, category or None),
                }
            )

        @QtCore.Slot(str, result=str)
        def usePrompt(self, prompt_id: str) -> str:
            return json.dumps(find_prompt(prompt_id) or {})

        @QtCore.Slot(str, str, result=str)
        def reviewDiff(self, path: str, decision: str) -> str:
            from opaihub.diff_review import record_diff_decision

            return json.dumps(record_diff_decision(self.root, path, decision))

        @QtCore.Slot(str, result=str)
        def scaffoldApp(self, payload_json: str) -> str:
            # Deterministic file writes (~5 small files) — GUI-thread safe.
            return json.dumps(scaffold_app_payload(self.root, payload_json))

        @QtCore.Slot(result=str)
        def appReceipt(self) -> str:
            return json.dumps(app_receipt_payload(self.root))

        @QtCore.Slot(result=str)
        def taskOutcomes(self) -> str:
            # Same summary the CLI prints (#288): GUI/CLI parity by construction.
            return json.dumps(outcomes_payload(self.root))

        @QtCore.Slot(result=str)
        def settingsData(self) -> str:
            return json.dumps(settings_payload(self.root))

        def _start_update_worker(self, fn) -> None:
            worker = Worker(fn)
            worker.done.connect(self.updateReady.emit)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(result=str)
        def updateStatus(self) -> str:
            return json.dumps(self._update_service.status())

        @QtCore.Slot()
        def maintainUpdates(self) -> None:
            if self._update_maintenance_running:
                return
            self._update_maintenance_running = True

            def maintain() -> dict[str, object]:
                try:
                    self._update_service.maintain()
                except Exception:  # noqa: BLE001 - persisted safe state is authoritative
                    _LOG.debug("Periodic updater maintenance failed", exc_info=True)
                return self._update_service.status()

            worker = Worker(maintain)
            worker.done.connect(self.updateReady.emit)

            def finished(w=worker) -> None:
                self._update_maintenance_running = False
                if w in self._workers:
                    self._workers.remove(w)

            worker.finished.connect(finished)
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(bool)
        def checkForUpdates(self, force: bool) -> None:
            def check() -> dict[str, object]:
                try:
                    self._update_service.check(
                        force=bool(force), allow_automatic_download=True
                    )
                except Exception:  # noqa: BLE001 - state/error contract is persisted
                    _LOG.debug("Updater discovery failed", exc_info=True)
                return self._update_service.status()

            self._start_update_worker(check)

        @QtCore.Slot(str)
        def updateAction(self, action: str) -> None:
            def apply_action() -> dict[str, object]:
                service = self._update_service
                current = service.store.load_operation()
                reply: dict[str, object] | None = None
                try:
                    if action in {"download", "retry"}:
                        service.download(current.operation_id)
                    elif action == "install_now":
                        service.install(current.operation_id, mode="now")
                    elif action == "when_idle":
                        service.install(current.operation_id, mode="when_idle")
                    elif action == "on_quit":
                        service.install(current.operation_id, mode="on_quit")
                    elif action == "later":
                        service.defer(current.operation_id)
                    elif action == "resume":
                        service.resume(current.operation_id)
                    elif action == "rollback":
                        service.rollback(current.operation_id)
                    elif action == "developer_apply":
                        reply = service.apply_developer_source()
                    elif action == "developer_apply_force":
                        reply = service.apply_developer_source(force=True)
                    elif action == "check":
                        service.check(force=True, allow_automatic_download=True)
                except Exception:  # noqa: BLE001 - never leak raw updater errors
                    _LOG.debug("Updater action failed: %s", action, exc_info=True)
                status = service.status()
                if reply is not None:
                    # Transient, per-action outcome (e.g. a developer apply):
                    # emitted once with the status, never persisted by the service.
                    status["developer_apply"] = reply
                return status

            self._start_update_worker(apply_action)

        @QtCore.Slot(str, str, result=str)
        def setUpdatePolicy(self, key: str, value: str) -> str:
            allowed = {
                "check_for_updates",
                "automatic_downloads",
                "automatic_install_on_quit",
            }
            if key not in allowed:
                return json.dumps(
                    {"ok": False, "error_category": "policy_change_invalid"}
                )
            try:
                self._update_service.set_policy(
                    **{key: str(value).casefold() in {"true", "on", "1"}}
                )
                return json.dumps({"ok": True, **self._update_service.status()})
            except Exception:  # noqa: BLE001 - stable public error only
                return json.dumps(
                    {"ok": False, "error_category": "policy_change_failed"}
                )

        @QtCore.Slot(result=str)
        def githubStatus(self) -> str:
            return json.dumps(github_status_payload())

        @QtCore.Slot(str, result=str)
        def githubConnect(self, token: str) -> str:
            # The user pastes their own PAT; it is validated and stored in the
            # keychain and never echoed back to the page (#300).
            return json.dumps(github_connect_payload(token))

        @QtCore.Slot(str, result=str)
        def githubSetPush(self, state: str) -> str:
            enabled = str(state or "").strip().lower() in {"on", "true", "1", "yes"}
            return json.dumps(github_set_push_payload(enabled))

        @QtCore.Slot(result=str)
        def githubDisconnect(self) -> str:
            return json.dumps(github_disconnect_payload())

        @QtCore.Slot(str, str, result=str)
        def saveProviderKey(self, provider: str, secret: str) -> str:
            from opaihub.credentials import CredentialStore

            try:
                return json.dumps(CredentialStore().set(provider, secret))
            except (ValueError, RuntimeError) as exc:
                return json.dumps(
                    {
                        "provider": provider,
                        "configured": False,
                        "error": safe_detail(exc),
                    }
                )

        @QtCore.Slot(str, result=str)
        def deleteProviderKey(self, provider: str) -> str:
            from opaihub.credentials import CredentialStore

            return json.dumps(CredentialStore().delete(provider))

        @QtCore.Slot(str, result=str)
        def testProvider(self, provider: str) -> str:
            # GitHub is not an AI-provider adapter; it has its own connector, so
            # route its test there for a real diagnostic instead of a bare
            # "Unsupported AI provider" (Bug 5).
            if str(provider or "").strip().lower() == "github":
                from opaihub.github_connector import verify_github_connection

                try:
                    return json.dumps(verify_github_connection())
                except (OSError, RuntimeError, ValueError) as exc:
                    return json.dumps(
                        {
                            "provider": "github",
                            "connected": False,
                            "authStatus": "provider_unavailable",
                            "safeDiagnostic": (
                                "GitHub connection check failed unexpectedly. "
                                "Try again shortly."
                            ),
                            "error": safe_detail(exc),
                        }
                    )
            from opaihub.provider_adapters import adapter_for

            try:
                return json.dumps(adapter_for(provider).probe(force=True))
            except (OSError, RuntimeError, ValueError) as exc:
                return json.dumps(
                    {
                        "provider": provider,
                        "connected": False,
                        "error": safe_detail(exc),
                    }
                )

        @QtCore.Slot(result=str)
        def refreshModels(self) -> str:
            return json.dumps(A.available_models(self.root, discover_local=True))

        @QtCore.Slot(str, str, str, result=str)
        def setProviderBalance(self, provider: str, amount: str, currency: str) -> str:
            """Store a user-entered balance for a provider without a balance API."""
            from opaihub.provider_balance import set_manual_balance

            try:
                snapshot = set_manual_balance(
                    self.root, provider, float(amount), currency=currency
                )
                return json.dumps({"ok": True, "balance": snapshot})
            except (TypeError, ValueError) as exc:
                return json.dumps({"ok": False, "error": safe_detail(exc)})

        @QtCore.Slot(result=str)
        def refreshBalances(self) -> str:
            """Force-refresh live balances and return the full overview."""
            try:
                return json.dumps(
                    {
                        "ok": True,
                        "balances": provider_balances_payload(
                            self.root, probe=True, force=True
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - never crash the page
                return json.dumps({"ok": False, "error": safe_detail(exc)})

        @QtCore.Slot(result=str)
        def refreshUsage(self) -> str:
            """Force-refresh live provider usage (safe header probes) and return
            the full overview. Never crashes the page."""
            try:
                return json.dumps(
                    {
                        "ok": True,
                        "usage": provider_usage_payload(
                            self.root, probe=True, force=True
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - never crash the page
                return json.dumps({"ok": False, "error": safe_detail(exc)})

        @QtCore.Slot(str, str, str, str, result=str)
        def saveUsageLimit(
            self, model_id: str, metric: str, limit: str, window: str
        ) -> str:
            from opaihub.gui_preferences import save_usage_limit

            try:
                save_usage_limit(
                    self.root,
                    model_id,
                    metric=metric,
                    limit=int(limit),
                    window=window,
                )
                return json.dumps({"ok": True})
            except (TypeError, ValueError) as exc:
                return json.dumps({"ok": False, "error": safe_detail(exc)})

        @QtCore.Slot(result=str)
        def repairCodexConfig(self) -> str:
            from opaihub.accounts import repair_codex_config

            try:
                return json.dumps(repair_codex_config())
            except (OSError, ValueError) as exc:
                return json.dumps({"repaired": False, "error": safe_detail(exc)})

        @QtCore.Slot(str, result=str)
        def disconnectAccount(self, provider: str) -> str:
            """Sign out via the provider's own CLI (never touches credential files)."""
            from opaihub.accounts import disconnect_account

            try:
                return json.dumps(disconnect_account(provider))
            except Exception as exc:  # noqa: BLE001 - always report cleanly
                return json.dumps(
                    {
                        "provider": provider,
                        "disconnected": False,
                        "message": safe_detail(exc),
                    }
                )

        @QtCore.Slot(str, str)
        def startProviderLogin(self, provider: str, request_id: str) -> None:
            """Launch the explicit visible-terminal login flow off the UI thread."""
            from opaihub.accounts import interactive_provider_login

            cancel = threading.Event()
            self._cancels[request_id] = cancel
            worker = Worker(lambda: interactive_provider_login(provider, cancel=cancel))

            def _done(result_json: str) -> None:
                self._cancels.pop(request_id, None)
                self.providerLoginReady.emit(
                    json.dumps(
                        {
                            "requestId": request_id,
                            "provider": provider,
                            "result": json.loads(result_json),
                        }
                    )
                )

            worker.done.connect(_done)
            # #380: teardown is only proven once run() has returned.
            worker.finished.connect(lambda rid=request_id: self._confirm_teardown(rid))
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str)
        def refreshConnectionDoctor(self, request_id: str) -> None:
            """Collect local CLI versions without blocking Qt's UI thread."""
            from opaihub.accounts import provider_connection_doctor

            worker = Worker(provider_connection_doctor)

            def _done(result_json: str) -> None:
                result = json.loads(result_json)
                self.connectionDoctorReady.emit(
                    json.dumps(
                        {
                            "requestId": request_id,
                            "entries": result if isinstance(result, list) else [],
                        }
                    )
                )

            worker.done.connect(_done)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str, result=str)
        def grantFreeConsent(self, model_id: str) -> str:
            """Remember consent for a free-tier model id (one-time confirmation)."""
            from opaihub.gui_preferences import grant_free_consent

            try:
                updated = grant_free_consent(self.root, model_id)
                return json.dumps({"ok": True, "freeConsent": updated["free_consent"]})
            except ValueError as exc:
                return json.dumps({"ok": False, "error": safe_detail(exc)})

        @QtCore.Slot(str, str)
        def savePref(self, key: str, value: str) -> None:
            allowed = {
                "default_model",
                "default_mode",
                "default_task_mode",
                "default_output_format",
                "show_control_panel",
                "density",
                "reduced_motion",
                "activity_copy",
                "onboarding_seen",
                "composer_style",
            }
            if key not in allowed:
                return
            # Full Auto pin contract (#137): selecting Full Auto never persists a
            # bare full-auto default. It must go through the explicit pin slot,
            # so a plain savePref for it downgrades to Safe Auto.
            if key == "default_mode" and value == "full-auto":
                value = "safe-auto"
            val: Any = value
            if value in ("true", "false"):
                val = value == "true"
            save_gui_preferences(self.root, {key: val})

        @QtCore.Slot(result=str)
        def pinFullAuto(self) -> str:
            """Explicitly pin Full Auto with a recorded acknowledgement (#137)."""
            from opaihub.autonomy import resolve_startup_mode
            from opaihub.gui_preferences import pin_full_auto

            prefs = pin_full_auto(self.root)
            return json.dumps(resolve_startup_mode(prefs).to_dict())

        @QtCore.Slot(result=str)
        def unpinFullAuto(self) -> str:
            """Clear the Full Auto pin and fall back to Safe Auto (#137)."""
            from opaihub.autonomy import resolve_startup_mode
            from opaihub.gui_preferences import unpin_full_auto

            prefs = unpin_full_auto(self.root)
            return json.dumps(resolve_startup_mode(prefs).to_dict())

        # ---- async slots --------------------------------------------- #
        @QtCore.Slot(str)
        def send(self, payload_json: str) -> None:
            try:
                payload = json.loads(payload_json)
            except ValueError:
                payload = {}
            text = str(payload.get("text", "")).strip()
            if not text:
                return
            request_id = str(payload.get("requestId") or uuid.uuid4().hex[:12])
            model_id = payload.get("model", "auto")
            mode = payload.get("mode", "safe-auto")
            turn_root = self.root
            turn_epoch = self._session_epoch.capture()
            from opai.gui_recents import resume_execution_context

            resume_context = (
                resume_execution_context(turn_root)
                if self._resume_context_active
                else {}
            )
            cancel = threading.Event()
            self._cancels[request_id] = cancel
            _persist_turn_start(turn_root, request_id, text, str(mode))

            # Activity batching (#226): worker threads append events to a
            # lock-guarded buffer; a GUI-thread QTimer drains it into ONE
            # `activityBatch` payload every ~33ms, so a burst of activity costs
            # one cross-thread signal instead of one per event. The legacy
            # per-event `activity` signal stays defined for the classic GUI
            # (#138) but the web path no longer floods it.
            batcher = ActivityBatcher(request_id)

            def flush_batch() -> None:
                payload = batcher.flush()
                if payload is not None:
                    self.activityBatch.emit(payload)
                # Beat the supervisor lease on the same tick (#295). A lease
                # that is never restamped goes stale mid-run, and a healthy
                # long task would start looking abandoned to the next reader.
                _beat_lease(turn_root, request_id)

            timer = QtCore.QTimer(self)
            timer.setInterval(FLUSH_INTERVAL_MS)
            timer.timeout.connect(flush_batch)
            timer.start()

            # Callbacks run on the worker thread; appending is thread-safe and
            # the GUI-thread timer does the emitting. Every payload carries the
            # request_id so the front-end drops anything stale/cancelled.
            def emit_event(event: dict[str, Any]) -> None:
                batcher.append(event)

            def emit_text(chunk: str) -> None:
                self.token.emit(json.dumps({"requestId": request_id, "text": chunk}))

            def job() -> dict[str, Any]:
                return handle_gui_message(
                    turn_root,
                    text,
                    model_id=model_id,
                    mode=mode,
                    focus_hint=payload.get("focus"),
                    output_instruction=output_format(payload.get("format")).get(
                        "instruction", ""
                    ),
                    on_event=emit_event,
                    on_text=emit_text,
                    cancel=cancel,
                    allow_cloud=bool(payload.get("allowCloud", False)),
                    allow_limit=bool(payload.get("allowLimit", False)),
                    # One-shot grants from the in-context approval cards. The
                    # mock bridge honored these in e2e, but the real bridge
                    # dropped them — the Approve buttons were no-ops (F17/F26).
                    allow_command=str(payload.get("allowCommand") or "") or None,
                    allow_edits_once=bool(payload.get("allowEditsOnce", False)),
                    resume_context=resume_context,
                )

            worker = Worker(job)

            def _done(result_json: str) -> None:
                self._cancels.pop(request_id, None)
                # Deliver the tail before the reply so no event is lost or
                # arrives after the answer (honesty invariant).
                timer.stop()
                flush_batch()
                timer.deleteLater()
                result = json.loads(result_json)
                self._session_epoch.run_if_current(
                    turn_epoch,
                    lambda: _persist_turn_result(
                        turn_root,
                        request_id,
                        result,
                        mode=str(mode),
                        build=False,
                    ),
                )
                self.replyReady.emit(
                    json.dumps({"requestId": request_id, "result": result})
                )

            worker.done.connect(_done)
            # #380: teardown is only proven once run() has returned.
            worker.finished.connect(lambda rid=request_id: self._confirm_teardown(rid))
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str)
        def build(self, payload_json: str) -> None:
            """One OPai Build turn from the GUI (#276): a chat message becomes a
            cheap, verified, targeted edit of the workspace app.

            Mirrors ``send`` exactly — same Worker thread, same batched activity
            path, same cancel plumbing — but runs the build loop instead of a
            plain chat turn, so the applied files, structural verification, and
            savings receipt all come back to the cockpit.
            """
            try:
                payload = json.loads(payload_json)
            except ValueError:
                payload = {}
            request = str(payload.get("text", "")).strip()
            if not request:
                return
            request_id = str(payload.get("requestId") or uuid.uuid4().hex[:12])
            model_id = payload.get("model") or None
            strict = bool(payload.get("strict", False))
            turn_root = self.root
            turn_epoch = self._session_epoch.capture()
            from opai.gui_recents import resume_execution_context

            resume_context = (
                resume_execution_context(turn_root)
                if self._resume_context_active
                else {}
            )
            cancel = threading.Event()
            self._cancels[request_id] = cancel
            _persist_turn_start(turn_root, request_id, request, "build")

            batcher = ActivityBatcher(request_id)

            def flush_batch() -> None:
                out = batcher.flush()
                if out is not None:
                    self.activityBatch.emit(out)

            timer = QtCore.QTimer(self)
            timer.setInterval(FLUSH_INTERVAL_MS)
            timer.timeout.connect(flush_batch)
            timer.start()

            def emit_event(event: dict[str, Any]) -> None:
                batcher.append(event)

            def emit_text(chunk: str) -> None:
                self.token.emit(json.dumps({"requestId": request_id, "text": chunk}))

            def job() -> dict[str, Any]:
                from opaihub.build_loop import run_build_request

                return run_build_request(
                    turn_root,
                    request,
                    model=model_id,
                    strict=strict,
                    on_event=emit_event,
                    on_text=emit_text,
                    cancel=cancel,
                    resume_context=resume_context,
                    allow_cloud=bool(payload.get("allowCloud", False)),
                    allow_limit=bool(payload.get("allowLimit", False)),
                )

            worker = Worker(job)

            def _done(result_json: str) -> None:
                self._cancels.pop(request_id, None)
                timer.stop()
                flush_batch()
                timer.deleteLater()
                result = json.loads(result_json)
                self._session_epoch.run_if_current(
                    turn_epoch,
                    lambda: _persist_turn_result(
                        turn_root,
                        request_id,
                        result,
                        mode="build",
                        build=True,
                    ),
                )
                self.buildReady.emit(
                    json.dumps({"requestId": request_id, "result": result})
                )

            worker.done.connect(_done)
            # #380: teardown is only proven once run() has returned.
            worker.finished.connect(lambda rid=request_id: self._confirm_teardown(rid))
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot()
        def discoverModels(self) -> None:
            """Discover loopback models off the GUI thread and publish the catalog."""

            worker = Worker(lambda: A.available_models(self.root, discover_local=True))

            def _done(result_json: str) -> None:
                self.modelsChanged.emit(result_json)

            worker.done.connect(_done)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str)
        def cancel(self, request_id: str) -> None:
            """Stop the request: set its cancel flag so the runner kills the CLI.

            Setting the flag is a *request*, not proof: the runner terminates its
            subprocess whenever it next notices. The front-end therefore shows
            "Stopping…" and waits for ``cancelReady`` (#380), which this bridge
            emits only once the worker thread has actually returned. Declaring
            "cancelled" at this moment instead would claim a paid provider call
            had stopped while it was very possibly still running (#295
            invariants 9 and 15).
            """
            rid = str(request_id)
            event = self._cancels.get(rid)
            if event is None:
                # Nothing to stop — already finished or never started. Say so
                # rather than leaving the UI waiting for a teardown that will
                # never be confirmed.
                self.cancelReady.emit(
                    json.dumps({"requestId": rid, "teardown": "not_running"})
                )
                return
            self._cancelling.add(rid)
            event.set()

        def _confirm_teardown(self, request_id: str) -> None:
            """Tell the UI a cancelled request's worker has genuinely stopped.

            Wired to ``QThread.finished``, which fires after ``run()`` returns —
            i.e. after the provider call unwound and its subprocess was reaped.
            That is the first moment "cancelled" is a fact rather than a hope.
            """
            rid = str(request_id)
            if rid not in self._cancelling:
                return
            self._cancelling.discard(rid)
            self.cancelReady.emit(
                json.dumps({"requestId": rid, "teardown": "complete"})
            )

        @QtCore.Slot(str)
        def runTool(self, name: str) -> None:
            def job():
                result = A.run_tool(self.root, name)
                if result.get("mutates") and result.get("apply"):
                    # Confirm on the GUI thread is awkward from a worker; for the
                    # web UI we surface the confirm text and apply on accept via a
                    # second call. Here we just return the prompt.
                    return {
                        "title": result.get("title"),
                        "text": result.get("confirm", result.get("text", "")),
                        "needs_confirm": True,
                        "apply": name,
                    }
                return {
                    "title": result.get("title", "Tool"),
                    "text": result.get("text", ""),
                    "needs_confirm": False,
                }

            worker = Worker(job)
            worker.done.connect(self.toolReady.emit)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str, result=str)
        def applyTool(self, name: str) -> str:
            # Legacy synchronous path; applyToolAsync is the responsive one.
            return json.dumps(apply_tool_payload(self.root, name))

        @QtCore.Slot()
        def openWorkspace(self) -> None:
            # Bring the window forward first so the native picker is never hidden
            # behind it, and start one level up so sibling projects are one click
            # away. Runs on the GUI thread (it's a slot), so the modal dialog is
            # safe.
            self.window.raise_()
            self.window.activateWindow()
            parent = self.root.parent
            start = str(parent if parent.exists() else self.root)
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self.window,
                "Open project folder",
                start,
                QtWidgets.QFileDialog.Option.ShowDirsOnly,
            )
            if chosen and is_valid_workspace(chosen):
                self._switch(chosen)

        @QtCore.Slot(result=str)
        def pickContextFiles(self) -> str:
            """Let the user select context files without exposing host paths."""
            self.window.raise_()
            self.window.activateWindow()
            chosen, _filter = QtWidgets.QFileDialog.getOpenFileNames(
                self.window,
                "Attach files",
                str(self.root),
            )
            return json.dumps(context_picker_payload(self.root, chosen))

        @QtCore.Slot(result=str)
        def pickContextFolder(self) -> str:
            """Let the user select one context folder inside the workspace."""
            self.window.raise_()
            self.window.activateWindow()
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self.window,
                "Add a folder",
                str(self.root),
                QtWidgets.QFileDialog.Option.ShowDirsOnly,
            )
            selected = [chosen] if chosen else []
            return json.dumps(context_picker_payload(self.root, selected))

        @QtCore.Slot(str)
        def switchWorkspace(self, path: str) -> None:
            if is_valid_workspace(path):
                self._switch(path)

        @QtCore.Slot(str)
        def openPath(self, target: str) -> None:
            """Open a file or folder from this project in the OS file manager."""
            resolved = resolve_openable(self.root, target)
            if resolved is not None:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(resolved)))

        @QtCore.Slot(result=str)
        def recents(self) -> str:
            return json.dumps(_recents(self.root))

        @QtCore.Slot(str)
        def saveRecent(self, text: str) -> None:
            from opai.gui_recents import add_recent

            add_recent(self.root, text)

        @QtCore.Slot(result=str)
        def clearRecents(self) -> str:
            return json.dumps(clear_history_payload(self.root))

        @QtCore.Slot(result=str)
        def listConversations(self) -> str:
            """Saved chats for the sidebar, without their transcripts."""
            from opai.gui_recents import list_conversations

            try:
                return json.dumps(
                    {"ok": True, "conversations": list_conversations(self.root)}
                )
            except (OSError, ValueError) as exc:
                # History is not the product. If it cannot be listed, say so
                # and let the user keep working rather than failing the view.
                return json.dumps(
                    {"ok": False, "conversations": [], "error": safe_detail(exc)[:200]}
                )

        @QtCore.Slot(str, result=str)
        def loadConversation(self, conversation_id: str) -> str:
            """One saved chat's full transcript, for reopening it read-only."""
            from opai.gui_recents import load_conversation

            try:
                record = load_conversation(self.root, str(conversation_id or ""))
            except (OSError, ValueError) as exc:
                return json.dumps({"ok": False, "error": safe_detail(exc)[:200]})
            if not record:
                return json.dumps(
                    {"ok": False, "error": "That chat is no longer available."}
                )
            return json.dumps({"ok": True, "conversation": record})

        @QtCore.Slot(result=str)
        def clearSession(self) -> str:
            result = self._session_epoch.invalidate_on_success(
                lambda: start_fresh_payload(self.root)
            )
            if result.get("ok"):
                self._resume_context_active = False
            return json.dumps(result)

        def _switch(self, path: str) -> None:
            self.root = resolve_gui_workspace(Path(path))
            self._resume_context_active = False
            self._session_epoch.invalidate()
            add_recent_workspace(self.root)
            self.window.setWindowTitle(f"OPai · {self.root.name}")
            self.workspaceChanged.emit(json.dumps(boot_payload(self.root)))

        @QtCore.Slot(str)
        def openExternal(self, url: str) -> None:
            if str(url).startswith(("http://", "https://")):
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

        @QtCore.Slot(str)
        def copyText(self, text: str) -> None:
            """Write-only clipboard for copy buttons; the page never reads it."""
            QtGui.QGuiApplication.clipboard().setText(str(text or "")[:20000])

        # ---- native window chrome ----------------------------------- #
        @QtCore.Slot()
        def startWindowMove(self) -> None:
            """Hand title-bar dragging to the operating system."""
            handle = self.window.windowHandle()
            if handle is not None:
                handle.startSystemMove()

        @QtCore.Slot(str)
        def startWindowResize(self, edge_name: str) -> None:
            """Preserve native edge resizing for the frameless app window."""
            edge = {
                "top": QtCore.Qt.Edge.TopEdge,
                "right": QtCore.Qt.Edge.RightEdge,
                "bottom": QtCore.Qt.Edge.BottomEdge,
                "left": QtCore.Qt.Edge.LeftEdge,
                "top-right": QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.RightEdge,
                "bottom-right": QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.RightEdge,
                "bottom-left": QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.LeftEdge,
                "top-left": QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge,
            }.get(edge_name)
            handle = self.window.windowHandle()
            if edge is not None and handle is not None:
                handle.startSystemResize(edge)

        @QtCore.Slot()
        def minimizeWindow(self) -> None:
            self.window.showMinimized()

        @QtCore.Slot()
        def toggleMaximizeWindow(self) -> None:
            if self.window.isMaximized():
                self.window.showNormal()
            else:
                self.window.showMaximized()

        @QtCore.Slot()
        def closeWindow(self) -> None:
            self.window.close()

    class Window(QtWidgets.QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"OPai · {root.name}")
            self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
            self.setMinimumSize(1040, 700)
            self.resize(1340, 880)
            self.view = QWebEngineView(self)
            s = self.view.settings()
            s.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
            )
            # Local-only render surface (#149): every byte of data arrives over
            # the QWebChannel bridge, so the page gets no network reach and no
            # clipboard permission. Copy buttons go through the write-only
            # bridge slot; external links go through openExternal.
            s.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
            )
            s.setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False
            )
            s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            # Local-only surface: HTML/CSS/JS/icons load from disk over file://
            # and every byte of data arrives over the bridge, so an HTTP cache
            # buys nothing and only causes staleness — edited CSS or swapped
            # icons would keep showing the old bytes until the cache evicts.
            # Disable it and clear any prior cache so a relaunch always renders
            # the current on-disk assets.
            prof = self.view.page().profile()
            prof.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
            prof.clearHttpCache()
            self.setCentralWidget(self.view)
            self.bridge = Bridge(self)
            self.channel = QWebChannel()
            self.channel.registerObject("bridge", self.bridge)
            self.view.page().setWebChannel(self.channel)
            self.view.setHtml("")  # avoid white flash before load
            self.view.load(_runtime_index_url(WEB_DIR))

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
            # Closing the window is how most users "stop" an AI app: cancel any
            # in-flight run (killing its CLI child) and drain workers before Qt
            # teardown, so nothing keeps spending after quit (#140).
            self.bridge.shutdown()
            with contextlib.suppress(Exception):
                self.bridge._update_service.install_on_quit()
            super().closeEvent(event)

    if QtWidgets.QApplication.instance() is None:
        # QtWebEngine needs a shared GL context set before the app is created,
        # and a crisp scale factor passed through on high-DPI displays.
        QtCore.QCoreApplication.setAttribute(
            QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts
        )
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    smoke_outcome: dict[str, Any] | None = None
    if artifact_smoke_result is not None:
        result_path = artifact_smoke_result.expanduser().resolve()
        poll_timer = QtCore.QTimer(window)
        poll_timer.setInterval(150)

        def finish_artifact_smoke(payload: dict[str, Any]) -> None:
            nonlocal smoke_outcome
            if smoke_outcome is not None:
                return
            smoke_outcome = payload
            poll_timer.stop()
            try:
                _write_artifact_smoke_result(result_path, payload)
            except OSError as exc:
                smoke_outcome = {
                    "ok": False,
                    "status": "result_write_failed",
                    "error": safe_detail(exc),
                }
            QtCore.QTimer.singleShot(0, window.close)

        def inspect_artifact_page(value: Any) -> None:
            try:
                page = json.loads(value) if isinstance(value, str) else {}
            except json.JSONDecodeError:
                page = {}
            if not isinstance(page, dict):
                page = {}
            if (
                page.get("documentReady")
                and page.get("app")
                and page.get("bridgeBooted")
            ):
                finish_artifact_smoke(
                    {
                        "ok": True,
                        "status": "ready",
                        "document_ready": True,
                        "app_element": True,
                        "bridge_booted": True,
                    }
                )

        def poll_artifact_page() -> None:
            window.view.page().runJavaScript(
                "JSON.stringify({"
                "documentReady: document.readyState === 'complete',"
                "app: Boolean(document.getElementById('app')),"
                "bridgeBooted: Boolean(window.__opai && window.__opai.state && "
                "window.__opai.state.boot)"
                "})",
                inspect_artifact_page,
            )

        def artifact_page_loaded(loaded: bool) -> None:
            if not loaded:
                finish_artifact_smoke({"ok": False, "status": "page_load_failed"})
                return
            poll_timer.start()
            poll_artifact_page()

        poll_timer.timeout.connect(poll_artifact_page)
        window.view.loadFinished.connect(artifact_page_loaded)
        QtCore.QTimer.singleShot(
            artifact_smoke_timeout_seconds * 1000,
            lambda: finish_artifact_smoke({"ok": False, "status": "timeout"}),
        )
    # Window/taskbar/Alt-Tab icon + Windows taskbar grouping, set before show so
    # the app never presents as a generic Python window (#148).
    from opai.gui_identity import apply_window_identity

    apply_window_identity(app, window)
    window.show()
    app.exec()
    if artifact_smoke_result is not None:
        if smoke_outcome is None:
            finish_artifact_smoke({"ok": False, "status": "closed_before_ready"})
        return smoke_outcome
    return 0


def launch(project_root: Path, task: str | None = None) -> int:
    """Open the web-rendered desktop window (blocks until closed)."""
    return int(_run_gui(project_root, initial_task=task) or 0)


def run_artifact_smoke(
    project_root: Path, result_path: Path, *, timeout_seconds: int = 30
) -> dict[str, Any]:
    """Launch and close a real QtWebEngine/bridge artifact smoke window."""
    outcome = _run_gui(
        project_root,
        artifact_smoke_result=result_path,
        artifact_smoke_timeout_seconds=timeout_seconds,
    )
    if not isinstance(outcome, dict):
        raise RuntimeError("artifact smoke did not produce an outcome")
    return outcome
