from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path
from typing import Any


def packaged_hub_root() -> Path:
    return Path(str(resources.files("opaihub").joinpath("data", "hub"))).resolve()


def hub_root(start: Path | None = None) -> Path:
    configured = os.environ.get("OPAI_HUB_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    current = (start or Path.cwd()).resolve()
    for path in [current, *current.parents]:
        if (path / "hub").is_dir():
            return path / "hub"

    packaged = packaged_hub_root()
    if (packaged / "registry").is_dir():
        return packaged

    return Path(__file__).resolve().parents[1] / "hub"


def load_registry(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except Exception:
        return json.loads(text)


def registry_file(name: str, root: Path | None = None) -> Path:
    return hub_root(root) / "registry" / f"{name}.yaml"


def load_named_registry(name: str, root: Path | None = None) -> Any:
    return load_registry(registry_file(name, root))


def registry_items(name: str, root: Path | None = None) -> list[dict[str, Any]]:
    data = load_named_registry(name, root)
    if isinstance(data, dict):
        for key in ["tools", "agents", "workflows", "mcp_servers", "models"]:
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return data
    return []
