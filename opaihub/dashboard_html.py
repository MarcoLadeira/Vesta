from __future__ import annotations

from html import escape
from pathlib import Path

from .analytics import build_analytics_summary
from .state import state_dir


def build_dashboard_html(project_root: Path) -> Path:
    summary = build_analytics_summary(project_root)
    counts = summary["registry_counts"]
    enabled = summary["effective_enabled"]
    count_cards = "\n".join(
        f"<li><strong>{escape(name)}</strong><span>{value}</span></li>"
        for name, value in counts.items()
    )
    tool_items = (
        "\n".join(f"<li>{escape(tool)}</li>" for tool in enabled["tools"])
        or "<li>none</li>"
    )
    mcp_items = (
        "\n".join(f"<li>{escape(server)}</li>" for server in enabled["mcp_servers"])
        or "<li>none</li>"
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OPai Dashboard</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #17202a; background: #f5f7fa; }}
    header {{ background: #111827; color: white; padding: 28px 32px; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 28px; }}
    h1, h2 {{ margin: 0 0 12px; letter-spacing: 0; }}
    section {{ margin: 0 0 24px; }}
    ul.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; padding: 0; }}
    .cards li {{ list-style: none; background: white; border: 1px solid #d7dee8; border-radius: 8px; padding: 14px; }}
    .cards span {{ display: block; font-size: 28px; margin-top: 8px; }}
    .panel {{ background: white; border: 1px solid #d7dee8; border-radius: 8px; padding: 16px; }}
    code {{ background: #edf2f7; padding: 2px 6px; border-radius: 5px; }}
  </style>
</head>
<body>
  <header>
    <h1>OPai 0.2.0 alpha.1</h1>
    <p>Local-first AI tools hub for low-cost coding, automation, and project workflows.</p>
  </header>
  <main>
    <section>
      <h2>Registry Counts</h2>
      <ul class="cards">{count_cards}</ul>
    </section>
    <section class="panel">
      <h2>Enabled Tools</h2>
      <ul>{tool_items}</ul>
    </section>
    <section class="panel">
      <h2>Enabled MCP Servers</h2>
      <ul>{mcp_items}</ul>
    </section>
    <section class="panel">
      <h2>Cost Posture</h2>
      <p>Estimated spend: <code>${summary["estimated_spend_usd"]:.2f}</code>. Telemetry: local only.</p>
    </section>
  </main>
</body>
</html>
"""
    path = state_dir(project_root) / "dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
