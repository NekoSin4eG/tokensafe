#!/usr/bin/env node

/**
 * TokenSafe - Universal Node.js CLI & MCP Launcher
 * Works with `npx tokensafe [mcp|proxy|stats|benchmark|install]`
 */

const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT_DIR = path.resolve(__dirname, '..');
const args = process.argv.slice(2);

// Locate system Python 3 interpreter
function findPython() {
  const candidates = ['python3', 'python'];
  for (const cmd of candidates) {
    try {
      const res = spawnSync(cmd, ['-c', 'import sys; assert sys.version_info >= (3, 10)'], {
        encoding: 'utf-8',
        stdio: 'pipe'
      });
      if (res.status === 0) return cmd;
    } catch (_) {}
  }
  return null;
}

const command = args[0] || 'install';

if (command === 'help' || command === '--help' || command === '-h') {
  console.log(`
⚡ TokenSafe - Autonomous Zero-Loss Token Optimizer for AI Coding Agents
Website: https://tokensafe.dev

Usage:
  npx tokensafe [command]

Commands:
  npx tokensafe              Interactive setup for Cursor, Antigravity, Claude & Codex
  npx tokensafe mcp          Start Universal MCP Server (for Claude Desktop / Cursor)
  npx tokensafe proxy [port] Start Transparent Local AI Proxy (default: 8765)
  npx tokensafe graph        Analyze repository God Nodes & dependency topology
  npx tokensafe map          Generate tokensafe-out/API_SURFACE.md interface snapshot
  npx tokensafe stats        Show token economy savings & performance metrics
  npx tokensafe benchmark    Run zero-loss verification benchmark suite
  npx tokensafe activate     Activate commercial or team seat license key
  npx tokensafe --help       Show this help screen
`);
  process.exit(0);
}

const python = findPython();
if (!python) {
  console.error('\n❌ TokenSafe requires Python 3.10+ installed on your system.');
  console.error('Please install Python from https://python.org or via your package manager:');
  console.error('  macOS:  brew install python3');
  console.error('  Debian: sudo apt-get install python3\n');
  process.exit(1);
}

let scriptPath = '';
let scriptArgs = [];

switch (command) {
  case 'mcp':
    scriptPath = path.join(ROOT_DIR, 'src', 'mcp_server.py');
    scriptArgs = args.slice(1);
    break;

  case 'proxy':
    scriptPath = path.join(ROOT_DIR, 'src', 'proxy.py');
    scriptArgs = args.slice(1);
    break;

  case 'graph':
  case 'map':
  case 'skeleton':
    scriptPath = path.join(ROOT_DIR, 'src', 'graph_engine.py');
    scriptArgs = args;
    break;

  case 'stats':
  case 'dashboard':
    scriptPath = path.join(ROOT_DIR, 'src', 'dashboard.py');
    scriptArgs = ['--cli', ...args.slice(1)];
    break;

  case 'benchmark':
    scriptPath = path.join(ROOT_DIR, 'src', 'benchmark.py');
    scriptArgs = args.slice(1);
    break;

  case 'activate':
    scriptPath = path.join(ROOT_DIR, 'src', 'license_manager.py');
    scriptArgs = args.slice(1);
    break;

  case 'install':
  case 'init':
    scriptPath = path.join(ROOT_DIR, 'src', 'installer.py');
    scriptArgs = args.slice(1);
    break;

  default:
    scriptPath = path.join(ROOT_DIR, 'src', 'installer.py');
    scriptArgs = args;
    break;
}

if (!fs.existsSync(scriptPath)) {
  console.error(`Error: TokenSafe script not found at ${scriptPath}`);
  process.exit(1);
}

const child = spawn(python, [scriptPath, ...scriptArgs], {
  stdio: 'inherit',
  cwd: process.cwd(),
  env: {
    ...process.env,
    PYTHONPATH: ROOT_DIR + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : '')
  }
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code || 0);
  }
});
