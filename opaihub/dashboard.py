from __future__ import annotations

from pathlib import Path

from .health import health_all
from .loader import registry_items
from .local_models import discover_local_models
from .state import effective_mcp_servers, effective_tools, load_state, state_dir


def build_dashboard(project_root: Path) -> Path:
    state = load_state(project_root)
    tools = effective_tools(project_root)
    mcps = effective_mcp_servers(project_root)
    health = health_all(project_root)
    local_models = discover_local_models(project_root)
    lines = [
        f"# OP AI Hub Dashboard: {project_root.name}",
        "",
        "## Registries",
        "",
        f"- Tools: {len(registry_items('tools', project_root))}",
        f"- Agents: {len(registry_items('agents', project_root))}",
        f"- Workflows: {len(registry_items('workflows', project_root))}",
        f"- MCP servers: {len(registry_items('mcp_servers', project_root))}",
        f"- Models: {len(registry_items('models', project_root))}",
        "",
        "## Project Overlay",
        "",
        f"- State file: `{state_dir(project_root) / 'project.json'}`",
        f"- Enabled tools: {', '.join(state.get('enabled_tools', [])) or 'none'}",
        f"- Disabled tools: {', '.join(state.get('disabled_tools', [])) or 'none'}",
        f"- Enabled MCP: {', '.join(state.get('enabled_mcp_servers', [])) or 'none'}",
        f"- Disabled MCP: {', '.join(state.get('disabled_mcp_servers', [])) or 'none'}",
        "",
        "## Effective Tools",
        "",
    ]
    lines.extend(
        f"- {tool['id']}: {'enabled' if tool['effective_enabled'] else 'disabled'}"
        for tool in tools
    )
    lines.extend(["", "## Effective MCP", ""])
    lines.extend(
        f"- {server['id']}: {'enabled' if server['effective_enabled'] else 'disabled'}"
        for server in mcps
    )
    lines.extend(["", "## Local Models", ""])
    lines.append(f"- Available: {local_models['available']}")
    if local_models["commands"]:
        lines.extend(
            f"- {name}: `{path}`" for name, path in local_models["commands"].items()
        )
    lines.extend(["", "## Health Summary", ""])
    for item in health:
        lines.append(f"- {item['id']}: {item['status']}")
    path = state_dir(project_root) / "dashboard.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
