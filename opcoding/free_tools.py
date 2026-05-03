from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .utils import command_exists, project_op_dir, run_command, write_json


PYTHON_TOOLS = ["ruff", "bandit", "pip-audit", "detect-secrets"]
NODE_TOOLS = ["pyright", "markdownlint-cli2", "prettier", "@biomejs/biome"]
GO_TOOLS = [
    "github.com/google/osv-scanner/cmd/osv-scanner@latest",
    "github.com/zricethezav/gitleaks/v8@latest",
    "github.com/rhysd/actionlint/cmd/actionlint@latest",
]


def tools_root(root: Path) -> Path:
    path = root / ".opcoding-tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def python_bin(root: Path, exe: str) -> Path:
    return tools_root(root) / "python" / ("Scripts" if os.name == "nt" else "bin") / exe


def node_bin(root: Path, exe: str) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return tools_root(root) / "node" / "node_modules" / ".bin" / f"{exe}{suffix}"


def go_bin(root: Path, exe: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return tools_root(root) / "go" / "bin" / f"{exe}{suffix}"


def _python_exe(root: Path) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return python_bin(root, f"python{suffix}")


def install_python_tools(root: Path, timeout: int = 600) -> dict[str, Any]:
    venv = tools_root(root) / "python"
    if not _python_exe(root).exists():
        created = run_command(f'python -m venv "{venv}"', root, timeout=timeout)
        if created.returncode != 0:
            return {
                "ok": False,
                "stage": "venv",
                "output": created.combined_output[-4000:],
            }
    pip = python_bin(root, "pip.exe" if os.name == "nt" else "pip")
    cmd = f'"{pip}" install --upgrade {" ".join(PYTHON_TOOLS)}'
    result = run_command(cmd, root, timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "stage": "python",
        "command": cmd,
        "output_tail": result.combined_output[-4000:],
    }


def install_node_tools(root: Path, timeout: int = 600) -> dict[str, Any]:
    prefix = tools_root(root) / "node"
    prefix.mkdir(parents=True, exist_ok=True)
    package_json = prefix / "package.json"
    if not package_json.exists():
        package_json.write_text(
            '{"private":true,"name":"opcoding-tools"}\n', encoding="utf-8"
        )
    cmd = f'npm install --prefix "{prefix}" --save-dev {" ".join(NODE_TOOLS)}'
    result = run_command(cmd, root, timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "stage": "node",
        "command": cmd,
        "output_tail": result.combined_output[-4000:],
    }


def install_go_tools(root: Path, timeout: int = 900) -> dict[str, Any]:
    if not command_exists("go"):
        return {"ok": False, "stage": "go", "output_tail": "go is not installed"}
    gobin = tools_root(root) / "go" / "bin"
    gobin.mkdir(parents=True, exist_ok=True)
    results = []
    for tool in GO_TOOLS:
        cmd = f'go install "{tool}"'
        env_prefix = (
            f'set "GOBIN={gobin}" && ' if os.name == "nt" else f'GOBIN="{gobin}" '
        )
        result = run_command(env_prefix + cmd, root, timeout=timeout)
        results.append(
            {
                "tool": tool,
                "ok": result.returncode == 0,
                "output_tail": result.combined_output[-2000:],
            }
        )
    return {
        "ok": all(item["ok"] for item in results),
        "stage": "go",
        "results": results,
    }


def install_tools(root: Path, tool_set: str = "core") -> dict[str, Any]:
    results = []
    if tool_set in {"core", "all"}:
        results.append(install_python_tools(root))
        results.append(install_node_tools(root))
    if tool_set in {"security", "all"}:
        results.append(install_go_tools(root))
    payload = {
        "tool_set": tool_set,
        "results": results,
        "ok": all(item.get("ok") for item in results),
    }
    write_json(project_op_dir(root) / "cache" / "tools-install.json", payload)
    return payload


def tools_doctor(root: Path) -> dict[str, Any]:
    checks = {
        "python_venv": _python_exe(root).exists(),
        "ruff": python_bin(root, "ruff.exe" if os.name == "nt" else "ruff").exists(),
        "bandit": python_bin(
            root, "bandit.exe" if os.name == "nt" else "bandit"
        ).exists(),
        "pip-audit": python_bin(
            root, "pip-audit.exe" if os.name == "nt" else "pip-audit"
        ).exists(),
        "detect-secrets": python_bin(
            root, "detect-secrets.exe" if os.name == "nt" else "detect-secrets"
        ).exists(),
        "pyright": node_bin(root, "pyright").exists(),
        "markdownlint-cli2": node_bin(root, "markdownlint-cli2").exists(),
        "prettier": node_bin(root, "prettier").exists(),
        "biome": node_bin(root, "biome").exists(),
        "osv-scanner": go_bin(root, "osv-scanner").exists(),
        "gitleaks": go_bin(root, "gitleaks").exists(),
        "actionlint": go_bin(root, "actionlint").exists(),
    }
    return {
        "root": str(tools_root(root)),
        "installed": checks,
        "policy": "free/local by default; network-heavy vulnerability/link checks are opt-in",
    }


def _workflow_files(root: Path) -> list[str]:
    workflows = root / ".github" / "workflows"
    if not workflows.exists():
        return []
    files = sorted(
        {
            path
            for pattern in ("*.yml", "*.yaml")
            for path in workflows.glob(pattern)
            if path.is_file()
        }
    )
    return [str(path.relative_to(root)) for path in files]


def run_tool(root: Path, name: str, timeout: int = 300) -> dict[str, Any]:
    actionlint_files = _workflow_files(root) if name == "actionlint" else []
    if name == "actionlint" and not actionlint_files:
        return {
            "ok": True,
            "tool": name,
            "command": "actionlint",
            "returncode": 0,
            "output_tail": "skipped: no GitHub Actions workflow files in this project",
        }
    commands = {
        "ruff": [
            str(python_bin(root, "ruff.exe" if os.name == "nt" else "ruff")),
            "check",
            ".",
        ],
        "ruff-format": [
            str(python_bin(root, "ruff.exe" if os.name == "nt" else "ruff")),
            "format",
            "--check",
            ".",
        ],
        "bandit": [
            str(python_bin(root, "bandit.exe" if os.name == "nt" else "bandit")),
            "-r",
            "opai",
            "opaihub",
            "opcoding",
            "-q",
        ],
        "pip-audit": [
            str(python_bin(root, "pip-audit.exe" if os.name == "nt" else "pip-audit")),
            ".",
            "--progress-spinner",
            "off",
            "--skip-editable",
        ],
        "detect-secrets": [
            str(
                python_bin(
                    root, "detect-secrets.exe" if os.name == "nt" else "detect-secrets"
                )
            ),
            "scan",
            "--all-files",
            "--exclude-files",
            r"(^|[\\/])\.opcoding-tools([\\/]|$)|(^|[\\/])\.ruff_cache([\\/]|$)|(^|[\\/])\.opcoding([\\/]|$)|(^|[\\/])\.opaihub([\\/]|$)|(^|[\\/])opai[\\/]assets[\\/].*\.png$",
            "--exclude-lines",
            "MORPH_API_KEY|api_key_env|api_key_present",
        ],
        "pyright": [str(node_bin(root, "pyright")), "opcoding"],
        "markdownlint": [
            str(node_bin(root, "markdownlint-cli2")),
            "**/*.md",
            "!build/**",
            "!dist/**",
        ],
        "prettier": [str(node_bin(root, "prettier")), "--check", "."],
        "biome": [str(node_bin(root, "biome")), "check", "."],
        "gitleaks": [
            str(go_bin(root, "gitleaks")),
            "detect",
            "--source",
            ".",
            "--no-banner",
        ],
        "actionlint": [str(go_bin(root, "actionlint")), *actionlint_files],
        "osv-scanner": [str(go_bin(root, "osv-scanner")), "scan", "."],
    }
    command = commands.get(name)
    if not command:
        return {
            "ok": False,
            "tool": name,
            "error": f"unknown tool: {name}",
            "available": sorted(commands),
        }
    result = run_command(command, root, timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "tool": name,
        "command": command,
        "returncode": result.returncode,
        "output_tail": result.combined_output[-6000:],
    }
