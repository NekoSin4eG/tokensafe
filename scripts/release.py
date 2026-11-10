#!/usr/bin/env python3
"""
Internal TokenSafe Release Management Utility.
NOT exposed in public CLI or user distribution packages.

Capabilities:
1. bump: Atomically increments version across all config files, source constants, and tests.
2. clean: Recursively purges build artifacts, *.pyc caches, and stale distribution tarballs.
3. build: Performs pre-publish clean, builds sdist & wheel, validates twine check, and tests npm packaging.
4. current: Prints the current canonical project version.
5. verify: Checks every version location carries the canonical version.
6. release: Full pipeline — preflight, tests, bump, build, git tag, GitHub release,
   PyPI, npm (2FA-aware), MCP Registry (auto DNS re-login), public repo sync and
   rollout verification. Idempotent: re-running resumes past completed phases.
"""

import sys
import os
import re
import json
import time
import shutil
import subprocess
from typing import Tuple, List, Optional, Dict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_current_version() -> str:
    """Reads canonical version from package.json."""
    pkg_path = os.path.join(PROJECT_ROOT, "package.json")
    with open(pkg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("version", "1.0.0")


def parse_semver(v: str) -> Tuple[int, int, int]:
    """Parses semantic version string into (major, minor, patch)."""
    clean = v.lstrip("v").strip()
    parts = clean.split(".")
    major = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return major, minor, patch


def calculate_next_version(current: str, bump_type: str) -> str:
    """Calculates next semver based on bump type: patch, minor, major, or explicit version."""
    bump_lower = bump_type.lower().strip()
    if re.match(r"^\d+\.\d+\.\d+$", bump_lower):
        return bump_lower

    major, minor, patch = parse_semver(current)
    if bump_lower in ("patch", "p"):
        return f"{major}.{minor}.{patch + 1}"
    elif bump_lower in ("minor", "m"):
        return f"{major}.{minor + 1}.0"
    elif bump_lower in ("major", "maj"):
        return f"{major + 1}.0.0"
    else:
        raise ValueError(f"Unknown bump target: '{bump_type}'. Use 'patch', 'minor', 'major', or explicit 'X.Y.Z'.")


def clean_artifacts() -> int:
    """Recursively removes build artifacts, __pycache__, and temporary files."""
    cleaned_count = 0
    print("🧹 Cleaning build artifacts and caches...")

    # 1. Directories to purge
    dirs_to_remove = ["dist", "build"]
    for d in dirs_to_remove:
        dir_path = os.path.join(PROJECT_ROOT, d)
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path, ignore_errors=True)
            cleaned_count += 1
            print(f"  ✓ Removed directory: {d}/")

    # 2. Walk project and purge __pycache__, *.egg-info, and *.pyc
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Skip node_modules and .git
        if "node_modules" in root or ".git" in root:
            continue

        for d in list(dirs):
            if d == "__pycache__" or d.endswith(".egg-info"):
                p = os.path.join(root, d)
                shutil.rmtree(p, ignore_errors=True)
                cleaned_count += 1
                rel = os.path.relpath(p, PROJECT_ROOT)
                print(f"  ✓ Removed cache directory: {rel}/")

        for f in files:
            if f.endswith((".pyc", ".pyo", ".pyd")) or f == ".DS_Store":
                p = os.path.join(root, f)
                try:
                    os.remove(p)
                    cleaned_count += 1
                except OSError:
                    pass

    print(f"✅ Cleanup complete: {cleaned_count} artifacts removed.\n")
    return cleaned_count


def replace_in_file(path: str, pattern: str, replacement: str) -> bool:
    """Replaces regex pattern in file, returns True if changed."""
    full_path = os.path.join(PROJECT_ROOT, path)
    if not os.path.exists(full_path):
        print(f"  ⚠️ File not found: {path}")
        return False

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_content, count = re.subn(pattern, replacement, content)
    if count == 0:
        print(f"  ⚠️ Pattern not matched in: {path}")
        return False

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"  ✓ Updated {path} ({count} match{'es' if count != 1 else ''})")
    return True


def bump_version(target: str) -> str:
    """Atomically updates version across all registered files."""
    old_version = get_current_version()
    new_version = calculate_next_version(old_version, target)

    print(f"🚀 Bumping TokenSafe version: {old_version} ➜ {new_version}\n")

    # 1. package.json
    replace_in_file(
        "package.json",
        r'("version"\s*:\s*)"[^"]+"',
        rf'\g<1>"{new_version}"'
    )

    # 2. pyproject.toml
    replace_in_file(
        "pyproject.toml",
        r'(version\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"'
    )

    # 3. plugin.json
    replace_in_file(
        "plugin.json",
        r'("version"\s*:\s*)"[^"]+"',
        rf'\g<1>"{new_version}"'
    )

    # 4. src/version_checker.py
    replace_in_file(
        "src/version_checker.py",
        r'(CURRENT_VERSION\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"'
    )

    # 5. src/cloud/ package (post-split home of the backend version constants)
    replace_in_file(
        "src/cloud/config.py",
        r'(HARDCODED_VERSION\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"'
    )
    replace_in_file(
        "src/cloud/services.py",
        r'TokenSafe-Backend/[0-9]+\.[0-9]+\.[0-9]+',
        f'TokenSafe-Backend/{new_version}'
    )
    replace_in_file(
        "src/cloud/stripe.py",
        r'TokenSafe-Backend/[0-9]+\.[0-9]+\.[0-9]+',
        f'TokenSafe-Backend/{new_version}'
    )

    # 6. src/mcp_server.py
    replace_in_file(
        "src/mcp_server.py",
        r'("name"\s*:\s*"tokensafe-mcp",\s*"version"\s*:\s*)"[^"]+"',
        rf'\g<1>"{new_version}"'
    )

    # 7. src/telemetry_sync.py
    replace_in_file(
        "src/telemetry_sync.py",
        r'TokenSafe-Sync/[0-9]+\.[0-9]+\.[0-9]+',
        f'TokenSafe-Sync/{new_version}'
    )

    # 8. tests/test_cloud_backend.py
    replace_in_file(
        "tests/test_cloud_backend.py",
        re.escape(f'client_version="{old_version}"'),
        f'client_version="{new_version}"'
    )
    replace_in_file(
        "tests/test_cloud_backend.py",
        re.escape(f'seats_after[0]["client_version"], "{old_version}"'),
        f'seats_after[0]["client_version"], "{new_version}"'
    )

    # 9. server.json (Official MCP Registry manifest)
    server_json_path = os.path.join(PROJECT_ROOT, "server.json")
    if os.path.exists(server_json_path):
        with open(server_json_path, "r", encoding="utf-8") as f:
            server_data = json.load(f)
        server_data["version"] = new_version
        for pkg in server_data.get("packages", []):
            if isinstance(pkg, dict) and "version" in pkg:
                pkg["version"] = new_version
        with open(server_json_path, "w", encoding="utf-8") as f:
            json.dump(server_data, f, indent=2)
            f.write("\n")
        print("  ✓ Updated server.json (root & package versions)")

    print(f"\n✅ All files successfully bumped to {new_version}!")
    return new_version


def build_packages() -> bool:
    """Cleans artifacts and builds Python distribution & validates npm package."""
    clean_artifacts()

    print("📦 Building Python distribution (sdist + wheel)...")
    res = subprocess.run([sys.executable, "-m", "build"], cwd=PROJECT_ROOT)
    if res.returncode != 0:
        print("❌ Python build failed.")
        return False

    print("\n🔍 Validating Python package with twine...")
    twine_res = subprocess.run([sys.executable, "-m", "twine", "check", "dist/*"], cwd=PROJECT_ROOT)
    if twine_res.returncode != 0:
        print("❌ Twine verification failed.")
        return False

    print("\n📦 Testing npm packaging dry-run...")
    npm_res = subprocess.run(["npm", "pack", "--dry-run"], cwd=PROJECT_ROOT)
    if npm_res.returncode != 0:
        print("❌ npm pack dry-run failed.")
        return False

    print("\n✅ Build and packaging verification complete! Ready for release.")
    return True


def verify_version_consistency(expected: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Checks that every file the bumper touches actually carries `expected`
    (defaults to package.json version). Returns (ok, problems).

    Server-side files (src/cloud/*, cloud tests) are OPTIONAL: the public
    export (bin/sync-public.sh) strips them, and the consistency gate runs
    there too. Missing optional files are skipped silently; a missing
    required file is drift."""
    version = expected or get_current_version()
    problems: List[str] = []
    optional_files = {
        "src/cloud/config.py",
        "src/cloud/services.py",
        "src/cloud/stripe.py",
        "tests/test_cloud_backend.py",
    }

    def check(path: str, snippet: str, label: str):
        try:
            with open(os.path.join(PROJECT_ROOT, path), "r", encoding="utf-8") as f:
                content = f.read()
            if snippet not in content:
                problems.append(f"{path}: expected '{snippet}' ({label})")
        except FileNotFoundError:
            if path not in optional_files:
                problems.append(f"{path}: file missing ({label})")

    check("package.json", f'"version": "{version}"', "npm version")
    check("pyproject.toml", f'version = "{version}"', "python version")
    check("plugin.json", f'"version": "{version}"', "plugin version")
    check("src/version_checker.py", f'CURRENT_VERSION = "{version}"', "client version")
    check("src/cloud/config.py", f'HARDCODED_VERSION = "{version}"', "backend version")
    check("src/cloud/services.py", f"TokenSafe-Backend/{version}", "services UA")
    check("src/cloud/stripe.py", f"TokenSafe-Backend/{version}", "stripe UA")
    check("src/mcp_server.py", f'"version": "{version}"', "mcp server version")
    check("src/telemetry_sync.py", f"TokenSafe-Sync/{version}", "sync UA")
    check("tests/test_cloud_backend.py", f'client_version="{version}"', "tests client_version")

    try:
        with open(os.path.join(PROJECT_ROOT, "server.json"), "r", encoding="utf-8") as f:
            server = json.load(f)
        if server.get("version") != version:
            problems.append(f"server.json: root version {server.get('version')} != {version}")
        for pkg in server.get("packages", []):
            if pkg.get("version") != version:
                problems.append(f"server.json: package {pkg.get('registryType')} version {pkg.get('version')} != {version}")
    except FileNotFoundError:
        problems.append("server.json: file missing")

    return (len(problems) == 0), problems


def generate_release_notes(new_version: str) -> str:
    """Drafts release notes from conventional commits since the previous tag."""
    try:
        last_tag = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "HEAD^"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        ).stdout.strip()
    except Exception:
        last_tag = ""
    if not last_tag:
        last_tag = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        ).stdout.strip().split("\n")[0]
    res = subprocess.run(
        ["git", "log", f"{last_tag}..HEAD", "--pretty=%s"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
    )
    subjects = [s.strip() for s in res.stdout.split("\n") if s.strip()]
    buckets: Dict[str, List[str]] = {}
    for s in reversed(subjects):  # chronological
        parts = s.split(":", 1)
        kind = parts[0].split("(")[0].strip() if len(parts) == 2 else "chore"
        buckets.setdefault(kind, []).append(s)
    labels = {"feat": "Features", "fix": "Fixes", "docs": "Docs",
              "perf": "Performance", "refactor": "Refactoring", "ci": "CI"}
    lines = [f"### Changes in v{new_version}", ""]
    for kind in ("feat", "fix", "perf", "refactor", "docs", "ci"):
        if kind in buckets:
            lines.append(f"**{labels.get(kind, kind)}**")
            lines.extend(f"- {s}" for s in buckets[kind])
            lines.append("")
    other = [s for k, items in buckets.items() if k not in ("feat", "fix", "perf", "refactor", "docs", "ci") for s in items]
    if other:
        lines.append("**Other**")
        lines.extend(f"- {s}" for s in other)
    lines.append("")
    lines.append("_Full methodology for any metrics referenced: [METRICS.md](https://github.com/tokensafe-dev/tokensafe/blob/main/METRICS.md)._")
    return "\n".join(lines)


def _sh(args: List[str], timeout: int = 120) -> Tuple[int, str]:
    res = subprocess.run(args, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout)
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def pypi_version_published(version: str) -> bool:
    try:
        res = subprocess.run(
            [sys.executable, "-c",
             f"import urllib.request;urllib.request.urlopen('https://pypi.org/pypi/tokensafe/{version}/json', timeout=10)"],
            capture_output=True, timeout=20)
        return res.returncode == 0
    except Exception:
        return False


def npm_version_published(version: str) -> bool:
    code, out = _sh(["npm", "view", f"tokensafe@{version}", "version"], timeout=30)
    return code == 0 and version in out


def registry_version_published(version: str) -> bool:
    try:
        import urllib.request
        url = f"https://registry.modelcontextprotocol.io/v0.1/servers/dev.tokensafe%2Ftokensafe/versions/{version}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
        meta = data.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
        return meta.get("status") == "active"
    except Exception:
        return False


def mcp_registry_login() -> Tuple[bool, str]:
    """Re-authenticates with the MCP Registry via the DNS method.
    The Ed25519 private key never leaves this process unprinted."""
    key_path = os.path.expanduser("~/.ssh/mcp_tokensafe.pem")
    if not os.path.exists(key_path):
        return False, f"private key not found at {key_path}"
    try:
        code = (
            "from cryptography.hazmat.primitives.serialization import load_pem_private_key;"
            "import sys;"
            "k = load_pem_private_key(open(sys.argv[1],'rb').read(), password=None);"
            "sys.stdout.write(k.private_bytes_raw().hex())"
        )
        res = subprocess.run([sys.executable, "-c", code, key_path],
                             capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            return False, "failed to read private key (cryptography package?)"
        priv = res.stdout.strip()
        code, out = _sh([os.path.expanduser("~/.local/bin/mcp-publisher"),
                         "login", "dns", "--domain", "tokensafe.dev",
                         "--private-key", priv], timeout=60)
        if code != 0:
            return False, out.strip()[:200]
        return True, "logged in"
    finally:
        priv = None


def wait_for_prod_version(version: str, attempts: int = 12, delay: int = 15) -> bool:
    """Polls the production /health until it reports the released version."""
    for _ in range(attempts):
        try:
            import urllib.request
            with urllib.request.urlopen("https://api.tokensafe.dev/health", timeout=10) as r:
                data = json.load(r)
            if data.get("version") == version:
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def run_release(target: str, otp: Optional[str] = None):
    """Full release pipeline. Every phase is idempotent: re-running after a
    failure or an interactive gate (npm OTP) resumes where it stopped."""
    import glob as _glob

    v = get_current_version()
    next_v = calculate_next_version(v, target)
    tag = f"v{next_v}"
    pending: List[str] = []

    print(f"🚀 Release pipeline: {v} ➜ {next_v}\n")

    # Phase 0: preflight -------------------------------------------------
    print("── Phase 0: preflight")
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    if branch != "main":
        print("❌ Must be on main. Aborting.")
        sys.exit(1)
    status = subprocess.run(["git", "status", "--porcelain"],
                            cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    # data/ runtime artifacts don't block
    blocking = [l for l in status.split("\n") if l.strip() and not l.startswith("?? data/")]
    if blocking:
        print("❌ Working tree has uncommitted changes (commit first):")
        for l in blocking[:10]:
            print(f"   {l}")
        sys.exit(1)
    ok, problems = verify_version_consistency(v)
    if not ok:
        print("❌ Current version files are inconsistent (run 'bump' or fix):")
        for p in problems:
            print(f"   {p}")
        sys.exit(1)
    print(f"  ✔ main, clean tree, v{v} consistent\n")

    # Phase 1: tests + bump + tests + verify ------------------------------
    print("── Phase 1: tests, bump, tests")
    code, out = _sh([sys.executable, "-m", "unittest", "discover", "tests"], timeout=300)
    if code != 0:
        print(out[-1500:])
        print("❌ Test suite failed. Aborting.")
        sys.exit(1)
    if v == next_v:
        print(f"  ℹ version already {v} — skipping bump (resume run)")
    else:
        bump_version(next_v)
    code, out = _sh([sys.executable, "-m", "unittest", "discover", "tests"], timeout=300)
    if code != 0:
        print(out[-1500:])
        print("❌ Post-bump test suite failed. Aborting.")
        sys.exit(1)
    ok, problems = verify_version_consistency(next_v)
    if not ok:
        for p in problems:
            print(f"   {p}")
        print("❌ Version inconsistency after bump. Aborting.")
        sys.exit(1)
    print(f"  ✔ tests green, v{next_v} consistent across all files\n")

    # Phase 2: build -------------------------------------------------------
    print("── Phase 2: build packages")
    if not build_packages():
        print("❌ Build failed. Aborting.")
        sys.exit(1)

    # Phase 3: commit, push, tag ------------------------------------------
    print("── Phase 3: commit bump, push, tag")
    if subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
                      capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "add", "-A"], cwd=PROJECT_ROOT, check=True)
        subprocess.run(["git", "commit", "-m", f"chore(release): bump version to {next_v}"],
                       cwd=PROJECT_ROOT, check=True)
        print("  ✔ bump committed")
    else:
        print("  ℹ nothing to commit (resume run)")
    code, out = _sh(["git", "push", "origin", "main"])
    if code != 0:
        print(out[-500:]); print("❌ push failed."); sys.exit(1)
    code, _ = _sh(["git", "rev-parse", tag])
    if code == 0:
        print(f"  ℹ tag {tag} already exists")
    else:
        subprocess.run(["git", "tag", tag], cwd=PROJECT_ROOT, check=True)
        print(f"  ✔ tag {tag} created")
    code, out = _sh(["git", "push", "origin", tag])
    if code != 0 and "already exists" not in out:
        print(out[-500:]); print("❌ tag push failed."); sys.exit(1)
    print("  ✔ pushed\n")

    # Phase 4: GitHub release ----------------------------------------------
    print("── Phase 4: GitHub release")
    code, _ = _sh(["gh", "release", "view", tag])
    if code == 0:
        print(f"  ℹ {tag} release already exists")
    else:
        notes = generate_release_notes(next_v)
        gh_args = ["gh", "release", "create", tag, "--title",
                   f"v{next_v}: {next_v} release", "--notes", notes]
        code, out = _sh(gh_args, timeout=60)
        if code != 0:
            print(out[-400:]); pending.append(f"gh release create {tag}")
        else:
            print(f"  ✔ {tag} published")

    # Phase 5: PyPI ---------------------------------------------------------
    print("── Phase 5: PyPI")
    if pypi_version_published(next_v):
        print(f"  ℹ {next_v} already on PyPI")
    else:
        dists = sorted(_glob.glob(os.path.join(PROJECT_ROOT, "dist", f"*{next_v}*")))
        if not dists:
            print("❌ dist artifacts missing."); sys.exit(1)
        code, out = _sh([sys.executable, "-m", "twine", "upload", "--non-interactive", *dists], timeout=300)
        if code != 0:
            print(out[-400:]); pending.append("twine upload")
        else:
            print("  ✔ PyPI uploaded")

    # Phase 6: npm ------------------------------------------------------------
    print("── Phase 6: npm (2FA)")
    if npm_version_published(next_v):
        print(f"  ℹ {next_v} already on npm")
    elif otp:
        code, out = _sh(["npm", "publish", "--otp", otp], timeout=180)
        if code != 0:
            print(out[-400:]); pending.append("npm publish (otp rejected?)")
        else:
            print("  ✔ npm published")
    else:
        code, out = _sh(["npm", "publish"], timeout=180)
        if code == 0:
            print("  ✔ npm published")
        elif "EOTP" in out:
            import re as _re
            m = _re.search(r"(https://www\.npmjs\.com/auth/cli/\S+)", out)
            print("  ⚠ npm requires 2FA. Complete auth, then re-run:")
            if m:
                print(f"      {m.group(1)}")
            print("      python3 scripts/release.py release --resume  (or: npm publish)")
            pending.append("npm publish (waiting for your 2FA)")
        else:
            print(out[-400:]); pending.append("npm publish")

    # Phase 7: MCP Registry -----------------------------------------------------
    print("── Phase 7: MCP Registry")
    if registry_version_published(next_v):
        print(f"  ℹ {next_v} already active in the registry")
    else:
        publisher = os.path.expanduser("~/.local/bin/mcp-publisher")
        code, out = _sh([publisher, "publish", "server.json"], timeout=120)
        if code == 0:
            print("  ✔ registry published")
        elif "token is expired" in out or "Unauthorized" in out:
            print("  ℹ registry JWT expired — re-authenticating via DNS key...")
            ok, msg = mcp_registry_login()
            if not ok:
                print(f"  ⚠ login failed: {msg}")
                pending.append("mcp-publisher publish (login failed)")
            else:
                code, out = _sh([publisher, "publish", "server.json"], timeout=120)
                if code == 0:
                    print("  ✔ registry published after re-login")
                else:
                    print(out[-300:]); pending.append("mcp-publisher publish")
        else:
            # Packages must exist on npm/PyPI before the registry accepts them.
            print(out[-300:])
            if not npm_version_published(next_v):
                print("  ⚠ npm package not published yet — registry needs it. Re-run after npm.")
            pending.append("mcp-publisher publish")

    # Phase 8: public repo ---------------------------------------------------------
    print("── Phase 8: public repo sync")
    sync_sh = os.path.join(PROJECT_ROOT, "bin", "sync-public.sh")
    stale_lock = os.path.join(PROJECT_ROOT, "data", ".sync.lock")
    if os.path.exists(stale_lock):
        try:
            pid = int(open(stale_lock).read().strip())
            import signal as _signal
            os.kill(pid, 0)
            print("  ⚠ telemetry sync is running; skipping public sync")
            pending.append("bin/sync-public.sh (sync lock busy)")
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            os.remove(stale_lock)
    if not pending or not any("sync-public" in p for p in pending):
        if os.path.exists(sync_sh):
            code, out = _sh(["bash", sync_sh], timeout=300)
            if code != 0:
                print(out[-400:]); pending.append("bin/sync-public.sh")
            else:
                print("  ✔ public repo synced + tag pushed")

    # Phase 9: rollout verification --------------------------------------------------
    print("── Phase 9: rollout verification")
    health_ok = wait_for_prod_version(next_v, attempts=8, delay=15)
    print(f"  {'✔' if health_ok else '⚠'} prod /health: "
          f"{'v' + next_v if health_ok else 'still rolling — check https://api.tokensafe.dev/health'}")
    print(f"  {'✔' if pypi_version_published(next_v) else '⚠'} PyPI {next_v}")
    print(f"  {'✔' if npm_version_published(next_v) else '⚠'} npm {next_v}")
    print(f"  {'✔' if registry_version_published(next_v) else '⚠'} MCP Registry {next_v} active")

    # Public repo CI (sync-public.sh just force-pushed there)
    public_ci_ok = False
    for _ in range(4):
        code, out = _sh(["gh", "run", "list", "-R", "tokensafe-dev/tokensafe",
                         "--limit", "1", "--json", "conclusion,databaseId",
                         "-q", ".[0].conclusion"], timeout=30)
        concl = out.strip()
        if concl in ("success", "failure"):
            public_ci_ok = (concl == "success")
            break
        time.sleep(30)
    print(f"  {'✔' if public_ci_ok else '⚠'} public repo CI: {concl if concl in ('success', 'failure') else 'pending'}")
    if not public_ci_ok and concl == "failure":
        pending.append("public repo CI failed — inspect gh run logs (tokensafe-dev/tokensafe)")

    print("\n════════════════════════════════════════")
    if pending:
        print(f"⏳ Pending ({len(pending)}) — resolve, then re-run the same command to continue:")
        for p in pending:
            print(f"   • {p}")
    else:
        print(f"🎉 Release {tag} shipped everywhere.")
    print("════════════════════════════════════════")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print(f"Current version: {get_current_version()}")
        print("\nUsage:")
        print("  python3 scripts/release.py current")
        print("  python3 scripts/release.py clean")
        print("  python3 scripts/release.py bump [patch|minor|major|X.Y.Z]")
        print("  python3 scripts/release.py build")
        print("  python3 scripts/release.py verify [X.Y.Z]        # version consistency across files")
        print("  python3 scripts/release.py release [patch|minor|major|X.Y.Z] [--otp=CODE]")
        sys.exit(0)

    cmd = sys.argv[1].lower().strip()
    if cmd == "current":
        print(get_current_version())
    elif cmd == "clean":
        clean_artifacts()
    elif cmd == "bump":
        target = sys.argv[2] if len(sys.argv) > 2 else "patch"
        bump_version(target)
    elif cmd == "build":
        ok = build_packages()
        sys.exit(0 if ok else 1)
    elif cmd == "verify":
        expected = sys.argv[2] if len(sys.argv) > 2 else None
        ok, problems = verify_version_consistency(expected)
        if ok:
            print(f"✅ All version locations consistent at {expected or get_current_version()}")
        else:
            for p in problems:
                print(f"❌ {p}")
            sys.exit(1)
    elif cmd == "release":
        args = sys.argv[2:]
        otp = None
        if args and args[-1].startswith("--otp="):
            otp = args[-1].split("=", 1)[1]
            args = args[:-1]
        target = args[0] if args else "patch"
        run_release(target, otp=otp)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
