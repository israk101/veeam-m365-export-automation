from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal, QEventLoop, QTimer, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from app import DashboardPage
from core.batch_report import build_batch_summary, write_batch_report
from core.paths import powershell_path
from core.queue import JobQueue, JobTask
from core.reports import list_reports


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


class FakeRunner(QObject):
    output = Signal(str, str)
    phase_changed = Signal(str)
    finished = Signal(int)
    failed_to_start = Signal(str)

    def start(self, shell, script, org, job, root, skip, **kwargs):
        self.org, self.job, self.root = org, job, Path(root)

    def complete(self, org=None):
        path = self.root / 'run' / 'Report_Summary.json'
        path.parent.mkdir()
        path.write_text(json.dumps(dict(Organization=org or self.org, JobName=self.job,
                                       Exchange={"Status": "SUCCESS"})))
        self.finished.emit(0)

    def stop(self):
        self.finished.emit(-1)


def pump(app):
    for _ in range(3):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def start_queue(tmp_path, qapp, tasks):
    queue = JobQueue(runner_factory=FakeRunner)
    queue.start(tasks, shell='mock', script=Path('mock.ps1'), workspace=tmp_path,
                skip_backup=True, concurrency=2)
    pump(qapp)
    return queue


def test_queue_overlaps_organizations_but_serializes_their_jobs(tmp_path, qapp):
    tasks = [JobTask('org-a', 'first'), JobTask('org-a', 'second'), JobTask('org-b', 'first')]
    queue = start_queue(tmp_path, qapp, tasks)
    results = []
    queue.finished.connect(lambda jobs, cancelled: results.extend(jobs))
    assert list(queue.active) == [0, 2]
    queue.active[0]['runner'].complete()
    pump(qapp)
    assert set(queue.active) == {1, 2}
    queue.active[2]['runner'].complete()
    queue.active[1]['runner'].complete()
    pump(qapp)
    assert not queue.running
    assert [j['job'] for j in results] == ['first', 'second', 'first']
    assert all(j['report'] for j in results)


def test_queue_cancellation_covers_pending_and_active_jobs(tmp_path, qapp):
    queue = start_queue(tmp_path, qapp, [JobTask('org', str(i)) for i in range(3)])
    results = []
    queue.finished.connect(lambda jobs, cancelled: results.append((jobs, cancelled)))
    queue.stop()
    pump(qapp)
    assert len(results) == 1
    assert results[0][1] is True
    assert len(results[0][0]) == 3
    assert all(j['cancelled'] and j['exit_code'] == -1 for j in results[0][0])
    assert not queue.running and not queue.active


def test_failed_start_advances_queue_and_wrong_org_is_rejected(tmp_path, qapp):
    queue = start_queue(tmp_path, qapp, [JobTask('org', 'bad'), JobTask('org', 'good')])
    queue.active[0]['runner'].failed_to_start.emit('missing executable')
    pump(qapp)
    assert list(queue.active) == [1]
    queue.active[1]['runner'].complete(org='wrong organization')
    pump(qapp)
    assert queue.results[0]['exit_code'] == -1
    assert queue.results[1]['report'] is None


def test_evidence_keeps_distinct_job_files_with_same_name(tmp_path):
    jobs = []
    for index in range(2):
        source = tmp_path / 'staging' / str(index) / 'same.msg'
        source.parent.mkdir(parents=True)
        source.write_bytes(bytes([index + 1]) * 10)
        jobs.append(dict(organization='org', job=str(index), exit_code=0, report={
            'Exchange': dict(Status='SUCCESS', LocalFile=str(source), SHA256=hashlib.sha256(source.read_bytes()).hexdigest())}))
    summary = build_batch_summary('org', jobs, True)
    report = write_batch_report(tmp_path / 'out', summary, report_formats=[])
    stored = json.loads(report.read_text())
    evidence = [Path(j['report']['Exchange']['LocalFile']) for j in stored['JobResults']]
    assert len(set(evidence)) == 2
    for index, path in enumerate(evidence):
        assert path.read_bytes() == bytes([index + 1]) * 10
        assert path.parent == report.parent / 'restore email'
    assert 'staging' in jobs[0]['report']['Exchange']['LocalFile']


def test_report_failure_is_recorded_and_preserves_retention_limit(tmp_path, monkeypatch):
    from core import pdf_report
    def fail(*args, **kwargs):
        raise RuntimeError('simulated PDF failure')
    monkeypatch.setattr(pdf_report, 'write_pdf_report', fail)
    old = tmp_path / 'org' / 'RestoreTest_20260901_000000'
    old.mkdir(parents=True)
    summary = build_batch_summary('org', [], True)
    path = write_batch_report(tmp_path, summary, max_keep=1, report_formats=['pdf'])
    assert not old.exists()
    assert list((tmp_path / 'org').iterdir()) == [path.parent]
    assert 'simulated PDF failure' in json.loads(path.read_text())['ReportErrors'][0]


def test_same_second_reports_are_isolated_and_retained(tmp_path):
    for _ in range(3):
        summary = build_batch_summary('org', [], True, datetime(2026, 9, 26, 12))
        write_batch_report(tmp_path, summary, max_keep=2, report_formats=[])
    assert sorted(p.name for p in (tmp_path / 'org').iterdir()) == [
        'RestoreTest_20260926_120001', 'RestoreTest_20260926_120002']


def test_history_prunes_evidence_and_job_trees(tmp_path, monkeypatch):
    import core.reports as module
    (tmp_path / 'org' / 'RestoreTest_20260926_120000' / 'restore email').mkdir(parents=True)
    good = tmp_path / 'org' / 'RestoreTest_20260926_120000' / 'Report_Summary.json'
    good.write_text('{}')
    (good.parent / 'restore email' / 'Report_Summary.json').write_text('{}')
    (tmp_path / 'Batch_20260926_120000' / 'Jobs').mkdir(parents=True)
    (tmp_path / 'Batch_20260926_120000' / 'Jobs' / 'Report_Summary.json').write_text('{}')
    reads = []
    original = module.read_report
    def read(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(module, 'read_report', read)
    assert len(list_reports(tmp_path)) == 1
    assert reads == [good]


@pytest.mark.parametrize('organization', ['Exchange', 'Jobs', 'OneDrive'])
def test_history_accepts_organizations_named_like_workloads(tmp_path, organization):
    summary = build_batch_summary(organization, [], True)
    path = write_batch_report(tmp_path, summary, report_formats=[])
    assert [report.path for report in list_reports(tmp_path)] == [path]


def test_dashboard_displays_each_organization_and_report_errors(tmp_path, qapp):
    for org in ['one', 'two']:
        summary = build_batch_summary(org, [], True)
        write_batch_report(tmp_path, summary, report_formats=[])
    page = DashboardPage(lambda: None)
    page.load_reports(list_reports(tmp_path))
    assert page.org_table.rowCount() == 2
    assert '2 organizations' in page.fleet_summary.text()
    assert page.open_evidence.isEnabled()
    page.close()


@pytest.mark.parametrize('limit,expected', [(100, 100), (0, 1000)])
def test_real_engine_with_mock_veeam_bounds_enumeration_and_closes_sessions(tmp_path, limit, expected):
    shell = powershell_path()
    if not shell:
        pytest.skip('PowerShell required')
    base = Path(__file__).resolve().parents[1]
    def literal(path):
        return str(path).replace("'", "''")
    wrapper = tmp_path / 'invoke.ps1'
    wrapper.write_text(f"""
. '{literal(base / 'tests/fixtures/mock-veeam.ps1')}'
& '{literal(base / 'scripts/Invoke-SimpleM365BackupRestoreTest.ps1')}' -OrganizationName org -JobName job -LocalRestoreRoot '{literal(tmp_path / 'output')}' -SkipBackup -SampleCandidateLimit {limit}
$engineCode = $LASTEXITCODE
$global:counts | ConvertTo-Json | Set-Content '{literal(tmp_path / 'counts.json')}'
exit $engineCode
""")
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(wrapper)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    counts = json.loads((tmp_path / 'counts.json').read_text(encoding='utf-8-sig'))
    assert counts == dict(Exchange=expected, OneDrive=expected, SharePoint=expected, Closed=3, Disconnected=1, Backups=0)
    report = list_reports(tmp_path / 'output')[0].data
    assert report['AllSuccessful'] is True
    assert set(report['PhaseTimings']) == {'Preflight', 'Backup', 'RestorePoint', 'Exchange', 'OneDrive', 'SharePoint'}
    for workload in ['Exchange', 'OneDrive', 'SharePoint']:
        item = report[workload]
        assert hashlib.sha256(Path(item['LocalFile']).read_bytes()).hexdigest().upper() == item['SHA256']
        assert item['Attempts'] == 1


def test_engine_has_no_cloud_restore_or_send_commands():
    import re
    engine = (Path(__file__).resolve().parents[1] / 'scripts/Invoke-SimpleM365BackupRestoreTest.ps1').read_text()
    assert not re.search(r'\b(?:Restore|Send|Set|Remove)-V(?:EX|EOD|ESP|BO)\w+', engine, re.I)
    assert 'Get-VBOJob -Name $JobName' not in engine


def test_real_processes_overlap_with_isolated_reports(tmp_path, qapp):
    shell = powershell_path()
    if not shell:
        pytest.skip('PowerShell required')
    script = tmp_path / 'fake.ps1'
    script.write_text('''
param($OrganizationName,$JobName,$LocalRestoreRoot,[switch]$SkipBackup)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$started = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
if ($JobName -eq 'one') {
    $barrier = Split-Path (Split-Path (Split-Path $LocalRestoreRoot))
    Set-Content (Join-Path $barrier "ready-$OrganizationName") 'ready'
    $deadline = (Get-Date).AddSeconds(8)
    while (@(Get-ChildItem $barrier -Filter 'ready-*').Count -lt 2 -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 50
    }
}
Start-Sleep -Milliseconds 200
@{ Organization=$OrganizationName; JobName=$JobName; Started=$started;
   Ended=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(); Exchange=@{Status='SUCCESS'} } |
    ConvertTo-Json | Set-Content (Join-Path $LocalRestoreRoot 'Report_Summary.json')
Write-Host '[OK] Unicode output: café'
''', encoding='utf-8')
    queue = JobQueue()
    results, outputs = [], []
    loop = QEventLoop()
    queue.finished.connect(lambda jobs, cancelled: (results.extend(jobs), loop.quit()))
    queue.output.connect(lambda text, level: outputs.append(text))
    queue.start([JobTask('a', 'one'), JobTask('a', 'two'), JobTask('b', 'one')],
                shell=shell, script=script, workspace=tmp_path / 'batch', skip_backup=True)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(15000)
    loop.exec()
    timeout.stop()
    if queue.running:
        queue.stop()
        pytest.fail('Queue failed to complete')
    assert len(results) == 3
    a1, a2, b1 = [r['report'] for r in results]
    assert max(a1['Started'], b1['Started']) < min(a1['Ended'], b1['Ended'])
    assert a2['Started'] >= a1['Ended']
    assert any('[a / one]' in line and 'café' in line for line in outputs), ascii(outputs)


def test_html_and_pdf_share_one_render(tmp_path, monkeypatch):
    from core import html_report, pdf_report
    calls = []
    def render(summary):
        calls.append(summary)
        return '<html>single render</html>'
    def pdf(root, summary, *, html_content):
        assert html_content == '<html>single render</html>'
        destination = Path(root) / 'Report_Summary.pdf'
        destination.write_bytes(b'%PDF-mocked')
        return destination
    monkeypatch.setattr(html_report, 'render_html_report', render)
    monkeypatch.setattr(pdf_report, 'write_pdf_report', pdf)
    path = write_batch_report(tmp_path, build_batch_summary('org', [], True), report_formats=['html', 'pdf'])
    assert len(calls) == 1
    assert path.with_suffix('.html').read_text() == '<html>single render</html>'


def test_missing_evidence_prevents_successful_finalization(tmp_path):
    summary = build_batch_summary('org', [dict(job='job', exit_code=0,
        report={'Exchange': dict(Status='SUCCESS', LocalFile=str(tmp_path / 'missing.msg'))})], True)
    old = tmp_path / 'org' / 'RestoreTest_20260901_000000'
    old.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        write_batch_report(tmp_path, summary, max_keep=1, report_formats=[])
    assert old.exists()
    assert list((tmp_path / 'org').iterdir()) == [old]


@pytest.mark.parametrize('status,expected_code', [('Success', 0), ('Failed', 2)])
def test_backup_waits_for_completion_without_requiring_session_id(tmp_path, status, expected_code):
    shell = powershell_path()
    if not shell:
        pytest.skip('PowerShell required')
    base = Path(__file__).resolve().parents[1]
    def literal(path):
        return str(path).replace("'", "''")
    wrapper = tmp_path / 'backup.ps1'
    wrapper.write_text(f"""
. '{literal(base / 'tests/fixtures/mock-veeam.ps1')}'
function Start-VBOJob {{ param($Job, [switch]$RunAsync)
    if ($RunAsync) {{ throw 'Test expects the documented synchronous contract' }}
    $global:counts.Backups++
}}
function Get-VBOJobSession {{ param($Job, [switch]$Last)
    if ($global:counts.Backups -ne 1) {{ throw 'Read session before synchronous backup completed' }}
    [pscustomobject]@{{ JobId='job1'; JobName='job'; Status='{status}' }}
}}
& '{literal(base / 'scripts/Invoke-SimpleM365BackupRestoreTest.ps1')}' -OrganizationName org -JobName job -LocalRestoreRoot '{literal(tmp_path / 'output')}'
$engineCode = $LASTEXITCODE
$global:counts | ConvertTo-Json | Set-Content '{literal(tmp_path / 'counts.json')}'
exit $engineCode
""", encoding='utf-8')
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(wrapper)], capture_output=True, text=True, timeout=15)
    assert result.returncode == expected_code, result.stdout + result.stderr
    report = list_reports(tmp_path / 'output')[0].data
    assert report['BackupStatus'] == status
    assert report['AllSuccessful'] is (expected_code == 0)
    counts = json.loads((tmp_path / 'counts.json').read_text(encoding='utf-8-sig'))
    assert counts['Backups'] == 1 and counts['Disconnected'] == 1
    if expected_code:
        assert counts['Exchange'] == counts['OneDrive'] == counts['SharePoint'] == counts['Closed'] == 0
        assert report['FatalError']


def test_real_process_cancellation_keeps_event_loop_responsive(tmp_path, qapp):
    shell = powershell_path()
    if not shell:
        pytest.skip('PowerShell required')
    script = tmp_path / 'slow.ps1'
    script.write_text('''
param($OrganizationName,$JobName,$LocalRestoreRoot,[switch]$SkipBackup)
Write-Host 'READY_TO_CANCEL'
while ($true) { Start-Sleep -Seconds 1 }
''', encoding='utf-8')
    queue = JobQueue()
    loop = QEventLoop()
    results, ticks = [], []
    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: ticks.append(1))
    def output(line, _level):
        if 'READY_TO_CANCEL' in line:
            heartbeat.start()
            queue.stop()
    queue.output.connect(output)
    queue.finished.connect(lambda jobs, cancelled: (results.append((jobs, cancelled)), loop.quit()))
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(10000)
    queue.start([JobTask('a', 'running'), JobTask('a', 'pending')], shell=shell,
                script=script, workspace=tmp_path / 'batch', skip_backup=True)
    loop.exec()
    timeout.stop()
    heartbeat.stop()
    if queue.running:
        for state in list(queue.active.values()):
            state['runner'].process.kill()
            state['runner'].process.waitForFinished(2000)
        pytest.fail('Cancellation failed to terminate the worker')
    assert len(results) == 1 and results[0][1] is True
    assert len(results[0][0]) == 2
    assert all(item['cancelled'] for item in results[0][0])
    # The Windows console worker needs the kill fallback; Qt must keep repainting.
    assert ticks
