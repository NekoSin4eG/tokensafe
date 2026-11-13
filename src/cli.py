#!/usr/bin/env python3
"""
TokenSafe Unified CLI Dispatcher.
Used by:
- npm CLI (`bin/tokensafe.js`)
- standalone executable (`bin/tokensafe`)
- PyPI console script entrypoint (`tokensafe` via `pyproject.toml`)
"""

import sys
import os
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.metrics import MetricsTracker
from src.dashboard import Dashboard, render_bar
from src.compactor import PromptCompactor
from src.tokenizer import count_tokens

def summarize_unittest_output(raw: str, full_traceback: bool = False) -> str:
    """Compacts `python3 -m unittest discover` output to what a developer
    actually needs: one green line, or per-failure test name + root cause.
    Keeps the suite run out of the context window (the raw output of a
    300+ test run with tracebacks is thousands of tokens)."""
    lines = raw.splitlines()
    ran = status = ""
    for ln in lines:
        s = ln.strip()
        if s.startswith("Ran ") and " test" in s:
            ran = s
        elif s.startswith("OK") or s.startswith("FAILED"):
            # "OK (skipped=N)" must count as success too, not just bare "OK".
            status = s
    # Failure blocks: one '=' * 70 line PER failure, then the title line,
    # a '-' * 70 rule and the traceback — each block is self-contained.
    blocks, cur = [], None
    for ln in lines:
        if len(ln) >= 10 and set(ln) == {"="}:
            if cur is not None:
                blocks.append(cur)
            cur = []
            continue
        if cur is not None:
            cur.append(ln)
    if cur is not None:
        blocks.append(cur)

    failures = []
    for block in blocks:
        title_idx = next((j for j, l in enumerate(block) if l.startswith(("FAIL: ", "ERROR: "))), None)
        if title_idx is None:
            continue
        title = block[title_idx]
        body = [l for l in block[title_idx + 1:]
                if not (len(l) >= 10 and set(l) == {"-"})
                and not l.strip().startswith(("Ran ", "FAILED", "OK"))]
        kind, name = title.split(" ", 1)
        if full_traceback:
            detail = [l.rstrip() for l in body if l.strip()]
        else:
            loc = next((l.strip() for l in reversed(body) if l.strip().startswith('File "')), "")
            cause_lines = [l.strip() for l in reversed(body)
                           if l.strip() and not l.strip().startswith('File "')
                           and not l.strip().startswith(("self.", "assert", "raise"))]
            # assertEqual-style failures end with a diff block ('- a' / '+ b');
            # the actual AssertionError line sits above it.
            cause = next((l for l in cause_lines if not l.startswith(("+", "-", "?", "!"))),
                         cause_lines[0] if cause_lines else "")
            if len(cause) > 200:
                cause = cause[:200] + " …"
            detail = [l for l in (loc, cause) if l]
        failures.append((kind.rstrip(": "), name, detail))

    if not ran and not failures:
        # Crash before unittest could report (import error, bad path):
        # fall back to the tail, which carries the exception.
        tail = [l for l in lines if l.strip()][-15:]
        return "\n".join(tail) if tail else "(no unittest output captured)"

    if status.startswith("OK"):
        return f"\033[32m✓ {ran} — all tests passed\033[0m"

    out = [f"\033[31m✗ {ran} — {status}\033[0m"]
    for kind, name, detail in failures:
        out.append(f"  \033[31m{kind}\033[0m {name}")
        for d in detail:
            out.append(f"      {d}")
    out.append("Full output: python3 -m unittest discover tests  (or: tokensafe tests --traceback)")
    return "\n".join(out)


def _print_help():
    print("TokenSafe CLI — usage: tokensafe <command> [args]\n")
    print("Commands:")
    print("  stats / markdown / html        Savings analytics (terminal / .md report / dashboard)")
    print("  status                         One-screen health check: hooks, weekly savings, tier, sync")
    print("  benchmark                      Zero-context-loss benchmark suite (simulated SWE-bench harness)")
    print("  graph / map / skeleton         Architecture graph / API surface / AST skeleton of a file")
    print("  test <prompt>                  Preview TOCP S-expression prompt compaction")
    print("  digest [--send] [--email=...]  Compile (and optionally dispatch) the weekly team digest")
    print("  guard <command>                Pre-flight safety check for a shell command")
    print("  doctor                         Audit MCP tool overhead and token tax")
    print("  tests [pytest] [--traceback]   Run the unit test suite (compact failure report)")
    print("  prune [--days N | --max N]     Compact metrics database (roll old events)")
    print("  vacuum                         Reclaim SQLite disk space")
    print("  reset                          Reset all local token metrics")
    print("  install | setup [--all]        Configure detected agent environments")
    print("  update | upgrade               Self-update npm/PyPI package and re-sync agents")
    print("  check-update                   Check for a newer published version")
    print("  activate / license             License activation and status")
    print("  sync                           Sync telemetry with the cloud backend")
    print("  providers / proxy / mcp        Providers info / proxy server / MCP stdio server")
    print("  --version                      Print version and exit")


def main():
    args = sys.argv[1:]
    command = args[0] if args else "stats"

    if command in ("--help", "-h", "help"):
        _print_help()
        return

    # Fast path: version queries must not construct MetricsTracker (which creates
    # the data dir and SQLite database). `tokensafe --version` may run in read-only
    # or foreign directories and must stay side-effect free.
    if command in ("version", "--version", "-v"):
        from src.version_checker import CURRENT_VERSION, VersionChecker
        print(f"TokenSafe v{CURRENT_VERSION}")
        if "--check" in args or "-c" in args:
            banner = VersionChecker.get_banner()
            if banner:
                print(banner)
            else:
                print("\033[32m✔ TokenSafe is up to date.\033[0m")
            if "--notify" in args or "--prompt" in args:
                VersionChecker.notify_os_update(force=True)
        return

    if command in ("status", "health"):
        from src.installer import AgentInstaller
        from src.license_manager import LicenseManager
        from src.telemetry_sync import TelemetrySync
        tracker = MetricsTracker()
        print("\n\033[1m\033[36m⚡ TOKENSAFE STATUS ⚡\033[0m\n")
        # Hooks wiring (best-effort scan of agent config files and plugin dirs)
        active_agents = []
        for key, info in AgentInstaller.detect_available_agents().items():
            hit = any(info.get(pk) and os.path.exists(info[pk]) for pk in
                      ("target_path", "plugin_path", "skills_path", "agents_md_path", "rules_path", "extension_path"))
            cfg = info.get("config_path")
            if not hit and cfg and os.path.exists(cfg):
                try:
                    with open(cfg, "r", encoding="utf-8", errors="replace") as f:
                        hit = "tokensafe" in f.read().lower()
                except Exception:
                    pass
            if hit:
                active_agents.append(key)
        if active_agents:
            print(f"  Hooks:     \033[32m✔ active\033[0m in {len(active_agents)} agent(s): {', '.join(active_agents)}")
        else:
            print("  Hooks:     \033[33m○ not detected\033[0m — run \033[1mtokensafe install\033[0m to configure your agents")
        # The value moment: recent measured savings
        weekly = tracker.get_weekly_saved()
        print(f"  This Week: \033[32m\033[1m{weekly['saved_tokens']:,} tokens saved\033[0m across {weekly['events']:,} events (≈${weekly['usd_saved']:.2f})")
        # Tier + quota state
        tier, payload = LicenseManager.get_active_license()
        quota = LicenseManager.check_quota(tracker)
        if tier == "free":
            q_saved = int(quota.get("monthly_saved", 0))
            q_limit = int(quota.get("monthly_limit") or 500_000)
            q_pct = min(100, int(q_saved / q_limit * 100)) if q_limit else 0
            q_state = "\033[31mQUOTA REACHED — Pro optimizations paused\033[0m" if quota.get("is_quota_exceeded") else f"{q_pct}% of monthly measured quota"
            print(f"  Tier:      \033[33mCOMMUNITY (Free)\033[0m — {q_saved:,} / {q_limit:,} tokens ({q_state})")
            print("  Upgrade:   \033[36mhttps://tokensafe.dev/signup\033[0m ($9/mo Solo · 7-Day Team Trial)")
        else:
            print(f"  Tier:      \033[32m{tier.upper()}\033[0m (Unlimited)")
        # Cloud heartbeat (non-blocking; keeps the machine visible server-side)
        try:
            TelemetrySync.sync_background()
            pending = int(TelemetrySync.get_sync_status().get("pending_events", 0) or 0)
            if pending:
                print(f"  Cloud:     {pending:,} events queued for telemetry sync (background)")
            else:
                print("  Cloud:     \033[32m✔ telemetry up to date\033[0m")
        except Exception:
            pass
        # Distillation cache visibility (get_stats existed but was never surfaced anywhere)
        try:
            from src.distiller_cache import DistillationCache
            cstats = DistillationCache.get_stats()
            lookups = cstats["hits"] + cstats["misses"]
            if lookups:
                print(f"  Cache:     \033[32m{cstats['hit_ratio_pct']}% distillation cache hit ratio\033[0m ({cstats['hits']:,}/{lookups:,} lookups · {cstats['persistent_entries']:,} entries on disk)")
        except Exception:
            pass
        print()

    elif command in ("stats", "dashboard"):
        tracker = MetricsTracker()
        dashboard = Dashboard(tracker)
        try:
            from src.telemetry_sync import TelemetrySync
            TelemetrySync.sync_background()
        except Exception:
            pass
        print(dashboard.render_terminal())

    elif command in ("html", "web"):
        from src.paths import resolve_data_dir
        tracker = MetricsTracker()
        dashboard = Dashboard(tracker)
        html_content = dashboard.render_html()
        out_path = os.path.join(resolve_data_dir(), "dashboard.html")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"\n⚡ Interactive HTML Dashboard generated at: {out_path}")
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", out_path], check=False)
            elif os.name == "nt":
                os.startfile(out_path)  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", out_path], check=False)
            print("   Opened in default browser.")
        except Exception:
            try:
                import webbrowser
                webbrowser.open(f"file://{os.path.abspath(out_path)}")
            except Exception:
                pass

    elif command in ("benchmark", "bench"):
        from src.benchmark import BenchmarkSuite
        from src.paths import resolve_data_dir
        print("\n⚡ Running Zero-Loss SWE-bench Benchmark Suite across real-world scenarios...")
        data = BenchmarkSuite.run_all()
        print(BenchmarkSuite.render_report(data))
        out_md = os.path.join(resolve_data_dir(), "BENCHMARK_REPORT.md")
        BenchmarkSuite.export_markdown_report(data, out_md)
        print(f"📊 Publication-ready markdown report saved to: {out_md}\n")

    elif command in ("setup", "install"):
        from src.installer import AgentInstaller
        if "--all" in args or "-a" in args or "-y" in args or "--auto" in args:
            print("\n\033[1m\033[36m⚡ TOKENSAFE UNIVERSAL MULTI-AGENT INSTALLER (--all) ⚡\033[0m\n")
            res = AgentInstaller.install_all(force=True)
            print("Configured AI agent environments:")
            for k, r in res["agents"].items():
                status = "\033[32m✔\033[0m" if r["success"] else "\033[31m✖\033[0m"
                print(f"  {status} \033[1m{r['name']:<28}\033[0m: {r['message']}")
            print(f"\n\033[32m✔ Universal project rules injected into: {', '.join(res['project_rules'])}\033[0m\n")
            AgentInstaller.maybe_prompt_proxy_enable(context="install")
        elif "--project" in args:
            agents_plugin = None
            if "--agents-plugin" in args:
                idx = args.index("--agents-plugin")
                if idx + 1 < len(args):
                    agents_plugin = args[idx + 1]
            res = AgentInstaller.install_project(os.getcwd(), agents_plugin_path=agents_plugin)
            print(f"\n\033[32m✔ TokenSafe rules injected into project: {', '.join(res['created_rules'])}\033[0m\n")
        else:
            AgentInstaller.run_interactive()

    elif command in ("update", "upgrade"):
        from src.installer import AgentInstaller
        AgentInstaller.self_update()

    elif command in ("check-update", "check_update"):
        from src.version_checker import CURRENT_VERSION, VersionChecker
        print(f"\n\033[1m\033[36m⚡ CHECKING FOR TOKENSAFE UPDATES ⚡\033[0m\n")
        print(f"  Current version: v{CURRENT_VERSION}")
        info = VersionChecker.check_version(force_fetch=True)
        latest = info.get("latest_version", CURRENT_VERSION)
        avail = info.get("update_available", False)
        print(f"  Latest version:  v{latest}")
        banner = VersionChecker.get_banner()
        if banner:
            print(banner)
        else:
            print("\n  \033[32m✔ TokenSafe is up to date.\033[0m\n")
        if avail or "--notify" in args or "--prompt" in args or "-p" in args:
            print("  \033[36mTriggering native OS update dialog...\033[0m")
            VersionChecker.notify_os_update(force=True)

    elif command in ("guard", "security"):
        from src.guard import TokenSafeGuard
        sub = args[1] if len(args) > 1 else "help"
        if sub in ("audit", "scan"):
            target_path = args[2] if len(args) > 2 else os.getcwd()
            if not os.path.exists(target_path):
                print(f"\n\033[31m✖ Target path not found: {target_path}\033[0m\n")
                sys.exit(1)
            print(f"\n\033[1m\033[36m🛡️ TOKENSAFE SECURITY & HYGIENE AUDIT 🛡️\033[0m")
            print(f"  Target: {target_path}\n")
            violations = []
            files_scanned = 0
            code_exts = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java")
            if os.path.isfile(target_path):
                files_scanned = 1
                try:
                    with open(target_path, "r", encoding="utf-8", errors="replace") as fp:
                        content = fp.read()
                    res = TokenSafeGuard.inspect_file_write(target_path, content)
                    if res.blocked:
                        violations.append((os.path.basename(target_path), res.check_name, res.reason))
                except Exception:
                    pass
            else:
                for root, dirs, files in os.walk(target_path):
                    dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "dist", "build", ".next", ".venv", "venv", "__pycache__")]
                    for f in files:
                        if f.endswith(code_exts):
                            files_scanned += 1
                            full_p = os.path.join(root, f)
                            try:
                                with open(full_p, "r", encoding="utf-8", errors="replace") as fp:
                                    content = fp.read()
                                res = TokenSafeGuard.inspect_file_write(full_p, content)
                                if res.blocked:
                                    rel_p = os.path.relpath(full_p, target_path)
                                    violations.append((rel_p, res.check_name, res.reason))
                            except Exception:
                                pass
            if not violations:
                print(f"  \033[32m✔ All {files_scanned} files passed TokenSafe Guard audit cleanly! Zero secret fallbacks or SQLi.\033[0m\n")
            else:
                print(f"  \033[31m✖ Found {len(violations)} security violations in {files_scanned} files:\033[0m\n")
                for rel_p, check, reason in violations:
                    print(f"    \033[31m• [{check}]\033[0m {rel_p}: {reason}")
                print()
        elif sub in ("check", "test") and len(args) > 2:
            cmd_to_test = " ".join(args[2:])
            print(f"\n\033[1m\033[36m🛡️ TOKENSAFE COMMAND GUARD CHECK 🛡️\033[0m\n")
            print(f"  Command: \033[1m{cmd_to_test}\033[0m")
            res = TokenSafeGuard.inspect_command(cmd_to_test)
            if res.blocked:
                print(f"  Status:  \033[31m\033[1mBLOCKED\033[0m [{res.check_name}]")
                print(f"  Reason:  {res.reason}\n")
            elif res.warning_only:
                print(f"  Status:  \033[33m\033[1mWARNING\033[0m [{res.check_name}]")
                print(f"  Notice:  {res.reason}\n")
            else:
                print(f"  Status:  \033[32m\033[1mALLOWED\033[0m (Command is safe and policy-compliant)\n")
        elif sub not in ("help", "--help", "-h", "check", "test") and len(args) > 1:
            cmd_to_test = " ".join(args[1:])
            print(f"\n\033[1m\033[36m🛡️ TOKENSAFE COMMAND GUARD CHECK 🛡️\033[0m\n")
            print(f"  Command: \033[1m{cmd_to_test}\033[0m")
            res = TokenSafeGuard.inspect_command(cmd_to_test)
            if res.blocked:
                print(f"  Status:  \033[31m\033[1mBLOCKED\033[0m [{res.check_name}]")
                print(f"  Reason:  {res.reason}\n")
            elif res.warning_only:
                print(f"  Status:  \033[33m\033[1mWARNING\033[0m [{res.check_name}]")
                print(f"  Notice:  {res.reason}\n")
            else:
                print(f"  Status:  \033[32m\033[1mALLOWED\033[0m (Command is safe and policy-compliant)\n")
        else:
            print("\nUsage:")
            print("  tokensafe guard \"<command>\"       Audit a terminal command line")
            print("  tokensafe guard check \"<command>\" Audit a terminal command line")
            print("  tokensafe guard audit [path]      Recursively audit source files for secrets & vulnerabilities\n")

    elif command == "proxy":
        from src.proxy import run_proxy
        sub = args[1] if len(args) > 1 else ""
        if sub in ("--help", "-h", "help"):
            print("Usage: tokensafe proxy [port]        Run in foreground (default: 8765)")
            print("       tokensafe proxy on            Install daemon + wire agents (one command)")
            print("       tokensafe proxy off           Unwire agents + remove daemon (rollback)")
            print("       tokensafe proxy status        Health, wiring state, heartbeat")
            return
        if sub == "on":
            from src.installer import AgentInstaller
            report = AgentInstaller.proxy_on()
            d, w = report["daemon"], report["wiring"]
            print(f"\n\033[1m\033[36m⚡ TOKENSAFE PROXY SETUP ⚡\033[0m\n")
            print(f"  Daemon:    {'✔ ' + d['message'] if d['ok'] else '✖ ' + d['message']}")
            print(f"  Health:    {'✔ proxy answering on 127.0.0.1:8765' if report['health'] else '✖ not reachable'}")
            print(f"  Wiring:    {'✔ ' + w['message'] if w['ok'] else '✖ ' + w['message']}")
            if report["manual_steps"]:
                print("  Manual (not auto-wirable):")
                for step in report["manual_steps"]:
                    print(f"    • {step}")
            print(f"\n  Rollback anytime: \033[1mtokensafe proxy off\033[0m\n")
            return
        if sub == "off":
            from src.installer import AgentInstaller
            report = AgentInstaller.proxy_off()
            u, d = report["unwired"], report["daemon"]
            print(f"\n  Unwired:   {'✔ ' + u['message'] if u['ok'] else '✖ ' + u['message']}")
            print(f"  Daemon:    {'✔ ' + d['message'] if d['ok'] else '✖ ' + d['message']}\n")
            return
        if sub == "status":
            from src.installer import AgentInstaller
            st = AgentInstaller.proxy_status()
            print(f"\n  Health:    {'✔ running on 127.0.0.1:8765' if st['health'] else '✖ not running'}")
            print(f"  Claude Code wired: {'✔ ' + str(st['claude_code_env_value']) if st['claude_code_wired'] else '○ not wired'}")
            hb = st.get("heartbeat_age_seconds")
            print(f"  Last traffic: {f'{hb}s ago' if hb is not None else 'no heartbeat yet'}\n")
            return
        port = 8765
        if len(args) > 1:
            try:
                port = int(args[1])
                if not (1 <= port <= 65535):
                    raise ValueError("port out of range")
            except ValueError:
                print(f"Invalid port '{args[1]}'. Expected integer 1..65535 (default: 8765).")
                sys.exit(1)
        run_proxy(port)

    elif command == "mcp":
        subcmd = args[1] if len(args) > 1 else "serve"
        if subcmd in ("disable", "off", "rm"):
            if len(args) < 3:
                print("Usage: tokensafe mcp disable <server_name>")
                sys.exit(1)
            target = args[2].strip()
            from src.environment_auditor import EnvironmentAuditor
            res = EnvironmentAuditor.disable_mcp(target)
            if res["success"]:
                print(f"\n\033[32m✔ MCP server '{target}' disabled successfully. Removed from active turn overhead.\033[0m\n")
            else:
                print(f"\n\033[31m✖ Server '{target}' not found or already disabled.\033[0m\n")
        elif subcmd in ("enable", "on", "add"):
            if len(args) < 3:
                print("Usage: tokensafe mcp enable <server_name>")
                sys.exit(1)
            target = args[2].strip()
            from src.environment_auditor import EnvironmentAuditor
            res = EnvironmentAuditor.enable_mcp(target)
            if res["success"]:
                print(f"\n\033[32m✔ MCP server '{target}' re-enabled successfully.\033[0m\n")
            else:
                print(f"\n\033[31m✖ Server '{target}' not found in disabled servers.\033[0m\n")
        elif subcmd in ("list", "ls", "status"):
            from src.environment_auditor import EnvironmentAuditor
            print(EnvironmentAuditor.format_cli_report())
        else:
            from src.mcp_server import run_mcp_server
            run_mcp_server()

    elif command in ("doctor", "audit", "tax"):
        from src.environment_auditor import EnvironmentAuditor
        print(EnvironmentAuditor.format_cli_report())

    elif command in ("graph", "nodes"):
        from src.graph_engine import GraphEngine
        target_dir = args[1] if len(args) > 1 else os.getcwd()
        graph = GraphEngine.get_instance(target_dir)
        print(f"\n🔍 TokenSafe Graph Engine — Knowledge Graph & Centrality Analysis")
        print(f"   Indexed: {len(graph.file_symbols)} code files, {len(graph.symbols)} symbols\n")
        god_nodes = graph.get_god_nodes(top_n=7)
        print("⚡ Architectural Hubs (God Nodes):")
        for idx, g in enumerate(god_nodes, 1):
            print(f"  {idx}. {g['file']}")
            print(f"     Degree Centrality: {g['centrality']} | Dependents: {g['dependents']} | Dependencies: {g['dependencies']} | Symbols: {g['symbols_count']}")
        print(f"\n💡 Tip: Run 'tokensafe map' to generate tokensafe-out/API_SURFACE.md\n")

    elif command in ("map", "surface"):
        from src.graph_engine import GraphEngine
        target_dir = args[1] if len(args) > 1 else os.getcwd()
        if not os.path.isdir(target_dir):
            print(f"\n\033[31m✖ Directory not found: {target_dir}\033[0m\n")
            sys.exit(1)
        graph = GraphEngine.get_instance(target_dir)
        out_file = os.path.join(os.path.abspath(target_dir), "tokensafe-out", "API_SURFACE.md")
        graph.generate_api_surface(out_file)
        if not os.path.exists(out_file):
            print(f"\n\033[31m✖ Could not write API Surface Map to: {out_file}\033[0m\n")
            sys.exit(1)
        print(f"\n✓ Generated API Surface Map: {out_file} ({os.path.getsize(out_file):,} bytes)")
        print(f"  AI agents can read this file in ~1.5k tokens instead of reading the entire repo.\n")

    elif command == "skeleton":
        from src.graph_engine import GraphEngine
        if len(args) < 2:
            print("Usage: tokensafe skeleton <file_path> [target_symbol]")
            sys.exit(1)
        file_path = os.path.abspath(args[1])
        target_symbol = args[2] if len(args) > 2 else None
        graph, _ = GraphEngine.for_file(file_path)
        res = graph.graph_guided_skeletonize(file_path, target_symbol)
        print(res)

    elif command == "activate":
        from src.license_manager import LicenseManager
        if len(args) < 2:
            print("Usage: tokensafe activate <LICENSE_KEY>")
            sys.exit(1)
        key = args[1].strip()
        ok, msg = LicenseManager.save_license(key)
        if ok:
            print(f"\n\033[32m\033[1m✔ {msg}\033[0m")
            # Automatically report version and seat activation to cloud
            try:
                from src.version_checker import VersionChecker
                VersionChecker.check_version(license_key=key, force_fetch=True)
            except Exception:
                pass
            # Automatically sync existing local token metrics to newly activated license
            try:
                from src.telemetry_sync import TelemetrySync
                sync_res = TelemetrySync.sync(license_key=key, timeout=3.0)
                if sync_res.get("success") and sync_res.get("synced_events", 0) > 0:
                    print(f"  \033[36m✔ Synced {sync_res['synced_events']:,} past optimization events to cloud dashboard.\033[0m")
            except Exception:
                pass
            print()
        else:
            print(f"\n\033[31m\033[1m✖ {msg}\033[0m\n")
            sys.exit(1)

    elif command in ("sync", "push"):
        from src.telemetry_sync import TelemetrySync
        print("\n\033[1m\033[36m⚡ SYNCHRONIZING TOKEN METRICS WITH DASHBOARD ⚡\033[0m\n")
        force_sync = "--force" in args or "-f" in args
        status_before = TelemetrySync.get_sync_status()
        print(f"  Local recorded events: {status_before['total_local_events']:,}")
        print(f"  Pending sync:          {status_before['pending_events']:,}\n")
        res = TelemetrySync.sync(force=force_sync, verbose=True)
        if res.get("success"):
            print(f"  \033[32m✔ {res.get('message', 'Synchronized successfully.')}\033[0m")
            status_after = TelemetrySync.get_sync_status()
            print(f"  Total synced events:   {status_after['total_synced_events']:,}\n")
        else:
            print(f"  \033[31m✖ Sync failed: {res.get('error', 'Unknown error')}\033[0m\n")

    elif command in ("license", "tier"):
        from src.license_manager import LicenseManager
        tier, payload = LicenseManager.get_active_license()
        print("\n\033[1m\033[36m⚡ TOKENSAFE LICENSE STATUS ⚡\033[0m\n")
        print(f"  Tier:       \033[32m\033[1m{tier.upper()}\033[0m")
        if tier != "free":
            print(f"  Email:      {payload.get('email', 'N/A')}")
            if payload.get("trial"):
                print(f"  Mode:       \033[33m7-Day Free Trial\033[0m")
            exp = payload.get("expires_at", 0)
            if exp > 0:
                import datetime
                dt = datetime.datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M:%S')
                print(f"  Expires:    {dt}")
            else:
                print(f"  Expires:    Never (Lifetime)")
        else:
            quota = LicenseManager.check_quota()
            q_saved = int(quota.get("monthly_saved", 0))
            q_limit = int(quota.get("monthly_limit") or 500_000)
            q_state = "\033[31mQUOTA REACHED (Pro optimizations paused)\033[0m" if quota.get("is_quota_exceeded") else "\033[32mActive\033[0m"
            print(f"  Status:     Free Tier ({q_saved:,} / {q_limit:,} measured tokens this month · {q_state})")
            print("  Upgrade:    https://tokensafe.dev/signup ($9/mo Solo with 7-Day Free Trial)\n")
        print()

    elif command in ("providers", "models", "keys"):
        from src.license_manager import LicenseManager
        from src.distiller import UniversalDistiller
        tier, _ = LicenseManager.get_active_license()
        print("\n\033[1m\033[36m⚡ TOKENSAFE LLM DISTILLATION PROVIDERS ⚡\033[0m\n")
        if tier == "free":
            print("  \033[33mTier: Free Community Edition\033[0m")
            print("  Managed Cloud Distillation (zero API keys) is reserved for Solo, Team, and Enterprise tiers.")
            print("  Free Community Edition operates with the zero-latency, zero-cost Local Regex Compactor.\n")
            print("  \033[1mTo unlock Cloud Distillation:\033[0m")
            print("  1. Start 7-day free trial or upgrade at: \033[36mhttps://tokensafe.dev\033[0m")
            print("  2. Activate your key: \033[32mtokensafe activate <KEY>\033[0m\n")
        elif not UniversalDistiller.is_self_hosted():
            print(f"  \033[32m✔\033[0m \033[1mTokenSafe Cloud Distiller\033[0m [Active: gemini-flash-lite-latest via Managed Cloud]")
            print("    Zero API keys required — managed centrally by your TokenSafe subscription.")
            print("    Shared Team Cache & sub-millisecond cloud cache active.\n")
            print("  \033[2m💡 Tip: To run self-hosted with custom BYOK keys in Docker, export TOKENSAFE_SELF_HOSTED=1.\033[0m\n")
        else:
            print("  \033[36mMode: Self-Hosted Docker (Custom BYOK Keys)\033[0m\n")
            configured = UniversalDistiller.get_configured_providers()
            all_p = UniversalDistiller.PROVIDERS
            for key, p in all_p.items():
                if key in configured:
                    c = configured[key]
                    print(f"  \033[32m✔\033[0m \033[1m{p['name']:<18}\033[0m [Active: {c['model']}]")
                else:
                    print(f"  \033[2m○ {p['name']:<18} [Set {p['env_key']} to activate]\033[0m")
            print("\nSet environment variable or add to data/config.json to activate.\n")

    elif command in ("markdown", "md", "export"):
        print(Dashboard(MetricsTracker()).render_markdown())

    elif command == "test":
        if len(args) < 2:
            print("Usage: tokensafe test \"<prompt text>\"")
            sys.exit(1)
        raw_prompt = " ".join(args[1:])
        res = PromptCompactor.compact(raw_prompt)

        print("\n\033[1m\033[36m⚡ TOKEN COMPRESSION TEST ⚡\033[0m\n")
        print(f"\033[1mOriginal Input:\033[0m  {res['original_text']}")
        print(f"\033[1mCompacted DSL:\033[0m   \033[32m\033[1m{res['compact_text']}\033[0m\n")
        print(f"\033[1mRouting Tier:\033[0m   \033[33m{res.get('tier', 'REGEX')}\033[0m")
        print(f"\033[1mExtracted Action:\033[0m {res['action']}")
        print(f"\033[1mTarget File:\033[0m      {res['target_file'] or 'None'}")
        print(f"\033[1mScope Symbol:\033[0m     {res['symbol'] or 'None'}")
        print(f"\033[1mTraits / Tags:\033[0m    {res['traits']}\n")
        print(f"\033[1mOriginal Tokens:\033[0m  {res['original_tokens']}")
        print(f"\033[1mCompacted Tokens:\033[0m {res['compact_tokens']}")
        print(f"\033[1mTokens Saved:\033[0m     \033[32m\033[1m+{res['saved_tokens']} tokens\033[0m ({render_bar(res['reduction_pct'])})\n")

    elif command in ("tests", "testsuite", "unittest"):
        tests_dir = os.path.join(PROJECT_ROOT, "tests")
        if not os.path.isdir(tests_dir):
            print("No tests/ directory found — run from a repository checkout "
                  "(installed packages do not ship the unit-test suite).")
            sys.exit(1)
        verbose = "--verbose" in args or "-v" in args
        full_tb = "--traceback" in args or "-tb" in args
        launcher = next((a for a in args[1:] if not a.startswith("-")), "unittest")
        if launcher == "unittest":
            cmd = [sys.executable, "-m", "unittest", "discover", "tests"]
        elif launcher == "pytest":
            # pytest is natively compact with quiet flags; the wrapper
            # guarantees them instead of trusting the caller.
            cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short"]
        else:
            print(f"Unknown test launcher '{launcher}' — supported: unittest (default), pytest.")
            sys.exit(1)
        print(f"\n⚡ Running the unit test suite ({launcher})...")
        try:
            proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
        except FileNotFoundError:
            print(f"Launcher '{launcher}' is not available — install it (e.g. pip install pytest) and retry.")
            sys.exit(1)
        raw = (proc.stdout or "") + (proc.stderr or "")
        if verbose:
            print(raw)
        elif launcher == "pytest":
            print(raw.rstrip() or "(no output)")
        else:
            print(summarize_unittest_output(raw, full_traceback=full_tb))
        sys.exit(proc.returncode)

    elif command in ("prune", "cleanup"):
        days = None
        max_events = None
        i = 1
        while i < len(args):
            if args[i] == "--days" and i + 1 < len(args):
                try:
                    days = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            elif args[i] in ("--max", "--max-events") and i + 1 < len(args):
                try:
                    max_events = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                i += 1

        if days is None and max_events is None:
            days = 30
            max_events = 5000

        print(f"\n\033[1m\033[36m⚡ DATA RETENTION PRUNE ⚡\033[0m")
        print(f"Policy: retain events < {days or 'any'} days old, max {max_events or 'unlimited'} entries.")
        res = MetricsTracker().prune(days=days, max_events=max_events, vacuum=True)
        b_before = res.get("db_size_before", 0) / 1024.0
        b_after = res.get("db_size_after", 0) / 1024.0
        print(f"\033[32m✔ Pruned {res['pruned_events']} granular events into lifetime rollup table.\033[0m")
        print(f"  DB Size: {b_before:.1f} KB ➔ {b_after:.1f} KB\n")

    elif command == "vacuum":
        print("\n\033[1m\033[36m⚡ VACUUMING METRICS DATABASE ⚡\033[0m")
        t = MetricsTracker()
        b_before = (os.path.getsize(t.db_path) if os.path.exists(t.db_path) else 0) / 1024.0
        t.vacuum()
        # Repair pass: rebuild session aggregates straight from events (guards
        # against sessions/events drift after crashes or manual DB edits).
        t.recalculate_sessions()
        b_after = (os.path.getsize(t.db_path) if os.path.exists(t.db_path) else 0) / 1024.0
        print(f"\033[32m✔ Database compacted + session aggregates rebuilt. Size: {b_before:.1f} KB ➔ {b_after:.1f} KB\033[0m\n")

    elif command in ("digest", "weekly-digest"):
        from src.weekly_digest import WeeklyDigestGenerator

        target_email = None
        do_send = "--send" in args

        idx = 1
        while idx < len(args):
            a = args[idx]
            if a.startswith("--email="):
                target_email = a.split("=", 1)[1].strip()
            elif a == "--email" and idx + 1 < len(args):
                target_email = args[idx + 1].strip()
                idx += 1
            idx += 1

        if not target_email:
            from src.license_manager import LicenseManager
            _, payload = LicenseManager.get_active_license()
            target_email = payload.get("email") or "teamlead@example.com"

        print(f"\n\033[1m\033[36m⚡ COMPILING FRIDAY TEAM ROI DIGEST ⚡\033[0m")
        print(f"Target Teamlead: {target_email}")

        stats = WeeklyDigestGenerator.get_team_weekly_stats(target_email)
        if not stats:
            stats = {
                "owner_email": target_email,
                "tier": "team",
                "seats_total": 1,
                "active_members_count": 0,
                "total_members_count": 0,
                "total_original_tokens": 0,
                "total_saved_tokens": 0,
                "total_saved_human": "0",
                "total_usd_saved": 0.0,
                "total_events": 0,
                "reduction_pct": 0.0,
                "leaderboard": [],
            }

        print(WeeklyDigestGenerator.render_email_text(stats))

        from src.paths import resolve_data_dir
        out_html = os.path.join(resolve_data_dir(), "weekly_digest_preview.html")
        os.makedirs(os.path.dirname(out_html), exist_ok=True)
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(WeeklyDigestGenerator.render_email_html(stats))
        print(f"\n📧 Apple-style email HTML preview generated at: {out_html}")

        if do_send:
            try:
                from src.mailer import TokenSafeMailer
                ok = TokenSafeMailer.send_weekly_digest(target_email, stats, sync=True)
                if ok:
                    print(f"\033[32m✔ Weekly digest successfully dispatched to {target_email}!\033[0m\n")
                else:
                    print(f"\033[33m○ Digest dispatch queued (SMTP_PASS not configured in local environment).\033[0m\n")
            except ImportError:
                print("\n\033[33m○ Email delivery is handled by TokenSafe Cloud; preview HTML saved locally.\033[0m\n")
        else:
            print("Tip: Run with --send to dispatch email, or --email=<addr> to target specific team.\n")

    elif command == "reset":
        confirm = input("Are you sure you want to reset all token metrics? [y/N]: ").strip().lower()
        if confirm == "y":
            MetricsTracker().reset()
            print("Metrics database reset successfully.")
        else:
            print("Reset cancelled.")

    else:
        print(f"Unknown command: {command}")
        print("Available commands: stats, status, sync, html, test, tests, doctor, providers, mcp, benchmark, proxy, graph, map, skeleton, guard, vacuum, prune, digest, activate, license, markdown, install, setup, update, check-update, reset. Run 'tokensafe help' for details.")
        sys.exit(1)

if __name__ == "__main__":
    main()
