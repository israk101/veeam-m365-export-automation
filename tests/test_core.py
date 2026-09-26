from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import time
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QPushButton

from core.config import ConfigManager
from core.batch_report import (
    build_batch_summary,
    build_organization_summaries,
    normalize_status,
    overall_status,
    write_batch_report,
)
from core.discovery import MARKER, flatten_inventory, parse_inventory_output
from core.html_report import render_html_report, write_html_report
from core.pdf_report import write_pdf_report
from core.paths import powershell_path
from core.reports import format_duration, latest_report, list_reports
from core.runner import PowerShellRunner
from ui.widgets import OrgJobTree
from ui.dialogs import StyledDialog
from app import ReportsPage, RunPage, SettingsPage, cleanup_batch_workspace


def _run_sample_helper(tmp_path: Path, body: str) -> dict:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is not installed")
    helper = Path(__file__).resolve().parents[1] / "scripts" / "RestoreSampleHelpers.ps1"
    helper_literal = str(helper).replace("'", "''")
    script = tmp_path / "helper-test.ps1"
    script.write_text(
        textwrap.dedent(
            f"""
            $ErrorActionPreference = 'Stop'
            function Write-WarnLog {{ param([string]$Message) }}
            . '{helper_literal}'
            {body}
            """
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout.strip())


def test_restore_engine_never_falls_back_to_another_job_restore_point() -> None:
    project_root = Path(__file__).resolve().parents[1]
    canonical = (project_root / "scripts" / "Invoke-SimpleM365BackupRestoreTest.ps1").read_text(encoding="utf-8-sig")
    assert "Get-VBORestorePoint -Job $job -Latest" in canonical
    assert "Get-VBORestorePoint -Organization $org" not in canonical
    assert "evitare di usare il Restore Point di un altro job" in canonical


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    manager = ConfigManager(path)
    manager.save({"restore_root": "D:\\RestoreTests", "organization": "legacy", "unknown": "ignored"})
    loaded = ConfigManager(path)
    assert loaded.get("restore_root") == "D:\\RestoreTests"
    assert "organization" not in loaded.data
    assert "unknown" not in loaded.data


def test_config_persists_multi_org_jobs(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    manager = ConfigManager(path)
    multi = {"OrgA": ["Job1", "Job2"], "OrgB": ["Job3"]}
    manager.save({"selected_multi_org_jobs": multi})
    loaded = ConfigManager(path)
    assert loaded.get("selected_multi_org_jobs") == multi


def test_config_persists_report_formats(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    manager = ConfigManager(path)
    manager.save({"report_formats": ["html", "pdf"]})
    loaded = ConfigManager(path)
    assert loaded.get("report_formats") == ["html", "pdf"]


def test_flatten_inventory_produces_correct_dict() -> None:
    payload = {
        "Organizations": [
            {"Name": "Contoso", "Jobs": [{"Name": "Job-Exchange", "Id": "1"}, {"Name": "Job-OneDrive", "Id": "2"}]},
            {"Name": "Fabrikam", "Jobs": [{"Name": "Fabrikam-All", "Id": "3"}]},
        ]
    }
    flattened = flatten_inventory(payload)
    assert list(flattened.keys()) == ["Contoso", "Fabrikam"]
    assert len(flattened["Contoso"]) == 2
    assert flattened["Contoso"][0]["Name"] == "Job-Exchange"
    assert flattened["Fabrikam"][0]["Name"] == "Fabrikam-All"


def test_reports_are_sorted(tmp_path: Path) -> None:
    older = tmp_path / "20260101_010101"
    newer = tmp_path / "20260102_010101"
    older.mkdir()
    newer.mkdir()
    (older / "Report_Summary.json").write_text(json.dumps({"RunTimestamp": "20260101_010101", "AllSuccessful": False}), encoding="utf-8")
    (newer / "Report_Summary.json").write_text(json.dumps({"RunTimestamp": "20260102_010101", "AllSuccessful": True}), encoding="utf-8")
    (older / "Report_Summary.json").touch()
    reports = list_reports(tmp_path)
    assert len(reports) == 2
    assert latest_report(tmp_path) == reports[0]


def test_invalid_report_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "Report_Summary.json").write_text("not json", encoding="utf-8")
    assert list_reports(tmp_path) == []


def test_runner_streams_structured_output(tmp_path: Path) -> None:
    shell = powershell_path()
    if not shell:
        return
    app = QApplication.instance() or QApplication([])
    script = tmp_path / "smoke.ps1"
    script.write_text('param($OrganizationName,$JobName,$LocalRestoreRoot,[switch]$SkipBackup)\nWrite-Host "[INFO] FASE 1"\nWrite-Host "[OK] done"\nexit 0\n', encoding="utf-8")
    runner = PowerShellRunner()
    seen: list[tuple[str, str]] = []
    result: list[int] = []
    loop = QEventLoop()
    runner.output.connect(lambda line, level: seen.append((line, level)))
    runner.finished.connect(lambda code: (result.append(code), loop.quit()))
    runner.start(shell, script, "tenant", "job", str(tmp_path), True)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    app.processEvents()
    assert result == [0]
    assert any(level == "success" and "done" in line for line, level in seen)


def test_inventory_parser_reads_organizations_and_jobs() -> None:
    payload = {"Server": "localhost", "Organizations": [{"Name": "tenant-a", "Jobs": [{"Name": "Exchange"}, {"Name": "Files"}]}]}
    parsed = parse_inventory_output("noise\n" + MARKER + json.dumps(payload))
    assert parsed["Organizations"][0]["Name"] == "tenant-a"
    assert [job["Name"] for job in parsed["Organizations"][0]["Jobs"]] == ["Exchange", "Files"]


def test_batch_report_merges_successful_workloads_across_jobs(tmp_path: Path) -> None:
    jobs = [
        {"job": "Mail", "exit_code": 2, "report_path": "mail.json", "report": {"Exchange": {"Status": "SUCCESS", "Subject": "message"}, "OneDrive": {"Status": "FAILED"}, "SharePoint": {"Status": "FAILED"}}},
        {"job": "OneDrive", "exit_code": 2, "report_path": "od.json", "report": {"Exchange": {"Status": "FAILED"}, "OneDrive": {"Status": "SUCCESS", "FileName": "file.docx"}, "SharePoint": {"Status": "FAILED"}}},
        {"job": "SharePoint", "exit_code": 2, "report_path": "sp.json", "report": {"Exchange": {"Status": "FAILED"}, "OneDrive": {"Status": "FAILED"}, "SharePoint": {"Status": "SUCCESS", "FileName": "site.docx"}}},
    ]
    summary = build_batch_summary("tenant-a", jobs, True, datetime(2026, 9, 18, 10, 30, 0))
    assert summary["AllSuccessful"] is False
    assert summary["OverallStatus"] == "WARNING"
    assert summary["SelectedJobs"] == ["Mail", "OneDrive", "SharePoint"]
    path = write_batch_report(tmp_path, summary)
    assert path.name == "Report_Summary.json"
    assert json.loads(path.read_text(encoding="utf-8"))["AllSuccessful"] is False


def test_partial_job_selection_success_and_failure(tmp_path: Path) -> None:
    # 2 of 3 jobs selected: both succeed
    jobs_pass = [
        {"job": "Mail", "exit_code": 0, "report_path": "mail.json", "report": {"Exchange": {"Status": "SUCCESS"}}},
        {"job": "OneDrive", "exit_code": 0, "report_path": "od.json", "report": {"OneDrive": {"Status": "SUCCESS"}}},
    ]
    summary_pass = build_batch_summary("israk.onmicrosoft.com", jobs_pass, True, datetime(2026, 9, 18, 12, 0, 0))
    assert summary_pass["AllSuccessful"] is True

    # 2 of 3 jobs selected: 1 succeeds, 1 fails
    jobs_fail = [
        {"job": "Mail", "exit_code": 0, "report_path": "mail.json", "report": {"Exchange": {"Status": "SUCCESS"}}},
        {"job": "OneDrive", "exit_code": 1, "report_path": "od.json", "report": {"OneDrive": {"Status": "FAILED"}}},
    ]
    summary_fail = build_batch_summary("israk.onmicrosoft.com", jobs_fail, True, datetime(2026, 9, 18, 12, 0, 0))
    assert summary_fail["AllSuccessful"] is False


def test_run_page_executes_multiple_jobs_and_writes_combined_report(tmp_path: Path) -> None:
    shell = powershell_path()
    if not shell:
        return
    app = QApplication.instance() or QApplication([])
    fake_script = tmp_path / "fake-restore.ps1"
    fake_script.write_text(r'''
param($OrganizationName,$JobName,$LocalRestoreRoot,[switch]$SkipBackup)
$run = Join-Path $LocalRestoreRoot (Get-Date -Format 'yyyyMMdd_HHmmss')
New-Item -ItemType Directory -Path $run -Force | Out-Null
$ok = @{ Status='SUCCESS'; FileName="$JobName-item.dat"; LocalFile=(Join-Path $run "$JobName-item.dat"); SizeBytes=10; SHA256='ABC' }
$bad = @{ Status='NOT_CONFIGURED' }
[IO.File]::WriteAllBytes($ok.LocalFile, [byte[]](1,2,3,4))
$report = [ordered]@{
  RunTimestamp=(Get-Date -Format 'yyyyMMdd_HHmmss'); Organization=$OrganizationName; JobName=$JobName
  BackupExecuted=(-not $SkipBackup); BackupStatus=$(if($SkipBackup){'SkippedByUser'}else{'Success'})
  RestorePointDate=(Get-Date).ToString(); Exchange=$(if($JobName -eq 'Mail'){$ok}else{$bad})
  OneDrive=$(if($JobName -eq 'Drive'){$ok}else{$bad}); SharePoint=$(if($JobName -eq 'Sites'){$ok}else{$bad}); AllSuccessful=$true
}
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $run 'Report_Summary.json') -Encoding utf8
Write-Host "[OK] $JobName complete"
exit 0
''', encoding="utf-8")
    config = ConfigManager(tmp_path / "settings.json")
    config.save({"organization": "tenant-a", "restore_root": str(tmp_path), "script_path": str(fake_script)})
    runner = PowerShellRunner()
    results: list[int] = []
    loop = QEventLoop()
    page = RunPage(config, runner, lambda code: (results.append(code), loop.quit()), auto_discover=False)
    page._inventory_loaded({"Organizations": [{"Name": "tenant-a", "Jobs": [{"Name": "Mail"}, {"Name": "Drive"}, {"Name": "Sites"}]}]})
    page._set_all_jobs(Qt.Checked)
    page.skip.setChecked(True)
    page.start()
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    app.processEvents()
    assert results == [0]
    report = latest_report(tmp_path)
    assert report is not None and report.passed
    assert report.data["SelectedJobs"] == ["Mail", "Drive", "Sites"]
    assert report.data["Exchange"]["Status"] == "SUCCESS"
    assert report.data["OneDrive"]["Status"] == "SUCCESS"
    assert report.data["SharePoint"]["Status"] == "SUCCESS"
    page.close()


def test_org_job_tree_widget() -> None:
    app = QApplication.instance() or QApplication([])
    tree = OrgJobTree()
    inventory = {
        "Contoso": [{"Name": "MailJob"}, {"Name": "FilesJob"}],
        "Fabrikam": [{"Name": "AllInOne"}],
    }
    tree.load_inventory(inventory, {"Contoso": ["MailJob"]})

    # Check preselection
    selected = tree.selected_jobs()
    assert ("Contoso", "MailJob") in selected
    assert ("Contoso", "FilesJob") not in selected
    assert ("Fabrikam", "AllInOne") not in selected
    assert "1 of 2 jobs selected" in tree.topLevelItem(0).text(0)

    # Check select all
    tree.set_all(Qt.Checked)
    selected_all = tree.selected_jobs()
    assert len(selected_all) == 3
    assert ("Contoso", "MailJob") in selected_all
    assert ("Contoso", "FilesJob") in selected_all
    assert ("Fabrikam", "AllInOne") in selected_all
    assert "Contoso  (2 jobs)" in tree.topLevelItem(0).text(0)
    assert "Fabrikam  (1 job)" in tree.topLevelItem(1).text(0)

    # Check unchecking one item dynamically updates label
    tree.topLevelItem(0).child(1).setCheckState(0, Qt.Unchecked)
    assert "1 of 2 jobs selected" in tree.topLevelItem(0).text(0)
    assert tree.topLevelItem(0).checkState(0) == Qt.PartiallyChecked

    # Check clear
    tree.set_all(Qt.Unchecked)
    assert len(tree.selected_jobs()) == 0
    assert tree.selected_dict() == {}
    assert "0 of 2 jobs selected" in tree.topLevelItem(0).text(0)

    # Filtering is case-insensitive, preserves hidden selections, and actions
    # apply only to visible matches.
    visible_orgs, visible_jobs = tree.filter_items("FABRIKAM")
    assert (visible_orgs, visible_jobs) == (1, 1)
    assert tree.topLevelItem(0).isHidden()
    assert not tree.topLevelItem(1).isHidden()
    tree.set_visible(Qt.Checked)
    assert tree.selected_jobs() == [("Fabrikam", "AllInOne")]
    visible_orgs, visible_jobs = tree.filter_items("mail")
    assert (visible_orgs, visible_jobs) == (1, 1)
    assert tree.selected_jobs() == [("Fabrikam", "AllInOne")]
    tree.filter_items("")
    assert not tree.topLevelItem(0).isHidden()
    assert not tree.topLevelItem(1).isHidden()
    tree.close()


def test_settings_focuses_on_operational_defaults(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path / "settings.json")
    page = SettingsPage(manager, lambda: None)
    assert not hasattr(page, "org")
    assert not hasattr(page, "job")
    assert page.restore.text() == str(manager.get("restore_root"))
    assert page.max_tests.value() == int(manager.get("max_restore_tests"))
    assert page.skip_backups.isChecked() is bool(manager.get("skip_backups"))
    page.close()


def test_html_report_generation(tmp_path: Path) -> None:
    summary = {
        "RunTimestamp": "20260918_120000",
        "Organization": "Contoso Ltd",
        "OrganizationsTested": ["Contoso Ltd", "Fabrikam Corp"],
        "AllSuccessful": True,
        "BackupExecuted": False,
        "BackupStatus": "SkippedByUser",
        "RestorePointDate": "18/09/2026 12:00:00",
        "Exchange": {
            "Status": "SUCCESS",
            "RestorePointDate": "18/09/2026 11:30:00",
            "SourceMailbox": "admin@contoso.com",
            "Subject": "Test Subject",
            "LocalFile": "C:\\Restore\\test.eml",
            "SizeBytes": 1024,
            "SHA256": "A" * 64,
        },
        "OneDrive": {
            "Status": "SUCCESS",
            "RestorePointDate": "18/09/2026 11:30:00",
            "SourceUser": "admin@contoso.com",
            "FileName": "doc.docx",
            "LocalFile": "C:\\Restore\\doc.docx",
            "SizeBytes": 2048,
            "SHA256": "B" * 64,
        },
        "SharePoint": {
            "Status": "SUCCESS",
            "RestorePointDate": "18/09/2026 11:30:00",
            "Site": "https://contoso.sharepoint.com/sites/team",
            "FileName": "site.docx",
            "LocalFile": "C:\\Restore\\site.docx",
            "SizeBytes": 4096,
            "SHA256": "C" * 64,
        },
        "JobResults": [
            {
                "organization": "Contoso Ltd",
                "job": "MailJob",
                "exit_code": 0,
                "report": {"AllSuccessful": True, "RestorePointDate": "18/09/2026 11:30:00"},
            },
            {
                "organization": "Fabrikam Corp",
                "job": "SharePointJob",
                "exit_code": 0,
                "report": {"AllSuccessful": True, "RestorePointDate": "18/09/2026 11:30:00"},
            },
        ],
    }

    html = render_html_report(summary)
    assert "<!DOCTYPE html>" in html
    assert "Contoso Ltd, Fabrikam Corp" in html
    assert "RESTORE TEST SUCCESS" in html
    assert "Logos Technologies" in html
    assert "A" * 16 in html

    out = write_html_report(tmp_path, summary)
    assert out.is_file()
    assert out.name == "Report_Summary.html"
    assert len(out.read_text(encoding="utf-8")) > 500


def test_html_report_separate_workload_jobs_show_correct_kpi(tmp_path: Path) -> None:
    jobs = [
        {
            "organization": "israk.onmicrosoft.com",
            "job": "Exchange Mail Backup",
            "exit_code": 0,
            "report_path": "mail.json",
            "report": {
                "Exchange": {"Status": "SUCCESS", "SourceMailbox": "user@test.com", "Subject": "Hello", "SizeBytes": 100, "SHA256": "A" * 64},
                "OneDrive": {"Status": "NOT_CONFIGURED", "Error": "The restore point does not contain any OneDrive data."},
                "SharePoint": {"Status": "NOT_CONFIGURED", "Error": "The restore point does not contain any SharePoint data."},
                "AllSuccessful": True,
            },
        },
        {
            "organization": "israk.onmicrosoft.com",
            "job": "One Drive Backups",
            "exit_code": 0,
            "report_path": "od.json",
            "report": {
                "Exchange": {"Status": "NOT_CONFIGURED", "Error": "The restore point does not contain any Exchange data."},
                "OneDrive": {"Status": "SUCCESS", "SourceUser": "user01", "FileName": "doc.pdf", "SizeBytes": 200, "SHA256": "B" * 64},
                "SharePoint": {"Status": "NOT_CONFIGURED", "Error": "The restore point does not contain any SharePoint data."},
                "AllSuccessful": True,
            },
        },
        {
            "organization": "israk.onmicrosoft.com",
            "job": "Share Point Sites Backup",
            "exit_code": 0,
            "report_path": "sp.json",
            "report": {
                "Exchange": {"Status": "NOT_CONFIGURED", "Error": "The restore point does not contain any Exchange data."},
                "OneDrive": {"Status": "NOT_CONFIGURED", "Error": "The restore point does not contain any OneDrive data."},
                "SharePoint": {"Status": "SUCCESS", "Site": "Sites", "FileName": "page.aspx", "SizeBytes": 300, "SHA256": "C" * 64},
                "AllSuccessful": True,
            },
        },
    ]

    summary = build_batch_summary("israk.onmicrosoft.com", jobs, True, datetime(2026, 9, 18, 11, 36, 5))
    assert summary["AllSuccessful"] is True

    html = render_html_report(summary)
    # KPI card should show 3/3 jobs completed
    assert '<div class="kpi-value">3/3</div>' in html
    assert "RESTORE TEST SUCCESS" in html

    # Badges should indicate individual workload success rather than EXIT 2 failure
    assert "SUCCESS (Exchange)" in html
    assert "SUCCESS (OneDrive)" in html
    assert "SUCCESS (SharePoint)" in html
    assert "badge-success" in html


def test_retention_enforces_max_5(tmp_path: Path) -> None:
    from core.retention import enforce_retention
    org_dir = tmp_path / "TestOrg"
    org_dir.mkdir()
    # Create 7 test directories
    for i in range(7):
        d = org_dir / f"RestoreTest_2026091{i}_120000"
        d.mkdir()
        (d / "Report_Summary.json").write_text("{}", encoding="utf-8")
    assert len(list(org_dir.iterdir())) == 7
    removed = enforce_retention(org_dir, max_keep=5)
    assert len(removed) == 2
    remaining = sorted(d.name for d in org_dir.iterdir() if d.is_dir())
    assert len(remaining) == 5
    # The two oldest (index 0, 1) should be gone
    assert "RestoreTest_20260910_120000" not in remaining
    assert "RestoreTest_20260911_120000" not in remaining
    # Newest should remain
    assert "RestoreTest_20260916_120000" in remaining


def test_retention_ignores_non_matching_dirs(tmp_path: Path) -> None:
    from core.retention import enforce_retention
    org_dir = tmp_path / "Org"
    org_dir.mkdir()
    # Create non-matching dirs that should be ignored
    (org_dir / "random_folder").mkdir()
    (org_dir / "notes.txt").write_text("hello", encoding="utf-8")
    # Create 3 test dirs - all should be kept
    for i in range(3):
        (org_dir / f"RestoreTest_2026090{i}_120000").mkdir()
    removed = enforce_retention(org_dir, max_keep=5)
    assert len(removed) == 0
    # Non-matching dirs should still exist
    assert (org_dir / "random_folder").is_dir()
    assert (org_dir / "notes.txt").is_file()


def test_sanitize_org_name() -> None:
    from core.retention import sanitize_org_name
    assert sanitize_org_name("contoso.onmicrosoft.com") == "contoso.onmicrosoft.com"
    assert sanitize_org_name("My Company Ltd") == "My_Company_Ltd"
    assert sanitize_org_name("org/with\\slashes") == "org_with_slashes"
    assert sanitize_org_name("   ") == "org"
    assert sanitize_org_name("") == "org"


def test_write_batch_report_creates_org_directory(tmp_path: Path) -> None:
    jobs = [
        {"job": "Mail", "organization": "tenant-a", "exit_code": 0, "report_path": "mail.json",
         "report": {"Exchange": {"Status": "SUCCESS"}, "OneDrive": {"Status": "SUCCESS"}, "SharePoint": {"Status": "SUCCESS"}, "AllSuccessful": True}},
    ]
    summary = build_batch_summary("tenant-a", jobs, True, datetime(2026, 9, 18, 14, 0, 0))
    path = write_batch_report(tmp_path, summary, max_keep=5)
    # Should be inside org-specific directory
    assert "tenant-a" in str(path)
    assert "RestoreTest_" in str(path.parent.name)
    assert path.name == "Report_Summary.json"
    assert path.is_file()
    # HTML and TXT should also exist
    assert (path.parent / "Report_Summary.html").is_file()
    assert (path.parent / "Report_Summary.txt").is_file()


def test_multi_org_run_writes_one_isolated_restore_test_per_organization(tmp_path: Path) -> None:
    workspace = tmp_path / "Batch_20260921_120000"
    jobs: list[dict] = []
    workloads = ("Exchange", "OneDrive", "SharePoint")
    for org_index in range(1, 6):
        organization = f"tenant-{org_index}.onmicrosoft.com"
        for job_index, workload in enumerate(workloads, start=1):
            source = workspace / "Jobs" / organization / f"job-{job_index}" / f"sample-{org_index}-{job_index}.bin"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(f"{organization}/{workload}".encode())
            report = {
                name: (
                    {"Status": "SUCCESS", "LocalFile": str(source)}
                    if name == workload else {"Status": "NOT_CONFIGURED"}
                )
                for name in workloads
            }
            jobs.append({
                "organization": organization,
                "job": f"job-{job_index}",
                "exit_code": 0,
                "report_path": str(source.parent / "Report_Summary.json"),
                "report": report,
                "duration_seconds": job_index,
            })

    summaries = build_organization_summaries(
        jobs,
        True,
        datetime(2026, 9, 21, 12, 0, 0),
    )
    assert len(summaries) == 5

    report_paths = [
        write_batch_report(tmp_path, summary, report_formats=["txt"])
        for summary in summaries
    ]
    assert cleanup_batch_workspace(workspace, None) is None
    assert not workspace.exists()

    for org_index, (summary, report_path) in enumerate(zip(summaries, report_paths), start=1):
        organization = f"tenant-{org_index}.onmicrosoft.com"
        assert summary["Organization"] == organization
        assert summary["OrganizationsTested"] == [organization]
        assert summary["DurationSeconds"] == 6
        assert len(summary["JobResults"]) == 3
        assert {entry["organization"] for entry in summary["JobResults"]} == {organization}
        assert report_path.parent == tmp_path / organization / "RestoreTest_20260921_120000"
        stored = json.loads(report_path.read_text(encoding="utf-8"))
        assert {entry["organization"] for entry in stored["JobResults"]} == {organization}
        assert all(Path(stored[name]["LocalFile"]).is_file() for name in workloads)

    assert not (tmp_path / "Batch_20260921_120000").exists()


def test_write_batch_report_retention_applied(tmp_path: Path) -> None:
    for i in range(6):
        ts = datetime(2026, 9, 10 + i, 12, 0, 0)
        jobs = [
            {"job": "Mail", "organization": "acme", "exit_code": 0, "report_path": "mail.json",
             "report": {"Exchange": {"Status": "SUCCESS"}, "OneDrive": {"Status": "SUCCESS"}, "SharePoint": {"Status": "SUCCESS"}, "AllSuccessful": True}},
        ]
        summary = build_batch_summary("acme", jobs, True, ts)
        write_batch_report(tmp_path, summary, max_keep=3)
    org_dir = tmp_path / "acme"
    remaining = sorted(d.name for d in org_dir.iterdir() if d.is_dir())
    assert len(remaining) == 3
    # Only the 3 newest should remain
    assert "RestoreTest_20260914_120000" in remaining
    assert "RestoreTest_20260915_120000" in remaining
    assert "RestoreTest_20260910_120000" not in remaining


def test_write_batch_report_copies_restored_files_into_workload_folders(tmp_path: Path) -> None:
    # Setup dummy source files in simulated job folders
    job_dir = tmp_path / "simulated_jobs"
    ex_src = job_dir / "exchange" / "sample.msg"
    od_src = job_dir / "onedrive" / "document.pdf"
    sp_src = job_dir / "sharepoint" / "presentation.pptx"

    ex_src.parent.mkdir(parents=True, exist_ok=True)
    od_src.parent.mkdir(parents=True, exist_ok=True)
    sp_src.parent.mkdir(parents=True, exist_ok=True)

    ex_src.write_bytes(b"dummy email content")
    od_src.write_bytes(b"dummy pdf content")
    sp_src.write_bytes(b"dummy pptx content")

    jobs = [
        {
            "job": "Mail Job",
            "organization": "contoso.com",
            "exit_code": 0,
            "report_path": "mail.json",
            "report": {
                "Exchange": {"Status": "SUCCESS", "LocalFile": str(ex_src)},
                "OneDrive": {"Status": "NOT_CONFIGURED"},
                "SharePoint": {"Status": "NOT_CONFIGURED"},
            },
        },
        {
            "job": "OneDrive Job",
            "organization": "contoso.com",
            "exit_code": 0,
            "report_path": "od.json",
            "report": {
                "Exchange": {"Status": "NOT_CONFIGURED"},
                "OneDrive": {"Status": "SUCCESS", "LocalFile": str(od_src)},
                "SharePoint": {"Status": "NOT_CONFIGURED"},
            },
        },
        {
            "job": "SharePoint Job",
            "organization": "contoso.com",
            "exit_code": 0,
            "report_path": "sp.json",
            "report": {
                "Exchange": {"Status": "NOT_CONFIGURED"},
                "OneDrive": {"Status": "NOT_CONFIGURED"},
                "SharePoint": {"Status": "SUCCESS", "LocalFile": str(sp_src)},
            },
        },
    ]

    summary = build_batch_summary("contoso.com", jobs, True, datetime(2026, 9, 18, 15, 30, 0))
    json_path = write_batch_report(tmp_path / "evidence", summary, max_keep=5)
    restore_dir = json_path.parent

    # Check evidence directory structure
    assert restore_dir.name == "RestoreTest_20260918_153000"
    assert (restore_dir / "Report_Summary.json").is_file()
    assert (restore_dir / "Report_Summary.html").is_file()
    assert (restore_dir / "Report_Summary.txt").is_file()

    # Check workload folders and copied files
    copied_ex = restore_dir / "restore email" / "sample.msg"
    copied_od = restore_dir / "restore one drive" / "document.pdf"
    copied_sp = restore_dir / "restore share point" / "presentation.pptx"

    assert copied_ex.is_file()
    assert copied_od.is_file()
    assert copied_sp.is_file()

    assert copied_ex.read_bytes() == b"dummy email content"
    assert copied_od.read_bytes() == b"dummy pdf content"
    assert copied_sp.read_bytes() == b"dummy pptx content"

    # Verify JSON summary points to the copied files inside the restore folder
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["Exchange"]["LocalFile"] == str(copied_ex)
    assert data["OneDrive"]["LocalFile"] == str(copied_od)
    assert data["SharePoint"]["LocalFile"] == str(copied_sp)


def test_pdf_report_generation(tmp_path: Path) -> None:
    """write_pdf_report should create a Report_Summary.pdf file."""
    summary = build_batch_summary(
        "test.onmicrosoft.com",
        [
            {
                "job": "TestJob",
                "organization": "test.onmicrosoft.com",
                "exit_code": 0,
                "report": {
                    "AllSuccessful": True,
                    "Exchange": {"Status": "Success", "SourceMailbox": "user@test.com", "Subject": "Test", "SHA256": "abc123def456", "SizeBytes": 1024},
                    "OneDrive": {"Status": "N/A"},
                    "SharePoint": {"Status": "N/A"},
                },
            }
        ],
        skip_backup=True,
    )
    pdf_path = write_pdf_report(tmp_path, summary)
    assert pdf_path.is_file()
    assert pdf_path.name == "Report_Summary.pdf"
    assert pdf_path.stat().st_size > 0
    # Verify it starts with %PDF header
    header = pdf_path.read_bytes()[:5]
    assert header == b"%PDF-"


def test_selective_report_formats_keep_internal_json(tmp_path: Path) -> None:
    jobs = [
        {
            "job": "Mail",
            "organization": "tenant-a",
            "exit_code": 0,
            "report_path": "mail.json",
            "report": {
                "Exchange": {"Status": "SUCCESS"},
                "OneDrive": {"Status": "N/A"},
                "SharePoint": {"Status": "N/A"},
                "AllSuccessful": True,
            },
        }
    ]
    summary = build_batch_summary("tenant-a", jobs, True, datetime(2026, 9, 21, 10, 0, 0))
    json_path = write_batch_report(tmp_path, summary, report_formats=["html"])
    report_dir = json_path.parent
    assert (report_dir / "Report_Summary.json").is_file()
    assert (report_dir / "Report_Summary.html").is_file()
    assert not (report_dir / "Report_Summary.txt").exists()
    assert not (report_dir / "Report_Summary.pdf").exists()


def test_duration_is_internal_json_only_and_statuses_are_english(tmp_path: Path) -> None:
    jobs = [
        {
            "job": "Mixed workload job",
            "organization": "tenant-a",
            "exit_code": 2,
            "report_path": "mixed.json",
            "report": {
                "Exchange": {"Status": "SUCCESS"},
                "OneDrive": {"Status": "FAILED"},
                "SharePoint": {"Status": "NOT_CONFIGURED"},
                "AllSuccessful": False,
            },
        }
    ]
    summary = build_batch_summary(
        "tenant-a",
        jobs,
        True,
        datetime(2026, 9, 21, 12, 0, 0),
        duration_seconds=3661.4,
    )
    assert summary["DurationSeconds"] == 3661
    assert summary["OverallStatus"] == "WARNING"
    assert overall_status(summary) == "WARNING"
    assert format_duration(summary["DurationSeconds"]) == "01:01:01"
    assert normalize_status("ATTENZIONE") == "WARNING"
    assert normalize_status("FALLITO") == "FAILED"
    assert normalize_status("NOT_CONFIGURED") == "NOT CONFIGURED"

    json_path = write_batch_report(tmp_path, summary, report_formats=["txt", "html"])
    stored = json.loads(json_path.read_text(encoding="utf-8"))
    assert stored["DurationSeconds"] == 3661
    assert stored["OverallStatus"] == "WARNING"

    text_report = json_path.with_suffix(".txt").read_text(encoding="utf-8")
    html_report = json_path.with_suffix(".html").read_text(encoding="utf-8")
    client_output = f"{text_report}\n{html_report}"
    assert "DurationSeconds" not in client_output
    assert "01:01:01" not in client_output
    assert "ATTENZIONE" not in client_output
    assert "FALLITO" not in client_output
    assert "NEEDS ATTENTION" not in client_output
    assert "WARNING" in text_report
    assert "RESTORE TEST WARNING" in html_report


def test_failed_overall_status_when_no_workload_succeeds() -> None:
    summary = {
        "AllSuccessful": False,
        "Exchange": {"Status": "FAILED"},
        "OneDrive": {"Status": "NOT_CONFIGURED"},
        "SharePoint": {"Status": "N/A"},
        "JobResults": [{"exit_code": 2}],
    }
    assert overall_status(summary) == "FAILED"


def test_run_page_live_timer_and_reports_duration_display(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path / "settings.json")
    manager.save({"restore_root": str(tmp_path)})
    runner = PowerShellRunner()
    run_page = RunPage(manager, runner, lambda _code: None, auto_discover=False)
    assert run_page.elapsed_label.text() == "Elapsed 00:00:00"
    run_page.batch_started_monotonic = time.monotonic() - 65
    run_page._update_elapsed()
    assert run_page.elapsed_label.text().startswith("Elapsed 00:01:")

    summary = build_batch_summary(
        "tenant-a",
        [{
            "job": "Mail",
            "organization": "tenant-a",
            "exit_code": 0,
            "report_path": "mail.json",
            "report": {
                "Exchange": {"Status": "SUCCESS"},
                "OneDrive": {"Status": "NOT_CONFIGURED"},
                "SharePoint": {"Status": "NOT_CONFIGURED"},
                "AllSuccessful": True,
            },
        }],
        True,
        datetime(2026, 9, 21, 12, 0, 0),
        duration_seconds=65,
    )
    write_batch_report(tmp_path, summary, report_formats=["txt"])
    reports_page = ReportsPage(manager)
    reports_page.refresh()
    assert "SUCCESS" in reports_page.list.item(0).text()
    assert "00:01:05" in reports_page.list.item(0).text()
    assert "Duration: 00:01:05" in reports_page.detail.toPlainText()
    run_page.close()
    reports_page.close()


def test_cleanup_batch_workspace_keeps_only_final_report_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "Batch_20260921_120000"
    (workspace / "Jobs" / "tenant" / "job").mkdir(parents=True)
    (workspace / "Jobs" / "tenant" / "job" / "temporary.bin").write_bytes(b"data")
    report_dir = tmp_path / "tenant" / "RestoreTest_20260921_120000"
    report_dir.mkdir(parents=True)
    (report_dir / "Report_Summary.json").write_text("{}", encoding="utf-8")

    assert cleanup_batch_workspace(workspace, report_dir) is None
    assert not workspace.exists()
    assert (report_dir / "Report_Summary.json").is_file()


def test_styled_dialog_uses_application_buttons() -> None:
    QApplication.instance() or QApplication([])
    dialog = StyledDialog(
        None,
        "Stop this test?",
        "PowerShell will be terminated.",
        tone="warning",
        accept_text="Stop test",
        cancel_text="Keep running",
        destructive=True,
    )
    buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
    assert buttons["Stop test"].objectName() == "Danger"
    assert buttons["Keep running"].objectName() == ""
    assert dialog.windowFlags() & Qt.FramelessWindowHint
    dialog.close()


def test_sample_export_skips_containers_and_retries_bad_candidates(tmp_path: Path) -> None:
    destination = str(tmp_path / "restore").replace("'", "''")
    result = _run_sample_helper(
        tmp_path,
        f"""
        $folder = [pscustomobject]@{{ Name = 'Folder'; IsContainer = $true }}
        $file = [pscustomobject]@{{ Name = 'report.docx'; IsContainer = $false }}
        $missingExtension = [pscustomobject]@{{ Name = 'README'; IsContainer = $false }}
        $destination = '{destination}'
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        [IO.File]::WriteAllBytes((Join-Path $destination 'existing.bin'), [byte[]](9))
        $candidates = @('empty', 'throws', 'good')
        $export = Invoke-VerifiedSampleExport `
            -Candidates $candidates `
            -DestinationRoot $destination `
            -Workload 'SharePoint' `
            -MaxAttempts 3 `
            -DisableRandomization `
            -ExportAction {{
                param($candidate, $attemptDirectory)
                if ($candidate -eq 'empty') {{
                    [IO.File]::WriteAllBytes((Join-Path $attemptDirectory 'empty.bin'), [byte[]]@())
                }} elseif ($candidate -eq 'throws') {{
                    throw 'simulated unavailable item'
                }} else {{
                    [IO.File]::WriteAllBytes((Join-Path $attemptDirectory 'existing.bin'), [byte[]](1, 2, 3, 4))
                }}
            }}
        [pscustomobject]@{{
            FolderAccepted = Test-RestoreDocumentCandidate -Item $folder
            FileAccepted = Test-RestoreDocumentCandidate -Item $file -RequireExtension
            MissingExtensionAccepted = Test-RestoreDocumentCandidate -Item $missingExtension -RequireExtension
            Success = $export.Success
            Attempts = $export.Attempts
            Size = $export.File.Length
            HashLength = $export.SHA256.Length
            CollisionAvoided = ($export.File.Name -ne 'existing.bin')
            OriginalPreserved = ((Get-Item (Join-Path $destination 'existing.bin')).Length -eq 1)
            WorkspacesLeft = @(Get-ChildItem $destination -Directory -Filter '.sample-attempts-*').Count
        }} | ConvertTo-Json -Compress
        """,
    )
    assert result == {
        "FolderAccepted": False,
        "FileAccepted": True,
        "MissingExtensionAccepted": False,
        "Success": True,
        "Attempts": 3,
        "Size": 4,
        "HashLength": 64,
        "CollisionAvoided": True,
        "OriginalPreserved": True,
        "WorkspacesLeft": 0,
    }


def test_sample_export_honors_attempt_limit_and_cleans_up(tmp_path: Path) -> None:
    destination = str(tmp_path / "restore").replace("'", "''")
    result = _run_sample_helper(
        tmp_path,
        f"""
        $destination = '{destination}'
        $export = Invoke-VerifiedSampleExport `
            -Candidates @('one', 'two', 'three') `
            -DestinationRoot $destination `
            -Workload 'OneDrive' `
            -MaxAttempts 2 `
            -DisableRandomization `
            -ExportAction {{
                param($candidate, $attemptDirectory)
                [IO.File]::WriteAllBytes((Join-Path $attemptDirectory "$candidate.bin"), [byte[]]@())
            }}
        [pscustomobject]@{{
            Success = $export.Success
            Attempts = $export.Attempts
            FilesLeft = @(Get-ChildItem $destination -File -Recurse).Count
            WorkspacesLeft = @(Get-ChildItem $destination -Directory -Filter '.sample-attempts-*').Count
        }} | ConvertTo-Json -Compress
        """,
    )
    assert result == {"Success": False, "Attempts": 2, "FilesLeft": 0, "WorkspacesLeft": 0}
