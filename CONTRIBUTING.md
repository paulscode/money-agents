# Contributing to Money Agents

Thank you for your interest in contributing! This document outlines the process for contributing to this project.

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/paulscode/money-agents.git
   cd money-agents
   ```
3. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Setup

See [README.md](README.md) for full setup instructions. The short version:

```bash
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
```

## Making Changes

- **Keep PRs focused** — one feature or fix per pull request
- **Write tests** — add or update tests for any changed behaviour
- **Follow existing code style** — the project uses Black (Python) and ESLint/Prettier (TypeScript)
- **Update documentation** — keep README and relevant docs in sync with changes

## Running Tests

```bash
# Backend tests
cd backend
pytest

# Frontend tests
cd frontend
npm test
```

## Submitting a Pull Request

1. Ensure all tests pass
2. Push your branch to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```
3. Open a pull request against the `main` branch
4. Fill in the pull request template
5. A maintainer will review your PR

## Reporting Bugs

Please use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) when opening an issue. Include:
- A clear description of the problem
- Steps to reproduce
- Expected vs actual behaviour
- Environment details (OS, Python/Node version, Docker version)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this standard.

## Questions?

Open a [GitHub Discussion](https://github.com/paulscode/money-agents/discussions) for general questions.
