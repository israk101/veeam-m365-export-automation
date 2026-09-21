from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal


class PowerShellRunner(QObject):
    output = Signal(str, str)
    phase_changed = Signal(str)
    finished = Signal(int)
    failed_to_start = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._buffer = ""

    def start(
        self,
        powershell: str,
        script: Path,
        organization: str,
        job_name: str,
        restore_root: str,
        skip_backup: bool,
    ) -> None:
        if self.is_running():
            return
        arguments = [
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-OrganizationName",
            organization,
            "-JobName",
            job_name,
            "-LocalRestoreRoot",
            restore_root,
        ]
        if skip_backup:
            arguments.append("-SkipBackup")
        self._buffer = ""
        self.process.setProgram(powershell)
        self.process.setArguments(arguments)
        self.process.start()

    def stop(self) -> None:
        if not self.is_running():
            return
        self.output.emit("[WARN] Stop requested. Closing the PowerShell process…", "warning")
        self.process.terminate()
        if not self.process.waitForFinished(2500):
            self.process.kill()

    def is_running(self) -> bool:
        return self.process.state() != QProcess.NotRunning

    def _read_output(self) -> None:
        self._buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        lines = self._buffer.splitlines(keepends=True)
        self._buffer = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._buffer = lines.pop()
        for raw in lines:
            self._emit_line(raw.rstrip("\r\n"))

    def _emit_line(self, line: str) -> None:
        if not line.strip():
            return
        upper = line.upper()
        if "[ERR]" in upper:
            level = "error"
        elif "[WARN]" in upper:
            level = "warning"
        elif "[OK]" in upper:
            level = "success"
        else:
            level = "info"
        self.output.emit(line, level)
        phase_patterns = (
            (r"FASE 1|PREFLIGHT", "Checking environment"),
            (r"AVVIO.*BACKUP|AVANZAMENTO BACKUP", "Running backup"),
            (r"\[1/3\]|EXCHANGE ONLINE", "Testing Exchange"),
            (r"\[2/3\]|ONEDRIVE", "Testing OneDrive"),
            (r"\[3/3\]|SHAREPOINT", "Testing SharePoint"),
            (r"REPORT (FINALE|CONCLUSIVO)|REPORT SALVATI", "Finalizing report"),
        )
        for pattern, phase in phase_patterns:
            if re.search(pattern, upper):
                self.phase_changed.emit(phase)
                break

    def _finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        if self._buffer:
            self._emit_line(self._buffer)
            self._buffer = ""
        self.finished.emit(exit_code)

    def _error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            self.failed_to_start.emit(self.process.errorString())
