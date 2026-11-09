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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.metrics import MetricsTracker
from src.dashboard import Dashboard, render_bar
from src.compactor import PromptCompactor
from src.tokenizer import count_tokens

def main():
    tracker = MetricsTracker()
    dashboard = Dashboard(tracker)

    args = sys.argv[1:]
    command = args[0] if args else "stats"

    if command in ("stats", "dashboard"):
        try:
            from src.telemetry_sync import TelemetrySync
            TelemetrySync.sync_background()
        except Exception:
            pass
        print(dashboard.render_terminal())

    elif command in ("html", "web"):
        html_content = dashboard.render_html()
        out_path = os.path.join(PROJECT_ROOT, "data", "dashboard.html")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"\n⚡ Interactive HTML Dashboard generated at: {out_path}")
        try:
            subprocess.run(["open", out_path])
            print("   Opened in default browser.")
        except Exception:
            pass

    elif command in ("benchmark", "bench"):
        from src.benchmark import BenchmarkSuite
        print("\n⚡ Running Zero-Loss SWE-bench Benchmark Suite across real-world scenarios...")
        data = BenchmarkSuite.run_all()
        print(BenchmarkSuite.render_report(data))
        out_md = os.path.join(PROJECT_ROOT, "data", "BENCHMARK_REPORT.md")
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
        elif "--project" in args:
            res = AgentInstaller.install_project(os.getcwd())
            print(f"\n\033[32m✔ TokenSafe rules injected into project: {', '.join(res['created_rules'])}\033[0m\n")
        else:
            AgentInstaller.run_interactive()

    elif command in ("update", "upgrade"):
        from src.installer import AgentInstaller
        AgentInstaller.self_update()

    elif command in ("version", "--version", "-v"):
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
            print(f"\n\033[1m\033[36m🛡️ TOKENSAFE SECURITY & HYGIENE AUDIT 🛡️\033[0m")
            print(f"  Target: {target_path}\n")
            violations = []
            files_scanned = 0
            code_exts = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java")
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
        elif sub in ("check", "test") or len(args) > 1:
            cmd_to_test = " ".join(args[2:]) if sub in ("check", "test") and len(args) > 2 else " ".join(args[1:])
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
            print("  tokensafe guard audit [path]      Recursively audit source files for secrets & vulnerabilities\n")

    elif command == "proxy":
        from src.proxy import run_proxy
        port = int(args[1]) if len(args) > 1 else 8765
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
        graph = GraphEngine(target_dir)
        graph.index_workspace()
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
        graph = GraphEngine(target_dir)
        graph.index_workspace()
        out_file = os.path.join(target_dir, "tokensafe-out", "API_SURFACE.md")
        graph.generate_api_surface(out_file)
        print(f"\n✓ Generated API Surface Map: {out_file} ({os.path.getsize(out_file):,} bytes)")
        print(f"  AI agents can read this file in ~1.5k tokens instead of reading the entire repo.\n")

    elif command == "skeleton":
        from src.graph_engine import GraphEngine
        if len(args) < 2:
            print("Usage: tokensafe skeleton <file_path> [target_symbol]")
            sys.exit(1)
        file_path = args[1]
        target_symbol = args[2] if len(args) > 2 else None
        target_dir = os.path.dirname(os.path.abspath(file_path)) or os.getcwd()
        graph = GraphEngine(target_dir)
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
            print("  Status:     Free Tier (up to 500,000 tokens/mo)")
            print("  Upgrade:    https://tokensafe.dev ($9/mo Solo with 7-Day Free Trial)\n")
        print()

    elif command in ("providers", "models", "keys"):
        from src.license_manager import LicenseManager
        from src.distiller import UniversalDistiller
        tier, _ = LicenseManager.get_active_license()
        print("\n\033[1m\033[36m⚡ TOKENSAFE LLM DISTILLATION PROVIDERS ⚡\033[0m\n")
        if tier == "free":
            print("  \033[33mTier: Free Community Edition\033[0m")
            print("  Neural Semantic Distillation (Gemini / OpenAI / DeepSeek) is reserved for Solo & Team tiers.")
            print("  Free Community Edition operates with the zero-latency, zero-cost Local Regex Compactor.\n")
            print("  \033[1mTo unlock Neural LLM Distillation:\033[0m")
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
        print(dashboard.render_markdown())

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
        res = tracker.prune(days=days, max_events=max_events, vacuum=True)
        b_before = res.get("db_size_before", 0) / 1024.0
        b_after = res.get("db_size_after", 0) / 1024.0
        print(f"\033[32m✔ Pruned {res['pruned_events']} granular events into lifetime rollup table.\033[0m")
        print(f"  DB Size: {b_before:.1f} KB ➔ {b_after:.1f} KB\n")

    elif command == "vacuum":
        print("\n\033[1m\033[36m⚡ VACUUMING METRICS DATABASE ⚡\033[0m")
        b_before = (os.path.getsize(tracker.db_path) if os.path.exists(tracker.db_path) else 0) / 1024.0
        tracker.vacuum()
        b_after = (os.path.getsize(tracker.db_path) if os.path.exists(tracker.db_path) else 0) / 1024.0
        print(f"\033[32m✔ Database compacted. Size: {b_before:.1f} KB ➔ {b_after:.1f} KB\033[0m\n")

    elif command in ("digest", "weekly-digest"):
        from src.weekly_digest import WeeklyDigestGenerator
        from src.mailer import TokenSafeMailer

        target_email = None
        do_send = "--send" in args

        for a in args:
            if a.startswith("--email="):
                target_email = a.split("=", 1)[1].strip()

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
                "seats_total": 5,
                "active_members_count": 4,
                "total_members_count": 5,
                "total_original_tokens": 5800000,
                "total_saved_tokens": 4200000,
                "total_saved_human": "4.2M",
                "total_usd_saved": 28.50,
                "total_events": 340,
                "reduction_pct": 72.4,
                "leaderboard": [
                    {"rank": 1, "email": "alex.dev@corp.com", "saved_tokens": 1650000, "saved_tokens_human": "1.7M", "usd_saved_formatted": "$11.20", "event_count": 120},
                    {"rank": 2, "email": "maria.arch@corp.com", "saved_tokens": 1200000, "saved_tokens_human": "1.2M", "usd_saved_formatted": "$8.10", "event_count": 94},
                    {"rank": 3, "email": "sergey.qa@corp.com", "saved_tokens": 850000, "saved_tokens_human": "850K", "usd_saved_formatted": "$5.70", "event_count": 68},
                    {"rank": 4, "email": target_email, "saved_tokens": 500000, "saved_tokens_human": "500K", "usd_saved_formatted": "$3.50", "event_count": 58}
                ]
            }

        print(WeeklyDigestGenerator.render_email_text(stats))

        out_html = os.path.join(PROJECT_ROOT, "data", "weekly_digest_preview.html")
        os.makedirs(os.path.dirname(out_html), exist_ok=True)
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(WeeklyDigestGenerator.render_email_html(stats))
        print(f"\n📧 Apple-style email HTML preview generated at: {out_html}")

        if do_send:
            ok = TokenSafeMailer.send_weekly_digest(target_email, stats, sync=True)
            if ok:
                print(f"\033[32m✔ Weekly digest successfully dispatched to {target_email}!\033[0m\n")
            else:
                print(f"\033[33m○ Digest dispatch queued (SMTP_PASS not configured in local environment).\033[0m\n")
        else:
            print("Tip: Run with --send to dispatch email, or --email=<addr> to target specific team.\n")

    elif command == "reset":
        confirm = input("Are you sure you want to reset all token metrics? [y/N]: ").strip().lower()
        if confirm == "y":
            tracker.reset()
            print("Metrics database reset successfully.")
        else:
            print("Reset cancelled.")

    else:
        print(f"Unknown command: {command}")
        print("Available commands: stats, sync, html, test, doctor, providers, mcp, benchmark, proxy, graph, map, vacuum, prune, digest, activate, license, markdown, setup, reset")
        sys.exit(1)

if __name__ == "__main__":
    main()
