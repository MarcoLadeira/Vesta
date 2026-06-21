from __future__ import annotations

from html import escape
from pathlib import Path

from .analytics import build_analytics_summary
from .benchmark import latest_benchmark_report
from .context_engine import profile_context
from .state import state_dir


def _items(items: list[str]) -> str:
    if not items:
        return "<li>none</li>"
    return "\n".join(f"<li>{escape(item)}</li>" for item in items)


def _money(value: object) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def build_dashboard_html(project_root: Path) -> Path:
    from opai.cockpit import build_cockpit

    root = project_root.expanduser().resolve()
    summary = build_analytics_summary(project_root)
    cockpit = build_cockpit(root)
    context = profile_context(root)
    benchmark = latest_benchmark_report(root)
    counts = summary["registry_counts"]
    enabled = summary["effective_enabled"]
    clients = cockpit["clients"]
    savings = cockpit["savings"]
    budget = cockpit["budget"]
    local = cockpit["local_models"]
    bench_claim = cockpit["benchmark"]["claim"]
    count_cards = "\n".join(
        f"<li><strong>{escape(name)}</strong><span>{value}</span></li>"
        for name, value in counts.items()
    )
    savings_empty = (
        '<p class="empty">No real routed tasks recorded yet. '
        'Run <code>opai route "&lt;task&gt;" --record</code> or '
        "<code>opai quickstart</code>.</p>"
        if not savings["has_data"]
        else ""
    )
    benchmark_status = (
        f"Latest run: <code>{escape(str(benchmark.get('run_id')))}</code>"
        if benchmark
        else "No benchmark history yet. Run <code>opai benchmark run --suite max --mode both</code>."
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OPai Dashboard</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #1b2430; background: #f7f8f5; }}
    header {{ background: #173b35; color: white; padding: 28px 32px; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
    h1, h2 {{ margin: 0 0 12px; letter-spacing: 0; }}
    section {{ margin: 0 0 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }}
    .wide {{ grid-column: 1 / -1; }}
    .panel {{ background: white; border: 1px solid #d8ded6; border-radius: 8px; padding: 16px; }}
    ul.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; padding: 0; }}
    .cards li {{ list-style: none; background: #fbfcfa; border: 1px solid #d8ded6; border-radius: 8px; padding: 12px; }}
    .cards span {{ display: block; font-size: 26px; margin-top: 8px; }}
    code {{ background: #eef2eb; padding: 2px 6px; border-radius: 5px; }}
    .ok {{ color: #0f6b45; font-weight: 700; }}
    .warn {{ color: #8a4d00; font-weight: 700; }}
    .empty {{ color: #6b5d2e; background: #fff7d6; border: 1px solid #ead68a; border-radius: 8px; padding: 10px; }}
    .muted {{ color: #647066; }}
  </style>
</head>
<body>
  <header>
    <h1>OPai Control Center</h1>
    <p>OPai is <strong>{escape(cockpit["status"].upper())}</strong> for <code>{escape(root.name)}</code>.</p>
  </header>
  <main>
    <section class="grid">
      <div class="panel">
        <h2>Activation</h2>
        <p class="{"ok" if cockpit["status"] == "on" else "warn"}">OPai {escape(cockpit["status"].upper())}</p>
        <p>Version <code>{escape(cockpit["version"])}</code> {escape(cockpit["release_stage"])}</p>
        <p class="muted">{escape(cockpit["project"]["root"])}</p>
      </div>
      <div class="panel">
        <h2>Clients</h2>
        <p><strong>{clients["active"]}/{clients["total"]}</strong> active</p>
        <p>Active: {escape(", ".join(clients["summary"]["active"]) or "none")}</p>
        <p>Missing: {escape(", ".join(clients["summary"]["missing"]) or "none")}</p>
      </div>
      <div class="panel">
        <h2>Savings Ledger</h2>
        <p><strong>{_money(savings["estimated_savings_usd"])}</strong> estimated saved</p>
        <p>{savings["routed_tasks"]} routed tasks, {savings["cloud_calls_avoided"]} cloud calls avoided</p>
        {savings_empty}
      </div>
      <div class="panel">
        <h2>Budget Firewall</h2>
        <p class="{"warn" if budget["panic"] else "ok"}">{"Panic mode ON" if budget["panic"] else "Budget ok"}</p>
        <p>Profile: <code>{escape(budget["profile"])}</code></p>
        <p>Today: {_money(budget["spent"]["today_usd"])} / cap {_money(budget["caps"]["daily_usd_limit"]) if budget["caps"]["daily_usd_limit"] is not None else "none"}</p>
      </div>
      <div class="panel">
        <h2>Context Waste</h2>
        <p>{context["waste_bytes"]:,} bytes removable ({context["waste_share"] * 100:.1f}%)</p>
        <p>Estimated wasted tokens: {context["estimated_tokens_wasted"]:,}</p>
        <p><code>opai context profile --markdown</code></p>
      </div>
      <div class="panel">
        <h2>Benchmark Proof</h2>
        <p>{bench_claim}</p>
        <p>{benchmark_status}</p>
      </div>
      <div class="panel">
        <h2>Proof Bundle</h2>
        <p>Private signed bundle for pilots and buyers.</p>
        <p><code>opai proof bundle --markdown</code></p>
      </div>
      <div class="panel">
        <h2>Local Model / Ask</h2>
        <p class="{"ok" if local["available"] else "warn"}">{"Local model available" if local["available"] else "No local model running"}</p>
        <p><code>opai ask "summarize my changes"</code></p>
      </div>
      <div class="panel">
        <h2>Launch Readiness</h2>
        <p>Controlled alpha: replace private checkout/form/analytics placeholders before hosting.</p>
        <p><code>npx wrangler pages deploy site --project-name opai --branch main</code></p>
      </div>
    </section>
    <section class="panel wide">
      <h2>Registry Counts</h2>
      <ul class="cards">{count_cards}</ul>
    </section>
    <section class="panel wide">
      <h2>Enabled Tools</h2>
      <ul>{_items(enabled["tools"])}</ul>
    </section>
    <section class="panel wide">
      <h2>Enabled MCP Servers</h2>
      <ul>{_items(enabled["mcp_servers"])}</ul>
    </section>
  </main>
</body>
</html>
"""
    path = state_dir(project_root) / "dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
