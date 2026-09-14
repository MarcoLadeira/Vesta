"""Versioned coding-workflow templates bundled with Vesta."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _template_path() -> Path:
    return (
        Path(__file__).resolve().parent
        / "data"
        / "hub"
        / "workflows"
        / "coding-agent.json"
    )


def workflow_templates() -> dict[str, dict[str, Any]]:
    data = json.loads(_template_path().read_text(encoding="utf-8"))
    return {str(item["id"]): item for item in data.get("workflows") or []}
