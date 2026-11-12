#!/usr/bin/env bash
# Installation script for TokenSafe Plugin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${HOME}/.gemini/config/plugins/tokensafe"

echo "⚡ Installing TokenSafe Plugin..."

mkdir -p "${HOME}/.gemini/config/plugins"

# Migrate away from the legacy plugin directory name: Antigravity keys plugin
# enable-state by folder name, so a stale legacy symlink would register the
# plugin twice and run every hook twice per turn. Remove only symlinks.
LEGACY_DIR="${HOME}/.gemini/config/plugins/agy-token-optimizer"
if [ -L "${LEGACY_DIR}" ]; then
    echo "  Removing legacy symlink at ${LEGACY_DIR}..."
    rm -f "${LEGACY_DIR}"
fi

if [ -L "${TARGET_DIR}" ] || [ -d "${TARGET_DIR}" ]; then
    echo "  Removing existing installation at ${TARGET_DIR}..."
    rm -rf "${TARGET_DIR}"
fi

echo "  Creating symlink: ${TARGET_DIR} -> ${SCRIPT_DIR}"
ln -s "${SCRIPT_DIR}" "${TARGET_DIR}"

if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3.10+ is required but not found in PATH."
    exit 1
fi

if ! python3 -c "import cryptography" 2>/dev/null; then
    echo "  Installing required dependency: cryptography>=42.0..."
    VENV_DIR="${HOME}/.tokensafe/venv"
    if python3 -m pip install --user --quiet "cryptography>=42.0" 2>/dev/null \
        || python3 -m pip install --quiet --break-system-packages "cryptography>=42.0" 2>/dev/null; then
        :
    elif python3 -m venv "${VENV_DIR}" 2>/dev/null \
        && "${VENV_DIR}/bin/python3" -m pip install --quiet "cryptography>=42.0" 2>/dev/null; then
        echo "  Installed 'cryptography>=42.0' into ${VENV_DIR}"
    else
        echo "❌ Failed to install 'cryptography>=42.0'. Run: pip install 'cryptography>=42.0'"
        exit 1
    fi
fi

echo "  Making CLI executable..."
chmod +x "${SCRIPT_DIR}/bin/tokensafe"
chmod +x "${SCRIPT_DIR}/bin/agy-tokens"

echo ""
echo "✅ TokenSafe successfully installed!"
echo "   Plugin location: ${TARGET_DIR}"
echo "   CLI tool: tokensafe (or ${SCRIPT_DIR}/bin/tokensafe)"
echo ""
echo "Try running:"
echo "   tokensafe stats"
echo "   tokensafe test \"Please rewrite the get_balance function in wallet.ts\""
