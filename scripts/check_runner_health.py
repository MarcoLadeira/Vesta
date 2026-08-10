"""Turn GitHub runner inventory into typed, candidate-bound health evidence."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def evaluate(
    inventory: dict[str, Any] | None,
    required_labels: set[str],
    candidate_sha: str,
    *,
    api_error: str | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "profile": "runner-health",
        "candidate_sha": candidate_sha,
        "required_labels": sorted(required_labels),
        "checked_at": _now(),
    }
    if not SHA_PATTERN.fullmatch(candidate_sha):
        return {
            **base,
            "verdict": "infrastructure_blocked",
            "reason": "candidate_sha_invalid",
        }
    if api_error is not None or not isinstance(inventory, dict):
        return {
            **base,
            "verdict": "infrastructure_blocked",
            "reason": "runner_api_unavailable",
            "api_error": str(api_error or "unreadable inventory")[:200],
        }
    runners = inventory.get("runners")
    if not isinstance(runners, list):
        return {
            **base,
            "verdict": "infrastructure_blocked",
            "reason": "runner_inventory_invalid",
        }

    candidates: list[dict[str, Any]] = []
    for runner in runners:
        if not isinstance(runner, dict):
            continue
        labels = {
            str(label.get("name"))
            for label in runner.get("labels", [])
            if isinstance(label, dict) and label.get("name")
        }
        if required_labels.issubset(labels):
            candidates.append(
                {
                    "name": str(runner.get("name") or "unknown")[:100],
                    "status": str(runner.get("status") or "unknown"),
                    "busy": bool(runner.get("busy")),
                    "labels": sorted(labels),
                }
            )
    healthy = [
        runner
        for runner in candidates
        if runner["status"] == "online" and runner["busy"] is False
    ]
    if not healthy:
        return {
            **base,
            "verdict": "runner_unavailable",
            "reason": "no_idle_online_runner_with_required_labels",
            "matching_runners": candidates,
        }
    return {
        **base,
        "verdict": "qualified",
        "reason": "idle_online_runner_available",
        "matching_runners": healthy,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--label", action="append", dest="labels", required=True)
    parser.add_argument("--api-error")
    args = parser.parse_args(argv)

    inventory: dict[str, Any] | None = None
    api_error = args.api_error
    if api_error is None:
        try:
            value = json.loads(args.input.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                inventory = value
            else:
                api_error = "inventory is not an object"
        except (AttributeError, OSError, json.JSONDecodeError) as error:
            api_error = f"inventory read failed: {type(error).__name__}"
    evidence = evaluate(
        inventory, set(args.labels), args.candidate_sha, api_error=api_error
    )
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"runner health: {evidence['verdict']} ({evidence['reason']})")
    return 0 if evidence["verdict"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
