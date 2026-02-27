"""Path traversal prevention."""

from __future__ import annotations

from pathlib import Path


def safe_resolve(base: Path, user_path: str) -> Path:
    """Resolve a user-provided path safely within a base directory.

    Returns the resolved path. Raises ValueError if the path escapes the base.
    """
    resolved = (base / user_path).resolve()
    resolved.relative_to(base.resolve())  # Raises ValueError if outside base
    return resolved
