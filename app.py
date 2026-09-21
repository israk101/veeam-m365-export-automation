from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
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
from core.batch_report import build_batch_summary, write_batch_report
from core.discovery import InventoryDiscovery, flatten_inventory
from core.paths import bundled_script, discovery_script, powershell_path, resolve_script, resource_path
from core.reports import Report, latest_report, list_reports, read_report
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
        passed = report.passed
        status_file = resource_path(f"assets/{'status_verified.svg' if passed else 'status_attention.svg'}")
        if status_file.is_file():
            self.summary_icon.setPixmap(QPixmap(str(status_file)).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.summary_icon.setText("✓" if passed else "!")
            self.summary_icon.setStyleSheet(f"font-size: 24px; color: {COLORS['green'] if passed else COLORS['red']}; font-weight: 700;")

        self.summary_title.setText("All workloads verified" if passed else "Restore test needs attention")
        self.summary_title.setStyleSheet(f"font-size: 15px; font-weight: 650; color: {'#FFFFFF' if passed else COLORS['red']};")
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
        self.batch_started: datetime | None = None
        self.batch_directory: Path | None = None
        self.current_job_root: Path | None = None
        self.stop_requested = False
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.addWidget(PageHeading("Guided operation", "Run restore test", "Choose an organization, select one or more jobs, then follow each restore test in one place."))
        body = QHBoxLayout()
        body.setSpacing(16)
        form_panel = QFrame()
        form_panel.setObjectName("Panel")
        form_panel.setMinimumWidth(390)
        form_panel.setMaximumWidth(480)
        panel_layout = QVBoxLayout(form_panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(11)
        form_title = QLabel("Test configuration")
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
        form = QFormLayout()
        form.setVerticalSpacing(11)
        self.restore = QLineEdit(str(config.get("restore_root")))
        restore_row = QHBoxLayout()
        restore_row.addWidget(self.restore)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_restore)
        restore_row.addWidget(browse)
        form.addRow("Restore folder", restore_row)
        panel_layout.addLayout(form)
        jobs_header = QHBoxLayout()
        jobs_label = QLabel("Organizations & Jobs")
        jobs_label.setObjectName("CardTitle")
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all_jobs(Qt.Checked))
        clear_all = QPushButton("Clear")
        clear_all.clicked.connect(lambda: self._set_all_jobs(Qt.Unchecked))
        jobs_header.addWidget(jobs_label)
        jobs_header.addStretch()
        jobs_header.addWidget(select_all)
        jobs_header.addWidget(clear_all)
        panel_layout.addLayout(jobs_header)
        self.job_tree = OrgJobTree()
        self.job_tree.setMinimumHeight(150)
        self.job_tree.setMaximumHeight(230)
        self.job_tree.setToolTip("Select jobs across any organization. Checked jobs run sequentially.")
        panel_layout.addWidget(self.job_tree)
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
        tip = QLabel("Extracted files remain in the selected folder. The test does not restore data back to Microsoft 365.")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        panel_layout.addWidget(tip)
        actions = QHBoxLayout()
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
        self.phase = QLabel("Ready")
        self.phase.setObjectName("Muted")
        title_row.addWidget(activity_title)
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
        self.sync_defaults()
        if auto_discover:
            QTimer.singleShot(0, self.refresh_inventory)

    def sync_defaults(self) -> None:
        if not self.restore.hasFocus():
            self.restore.setText(str(self.config.get("restore_root")))
        default_skip = bool(self.config.get("skip_backups", True))
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
        count = len(self.inventory)
        self.inventory_status.setText(f"{count} organization{'s' if count != 1 else ''} found on localhost")

    def _inventory_failed(self, message: str) -> None:
        self.refresh_inventory_button.setEnabled(True)
        self.job_tree.setEnabled(False)
        self.inventory_status.setText("Discovery unavailable — select Refresh to retry")
        self.log.append_line(f"[ERR] Organization discovery failed: {message}", "error")

    def _set_all_jobs(self, state: Qt.CheckState) -> None:
        self.job_tree.set_all(state)

    def _selected_jobs(self) -> list[str]:
        return [job for _, job in self.job_tree.selected_jobs()]

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
        first_org = selections[0].org_name
        first_job = selections[0].job_name
        self.config.save({
            "organization": first_org,
            "job_name": first_job,
            "selected_jobs": [s.job_name for s in selections],
            "selected_multi_org_jobs": multi_dict,
            "restore_root": restore,
        })
        self.log.clear()
        mode = "latest restore points" if self.skip.isChecked() else "backup then restore"
        org_label = first_org if len(multi_dict) <= 1 else f"{len(multi_dict)} organizations"
        self.log.append_line(f"[INFO] Starting {len(selections)} job(s) for {org_label} — {mode}.", "info")
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.open_folder_button.setEnabled(False)
        self.refresh_inventory_button.setEnabled(False)
        self.job_tree.setEnabled(False)
        self.progress.setRange(0, 0)
        self.pending_jobs = list(selections)
        self.completed_jobs = []
        self.batch_started = datetime.now()
        self.batch_directory = Path(restore) / f"Batch_{self.batch_started.strftime('%Y%m%d_%H%M%S')}"
        self.stop_requested = False
        self._start_next_job()

    def _start_next_job(self) -> None:
        if self.stop_requested or not self.pending_jobs:
            self._finish_batch()
            return
        self.current_job = self.pending_jobs.pop(0)
        self.current_started_at = datetime.now().timestamp()
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
        self._start_next_job()

    def _finish_batch(self) -> None:
        orgs = list(dict.fromkeys(job.get("organization", "") for job in self.completed_jobs if job.get("organization")))
        org_label = orgs[0] if len(orgs) == 1 else (f"{len(orgs)} organizations" if orgs else "")
        summary = build_batch_summary(org_label, self.completed_jobs, self.skip.isChecked(), self.batch_started)
        max_keep = int(self.config.get("max_restore_tests", 5))
        report_formats = list(self.config.get("report_formats", ["txt", "html", "pdf"]))
        report_path = write_batch_report(
            self.restore.text().strip(),
            summary,
            max_keep=max_keep,
            report_formats=report_formats,
        ) if self.completed_jobs else None
        if report_path and report_path.is_file():
            self.batch_directory = report_path.parent
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.open_folder_button.setEnabled(bool(self.batch_directory and self.batch_directory.exists()))
        self.refresh_inventory_button.setEnabled(True)
        self.job_tree.setEnabled(bool(self.inventory))
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if summary.get("AllSuccessful") else 0)
        if self.stop_requested:
            self.phase.setText("Stopped by user")
            self.log.append_line("[WARN] Multi-job run stopped by user.", "warning")
        elif summary.get("AllSuccessful"):
            self.phase.setText("All workloads verified")
            self.log.append_line(f"[OK] Combined report passed: {report_path}", "success")
        else:
            self.phase.setText("Combined report needs attention")
            self.log.append_line(f"[WARN] Combined report needs attention: {report_path}", "warning")
        self.finished_callback(0 if summary.get("AllSuccessful") else 2)

    def _start_error(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.refresh_inventory_button.setEnabled(True)
        self.job_tree.setEnabled(bool(self.inventory))
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.phase.setText("Could not start")
        show_error(self, "Could not start PowerShell", message)

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
            status = "Passed" if report.passed else "Needs attention"
            item = QListWidgetItem(f"{status}  ·  {report.timestamp}\n{report.data.get('Organization', 'Unknown tenant')}")
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
            f"Backup: {data.get('BackupStatus', '—')}",
            f"Restore point: {data.get('RestorePointDate', '—')}",
            "",
        ]
        for name in ("Exchange", "OneDrive", "SharePoint"):
            item: dict[str, Any] = data.get(name) or {}
            lines.extend([
                name.upper(),
                f"  Status: {item.get('Status', '—')}",
                f"  Source: {item.get('SourceMailbox') or item.get('SourceUser') or item.get('Site') or '—'}",
                f"  Item: {item.get('Subject') or item.get('FileName') or '—'}",
                f"  File: {item.get('LocalFile', '—')}",
                f"  SHA-256: {item.get('SHA256', '—')}",
                "",
            ])
        self.detail_title.setText("Restore test passed" if report.passed else "Restore test needs attention")
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
        root.addWidget(PageHeading("Preferences", "Settings", "Set sensible defaults once; you can still change them before every run."))
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setMaximumWidth(780)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 22, 22, 22)
        title = QLabel("Default values")
        title.setObjectName("CardTitle")
        panel_layout.addWidget(title)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        self.org = QLineEdit(str(config.get("organization")))
        self.job = QLineEdit(str(config.get("job_name")))
        self.restore = QLineEdit(str(config.get("restore_root")))
        restore_row = QHBoxLayout()
        restore_row.addWidget(self.restore)
        restore_browse = QPushButton("Browse")
        restore_browse.clicked.connect(self._browse_restore)
        restore_row.addWidget(restore_browse)
        self.script = QLineEdit(str(config.get("script_path")))
        self.script.setPlaceholderText(f"Bundled script ({bundled_script()})")
        script_row = QHBoxLayout()
        script_row.addWidget(self.script)
        script_browse = QPushButton("Browse")
        script_browse.clicked.connect(self._browse_script)
        script_row.addWidget(script_browse)
        self.max_tests = QSpinBox()
        self.max_tests.setRange(1, 100)
        self.max_tests.setValue(int(config.get("max_restore_tests", 5)))
        self.max_tests.setToolTip("Oldest restore test folders are automatically deleted when this limit is exceeded.")
        form.addRow("Organization", self.org)
        form.addRow("Backup job", self.job)
        form.addRow("Restore folder", restore_row)
        form.addRow("Custom script", script_row)
        form.addRow("Max tests per org", self.max_tests)

        self.skip_backups = QCheckBox("Don't run backup jobs (use latest restore points)")
        self.skip_backups.setChecked(bool(config.get("skip_backups", True)))
        self.skip_backups.setToolTip("Default value for new tests on the Run page: test existing restore points without running new backups.")
        form.addRow("Default backup behavior", self.skip_backups)

        selected_formats = {str(item).lower() for item in (config.get("report_formats", ["txt", "html", "pdf"]) or [])}
        formats_widget = QWidget()
        formats_layout = QHBoxLayout(formats_widget)
        formats_layout.setContentsMargins(0, 0, 0, 0)
        formats_layout.setSpacing(18)
        self.format_txt = QCheckBox("Text (.txt)")
        self.format_html = QCheckBox("HTML (.html)")
        self.format_pdf = QCheckBox("PDF (.pdf)")
        self.format_txt.setChecked("txt" in selected_formats)
        self.format_html.setChecked("html" in selected_formats)
        self.format_pdf.setChecked("pdf" in selected_formats)
        for checkbox in (self.format_txt, self.format_html, self.format_pdf):
            formats_layout.addWidget(checkbox)
        formats_layout.addStretch()
        form.addRow("Final report formats", formats_widget)

        panel_layout.addLayout(form)
        note = QLabel(
            "Leave Custom script empty to use the tested copy bundled inside the application.\n"
            "JSON evidence is always generated because Dashboard and Reports use it internally. "
            "Choose one or more user-facing final report formats above.\n"
            "Oldest restore test folders are pruned automatically when the per-org limit is reached."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        panel_layout.addWidget(note)
        save = QPushButton("Save settings")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        panel_layout.addWidget(save, alignment=Qt.AlignLeft)
        root.addWidget(panel, alignment=Qt.AlignLeft)
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
            "organization": self.org.text().strip(),
            "job_name": self.job.text().strip(),
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
