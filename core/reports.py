from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Report:
    path: Path
    data: dict[str, Any]
    modified: float

    @property
    def passed(self) -> bool:
        return bool(self.data.get("AllSuccessful", False))

    @property
    def timestamp(self) -> str:
        raw = str(self.data.get("RunTimestamp", ""))
        try:
            return datetime.strptime(raw, "%Y%m%d_%H%M%S").strftime("%d %b %Y · %H:%M")
        except ValueError:
            return datetime.fromtimestamp(self.modified).strftime("%d %b %Y · %H:%M")


def read_report(path: Path) -> Report | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return None
        return Report(path=path, data=data, modified=path.stat().st_mtime)
    except (OSError, json.JSONDecodeError):
        return None


def list_reports(root: str | Path) -> list[Report]:
    base = Path(root).expanduser()
    if not base.is_dir():
        return []
    reports: list[Report] = []
    for path in base.rglob("Report_Summary.json"):
        # Accept reports at depth 2 from root:
        #   <root>/Batch_*/Report_Summary.json          (legacy)
        #   <root>/<OrgName>/RestoreTest_*/Report_Summary.json  (new)
        rel = path.relative_to(base)
        depth = len(rel.parts) - 1  # number of directories above the file
        if depth > 3:
            continue  # skip job-level sub-reports buried deeper
        item = read_report(path)
        if item:
            reports.append(item)
    return sorted(reports, key=lambda item: item.modified, reverse=True)


def latest_report(root: str | Path) -> Report | None:
    reports = list_reports(root)
    return reports[0] if reports else None


def human_size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "—"


def format_duration(value: Any) -> str:
    """Format a duration in seconds as HH:MM:SS."""
    try:
        total_seconds = max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return "—"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
