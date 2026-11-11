# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.4.x   | :white_check_mark: |
| 2.3.x   | :white_check_mark: |
| < 2.3.0 | :x:                |

---

## Reporting a Vulnerability

We take the security of TokenSafe and our users' credentials seriously.

If you discover a security vulnerability (such as secret leak risks, proxy bypasses, or license verification flaws), **do not open a public issue**.

### Preferred Channels

1. **GitHub Security Advisory**: Submit a private report via [GitHub Advisory Submission](https://github.com/tokensafe-dev/tokensafe/security/advisories/new).
2. **Email**: Send details directly to `support@tokensafe.dev`.

### What to Include

Please provide:
- Description of the issue and affected components (e.g., `src/secret_redactor.py`, `src/proxy.py`, `src/license_manager.py`).
- Steps to reproduce or a minimal proof of concept (PoC).
- Potential impact.

### Response Timeline

- **Initial Response**: Within 48 hours.
- **Triage & Status Update**: Within 5 business days.
- **Public Disclosure**: Coordinated after a patched release is published.
