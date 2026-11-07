"""
Terminal Dashboard & Metrics Visualizer for Antigravity Token Optimizer.
Provides colorful CLI reports, ASCII progress bars, and Markdown exports.
"""

import sys
from datetime import datetime
from typing import Dict, Any, List
from src.metrics import MetricsTracker

# ANSI color codes for terminal display
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

class Dashboard:
    """Generates visual terminal and markdown dashboards."""

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
                s_name = {
                    "PROMPT": "Prompt Compactor (TOCP)",
                    "TOOL_READ": "File Tool Slicer (AST)",
                    "COMMAND_OUTPUT": "Terminal Log Sanitizer"
                }.get(source, source)
                out.append(f"  • {BOLD}{s_name:<28}{RESET} : {GREEN}{data['saved']:,} tokens saved{RESET} ({data['count']} events)")
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

                out.append(f"  [{DIM}{t_str}{RESET}] {CYAN}{ev_type:<14}{RESET} {detail_desc:<20} ➔ {GREEN}+{saved:,} saved{RESET} ({pct_ev:.1f}%)")

        out.append(f"\n{DIM}Run `agy-tokens test \"<prompt>\"` to test compression on custom input.{RESET}\n")
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
            s_name = {
                "PROMPT": "Prompt Compactor (TOCP)",
                "TOOL_READ": "File Tool Slicer (AST)",
                "COMMAND_OUTPUT": "Terminal Log Sanitizer"
            }.get(source, source)
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
