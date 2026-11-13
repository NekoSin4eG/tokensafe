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
import tempfile
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


def replace_in_file(path: str, pattern: str, replacement: str, optional: bool = False, root_dir: Optional[str] = None) -> bool:
    """Replaces regex pattern in file under `root_dir` (defaults to PROJECT_ROOT), returns True if changed."""
    base_dir = root_dir or PROJECT_ROOT
    full_path = os.path.join(base_dir, path)
    if not os.path.exists(full_path):
        if not optional:
            print(f"  ⚠️ File not found: {path}")
        return False

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_content, count = re.subn(pattern, replacement, content)
    if count == 0:
        if not optional:
            print(f"  ⚠️ Pattern not matched in: {path}")
        return False

    if new_content != content:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"  ✓ Updated {path} ({count} match{'es' if count != 1 else ''})")
        return True
    return False


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
    replace_in_file(
        ".codex-plugin/plugin.json",
        r'("version"\s*:\s*)"[^"]+"',
        rf'\g<1>"{new_version}"',
        optional=True,
    )

    # 4. src/version_checker.py
    replace_in_file(
        "src/version_checker.py",
        r'(CURRENT_VERSION\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"'
    )

    # 5. src/cloud/ package (optional post-split home of the backend version constants)
    replace_in_file(
        "src/cloud/config.py",
        r'(HARDCODED_VERSION\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"',
        optional=True,
    )
    replace_in_file(
        "src/cloud/services.py",
        r'TokenSafe-Backend/[0-9]+\.[0-9]+\.[0-9]+',
        f'TokenSafe-Backend/{new_version}',
        optional=True,
    )
    replace_in_file(
        "src/cloud/stripe.py",
        r'TokenSafe-Backend/[0-9]+\.[0-9]+\.[0-9]+',
        f'TokenSafe-Backend/{new_version}',
        optional=True,
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

    # 8. tests/test_cloud_backend.py (optional post-split)
    replace_in_file(
        "tests/test_cloud_backend.py",
        re.escape(f'client_version="{old_version}"'),
        f'client_version="{new_version}"',
        optional=True,
    )
    replace_in_file(
        "tests/test_cloud_backend.py",
        re.escape(f'seats_after[0]["client_version"], "{old_version}"'),
        f'seats_after[0]["client_version"], "{new_version}"',
        optional=True,
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

    Server-side files (src/cloud/*, cloud tests) are OPTIONAL: they live in
    the private tokensafe-cloud repository, and this orchestrator runs from
    the public client checkout. Missing optional files are skipped
    silently; a missing required file is drift."""
    version = expected or get_current_version()
    problems: List[str] = []
    optional_files = {
        ".codex-plugin/plugin.json",
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
    check(".codex-plugin/plugin.json", f'"version": "{version}"', "codex plugin version")
    check("src/version_checker.py", f'CURRENT_VERSION = "{version}"', "client version")
    check("src/cloud/config.py", f'HARDCODED_VERSION = "{version}"', "backend version")
    check("src/cloud/services.py", "HARDCODED_VERSION", "services UA constant")
    check("src/cloud/stripe.py", "HARDCODED_VERSION", "stripe UA constant")
    check("src/mcp_server.py", f'"version": "{version}"', "mcp server version")
    check("src/telemetry_sync.py", f"TokenSafe-Sync/{version}", "sync UA")
    check("tests/test_cloud_backend.py", "LATEST_VERSION", "tests client_version constant")

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
    """Drafts release notes from conventional commits since the previous release tag."""
    current_tag = f"v{new_version}"
    tags_res = subprocess.run(
        ["git", "tag", "--sort=-v:refname"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
    )
    prev_tags = [t.strip() for t in tags_res.stdout.splitlines() if t.strip() and t.strip() != current_tag]
    last_tag = prev_tags[0] if prev_tags else ""
    if not last_tag:
        last_tag = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        ).stdout.strip().split("\n")[0]
    res = subprocess.run(
        ["git", "log", f"{last_tag}..HEAD", "--pretty=%s"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
    )
    subjects = [
        s.strip() for s in res.stdout.split("\n")
        if s.strip() and not s.strip().startswith("chore(release): bump version")
    ]
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


def _sh(args: List[str], timeout: int = 120, cwd: Optional[str] = None) -> Tuple[int, str]:
    res = subprocess.run(args, cwd=cwd or PROJECT_ROOT, capture_output=True, text=True, timeout=timeout)
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def resolve_npm_token() -> str:
    """Resolves npm granular/publish token from env or untracked .env files (no hardcoded fallbacks)."""
    for key in ("NODE_AUTH_TOKEN", "NPM_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    for fname in (".env", ".env.local", ".env.release"):
        env_path = os.path.join(PROJECT_ROOT, fname)
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() in ("NODE_AUTH_TOKEN", "NPM_TOKEN"):
                        cleaned = v.strip().strip("'\"")
                        if cleaned:
                            return cleaned
        except OSError:
            pass
    return ""


def npm_publish_with_auth(otp: Optional[str] = None) -> Tuple[int, str, str]:
    """Publishes to npm using explicit OTP, ephemeral token userconfig, or local npmrc.
    Returns (returncode, output, auth_mode)."""
    if otp:
        code, out = _sh(["npm", "publish", "--otp", otp], timeout=180)
        return code, out, "otp"

    token = resolve_npm_token()
    if token:
        fd, tmp_npmrc = tempfile.mkstemp(prefix=".tokensafe_npmrc_", text=True)
        try:
            os.chmod(tmp_npmrc, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"//registry.npmjs.org/:_authToken={token}\n")
            code, out = _sh(["npm", "publish", "--userconfig", tmp_npmrc], timeout=180)
            return code, out, "token"
        finally:
            try:
                os.unlink(tmp_npmrc)
            except OSError:
                pass

    code, out = _sh(["npm", "publish"], timeout=180)
    return code, out, "local"


def resolve_cloud_repo() -> Optional[str]:
    """Locates the sibling tokensafe-cloud checkout for automatic post-PyPI sync."""
    candidates = [
        os.environ.get("TOKENSAFE_CLOUD_DIR", "").strip(),
        os.path.join(os.path.dirname(PROJECT_ROOT), "tokensafe-cloud"),
    ]
    for path in candidates:
        if path and os.path.isdir(os.path.join(path, ".git")) and os.path.exists(os.path.join(path, "src", "cloud", "config.py")):
            return os.path.abspath(path)
    return None


def wait_for_pypi_simple(version: str, attempts: int = 18, delay: int = 10) -> bool:
    """Waits for PyPI's Simple Index CDN to serve `version` before triggering cloud CI/Docker builds."""
    import urllib.request
    found = False
    for _ in range(attempts):
        try:
            req = urllib.request.Request("https://pypi.org/simple/tokensafe/", headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read().decode("utf-8", errors="ignore")
            if version in body:
                found = True
                break
        except Exception:
            pass
        time.sleep(delay)
    if found:
        # Give global Fastly CDN edges across AWS/Azure data centers 30s to replicate before Docker builds run
        time.sleep(30)
        return True
    return False


def update_cloud_repo_version(cloud_root: str, next_v: str) -> None:
    """Updates all version pins & manifests inside tokensafe-cloud to `next_v`."""
    replace_in_file(
        "src/cloud/config.py",
        r'(HARDCODED_VERSION\s*=\s*)"[^"]+"',
        rf'\g<1>"{next_v}"',
        root_dir=cloud_root,
    )
    replace_in_file(
        "Dockerfile.cloud",
        r'tokensafe>=[0-9]+\.[0-9]+\.[0-9]+,<3',
        f"tokensafe>={next_v},<3",
        root_dir=cloud_root,
    )
    replace_in_file(
        ".github/workflows/test.yml",
        r'tokensafe>=[0-9]+\.[0-9]+\.[0-9]+,<3',
        f"tokensafe>={next_v},<3",
        root_dir=cloud_root,
    )
    replace_in_file("package.json", r'("version"\s*:\s*)"[^"]+"', rf'\g<1>"{next_v}"', root_dir=cloud_root)
    replace_in_file("plugin.json", r'("version"\s*:\s*)"[^"]+"', rf'\g<1>"{next_v}"', root_dir=cloud_root)
    replace_in_file(".codex-plugin/plugin.json", r'("version"\s*:\s*)"[^"]+"', rf'\g<1>"{next_v}"', root_dir=cloud_root, optional=True)
    replace_in_file("pyproject.toml", r'(version\s*=\s*)"[^"]+"', rf'\g<1>"{next_v}"', root_dir=cloud_root)
    replace_in_file("src/version_checker.py", r'(CURRENT_VERSION\s*=\s*)"[^"]+"', rf'\g<1>"{next_v}"', root_dir=cloud_root, optional=True)
    replace_in_file("src/mcp_server.py", r'("name"\s*:\s*"tokensafe-mcp",\s*"version"\s*:\s*)"[^"]+"', rf'\g<1>"{next_v}"', root_dir=cloud_root, optional=True)
    replace_in_file("src/telemetry_sync.py", r'TokenSafe-Sync/[0-9]+\.[0-9]+\.[0-9]+', f'TokenSafe-Sync/{next_v}', root_dir=cloud_root, optional=True)

    server_json_path = os.path.join(cloud_root, "server.json")
    if os.path.exists(server_json_path):
        with open(server_json_path, "r", encoding="utf-8") as f:
            server_data = json.load(f)
        changed = server_data.get("version") != next_v
        server_data["version"] = next_v
        for pkg in server_data.get("packages", []):
            if isinstance(pkg, dict) and "version" in pkg:
                if pkg["version"] != next_v:
                    changed = True
                pkg["version"] = next_v
        if changed:
            with open(server_json_path, "w", encoding="utf-8") as f:
                json.dump(server_data, f, indent=2)
                f.write("\n")
            print("  ✓ Updated server.json (tokensafe-cloud)")


def sync_and_push_cloud_repo(cloud_root: str, next_v: str) -> Tuple[bool, str, Optional[str]]:
    """Aligns tokensafe-cloud to `next_v`, runs cloud tests over fresh client overlay, commits, and pushes."""
    update_cloud_repo_version(cloud_root, next_v)
    code, status_out = _sh(["git", "status", "--porcelain"], cwd=cloud_root)
    dirty = [line for line in status_out.splitlines() if line.strip() and not line.startswith("?? data/")]
    if not dirty:
        sha = _sh(["git", "rev-parse", "HEAD"], cwd=cloud_root)[1].strip()
        return True, "already synced", sha

    # Mirror cloud CI (checkout src/ + fresh client src/*.py) in an isolated temp dir
    # so stale untracked copies inside cloud_root cannot mask client/cloud compatibility issues.
    with tempfile.TemporaryDirectory(prefix="ts_cloud_ci_") as overlay_dir:
        overlay_src = os.path.join(overlay_dir, "src")
        shutil.copytree(os.path.join(PROJECT_ROOT, "src"), overlay_src)
        cloud_src = os.path.join(cloud_root, "src")
        for item in os.listdir(cloud_src):
            s_path = os.path.join(cloud_src, item)
            d_path = os.path.join(overlay_src, item)
            if item in ("cloud", "cloud_backend.py", "mailer.py"):
                if os.path.isdir(s_path):
                    shutil.copytree(s_path, d_path, dirs_exist_ok=True)
                else:
                    shutil.copy2(s_path, d_path)
        shutil.copytree(os.path.join(cloud_root, "tests"), os.path.join(overlay_dir, "tests"))
        for dname in ("hooks", "rules", "skills"):
            d_src = os.path.join(cloud_root, dname)
            if os.path.isdir(d_src):
                shutil.copytree(d_src, os.path.join(overlay_dir, dname))
        for fname in ("package.json", "plugin.json", "pyproject.toml", "server.json", "Dockerfile.cloud"):
            fpath = os.path.join(cloud_root, fname)
            if os.path.exists(fpath):
                shutil.copy2(fpath, os.path.join(overlay_dir, fname))
        code, out = _sh([sys.executable, "-m", "unittest", "discover", "tests"], timeout=300, cwd=overlay_dir)
    if code != 0:
        print(out[-1500:])
        return False, "tokensafe-cloud tests failed", None

    _sh(["git", "add", "src/cloud/config.py", "Dockerfile.cloud", ".github/workflows/test.yml",
         "package.json", "plugin.json", ".codex-plugin/plugin.json", "pyproject.toml", "server.json"], cwd=cloud_root)
    c_code, c_out = _sh(["git", "commit", "-m", f"chore(release): align cloud backend and manifests to v{next_v}"], cwd=cloud_root)
    if c_code != 0:
        return False, f"commit failed: {c_out[-300:]}", None
    p_code, p_out = _sh(["git", "push", "origin", "main"], cwd=cloud_root)
    if p_code != 0:
        return False, f"push failed: {p_out[-300:]}", None

    # Refresh the local gitignored dist copies (src/*.py) so the cloud working
    # tree matches the freshly released client instead of drifting stale (the
    # CI merge step does the same against a fresh checkout; locally the stale
    # copies would otherwise shadow `import src` during manual test runs).
    try:
        import glob as _glob
        for src_file in _glob.glob(os.path.join(PROJECT_ROOT, "src", "*.py")):
            shutil.copy2(src_file, os.path.join(cloud_root, "src", os.path.basename(src_file)))
    except Exception:
        pass

    sha = _sh(["git", "rev-parse", "HEAD"], cwd=cloud_root)[1].strip()
    return True, "pushed", sha


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


def wait_for_npm(version: str, attempts: int = 24, delay: int = 10) -> bool:
    """Waits for the npm registry to serve `version` (bounded ~4 min). The MCP
    Registry refuses packages npm does not list yet, so a CDN lag right after
    publish used to break Phase 7 and force a manual resume."""
    for _ in range(attempts):
        if npm_version_published(version):
            return True
        time.sleep(delay)
    return False


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


def run_release(target: str, otp: Optional[str] = None, no_bump: bool = False, skip_cloud: bool = False):
    """Full release pipeline. Every phase is idempotent: re-running after a
    failure or an interactive gate (npm OTP) resumes where it stopped."""
    import glob as _glob

    v = get_current_version()
    # Resume semantics: if the current version is already tagged AND published
    # somewhere, this run finishes THAT version instead of bumping again —
    # 'release minor' after an npm-OTP pause must not silently create 1.(n+1).0.
    tag_sha = _sh(["git", "rev-parse", f"v{v}"])[1].strip() if _sh(["git", "rev-parse", f"v{v}"])[0] == 0 else ""
    head_sha = _sh(["git", "rev-parse", "HEAD"])[1].strip()
    tag_at_head = bool(tag_sha and tag_sha == head_sha)
    tag_exists = bool(tag_sha)
    gh_release_exists = _sh(["gh", "release", "view", f"v{v}"])[0] == 0
    fully_shipped = (
        tag_exists and gh_release_exists
        and pypi_version_published(v) and npm_version_published(v)
        and registry_version_published(v)
    )
    if no_bump:
        next_v = v
        print(f"🚀 Release pipeline: FINISH-ONLY v{next_v} (--no-bump: complete pending channels, no version bump)\n")
    elif tag_at_head and not fully_shipped:
        # In-flight release (e.g. paused on npm 2FA at this commit): finish THIS version.
        next_v = v
        print(f"🚀 Release pipeline: RESUME v{next_v} (tagged at HEAD, channels incomplete — completing)\n")
    elif fully_shipped or (tag_exists and not tag_at_head):
        next_v = calculate_next_version(v, target)
        if not fully_shipped and tag_exists and not tag_at_head:
            print(f"ℹ  Current commit is ahead of tag v{v} — advancing to NEW release v{next_v}.\n")
        else:
            print(f"⚠  v{v} is already fully shipped everywhere — a NEW release v{next_v} will be created.")
            print(f"   If you only wanted to finish pending channels, abort NOW and use --no-bump. Continuing in 5s...")
            time.sleep(5)
            print(f"🚀 Release pipeline: {v} ➜ {next_v}\n")
    else:
        next_v = calculate_next_version(v, target)
        print(f"🚀 Release pipeline: {v} ➜ {next_v}\n")
    tag = f"v{next_v}"
    pending: List[str] = []

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

    cloud_root = None if skip_cloud else resolve_cloud_repo()
    if cloud_root:
        c_branch = _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cloud_root)[1].strip()
        if c_branch != "main":
            print(f"❌ Sibling tokensafe-cloud must be on main (currently {c_branch}). Pass --skip-cloud to bypass.")
            sys.exit(1)
        c_status = _sh(["git", "status", "--porcelain"], cwd=cloud_root)[1].strip()
        c_blocking = [l for l in c_status.splitlines() if l.strip() and not l.startswith("?? data/")]
        if c_blocking:
            print("❌ Sibling tokensafe-cloud has uncommitted changes (commit/stash or pass --skip-cloud):")
            for l in c_blocking[:10]:
                print(f"   {l}")
            sys.exit(1)
    print(f"  ✔ main, clean tree, v{v} consistent" + (" (cloud sync enabled)\n" if cloud_root else "\n"))

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
    print("── Phase 6: npm (token / 2FA)")
    if npm_version_published(next_v):
        print(f"  ℹ {next_v} already on npm")
    else:
        code, out, mode = npm_publish_with_auth(otp=otp)
        if code == 0 and ("staged" in out.lower() or "waiting for" in out.lower()):
            print("  ⏳ npm staged (waiting for 2FA/publish confirmation in browser or email)")
            pending.append("npm publish staged — approve in npmjs.com, then re-run --resume")
        elif code == 0:
            print(f"  ✔ npm published ({mode})")
            # Give the npm registry a bounded window to list the version before
            # Phase 7 (the MCP Registry needs it) — avoids the CDN-lag pending.
            if not wait_for_npm(next_v):
                print("  ⚠ npm registry did not list the version yet — Phase 7 may need a resume")
        elif "previously staged version" in out or "E409" in out:
            print("  ⏳ npm version is already STAGED and waiting for your 2FA confirmation on npmjs.com (or email link).")
            print("     Approve the staged publish on npmjs.com, then re-run: python3 scripts/release.py release --resume")
            pending.append("npm publish (staged — approve 2FA on npmjs.com)")
        elif "EOTP" in out:
            import re as _re
            m = _re.search(r"(https://www\.npmjs\.com/auth/cli/\S+)", out)
            print("  ⚠ npm requires 2FA. Complete auth (or set NPM_TOKEN with Bypass 2FA), then re-run:")
            if m:
                print(f"      {m.group(1)}")
            print("      python3 scripts/release.py release --resume  (or: npm publish)")
            pending.append("npm publish (waiting for your 2FA)")
        else:
            print(out[-400:]); pending.append(f"npm publish ({mode})")

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
            published_now = False
            if not npm_version_published(next_v):
                print("  ⏳ npm CDN lag — waiting for the version to appear, then retrying the registry...")
                if wait_for_npm(next_v):
                    code_r, out_r = _sh([publisher, "publish", "server.json"], timeout=120)
                    if code_r == 0:
                        print("  ✔ registry published after npm CDN catch-up")
                        published_now = True
                    else:
                        print(out_r[-300:])
            if not published_now and not registry_version_published(next_v):
                pending.append("mcp-publisher publish")

    # Phase 8: tokensafe-cloud sync & auto-deploy ------------------------------
    cloud_head_sha: Optional[str] = None
    if cloud_root:
        print("── Phase 8: tokensafe-cloud sync & deploy")
        if pypi_version_published(next_v):
            simple_ok = wait_for_pypi_simple(next_v, attempts=18, delay=10)
            if not simple_ok:
                print("  ⚠ PyPI simple index CDN did not show version yet — skipping cloud push to avoid CI failure")
                pending.append(f"tokensafe-cloud sync (retry once PyPI simple serves {next_v})")
            else:
                ok_c, msg_c, cloud_head_sha = sync_and_push_cloud_repo(cloud_root, next_v)
                if ok_c:
                    print(f"  ✔ tokensafe-cloud {msg_c} ({(cloud_head_sha or '')[:7]})")
                else:
                    print(f"  ⚠ tokensafe-cloud sync failed: {msg_c}")
                    pending.append("tokensafe-cloud sync & push")
        else:
            print("  ℹ PyPI not published yet — skipping cloud sync")
            pending.append("tokensafe-cloud sync & push (after PyPI)")

    # Phase 9: rollout verification --------------------------------------------------
    print("── Phase 9: rollout verification")
    health_attempts, health_delay = (8, 15) if cloud_head_sha else (2, 2)
    health_ok = wait_for_prod_version(next_v, attempts=health_attempts, delay=health_delay)
    print(f"  {'✔' if health_ok else '⚠' if cloud_head_sha else 'ℹ'} prod /health: "
          f"{'v' + next_v if health_ok else 'still rolling — check https://api.tokensafe.dev/health'}")
    print(f"  {'✔' if pypi_version_published(next_v) else '⚠'} PyPI {next_v}")
    print(f"  {'✔' if npm_version_published(next_v) else '⚠'} npm {next_v}")
    print(f"  {'✔' if registry_version_published(next_v) else '⚠'} MCP Registry {next_v} active")

    def _watch_ci(repo_slug: str, sha: str, workflow: str) -> Tuple[bool, str]:
        concl_res = "pending"
        for _ in range(6):
            code_r, out_r = _sh(["gh", "run", "list", "-R", repo_slug,
                                 "--limit", "10", "--json", "conclusion,workflowName,headSha,status"], timeout=30)
            if code_r == 0 and out_r.strip():
                try:
                    runs = json.loads(out_r)
                    matched = [r for r in runs if r.get("workflowName") == workflow and r.get("headSha") == sha]
                    if matched:
                        concl_res = matched[0].get("conclusion") or matched[0].get("status") or "pending"
                        if concl_res in ("success", "failure", "cancelled"):
                            return (concl_res == "success"), concl_res
                except Exception:
                    pass
            time.sleep(20)
        return False, concl_res

    head_sha = _sh(["git", "rev-parse", "HEAD"])[1].strip()
    public_ci_ok, concl = _watch_ci("tokensafe-dev/tokensafe", head_sha, "Tests & Lint")
    print(f"  {'✔' if public_ci_ok else '⚠'} public repo CI ({head_sha[:7]}): {concl}")
    if concl in ("failure", "cancelled"):
        pending.append("public repo CI failed — inspect gh run logs (tokensafe-dev/tokensafe)")

    if cloud_head_sha:
        cloud_ci_ok, cloud_concl = _watch_ci("tokensafe-dev/tokensafe-cloud", cloud_head_sha, "Tests & Lint")
        print(f"  {'✔' if cloud_ci_ok else '⚠'} cloud repo CI ({cloud_head_sha[:7]}): {cloud_concl}")
        if cloud_concl in ("failure", "cancelled"):
            pending.append("cloud repo CI failed — inspect gh run logs (tokensafe-dev/tokensafe-cloud)")

    print("\n════════════════════════════════════════")
    if pending:
        print(f"⏳ Pending ({len(pending)}) — resolve, then re-run the same command to continue:")
        for p in pending:
            print(f"   • {p}")
    else:
        print(f"🎉 Release {tag} shipped everywhere.")
    print("════════════════════════════════════════")


def parse_release_args(args: List[str]) -> Tuple[str, Optional[str], bool, bool]:
    """Parses `release` subcommand args: `[target] [--otp=CODE|--otp CODE] [--no-bump|--resume] [--skip-cloud]`."""
    otp: Optional[str] = None
    no_bump = False
    skip_cloud = False
    positional: List[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--otp="):
            otp = arg.split("=", 1)[1].strip() or None
        elif arg == "--otp" and i + 1 < len(args):
            i += 1
            otp = args[i].strip() or None
        elif arg in ("--no-bump", "--resume"):
            no_bump = True
        elif arg == "--skip-cloud":
            skip_cloud = True
        elif not arg.startswith("-"):
            positional.append(arg)
        i += 1
    target = positional[0] if positional else "patch"
    return target, otp, no_bump, skip_cloud


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
        print("  python3 scripts/release.py release [patch|minor|major|X.Y.Z] [--otp=CODE] [--no-bump|--resume] [--skip-cloud]")
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
        target, otp, no_bump, skip_cloud = parse_release_args(sys.argv[2:])
        run_release(target, otp=otp, no_bump=no_bump, skip_cloud=skip_cloud)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
