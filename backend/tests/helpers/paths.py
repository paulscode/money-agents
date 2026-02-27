"""Canonical path resolution for tests.

This module is the SINGLE SOURCE OF TRUTH for resolving file paths in tests.
Import from here instead of computing paths per-file. This prevents drift
when tests are written across different AI chat sessions, some running on the
host and some inside the Docker container.

Usage::

    from tests.helpers.paths import (
        IN_DOCKER, BACKEND_ROOT, PROJECT_ROOT,
        backend_file, project_file, require_file,
    )

    # Read a backend source file (always available)
    source = backend_file("app", "main.py").read_text()

    # Read a project-level file that may not exist in Docker
    path = project_file("docker-compose.yml")
    if not path.exists():
        pytest.skip("docker-compose.yml not available in Docker")

    # Or use the convenience helper that auto-skips:
    source = require_file("frontend/src/App.tsx")
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

IN_DOCKER: bool = (
    os.path.exists("/.dockerenv")
    or os.environ.get("CONTAINER", "") == "1"
    # Also detect by checking if we're running at /app (the backend container root)
    or str(Path(__file__).resolve()).startswith("/app/")
)
"""True when tests are running inside the backend Docker container."""


# ---------------------------------------------------------------------------
# Root paths
# ---------------------------------------------------------------------------

BACKEND_ROOT: Path = Path(__file__).resolve().parents[2]
"""The backend root directory.

- On host: /home/<user>/workspace/money-agents/backend
- In Docker: /app

This is always valid — tests always run from within the backend tree.
"""

PROJECT_ROOT: Path | None = BACKEND_ROOT.parent if not IN_DOCKER else None
"""The monorepo root directory (one level above backend/).

- On host: /home/<user>/workspace/money-agents
- In Docker: None (project-level files are not mounted)

Always check ``is not None`` and ``.exists()`` before using.
"""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def backend_file(*parts: str) -> Path:
    """Resolve a path relative to the backend root.

    Example::

        backend_file("app", "services", "llm_service.py")
        # → /app/app/services/llm_service.py  (Docker)
        # → /home/.../backend/app/services/llm_service.py  (host)
    """
    return BACKEND_ROOT.joinpath(*parts)


def project_file(*parts: str) -> Path:
    """Resolve a path relative to the project root.

    Returns a path even in Docker (so callers can check ``.exists()``),
    but the path will almost certainly not exist inside the container.

    Example::

        project_file("frontend", "src", "App.tsx")
        project_file("docker-compose.yml")
    """
    if PROJECT_ROOT is not None:
        return PROJECT_ROOT.joinpath(*parts)
    # In Docker, return a path under / that won't exist — callers check .exists()
    return Path("/", *parts)


def require_file(rel_path: str) -> str:
    """Read a file by relative path, auto-skipping if unavailable.

    Tries ``backend/`` prefix stripping for compatibility with paths written
    as ``"backend/app/main.py"`` (common when tests are written on the host).

    Raises ``pytest.skip`` if the file doesn't exist.

    Example::

        source = require_file("backend/app/main.py")
        source = require_file("frontend/src/App.tsx")
    """
    # Normalise path separators
    rel_path = rel_path.replace("\\", "/")

    # Try backend-relative first (with prefix stripping)
    if rel_path.startswith("backend/"):
        candidate = backend_file(*rel_path[len("backend/"):].split("/"))
        if candidate.exists():
            return candidate.read_text()

    # Try backend-relative (without prefix)
    candidate = backend_file(*rel_path.split("/"))
    if candidate.exists():
        return candidate.read_text()

    # Try project-relative
    candidate = project_file(*rel_path.split("/"))
    if candidate.exists():
        return candidate.read_text()

    pytest.skip(f"File not available in this environment: {rel_path}")
    # pytest.skip raises — this is unreachable, but makes type checkers happy
    return ""  # pragma: no cover
