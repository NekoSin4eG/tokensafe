# Contributing to TokenSafe

Thank you for your interest in contributing to TokenSafe! We welcome contributions to improve token efficiency, agent hooks, AST parsers, and Model Context Protocol (MCP) tooling.

---

## 🎯 Core Philosophy & Guidelines

1. **Zero External Runtime Dependencies**:
   - The TokenSafe client (`src/` excluding cloud services) must strictly run on the Python standard library.
   - Do not add runtime dependencies to `pyproject.toml` or `package.json`. Standard library portability across all operating systems is mandatory.
2. **Deterministic Token Economy**:
   - Every modification to compactor, slicer, or AST skeletonizer must be benchmarked for token savings and verified for zero code semantics loss.
3. **Comprehensive Unit Tests**:
   - Every new feature, hook, or bug fix must include corresponding tests under `tests/`.

---

## 🛠️ Local Development Setup

Clone the repository and verify your Python environment:

```bash
git clone https://github.com/NekoSin4eG/tokensafe.git
cd tokensafe

# Verify Python version (requires Python 3.10+)
python3 --version
```

### Running Tests

Execute the full test suite using Python's standard `unittest`:

```bash
python3 -m unittest discover tests
```

To run a single test module:

```bash
python3 -m unittest tests/test_compactor.py
```

### Testing the CLI

```bash
# Run local standalone Python binary
./bin/tokensafe --help

# Test Node.js launcher
node ./bin/tokensafe.js --help
```

---

## 🔀 Submitting a Pull Request

1. **Fork and branch**: Create a descriptive feature branch (`feat/new-slicer` or `fix/mcp-schema-parsing`).
2. **Write tests**: Add unit tests for your changes in `tests/`.
3. **Ensure tests pass**: Run `python3 -m unittest discover tests`.
4. **Open a PR**: Fill out the PR template with a summary of changes, benchmark numbers (if applicable), and testing steps.

---

## 💬 Community & Questions

- Report bugs or request features via [GitHub Issues](https://github.com/NekoSin4eG/tokensafe/issues).
- For security vulnerabilities, refer to [SECURITY.md](SECURITY.md).
