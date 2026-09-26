"""Retention policy: keep only the N most-recent restore-test sessions per organization."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


# Matches directories created by write_batch_report: RestoreTest_YYYYMMDD_HHMMSS
_TEST_DIR_PATTERN = re.compile(r"^RestoreTest_\d{8}_\d{6}$")


def _test_dirs(org_dir: Path) -> list[Path]:
    """Return RestoreTest_* subdirectories sorted oldest-first by name (timestamp)."""
    if not org_dir.is_dir():
        return []
    dirs = [
        d for d in org_dir.iterdir()
        if d.is_dir() and not d.is_symlink() and not d.is_junction() and _TEST_DIR_PATTERN.match(d.name)
    ]
    return sorted(dirs, key=lambda d: d.name)


def enforce_retention(org_dir: Path, max_keep: int = 5) -> list[Path]:
    """Delete the oldest RestoreTest_* directories so that at most *max_keep* remain.

    Returns the list of directories that were removed.
    """
    if max_keep < 1:
        max_keep = 1
    dirs = _test_dirs(org_dir)
    removed: list[Path] = []
    while len(dirs) > max_keep:
        oldest = dirs.pop(0)
        try:
            shutil.rmtree(oldest)
            removed.append(oldest)
        except OSError:
            pass
    return removed


def sanitize_org_name(org: str) -> str:
    """Turn an organization name into a safe directory name."""
    safe = re.sub(r"[^A-Za-z0-9._\-]+", "_", org).strip("._")
    return safe or "org"
