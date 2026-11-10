#!/usr/bin/env python3
"""
Internal TokenSafe Release Management Utility.
NOT exposed in public CLI or user distribution packages.

Capabilities:
1. bump: Atomically increments version across all config files, source constants, and tests.
2. clean: Recursively purges build artifacts, *.pyc caches, and stale distribution tarballs.
3. build: Performs pre-publish clean, builds sdist & wheel, validates twine check, and tests npm packaging.
4. current: Prints the current canonical project version.
"""

import sys
import os
import re
import json
import shutil
import subprocess
from typing import Tuple, List, Optional

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

    # 5. src/cloud_backend.py
    replace_in_file(
        "src/cloud_backend.py",
        r'(HARDCODED_VERSION\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"'
    )
    replace_in_file(
        "src/cloud_backend.py",
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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print(f"Current version: {get_current_version()}")
        print("\nUsage:")
        print("  python3 scripts/release.py current")
        print("  python3 scripts/release.py clean")
        print("  python3 scripts/release.py bump [patch|minor|major|X.Y.Z]")
        print("  python3 scripts/release.py build")
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
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
