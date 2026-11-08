"""
Terminal & Interactive HTML Dashboard for Antigravity Token Optimizer.
Provides colorful CLI reports, Markdown exports, and rich modern HTML widgets.
"""

import sys
import os
import json
from datetime import datetime
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.metrics import MetricsTracker

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
DIM = "\033[2m"

def render_bar(percentage: float, width: int = 24) -> str:
    """Renders a visual ASCII progress bar."""
    filled = int((percentage / 100.0) * width)
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percentage:.1f}%"

EVENT_NAMES = {
    "PROMPT": "Prompt Compactor (TOCP)",
    "TOOL_READ": "File Line Slicer (AST)",
    "AST_SKELETON": "AST Skeletonizer (Signatures)",
    "GRAPH_SKELETON": "Graph-Guided Slicer (Call-Graph)",
    "GRAPH_TOPOLOGY": "Architecture Graph (God Nodes)",
    "COMMAND_FILTER": "Command Sanitizer (CLI)",
    "OUTPUT_GUARD": "Output Token Guard (write_file)",
    "LOOP_GUARD": "Anti-Stuck Loop Guard",
    "HISTORY_FOLD": "Multi-turn History Fold",
    "COMMAND_OUTPUT": "Terminal Log Sanitizer"
}

class Dashboard:
    """Generates visual terminal, markdown, and interactive HTML dashboards."""

    def __init__(self, tracker: MetricsTracker):
        self.tracker = tracker

    def render_terminal(self) -> str:
        """Returns styled ANSI string for terminal display."""
        summary = self.tracker.get_summary()
        recent = self.tracker.get_recent_events(limit=6)

        total_saved = summary["total_saved_tokens"]
        pct = summary["overall_reduction_pct"]
        usd = summary["total_usd_saved"]
        orig = summary["total_original_tokens"]
        opt = summary["total_optimized_tokens"]
        events_count = summary["total_events"]

        out = []
        out.append(f"\n{BOLD}{CYAN}╔════════════════════════════════════════════════════════════════════╗{RESET}")
        out.append(f"{BOLD}{CYAN}║           ⚡ ANTIGRAVITY TOKEN OPTIMIZER DASHBOARD ⚡            ║{RESET}")
        out.append(f"{BOLD}{CYAN}╚════════════════════════════════════════════════════════════════════╝{RESET}\n")

        # Top summary cards
        out.append(f"  {BOLD}Total Tokens Saved:{RESET}  {GREEN}{BOLD}{total_saved:,}{RESET} tokens")
        out.append(f"  {BOLD}Efficiency Gain:{RESET}     {YELLOW}{BOLD}{render_bar(pct)}{RESET}")
        out.append(f"  {BOLD}Estimated Savings:{RESET}   {GREEN}${usd:.4f} USD{RESET}")
        out.append(f"  {BOLD}Context Traffic:{RESET}     {orig:,} raw ➔ {opt:,} optimized ({events_count} events)\n")

        # Breakdown by category
        out.append(f"{BOLD}── Breakdown by Source ──────────────────────────────────────────────{RESET}")
        breakdown = summary.get("breakdown", {})
        if not breakdown:
            out.append(f"  {DIM}No events recorded yet. Run a prompt to see live optimization.{RESET}\n")
        else:
            for source, data in breakdown.items():
                s_name = EVENT_NAMES.get(source, source)
                out.append(f"  • {BOLD}{s_name:<32}{RESET} : {GREEN}{data['saved']:,} tokens saved{RESET} ({data['count']} events)")
            out.append("")

        # Recent events log
        out.append(f"{BOLD}── Recent Optimizations ─────────────────────────────────────────────{RESET}")
        if not recent:
            out.append(f"  {DIM}No activity recorded yet.{RESET}")
        else:
            for ev in recent:
                t_str = ev["timestamp"].split("T")[1][:8] if "T" in ev["timestamp"] else ev["timestamp"]
                ev_type = ev["event_type"]
                saved = ev["saved_tokens"]
                pct_ev = ev["reduction_pct"]
                details = ev.get("details", {})
                detail_desc = details.get("action", "") or details.get("target_file", "") or ""
                if detail_desc:
                    detail_desc = f"({detail_desc})"

                out.append(f"  [{DIM}{t_str}{RESET}] {CYAN}{ev_type:<15}{RESET} {detail_desc:<20} ➔ {GREEN}+{saved:,} saved{RESET} ({pct_ev:.1f}%)")

        out.append(f"\n{DIM}Run `agy-tokens test \"<prompt>\"` to test compression on custom input.{RESET}")
        out.append(f"{DIM}Run `agy-tokens html` to view interactive browser dashboard.{RESET}\n")
        return "\n".join(out)

    def render_markdown(self) -> str:
        """Returns GitHub-flavored Markdown report."""
        summary = self.tracker.get_summary()
        recent = self.tracker.get_recent_events(limit=10)

        total_saved = summary["total_saved_tokens"]
        pct = summary["overall_reduction_pct"]
        usd = summary["total_usd_saved"]
        orig = summary["total_original_tokens"]
        opt = summary["total_optimized_tokens"]

        md = []
        md.append("# ⚡ Antigravity Token Optimizer Report\n")
        md.append(f"> **Cumulative Resource Efficiency:** **{pct:.1f}% Reduction** across {summary['total_events']} events.\n")
        md.append("| Metric | Value |")
        md.append("| :--- | :--- |")
        md.append(f"| **Total Tokens Saved** | **{total_saved:,} tokens** |")
        md.append(f"| **Context Ratio** | {orig:,} raw ➔ {opt:,} optimized |")
        md.append(f"| **Net Efficiency Gain** | `{render_bar(pct)}` |")
        md.append(f"| **Estimated Value Saved** | **${usd:.4f} USD** |\n")

        md.append("### Source Breakdown\n")
        md.append("| Pipeline Stage | Events | Tokens Saved |")
        md.append("| :--- | :--- | :--- |")
        for source, data in summary.get("breakdown", {}).items():
            s_name = EVENT_NAMES.get(source, source)
            md.append(f"| **{s_name}** | {data['count']} | `{data['saved']:,}` |")

        md.append("\n### Recent Optimization Events\n")
        md.append("| Timestamp | Event Type | Scope | Tokens Before ➔ After | Saved |")
        md.append("| :--- | :--- | :--- | :--- | :--- |")
        for ev in recent:
            t_str = ev["timestamp"].split("T")[1][:8] if "T" in ev["timestamp"] else ev["timestamp"]
            details = ev.get("details", {})
            scope = details.get("target_file") or details.get("action") or "General"
            md.append(f"| `{t_str}` | `{ev['event_type']}` | `{scope}` | {ev['original_tokens']} ➔ {ev['optimized_tokens']} | **+{ev['saved_tokens']:,} (-{ev['reduction_pct']}%)** |")

        return "\n".join(md)

    def render_html(self) -> str:
        """Returns modern, Apple-style interactive HTML dashboard."""
        summary = self.tracker.get_summary()
        recent = self.tracker.get_recent_events(limit=15)

        total_saved = summary["total_saved_tokens"]
        pct = summary["overall_reduction_pct"]
        usd = summary["total_usd_saved"]
        orig = summary["total_original_tokens"]
        opt = summary["total_optimized_tokens"]
        events_count = summary["total_events"]

        breakdown_rows = ""
        for source, data in summary.get("breakdown", {}).items():
            s_name = EVENT_NAMES.get(source, source)
            s_pct = round((data['saved'] / total_saved * 100.0), 1) if total_saved > 0 else 0
            breakdown_rows += f"""
            <div class="source-card">
              <div class="source-header">
                <span class="source-name">{s_name}</span>
                <span class="source-saved">+{data['saved']:,} tokens</span>
              </div>
              <div class="progress-track">
                <div class="progress-fill" style="width: {s_pct}%;"></div>
              </div>
              <div class="source-footer">
                <span>{data['count']} events</span>
                <span>{s_pct}% of total savings</span>
              </div>
            </div>
            """

        recent_rows = ""
        for ev in recent:
            t_str = ev["timestamp"].split("T")[1][:8] if "T" in ev["timestamp"] else ev["timestamp"]
            details = ev.get("details", {})
            scope = details.get("target_file") or details.get("action") or ev["event_type"]
            recent_rows += f"""
            <tr>
              <td><span class="badge time">{t_str}</span></td>
              <td><span class="badge type">{ev['event_type']}</span></td>
              <td class="scope-cell">{scope}</td>
              <td class="tokens-cell">{ev['original_tokens']:,} ➔ {ev['optimized_tokens']:,}</td>
              <td class="saved-cell">+{ev['saved_tokens']:,} (-{ev['reduction_pct']}%)</td>
            </tr>
            """

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>⚡ Antigravity Token Optimizer Dashboard</title>
  <style>
    :root {{
      --bg: #090a0f;
      --card-bg: rgba(22, 27, 34, 0.7);
      --border: rgba(255, 255, 255, 0.08);
      --accent: #00f2fe;
      --accent-grad: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
      --green: #10b981;
      --text: #f3f4f6;
      --text-dim: #9ca3af;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding: 40px 20px; display: flex; justify-content: center; }}
    .container {{ width: 100%; max-width: 1000px; }}
    .header {{ margin-bottom: 32px; display: flex; justify-content: space-between; align-items: flex-end; }}
    .title {{ font-size: 28px; font-weight: 700; background: var(--accent-grad); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
    .subtitle {{ color: var(--text-dim); font-size: 14px; margin-top: 4px; }}
    
    .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 32px; }}
    .stat-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 16px; padding: 20px; backdrop-filter: blur(12px); }}
    .stat-label {{ font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin-bottom: 8px; }}
    .stat-value {{ font-size: 32px; font-weight: 800; color: #fff; }}
    .stat-value.highlight {{ color: var(--green); }}

    .section-title {{ font-size: 18px; font-weight: 700; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }}
    .sources-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 32px; }}
    .source-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 14px; padding: 18px; }}
    .source-header {{ display: flex; justify-content: space-between; font-weight: 600; font-size: 14px; margin-bottom: 10px; }}
    .source-saved {{ color: var(--green); }}
    .progress-track {{ height: 8px; background: rgba(255,255,255,0.06); border-radius: 4px; overflow: hidden; margin-bottom: 8px; }}
    .progress-fill {{ height: 100%; background: var(--accent-grad); border-radius: 4px; }}
    .source-footer {{ display: flex; justify-content: space-between; font-size: 12px; color: var(--text-dim); }}

    .table-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }}
    th {{ background: rgba(255,255,255,0.03); padding: 14px 18px; color: var(--text-dim); font-weight: 600; border-bottom: 1px solid var(--border); }}
    td {{ padding: 14px 18px; border-bottom: 1px solid var(--border); }}
    tr:last-child td {{ border-bottom: none; }}
    .badge {{ padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; font-family: monospace; }}
    .badge.time {{ background: rgba(255,255,255,0.06); color: var(--text-dim); }}
    .badge.type {{ background: rgba(79, 172, 254, 0.15); color: #4facfe; }}
    .scope-cell {{ font-weight: 500; }}
    .tokens-cell {{ color: var(--text-dim); font-family: monospace; }}
    .saved-cell {{ color: var(--green); font-weight: 600; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div>
        <h1 class="title">⚡ Antigravity Token Optimizer</h1>
        <p class="subtitle">Next-gen Autonomous Token Economy & Context Preservation</p>
      </div>
      <div class="subtitle">Live SQLite Tracking</div>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">Total Tokens Saved</div>
        <div class="stat-value highlight">{total_saved:,}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Net Efficiency Gain</div>
        <div class="stat-value">{pct:.1f}%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Estimated Value Saved</div>
        <div class="stat-value highlight">${usd:.4f}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Context Traffic</div>
        <div class="stat-value" style="font-size: 20px; line-height: 38px;">{orig:,} ➔ {opt:,}</div>
      </div>
    </div>

    <h2 class="section-title">Pipeline Breakdown</h2>
    <div class="sources-grid">
      {breakdown_rows}
    </div>

    <h2 class="section-title">Recent Optimization Stream</h2>
    <div class="table-card">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Stage</th>
            <th>Target Scope</th>
            <th>Context Window</th>
            <th>Savings</th>
          </tr>
        </thead>
        <tbody>
          {recent_rows}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
        return html

def main():
    tracker = MetricsTracker()
    dash = Dashboard(tracker)
    if "--html" in sys.argv:
        print(dash.render_html())
    elif "--markdown" in sys.argv or "--md" in sys.argv:
        print(dash.render_markdown())
    else:
        print(dash.render_terminal())

if __name__ == "__main__":
    main()
