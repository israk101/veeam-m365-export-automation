"""Bounded, process-isolated execution; never overlap jobs in one organization."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from core.reports import latest_report
from core.retention import sanitize_org_name
from core.runner import PowerShellRunner


@dataclass(frozen=True)
class JobTask:
    org_name: str
    job_name: str


class JobQueue(QObject):
    output = Signal(str, str)
    progress = Signal(int, int, str)
    finished = Signal(list, bool)

    def __init__(self, parent=None, runner_factory: Callable = PowerShellRunner):
        super().__init__(parent)
        self.runner_factory = runner_factory
        self.active: dict[int, dict] = {}
        self.pending: list[tuple[int, JobTask]] = []
        self.results: dict[int, dict] = {}
        self.running = False
        self.cancelled = False

    def start(self, tasks, *, shell, script, workspace, skip_backup, concurrency=2, candidate_limit=None):
        if self.running:
            raise RuntimeError("A batch is already running")
        self.shell, self.script, self.workspace = shell, script, Path(workspace)
        self.skip_backup, self.candidate_limit = skip_backup, candidate_limit
        self.concurrency = max(1, min(4, int(concurrency)))
        self.pending = list(enumerate(tasks))
        self.total = len(self.pending)
        self.results.clear()
        self.cancelled = False
        self.running = True
        QTimer.singleShot(0, self._pump)

    def _pump(self):
        if not self.running:
            return
        while not self.cancelled and len(self.active) < self.concurrency:
            busy = {v["task"].org_name.casefold() for v in self.active.values()}
            eligible = next((i for i, (_, task) in enumerate(self.pending)
                             if task.org_name.casefold() not in busy), None)
            if eligible is None:
                break
            index, task = self.pending.pop(eligible)
            root = self.workspace / "Jobs" / sanitize_org_name(task.org_name) / f"{index:04d}_{sanitize_org_name(task.job_name)}"
            runner = self.runner_factory(self)
            self.active[index] = dict(task=task, root=root, runner=runner,
                                      started=datetime.now().timestamp(), clock=monotonic(), phase="Starting")
            runner.output.connect(lambda line, level, i=index: self._output(i, line, level))
            runner.phase_changed.connect(lambda phase, i=index: self._phase(i, phase))
            runner.finished.connect(lambda code, i=index: self._complete(i, code))
            runner.failed_to_start.connect(lambda error, i=index: self._failed(i, error))
            self._output(index, "[INFO] Starting job", "info")
            try:
                root.mkdir(parents=True, exist_ok=True)
                options = {} if self.candidate_limit is None else {"sample_candidate_limit": self.candidate_limit}
                runner.start(self.shell, self.script, task.org_name, task.job_name, str(root), self.skip_backup, **options)
            except Exception as exc:
                self._failed(index, str(exc))
        self._progress()
        if not self.active and not self.pending:
            self.running = False
            self.finished.emit([self.results[i] for i in sorted(self.results)], self.cancelled)

    def _output(self, index, line, level):
        if index in self.active:
            task = self.active[index]["task"]
            self.output.emit(f"[{task.org_name} / {task.job_name}] {line}", level)

    def _phase(self, index, phase):
        if index in self.active:
            self.active[index]["phase"] = phase
            self._progress()

    def _progress(self):
        labels = [f"{v['task'].org_name} / {v['task'].job_name}: {v['phase']}" for v in self.active.values()]
        self.progress.emit(len(self.results), self.total, "\n".join(labels))

    def _failed(self, index, error):
        self._output(index, f"[ERR] Could not start: {error}", "error")
        self._complete(index, -1, error)

    def _complete(self, index, code, error=None):
        state = self.active.pop(index, None)
        if state is None:
            return
        task = state["task"]
        report = latest_report(state["root"])
        valid = (report is not None and report.modified >= state["started"] - 1
                 and report.data.get("JobName") == task.job_name
                 and report.data.get("Organization") == task.org_name)
        self.results[index] = dict(
            organization=task.org_name, job=task.job_name, exit_code=code,
            report_path=str(report.path) if valid else None,
            report=report.data if valid else None,
            duration_seconds=round(monotonic() - state["clock"], 3),
            cancelled=self.cancelled, error=error,
        )
        self.output.emit(f"[{task.org_name} / {task.job_name}] Finished · exit {code}", "info" if code == 0 else "warning")
        runner = state["runner"]
        # Release callbacks before deferred QObject destruction. In particular,
        # finished/failed-to-start must not retain a completed queue through lambdas.
        runner.output.disconnect()
        runner.phase_changed.disconnect()
        runner.finished.disconnect()
        runner.failed_to_start.disconnect()
        runner.deleteLater()
        QTimer.singleShot(0, self._pump)

    def stop(self):
        if not self.running:
            return
        self.cancelled = True
        for index, task in self.pending:
            self.results[index] = dict(organization=task.org_name, job=task.job_name,
                                       exit_code=-1, report=None, report_path=None,
                                       duration_seconds=0, cancelled=True, error="Cancelled before starting")
        self.pending.clear()
        for state in list(self.active.values()):
            state["runner"].stop()
        QTimer.singleShot(0, self._pump)
