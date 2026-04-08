"""Workspace-relative path helpers."""

from __future__ import annotations

import os
from pathlib import Path


def workspace_root() -> Path:
    """Return the repository root, honoring explicit overrides first."""
    override = os.getenv("SKIM_WORKSPACE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


DATA_DIR = workspace_root() / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
