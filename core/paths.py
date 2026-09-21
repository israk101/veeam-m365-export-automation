from __future__ import annotations

import shutil
import sys
from pathlib import Path


SCRIPT_NAME = "Invoke-SimpleM365BackupRestoreTest.ps1"
DISCOVERY_SCRIPT_NAME = "Get-VeeamM365Inventory.ps1"


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / relative


def bundled_script() -> Path:
    return resource_path(f"scripts/{SCRIPT_NAME}")


def discovery_script() -> Path:
    return resource_path(f"scripts/{DISCOVERY_SCRIPT_NAME}")


def resolve_script(configured: str) -> Path:
    candidate = Path(configured).expanduser() if configured.strip() else bundled_script()
    return candidate.resolve()


def powershell_path() -> str | None:
    return shutil.which("pwsh.exe") or shutil.which("pwsh")
