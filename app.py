from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import ConfigManager
from core.batch_report import build_organization_summaries, normalize_status, overall_status, write_batch_report
from core.discovery import InventoryDiscovery, flatten_inventory
from core.paths import bundled_script, discovery_script, powershell_path, resolve_script, resource_path
from core.reports import Report, format_duration, latest_report, list_reports, read_report
from core.runner import PowerShellRunner
from ui.theme import COLORS, stylesheet
from ui.widgets import LogConsole, OrgJobTree, PageHeading, StatusCard
from ui.dialogs import ask_confirmation, show_error, show_information, show_warning


@dataclass
class PendingJob:
    org_name: str
    job_name: str


def clear_layout(layout: QVBoxLayout | QHBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def cleanup_batch_workspace(workspace: Path | None, report_directory: Path | None) -> str | None:
    """Remove temporary job output after the final report has been written.

    Normal GUI runs write final reports below organization directories, so the
    whole ``Batch_*`` staging directory can go. The Jobs-only branch preserves
    compatibility if a caller deliberately writes a final report in Batch.
    """
    if not workspace or not workspace.is_dir() or not workspace.name.startswith("Batch_"):
        return None
    try:
        target = workspace / "Jobs" if report_directory == workspace else workspace
        if target.is_dir():
            shutil.rmtree(target)
        return None
    except OSError as exc:
        return str(exc)


class DashboardPage(QWidget):
    def __init__(self, run_callback) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(18)
        header_row = QHBoxLayout()
        header_row.addWidget(PageHeading("Restore confidence", "Dashboard", "A clear view of your latest Microsoft 365 recovery test."))
        header_row.addStretch()
        run = QPushButton("Run restore test")
        run.setObjectName("Primary")
        run.clicked.connect(run_callback)
        header_row.addWidget(run, alignment=Qt.AlignTop)
        layout.addLayout(header_row)

        self.summary = QFrame()
        self.summary.setObjectName("Card")
        summary_layout = QHBoxLayout(self.summary)
        summary_layout.setContentsMargins(20, 16, 20, 16)
        summary_layout.setSpacing(16)

        self.summary_icon = QLabel()
        self.summary_icon.setFixedSize(32, 32)
        idle_svg = resource_path("assets/status_idle.svg")
        if idle_svg.is_file():
            self.summary_icon.setPixmap(QPixmap(str(idle_svg)).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.summary_icon.setText("○")
            self.summary_icon.setStyleSheet(f"font-size: 24px; color: {COLORS['muted']};")

        self.summary_title = QLabel("No restore tests yet")
        self.summary_title.setObjectName("CardTitle")
        self.summary_title.setStyleSheet("font-size: 15px; font-weight: 650; color: #FFFFFF;")

        self.summary_detail = QLabel("Choose Run restore test to verify Exchange, OneDrive, and SharePoint.")
        self.summary_detail.setObjectName("Muted")

        summary_text = QVBoxLayout()
        summary_text.setSpacing(3)
        summary_text.addWidget(self.summary_title)
        summary_text.addWidget(self.summary_detail)

        summary_layout.addWidget(self.summary_icon)
        summary_layout.addLayout(summary_text)
        summary_layout.addStretch()
        layout.addWidget(self.summary)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.exchange = StatusCard("Exchange", glyph="✉", icon_path="assets/workload_email.png")
        self.onedrive = StatusCard("OneDrive", glyph="◈", icon_path="assets/workload_onedrive.png")
        self.sharepoint = StatusCard("SharePoint", glyph="▦", icon_path="assets/workload_sharepoint.png")
        cards.addWidget(self.exchange)
        cards.addWidget(self.onedrive)
        cards.addWidget(self.sharepoint)
        layout.addLayout(cards)

        panel = QFrame()
        panel.setObjectName("Panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        panel_layout.setSpacing(6)
        label = QLabel("Latest run details")
        label.setObjectName("CardTitle")
        self.details = QLabel("Reports will appear here after the first completed run.")
        self.details.setObjectName("Muted")
        self.details.setWordWrap(True)
        panel_layout.addWidget(label)
        panel_layout.addWidget(self.details)
        layout.addWidget(panel)
        layout.addStretch()

    def load_report(self, report: Report | None) -> None:
        if not report:
            idle_svg = resource_path("assets/status_idle.svg")
            if idle_svg.is_file():
                self.summary_icon.setPixmap(QPixmap(str(idle_svg)).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                self.summary_icon.setText("○")
                self.summary_icon.setStyleSheet(f"font-size: 24px; color: {COLORS['muted']};")
            self.summary_title.setText("No restore tests yet")
            self.summary_title.setStyleSheet("font-size: 15px; font-weight: 650; color: #FFFFFF;")
            self.summary_detail.setText("Choose Run restore test to verify Exchange, OneDrive, and SharePoint.")
            self.exchange.update_data({})
            self.onedrive.update_data({})
            self.sharepoint.update_data({})
            self.details.setText("Reports will appear here after the first completed run.")
            return

        data = report.data
        status = overall_status(data)
        passed = status == "SUCCESS"
        status_color = COLORS["green"] if passed else (COLORS["amber"] if status == "WARNING" else COLORS["red"])
        status_file = resource_path(f"assets/{'status_verified.svg' if passed else 'status_attention.svg'}")
        if status_file.is_file():
            self.summary_icon.setPixmap(QPixmap(str(status_file)).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.summary_icon.setText("✓" if passed else "!")
            self.summary_icon.setStyleSheet(f"font-size: 24px; color: {status_color}; font-weight: 700;")

        self.summary_title.setText(f"Restore test {status}")
        self.summary_title.setStyleSheet(f"font-size: 15px; font-weight: 650; color: {'#FFFFFF' if passed else status_color};")
        self.summary_detail.setText(f"{data.get('Organization', 'Unknown tenant')} · {report.timestamp}")
        self.exchange.update_data(data.get("Exchange"))
        self.onedrive.update_data(data.get("OneDrive"))
        self.sharepoint.update_data(data.get("SharePoint"))
        self.details.setText(
            f"Backup: {data.get('BackupStatus', '—')}    ·    Job: {data.get('JobName', '—')}    ·    Restore point: {data.get('RestorePointDate', '—')}"
        )


class RunPage(QWidget):
    def __init__(self, config: ConfigManager, runner: PowerShellRunner, finished_callback, auto_discover: bool = True) -> None:
        super().__init__()
        self.config = config
        self.runner = runner
        self.finished_callback = finished_callback
        self.discovery = InventoryDiscovery(self)
        self.inventory: dict[str, list[dict[str, str]]] = {}
        self.pending_jobs: list[PendingJob] = []
        self.completed_jobs: list[dict[str, Any]] = []
        self.current_job: PendingJob | None = None
        self.current_started_at = 0.0
        self.current_started_monotonic: float | None = None
        self.batch_started: datetime | None = None
        self.batch_started_monotonic: float | None = None
        self.batch_directory: Path | None = None
        self.current_job_root: Path | None = None
        self.stop_requested = False
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.addWidget(PageHeading(
            "Guided operation",
            "Run restore test",
            "Find organizations, choose their jobs, confirm the destination, then follow the run live.",
        ))
        body = QHBoxLayout()
        body.setSpacing(16)
        form_panel = QFrame()
        form_panel.setObjectName("Panel")
        form_panel.setMinimumWidth(420)
        form_panel.setMaximumWidth(520)
        panel_layout = QVBoxLayout(form_panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(8)
        form_title = QLabel("1 · Choose organizations and jobs")
        form_title.setObjectName("CardTitle")
        panel_layout.addWidget(form_title)
        inventory_row = QHBoxLayout()
        self.inventory_status = QLabel("Discovering local organizations…")
        self.inventory_status.setObjectName("Muted")
        self.refresh_inventory_button = QPushButton("Refresh")
        self.refresh_inventory_button.clicked.connect(self.refresh_inventory)
        inventory_row.addWidget(self.inventory_status, stretch=1)
        inventory_row.addWidget(self.refresh_inventory_button)
        panel_layout.addLayout(inventory_row)

        self.job_search = QLineEdit()
        self.job_search.setObjectName("SearchInput")
        self.job_search.setPlaceholderText("Search organizations or jobs…")
        self.job_search.setClearButtonEnabled(True)
        self.job_search.setEnabled(False)
        self.job_search.textChanged.connect(self._filter_jobs)
        panel_layout.addWidget(self.job_search)

        self.filter_status = QLabel("Inventory will appear here after discovery.")
        self.filter_status.setObjectName("Muted")
        panel_layout.addWidget(self.filter_status)

        self.job_tree = OrgJobTree()
        self.job_tree.setMinimumHeight(145)
        self.job_tree.setToolTip("Select jobs across any organization. Checked jobs run sequentially.")
        self.job_tree.selection_changed.connect(self._update_selection_summary)
        panel_layout.addWidget(self.job_tree, stretch=1)

        selection_row = QHBoxLayout()
        self.selection_status = QLabel("No jobs selected")
        self.selection_status.setObjectName("Muted")
        self.select_visible_button = QPushButton("Select visible")
        self.select_visible_button.setEnabled(False)
        self.select_visible_button.setToolTip("Select only the jobs currently shown by the search filter.")
        self.select_visible_button.clicked.connect(lambda: self._set_visible_jobs(Qt.Checked))
        self.clear_visible_button = QPushButton("Clear visible")
        self.clear_visible_button.setEnabled(False)
        self.clear_visible_button.setToolTip("Clear only the jobs currently shown by the search filter.")
        self.clear_visible_button.clicked.connect(lambda: self._set_visible_jobs(Qt.Unchecked))
        selection_row.addWidget(self.selection_status, stretch=1)
        selection_row.addWidget(self.select_visible_button)
        selection_row.addWidget(self.clear_visible_button)
        panel_layout.addLayout(selection_row)

        options_title = QLabel("2 · Configure this run")
        options_title.setObjectName("CardTitle")
        panel_layout.addWidget(options_title)
        destination_label = QLabel("Restore destination")
        destination_label.setObjectName("Muted")
        panel_layout.addWidget(destination_label)
        self.restore = QLineEdit(str(config.get("restore_root")))
        self.restore.setToolTip("Final reports are grouped into one folder per organization under this path.")
        restore_row = QHBoxLayout()
        restore_row.addWidget(self.restore)
        self.restore_browse_button = QPushButton("Browse")
        self.restore_browse_button.clicked.connect(self._browse_restore)
        restore_row.addWidget(self.restore_browse_button)
        panel_layout.addLayout(restore_row)
        skip_row = QHBoxLayout()
        skip_row.setSpacing(10)
        self.skip = QCheckBox("Don't run backup jobs")
        self.skip.setToolTip("When checked, tests latest existing restore points without executing new backups. Default is configured in Settings.")
        self.skip.setChecked(bool(config.get("skip_backups", True)))
        self.skip_default_label = QLabel()
        self.skip_default_label.setObjectName("Muted")
        self.skip_default_label.setStyleSheet("font-size: 11px; color: #888888;")
        skip_row.addWidget(self.skip)
        skip_row.addWidget(self.skip_default_label)
        skip_row.addStretch()
        panel_layout.addLayout(skip_row)
        tip = QLabel("Each organization receives its own report and evidence folder. Nothing is restored back to Microsoft 365.")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        panel_layout.addWidget(tip)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.run_button = QPushButton("Run selected jobs")
        self.run_button.setObjectName("Primary")
        self.run_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("Danger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        self.open_folder_button = QPushButton("Open test folder")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._open_test_folder)
        actions.addWidget(self.run_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.open_folder_button)
        panel_layout.addLayout(actions)
        body.addWidget(form_panel)
        activity = QFrame()
        activity.setObjectName("Panel")
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(20, 20, 20, 20)
        title_row = QHBoxLayout()
        activity_title = QLabel("Live activity")
        activity_title.setObjectName("CardTitle")
        self.elapsed_label = QLabel("Elapsed 00:00:00")
        self.elapsed_label.setObjectName("TimerBadge")
        self.phase = QLabel("Ready")
        self.phase.setObjectName("Muted")
        title_row.addWidget(activity_title)
        title_row.addWidget(self.elapsed_label)
        title_row.addStretch()
        title_row.addWidget(self.phase)
        activity_layout.addLayout(title_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        activity_layout.addWidget(self.progress)
        self.log = LogConsole()
        self.log.setPlaceholderText("PowerShell output will stream here…")
        activity_layout.addWidget(self.log, stretch=1)
        body.addWidget(activity, stretch=1)
        root.addLayout(body, stretch=1)
        runner.output.connect(self._on_output)
        runner.phase_changed.connect(self._set_phase)
        runner.finished.connect(self._complete)
        runner.failed_to_start.connect(self._start_error)
        self.discovery.completed.connect(self._inventory_loaded)
        self.discovery.failed.connect(self._inventory_failed)
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(250)
        self.elapsed_timer.timeout.connect(self._update_elapsed)
        self.sync_defaults()
        if auto_discover:
            QTimer.singleShot(0, self.refresh_inventory)

    def sync_defaults(self) -> None:
        if not self.restore.hasFocus():
            self.restore.setText(str(self.config.get("restore_root")))
        default_skip = bool(self.config.get("skip_backups", True))
        if not self.skip.hasFocus() and not self.runner.is_running():
            self.skip.setChecked(default_skip)
        self.skip_default_label.setText(f"(Default: {'On' if default_skip else 'Off'} in Settings)")

    def refresh_inventory(self) -> None:
        shell = powershell_path()
        script = discovery_script()
        if not shell:
            self._inventory_failed("PowerShell 7 (pwsh.exe) was not found.")
            return
        if not script.is_file():
            self._inventory_failed(f"Discovery helper not found: {script}")
            return
        self.refresh_inventory_button.setEnabled(False)
        self.job_tree.setEnabled(False)
        self.job_search.setEnabled(False)
        self.select_visible_button.setEnabled(False)
        self.clear_visible_button.setEnabled(False)
        self.inventory_status.setText("Connecting to localhost…")
        self.discovery.start(shell, script)

    def _inventory_loaded(self, payload: dict[str, Any]) -> None:
        if isinstance(payload, dict) and "Organizations" in payload:
            self.inventory = flatten_inventory(payload)
        elif isinstance(payload, dict):
            self.inventory = payload
        else:
            self.inventory = {}
        preselected = self.config.get("selected_multi_org_jobs") or {}
        self.job_tree.load_inventory(self.inventory, preselected)
        self.refresh_inventory_button.setEnabled(True)
        self.job_tree.setEnabled(bool(self.inventory))
        self.job_search.setEnabled(bool(self.inventory))
        self.select_visible_button.setEnabled(bool(self.inventory))
        self.clear_visible_button.setEnabled(bool(self.inventory))
        count = len(self.inventory)
        self.inventory_status.setText(f"{count} organization{'s' if count != 1 else ''} found on localhost")
        self._filter_jobs(self.job_search.text())
        self._update_selection_summary()

    def _inventory_failed(self, message: str) -> None:
        self.refresh_inventory_button.setEnabled(True)
        self.job_tree.setEnabled(False)
        self.job_search.setEnabled(False)
        self.select_visible_button.setEnabled(False)
        self.clear_visible_button.setEnabled(False)
        self.inventory_status.setText("Discovery unavailable — select Refresh to retry")
        self.filter_status.setText("Inventory unavailable")
        self.log.append_line(f"[ERR] Organization discovery failed: {message}", "error")

    def _set_all_jobs(self, state: Qt.CheckState) -> None:
        self.job_tree.set_all(state)

    def _set_visible_jobs(self, state: Qt.CheckState) -> None:
        self.job_tree.set_visible(state)

    def _filter_jobs(self, query: str) -> None:
        organizations, jobs = self.job_tree.filter_items(query)
        if query.strip():
            self.filter_status.setText(
                f"{organizations} matching organization{'s' if organizations != 1 else ''} · "
                f"{jobs} job{'s' if jobs != 1 else ''} shown"
            )
        else:
            total_jobs = sum(len(items) for items in self.inventory.values())
            self.filter_status.setText(
                f"{len(self.inventory)} organization{'s' if len(self.inventory) != 1 else ''} · "
                f"{total_jobs} job{'s' if total_jobs != 1 else ''}"
            )

    def _update_selection_summary(self) -> None:
        selected = self.job_tree.selected_jobs()
        organizations = len({organization for organization, _job in selected})
        jobs = len(selected)
        if jobs:
            self.selection_status.setText(
                f"{jobs} job{'s' if jobs != 1 else ''} across "
                f"{organizations} organization{'s' if organizations != 1 else ''} selected"
            )
        else:
            self.selection_status.setText("No jobs selected")

    def _set_configuration_enabled(self, enabled: bool) -> None:
        has_inventory = bool(self.inventory)
        self.job_tree.setEnabled(enabled and has_inventory)
        self.job_search.setEnabled(enabled and has_inventory)
        self.select_visible_button.setEnabled(enabled and has_inventory)
        self.clear_visible_button.setEnabled(enabled and has_inventory)
        self.restore.setEnabled(enabled)
        self.restore_browse_button.setEnabled(enabled)
        self.skip.setEnabled(enabled)

    def _selected_multi_org_jobs(self) -> list[PendingJob]:
        return [PendingJob(org, job) for org, job in self.job_tree.selected_jobs()]

    def _browse_restore(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose restore folder", self.restore.text())
        if chosen:
            self.restore.setText(chosen)

    def start(self) -> None:
        selections = self._selected_multi_org_jobs()
        restore = self.restore.text().strip()
        if not selections or not restore:
            show_warning(self, "Missing information", "Select at least one job and provide a restore folder.")
            return
        shell = powershell_path()
        script = resolve_script(str(self.config.get("script_path", "")))
        if not shell:
            show_error(self, "PowerShell 7 not found", "Install PowerShell 7 (pwsh.exe) before running a restore test.")
            return
        if not script.is_file():
            show_error(self, "Script not found", f"The configured PowerShell script does not exist:\n{script}")
            return
        Path(restore).mkdir(parents=True, exist_ok=True)
        multi_dict = self.job_tree.selected_dict()
        self.config.save({
            "selected_multi_org_jobs": multi_dict,
            "restore_root": restore,
        })
        self.log.clear()
        mode = "latest restore points" if self.skip.isChecked() else "backup then restore"
        org_label = selections[0].org_name if len(multi_dict) <= 1 else f"{len(multi_dict)} organizations"
        self.log.append_line(f"[INFO] Starting {len(selections)} job(s) for {org_label} — {mode}.", "info")
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.open_folder_button.setEnabled(False)
        self.refresh_inventory_button.setEnabled(False)
        self._set_configuration_enabled(False)
        self.progress.setRange(0, 0)
        self.pending_jobs = list(selections)
        self.completed_jobs = []
        self.batch_started = datetime.now()
        self.batch_started_monotonic = monotonic()
        self.elapsed_label.setText("Elapsed 00:00:00")
        self.elapsed_timer.start()
        self.batch_directory = Path(restore) / f"Batch_{self.batch_started.strftime('%Y%m%d_%H%M%S')}"
        self.stop_requested = False
        self._start_next_job()

    def _start_next_job(self) -> None:
        if self.stop_requested or not self.pending_jobs:
            self._finish_batch()
            return
        self.current_job = self.pending_jobs.pop(0)
        self.current_started_at = datetime.now().timestamp()
        self.current_started_monotonic = monotonic()
        safe_org = re.sub(r"[^A-Za-z0-9._-]+", "_", self.current_job.org_name).strip("._") or "org"
        safe_job = re.sub(r"[^A-Za-z0-9._-]+", "_", self.current_job.job_name).strip("._") or "job"
        self.current_job_root = (self.batch_directory or Path(self.restore.text().strip())) / "Jobs" / safe_org / safe_job
        self.current_job_root.mkdir(parents=True, exist_ok=True)
        total = len(self.completed_jobs) + len(self.pending_jobs) + 1
        current = len(self.completed_jobs) + 1
        job_display = f"{self.current_job.org_name} / {self.current_job.job_name}"
        self.phase.setText(f"Job {current} of {total}: {job_display}")
        self.log.append_line(f"[INFO] ── Job {current}/{total}: {job_display} ──", "info")
        self.runner.start(
            powershell_path() or "pwsh.exe",
            resolve_script(str(self.config.get("script_path", ""))),
            self.current_job.org_name,
            self.current_job.job_name,
            str(self.current_job_root),
            self.skip.isChecked(),
        )

    def stop(self) -> None:
        confirmed = ask_confirmation(
            self,
            "Stop this test?",
            "PowerShell will be terminated. Any files already extracted will remain on disk.",
            accept_text="Stop test",
            cancel_text="Keep running",
            destructive=True,
        )
        if confirmed:
            self.stop_requested = True
            self.pending_jobs.clear()
            self.runner.stop()

    def _set_phase(self, phase: str) -> None:
        label = f"{self.current_job.job_name} · {phase}" if self.current_job else phase
        self.phase.setText(label)

    def _on_output(self, line: str, level: str) -> None:
        prefix = f"[{self.current_job.job_name}] " if self.current_job else ""
        self.log.append_line(f"{prefix}{line}", level)

    def _complete(self, code: int) -> None:
        if not self.current_job:
            return
        report = latest_report(self.current_job_root) if self.current_job_root else None
        report_data: dict[str, Any] | None = None
        report_path: str | None = None
        if report and report.modified >= self.current_started_at - 1 and str(report.data.get("JobName", "")) == self.current_job.job_name:
            report_data = report.data
            report_path = str(report.path)
        self.completed_jobs.append({
            "organization": self.current_job.org_name,
            "job": self.current_job.job_name,
            "exit_code": code,
            "report_path": report_path,
            "report": report_data,
            "duration_seconds": max(
                0,
                int(round(monotonic() - self.current_started_monotonic)),
            ) if self.current_started_monotonic is not None else 0,
        })
        rep = report_data if isinstance(report_data, dict) else {}
        passed_wls = [
            wl for wl in ("Exchange", "OneDrive", "SharePoint")
            if isinstance(rep.get(wl), dict) and str(rep.get(wl, {}).get("Status", "")).upper() == "SUCCESS"
        ]
        has_success = (code == 0) or bool(rep.get("AllSuccessful")) or bool(passed_wls)
        level = "success" if has_success else "warning"
        status_tag = "OK" if has_success else "WARN"
        wl_info = f" ({', '.join(passed_wls)})" if passed_wls and len(passed_wls) < 3 else ""
        self.log.append_line(f"[{status_tag}] Job '{self.current_job.job_name}' finished with exit code {code}{wl_info}.", level)
        self.current_job = None
        self.current_started_monotonic = None
        self._start_next_job()

    def _finish_batch(self) -> None:
        duration_seconds = self._elapsed_seconds()
        self.elapsed_timer.stop()
        self.elapsed_label.setText(f"Completed in {format_duration(duration_seconds)}")
        summaries = build_organization_summaries(
            self.completed_jobs,
            self.skip.isChecked(),
            self.batch_started,
        )
        max_keep = int(self.config.get("max_restore_tests", 5))
        report_formats = list(self.config.get("report_formats", ["txt", "html", "pdf"]))
        report_paths: list[Path] = []
        for summary in summaries:
            report_path = write_batch_report(
                self.restore.text().strip(),
                summary,
                max_keep=max_keep,
                report_formats=report_formats,
            )
            if report_path.is_file():
                report_paths.append(report_path)
                status = overall_status(summary)
                level = "success" if summary.get("AllSuccessful") else "warning"
                tag = "OK" if summary.get("AllSuccessful") else "WARN"
                self.log.append_line(
                    f"[{tag}] {summary.get('Organization')} report: {status}: {report_path}",
                    level,
                )

        if report_paths and len(report_paths) == len(summaries):
            cleanup_error = cleanup_batch_workspace(self.batch_directory, None)
            if cleanup_error:
                self.log.append_line(f"[WARN] Could not remove temporary test files: {cleanup_error}", "warning")
            else:
                self.log.append_line("[INFO] Temporary test workspace removed.", "info")
            self.batch_directory = (
                report_paths[0].parent
                if len(report_paths) == 1
                else Path(self.restore.text().strip())
            )
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.open_folder_button.setEnabled(bool(self.batch_directory and self.batch_directory.exists()))
        self.refresh_inventory_button.setEnabled(True)
        self._set_configuration_enabled(True)
        self.progress.setRange(0, 100)
        all_successful = bool(summaries) and all(summary.get("AllSuccessful") for summary in summaries)
        any_successful = any(summary.get("AllSuccessful") for summary in summaries)
        self.progress.setValue(100 if all_successful else 0)
        if self.stop_requested:
            self.phase.setText("Stopped by user")
            self.log.append_line("[WARN] Multi-job run stopped by user.", "warning")
        elif all_successful:
            self.phase.setText("All organization reports verified")
        else:
            status = "WARNING" if any_successful else "FAILED"
            self.phase.setText(f"Organization reports: {status}")
        self.finished_callback(0 if all_successful else 2)

    def _start_error(self, message: str) -> None:
        duration_seconds = self._elapsed_seconds()
        self.elapsed_timer.stop()
        self.elapsed_label.setText(f"Stopped at {format_duration(duration_seconds)}")
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.refresh_inventory_button.setEnabled(True)
        self._set_configuration_enabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.phase.setText("Could not start")
        show_error(self, "Could not start PowerShell", message)

    def _elapsed_seconds(self) -> int:
        if self.batch_started_monotonic is None:
            return 0
        return max(0, int(monotonic() - self.batch_started_monotonic))

    def _update_elapsed(self) -> None:
        self.elapsed_label.setText(f"Elapsed {format_duration(self._elapsed_seconds())}")

    def _open_test_folder(self) -> None:
        if self.batch_directory and self.batch_directory.exists():
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.batch_directory)))


class ReportsPage(QWidget):
    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config = config
        self.reports: list[Report] = []
        root = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(PageHeading("Evidence", "Reports", "Review prior results and open the exact evidence folder."))
        heading.addStretch()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        heading.addWidget(refresh, alignment=Qt.AlignTop)
        root.addLayout(heading)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.list = QListWidget()
        self.list.setMinimumWidth(315)
        self.list.currentRowChanged.connect(self._select)
        body.addWidget(self.list)
        detail_panel = QFrame()
        detail_panel.setObjectName("Panel")
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(20, 20, 20, 20)
        self.detail_title = QLabel("Select a report")
        self.detail_title.setObjectName("CardTitle")
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Report details will appear here.")
        buttons = QHBoxLayout()
        self.open_folder_button = QPushButton("Open evidence folder")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.open_txt_button = QPushButton("Open text report")
        self.open_txt_button.setEnabled(False)
        self.open_txt_button.clicked.connect(self._open_txt)
        self.open_html_button = QPushButton("Open HTML report")
        self.open_html_button.setObjectName("Primary")
        self.open_html_button.setEnabled(False)
        self.open_html_button.clicked.connect(self._open_html)
        self.open_pdf_button = QPushButton("Open PDF report")
        self.open_pdf_button.setEnabled(False)
        self.open_pdf_button.clicked.connect(self._open_pdf)
        buttons.addWidget(self.open_folder_button)
        buttons.addWidget(self.open_txt_button)
        buttons.addWidget(self.open_html_button)
        buttons.addWidget(self.open_pdf_button)
        buttons.addStretch()
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail, stretch=1)
        detail_layout.addLayout(buttons)
        body.addWidget(detail_panel, stretch=1)
        root.addLayout(body, stretch=1)

    def refresh(self) -> None:
        self.reports = list_reports(str(self.config.get("restore_root")))
        self.list.clear()
        for report in self.reports:
            status = overall_status(report.data)
            duration = format_duration(report.data.get("DurationSeconds"))
            duration_suffix = f"  ·  {duration}" if duration != "—" else ""
            item = QListWidgetItem(f"{status}{duration_suffix}  ·  {report.timestamp}\n{report.data.get('Organization', 'Unknown tenant')}")
            item.setToolTip(str(report.path))
            self.list.addItem(item)
        if self.reports:
            self.list.setCurrentRow(0)
        else:
            self.detail_title.setText("No reports found")
            self.detail.setPlainText(f"No Report_Summary.json files were found under:\n{self.config.get('restore_root')}")
            self.open_folder_button.setEnabled(False)
            self.open_txt_button.setEnabled(False)
            self.open_html_button.setEnabled(False)
            self.open_pdf_button.setEnabled(False)

    def _select(self, row: int) -> None:
        if row < 0 or row >= len(self.reports):
            return
        report = self.reports[row]
        data = report.data
        lines = [
            f"Organization: {data.get('Organization', '—')}",
            f"Job: {data.get('JobName', '—')}",
            f"Run: {report.timestamp}",
            f"Duration: {format_duration(data.get('DurationSeconds'))}",
            f"Backup: {data.get('BackupStatus', '—')}",
            f"Restore point: {data.get('RestorePointDate', '—')}",
            "",
        ]
        for name in ("Exchange", "OneDrive", "SharePoint"):
            item: dict[str, Any] = data.get(name) or {}
            lines.extend([
                name.upper(),
                f"  Status: {normalize_status(item.get('Status'), 'N/A')}",
                f"  Source: {item.get('SourceMailbox') or item.get('SourceUser') or item.get('Site') or '—'}",
                f"  Item: {item.get('Subject') or item.get('FileName') or '—'}",
                f"  File: {item.get('LocalFile', '—')}",
                f"  SHA-256: {item.get('SHA256', '—')}",
                "",
            ])
        self.detail_title.setText(f"Restore test {overall_status(data)}")
        self.detail.setPlainText("\n".join(lines))
        self.open_folder_button.setEnabled(True)
        self.open_txt_button.setEnabled(report.path.with_suffix(".txt").exists())
        self.open_html_button.setEnabled(report.path.with_suffix(".html").exists())
        self.open_pdf_button.setEnabled(report.path.with_suffix(".pdf").exists())

    def _current(self) -> Report | None:
        row = self.list.currentRow()
        return self.reports[row] if 0 <= row < len(self.reports) else None

    def _open_folder(self) -> None:
        if report := self._current():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report.path.parent)))

    def _open_txt(self) -> None:
        if report := self._current():
            txt_path = report.path.with_suffix(".txt")
            if txt_path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(txt_path)))

    def _open_html(self) -> None:
        if report := self._current():
            html_path = report.path.with_suffix(".html")
            if html_path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path)))

    def _open_pdf(self) -> None:
        if report := self._current():
            pdf_path = report.path.with_suffix(".pdf")
            if pdf_path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf_path)))


class SettingsPage(QWidget):
    def __init__(self, config: ConfigManager, save_callback) -> None:
        super().__init__()
        self.config = config
        self.save_callback = save_callback
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.addWidget(PageHeading(
            "Preferences",
            "Settings",
            "Configure storage, run behavior and report output. Organizations and jobs are selected in Run test.",
        ))

        cards = QHBoxLayout()
        cards.setSpacing(16)

        storage_panel = QFrame()
        storage_panel.setObjectName("Panel")
        storage_layout = QVBoxLayout(storage_panel)
        storage_layout.setContentsMargins(22, 20, 22, 20)
        storage_layout.setSpacing(12)
        storage_title = QLabel("Storage & retention")
        storage_title.setObjectName("CardTitle")
        storage_description = QLabel("Choose where organization reports are stored and how much history is retained.")
        storage_description.setObjectName("Muted")
        storage_description.setWordWrap(True)
        storage_layout.addWidget(storage_title)
        storage_layout.addWidget(storage_description)

        storage_form = QFormLayout()
        storage_form.setVerticalSpacing(14)
        self.restore = QLineEdit(str(config.get("restore_root")))
        restore_row = QHBoxLayout()
        restore_row.addWidget(self.restore)
        restore_browse = QPushButton("Browse")
        restore_browse.clicked.connect(self._browse_restore)
        restore_row.addWidget(restore_browse)
        self.max_tests = QSpinBox()
        self.max_tests.setRange(1, 100)
        self.max_tests.setValue(int(config.get("max_restore_tests", 5)))
        self.max_tests.setToolTip("Oldest restore test folders are automatically deleted when this limit is exceeded.")
        storage_form.addRow("Restore folder", restore_row)
        storage_form.addRow("Tests kept per org", self.max_tests)
        storage_layout.addLayout(storage_form)
        storage_layout.addStretch()
        cards.addWidget(storage_panel, stretch=1)

        output_panel = QFrame()
        output_panel.setObjectName("Panel")
        output_layout = QVBoxLayout(output_panel)
        output_layout.setContentsMargins(22, 20, 22, 20)
        output_layout.setSpacing(12)
        output_title = QLabel("Run defaults & reports")
        output_title.setObjectName("CardTitle")
        output_description = QLabel("Set the starting behavior for new tests and choose client-facing report formats.")
        output_description.setObjectName("Muted")
        output_description.setWordWrap(True)
        output_layout.addWidget(output_title)
        output_layout.addWidget(output_description)

        self.skip_backups = QCheckBox("Use latest restore points by default")
        self.skip_backups.setChecked(bool(config.get("skip_backups", True)))
        self.skip_backups.setToolTip("Starts new runs with backup jobs disabled. You can change it in Run test before starting.")
        output_layout.addWidget(self.skip_backups)
        skip_help = QLabel("When enabled, the app tests existing restore points without starting a new backup job.")
        skip_help.setObjectName("Muted")
        skip_help.setWordWrap(True)
        output_layout.addWidget(skip_help)

        selected_formats = {str(item).lower() for item in (config.get("report_formats", ["txt", "html", "pdf"]) or [])}
        formats_label = QLabel("Final report formats")
        formats_label.setObjectName("Muted")
        output_layout.addWidget(formats_label)
        self.format_txt = QCheckBox("Text (.txt)")
        self.format_html = QCheckBox("HTML (.html)")
        self.format_pdf = QCheckBox("PDF (.pdf)")
        self.format_txt.setChecked("txt" in selected_formats)
        self.format_html.setChecked("html" in selected_formats)
        self.format_pdf.setChecked("pdf" in selected_formats)
        for checkbox in (self.format_txt, self.format_html, self.format_pdf):
            output_layout.addWidget(checkbox)
        json_help = QLabel("Internal JSON is always generated for Dashboard and Reports.")
        json_help.setObjectName("Muted")
        json_help.setWordWrap(True)
        output_layout.addWidget(json_help)
        output_layout.addStretch()
        cards.addWidget(output_panel, stretch=1)
        root.addLayout(cards)

        advanced_panel = QFrame()
        advanced_panel.setObjectName("Panel")
        advanced_layout = QVBoxLayout(advanced_panel)
        advanced_layout.setContentsMargins(22, 18, 22, 18)
        advanced_layout.setSpacing(10)
        advanced_header = QHBoxLayout()
        advanced_title = QLabel("Advanced · PowerShell engine")
        advanced_title.setObjectName("CardTitle")
        advanced_hint = QLabel("Leave empty to use the tested script bundled with the app.")
        advanced_hint.setObjectName("Muted")
        advanced_header.addWidget(advanced_title)
        advanced_header.addStretch()
        advanced_header.addWidget(advanced_hint)
        advanced_layout.addLayout(advanced_header)
        self.script = QLineEdit(str(config.get("script_path")))
        self.script.setPlaceholderText(f"Bundled script ({bundled_script()})")
        script_row = QHBoxLayout()
        script_row.addWidget(self.script)
        script_browse = QPushButton("Browse")
        script_browse.clicked.connect(self._browse_script)
        script_row.addWidget(script_browse)
        advanced_layout.addLayout(script_row)
        root.addWidget(advanced_panel)

        save = QPushButton("Save settings")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        root.addWidget(save, alignment=Qt.AlignRight)
        root.addStretch()

    def _browse_restore(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose default restore folder", self.restore.text())
        if chosen: self.restore.setText(chosen)

    def _browse_script(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose PowerShell script", self.script.text(), "PowerShell scripts (*.ps1)")
        if chosen: self.script.setText(chosen)

    def _save(self) -> None:
        restore = self.restore.text().strip()
        if not restore:
            show_warning(self, "Restore folder required", "Choose a default restore folder.")
            return
        custom = self.script.text().strip()
        if custom and not Path(custom).is_file():
            show_warning(self, "Script not found", "The custom PowerShell script does not exist.")
            return
        report_formats = [
            name for name, checkbox in (
                ("txt", self.format_txt),
                ("html", self.format_html),
                ("pdf", self.format_pdf),
            )
            if checkbox.isChecked()
        ]
        if not report_formats:
            show_warning(self, "Report format required", "Select at least one final report format: Text, HTML, or PDF.")
            return
        self.config.save({
            "restore_root": restore,
            "script_path": custom,
            "max_restore_tests": self.max_tests.value(),
            "skip_backups": self.skip_backups.isChecked(),
            "report_formats": report_formats,
        })
        self.save_callback()
        names = {"txt": "Text", "html": "HTML", "pdf": "PDF"}
        selected = ", ".join(names[item] for item in report_formats)
        show_information(self, "Settings saved", f"Your defaults have been saved.\nFinal reports: {selected}.", success=True)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = ConfigManager()
        self.runner = PowerShellRunner(self)
        self.setWindowTitle("Veeam M365 Restore Tester")
        self.resize(1240, 790)
        self.setMinimumSize(980, 680)
        self.setStyleSheet(stylesheet())
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(14, 20, 14, 18)

        brand_header = QFrame()
        brand_layout = QHBoxLayout(brand_header)
        brand_layout.setContentsMargins(4, 0, 4, 8)
        brand_layout.setSpacing(10)
        icon_path = resource_path("assets/logos_icon.png")
        if icon_path.is_file():
            icon_label = QLabel()
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                icon_label.setFixedSize(32, 32)
                brand_layout.addWidget(icon_label)
        titles_v = QVBoxLayout()
        titles_v.setSpacing(1)
        brand = QLabel("VEEAM  M365")
        brand.setObjectName("Eyebrow")
        product = QLabel("Restore Tester")
        product.setStyleSheet("font-size: 16px; font-weight: 700; color: #FFFFFF; letter-spacing: -0.2px;")
        titles_v.addWidget(brand)
        titles_v.addWidget(product)
        brand_layout.addLayout(titles_v)
        brand_layout.addStretch()
        nav.addWidget(brand_header)
        nav.addSpacing(18)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(lambda: self.show_page(1))
        self.run_page = RunPage(self.config, self.runner, self._run_finished)
        self.reports = ReportsPage(self.config)
        self.settings = SettingsPage(self.config, self._settings_saved)
        for page in (self.dashboard, self.run_page, self.reports, self.settings):
            self.stack.addWidget(page)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        nav_items = (
            ("Dashboard", "assets/nav_dashboard.svg", 0),
            ("Run test", "assets/nav_run.svg", 1),
            ("Reports", "assets/nav_reports.svg", 2),
            ("Settings", "assets/nav_settings.svg", 3),
        )
        for text, icon_rel, index in nav_items:
            button = QPushButton(f"  {text}")
            button.setObjectName("Nav")
            button.setCheckable(True)
            icon_file = resource_path(icon_rel)
            if icon_file.is_file():
                button.setIcon(QIcon(str(icon_file)))
                button.setIconSize(QSize(16, 16))
            button.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
            self.group.addButton(button, index)
            nav.addWidget(button)
        self.group.button(0).setChecked(True)
        nav.addStretch()

        footer_box = QFrame()
        footer_layout = QVBoxLayout(footer_box)
        footer_layout.setContentsMargins(4, 8, 4, 4)
        footer_layout.setSpacing(4)
        ps_ready = bool(powershell_path())
        shell_status = QLabel("●  PowerShell 7 ready" if ps_ready else "○  PowerShell 7 missing")
        shell_status.setStyleSheet(f"font-size: 11px; color:{COLORS['green'] if ps_ready else COLORS['red']};")
        admin = QLabel("●  Administrator" if self._is_admin() else "○  Standard session")
        admin.setObjectName("Muted")
        admin.setStyleSheet("font-size: 11px; color: #888888;")
        admin.setToolTip("The packaged app requests administrator access at startup.")
        footer_layout.addWidget(shell_status)
        footer_layout.addWidget(admin)
        nav.addWidget(footer_box)

        shell.addWidget(sidebar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 24)
        content_layout.addWidget(self.stack)
        shell.addWidget(content, stretch=1)
        self.refresh_all()

    @staticmethod
    def _is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if button := self.group.button(index):
            button.setChecked(True)
        if index == 2:
            self.reports.refresh()
        elif index == 1:
            self.run_page.sync_defaults()

    def refresh_all(self) -> None:
        report = latest_report(str(self.config.get("restore_root")))
        self.dashboard.load_report(report)
        self.reports.refresh()

    def _run_finished(self, _code: int) -> None:
        self.refresh_all()
        report = latest_report(str(self.config.get("restore_root")))
        if report:
            self.dashboard.load_report(report)

    def _settings_saved(self) -> None:
        self.run_page.sync_defaults()
        self.refresh_all()

    def closeEvent(self, event) -> None:
        if self.runner.is_running():
            confirmed = ask_confirmation(
                self,
                "Test still running",
                "Stop the active test and close the application? Extracted files already written to disk will remain available.",
                accept_text="Stop and close",
                cancel_text="Keep open",
                destructive=True,
            )
            if not confirmed:
                event.ignore()
                return
            self.runner.stop()
        event.accept()
