from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, Signal


MARKER = "__VEEAM_INVENTORY__"


def parse_inventory_output(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            payload = json.loads(line[len(MARKER):])
            if not isinstance(payload, dict) or not isinstance(payload.get("Organizations"), list):
                raise ValueError("PowerShell returned an invalid inventory payload.")
            return payload
    detail = output.strip() or "PowerShell returned no output."
    raise ValueError(detail)


def flatten_inventory(payload: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Return {org_name: [{Name, Id}, ...]} from the raw inventory payload."""
    result: dict[str, list[dict[str, str]]] = {}
    for org in payload.get("Organizations", []):
        name = str(org.get("Name", ""))
        if name:
            result[name] = [
                {"Name": str(j.get("Name", "")), "Id": str(j.get("Id", ""))}
                for j in org.get("Jobs", []) if str(j.get("Name", ""))
            ]
    return result


class InventoryDiscovery(QObject):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

    def start(self, powershell: str, script: Path) -> None:
        if self.process.state() != QProcess.NotRunning:
            return
        self.process.setProgram(powershell)
        self.process.setArguments([
            "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(script),
        ])
        self.process.start()

    def is_running(self) -> bool:
        return self.process.state() != QProcess.NotRunning

    def _finished(self, _code: int, _status: QProcess.ExitStatus) -> None:
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        try:
            self.completed.emit(parse_inventory_output(output))
        except (ValueError, json.JSONDecodeError) as exc:
            self.failed.emit(str(exc))

    def _error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            self.failed.emit(self.process.errorString())
