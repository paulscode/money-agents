"""
Centralized file-path validation for tool executors (SA3-C1).

All tool executors that accept agent-supplied file paths must call
``validate_tool_file_path()`` BEFORE opening or forwarding the path.
This prevents arbitrary file read from the backend container filesystem
(e.g. /app/.env, /proc/self/environ, private keys).

Allowed roots are intentionally narrow:
  /data            – shared data volume (tool outputs, media cache)
  /app/uploads     – user file uploads
  /tmp             – ephemeral files (sandbox artefacts, temp downloads)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories that tool-supplied file paths are permitted to reference.
# Paths are resolved (symlinks/.. collapsed) before checking.
ALLOWED_DATA_ROOTS: tuple[str, ...] = (
    "/data",
    "/app/uploads",
    "/tmp",
)


def validate_tool_file_path(path_str: str, *, label: str = "file") -> Path:
    """Validate that *path_str* resolves inside an allowed directory.

    Args:
        path_str: The raw path string from agent/tool parameters.
        label:    Human-readable label for logging (e.g. "audio_path").

    Returns:
        The resolved ``Path`` object for safe use with ``open()``.

    Raises:
        ValueError: If the path is empty, contains traversal components,
            or resolves outside all allowed roots.
    """
    if not path_str or not path_str.strip():
        raise ValueError(f"Empty {label} path")

    # Normalise and resolve (collapses .., symlinks, etc.)
    resolved = Path(os.path.normpath(path_str)).resolve()

    for root in ALLOWED_DATA_ROOTS:
        try:
            # Python 3.9+ is_relative_to
            if resolved.is_relative_to(root):
                return resolved
        except AttributeError:
            # Fallback for Python 3.8
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue

    # Rejected – log for forensics
    logger.warning(
        "SA3-C1 path validation REJECTED %s=%r (resolved=%s)",
        label, path_str, resolved,
    )
    raise ValueError(
        f"Path not in allowed directories. "
        f"Tool file paths must be under /data, /app/uploads, or /tmp."
    )
