from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "selected_multi_org_jobs": {},
    "restore_root": r"C:\VeeamRestoreLocalTest",
    "script_path": "",
    "max_restore_tests": 5,
    "skip_backups": True,
    "report_formats": ["txt", "html", "pdf"],
}


class ConfigManager:
    def __init__(self, path: Path | None = None) -> None:
        appdata = Path(os.environ.get("APPDATA", Path.home()))
        self.path = path or appdata / "VeeamM365RestoreTester" / "settings.json"
        self.data = DEFAULTS.copy()
        self.load()

    def load(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return self.data.copy()

    def save(self, values: dict[str, Any]) -> None:
        self.data.update({k: v for k, v in values.items() if k in DEFAULTS})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)
