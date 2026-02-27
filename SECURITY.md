# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability, please report it responsibly by:

1. **Email**: Open a private [GitHub Security Advisory](https://github.com/paulscode/money-agents/security/advisories/new) on the repository.

2. **Include in your report**:
   - A description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact
   - Any suggested fixes (optional)

## Response Timeline

- **Acknowledgement**: Within 48 hours
- **Initial assessment**: Within 7 days
- **Fix or mitigation**: Depends on severity; critical issues are prioritised

## Scope

This project handles:
- API keys and credentials (stored in `.env`, never committed)
- JWT authentication tokens
- Bitcoin/Lightning Network wallet operations
- AI model API access

Reports related to these areas are especially appreciated.

## Disclosure Policy

We follow coordinated disclosure. Please allow us reasonable time to address the issue before any public disclosure.
