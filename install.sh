#!/usr/bin/env bash
# Installation script for Antigravity Token Optimizer Plugin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${HOME}/.gemini/config/plugins/agy-token-optimizer"

echo "⚡ Installing Antigravity Token Optimizer Plugin..."

mkdir -p "${HOME}/.gemini/config/plugins"

if [ -L "${TARGET_DIR}" ] || [ -d "${TARGET_DIR}" ]; then
    echo "  Removing existing installation at ${TARGET_DIR}..."
    rm -rf "${TARGET_DIR}"
fi

echo "  Creating symlink: ${TARGET_DIR} -> ${SCRIPT_DIR}"
ln -s "${SCRIPT_DIR}" "${TARGET_DIR}"

echo "  Making agy-tokens executable..."
chmod +x "${SCRIPT_DIR}/bin/agy-tokens"

echo ""
echo "✅ Antigravity Token Optimizer successfully installed!"
echo "   Plugin location: ${TARGET_DIR}"
echo "   CLI tool: ${SCRIPT_DIR}/bin/agy-tokens"
echo ""
echo "Try running:"
echo "   ${SCRIPT_DIR}/bin/agy-tokens stats"
echo "   ${SCRIPT_DIR}/bin/agy-tokens test \"Слушай, перепиши функцию get_balance в wallet.ts\""
