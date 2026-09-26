"""Offline runtime check: synthetic reports/UI only, no Veeam connection."""
from pathlib import Path
import hashlib
import json
import traceback


def run_self_test(destination: str) -> int:
    """Create a new output directory, validate PDF/UI, and write result.json.

    Refuses an existing destination. Uses isolated settings and never calls
    inventory, backup or restore APIs. Returns 0 on success, 1 on failure.
    """
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=False)
    try:
        from datetime import datetime
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication
        from app import MainWindow
        from core.config import ConfigManager
        from core.batch_report import build_batch_summary, write_batch_report
        qt = QApplication.instance() or QApplication([])
        qt.setStyle('Fusion')
        font = Path('C:/Windows/Fonts/segoeui.ttf')
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
        qt.setFont(QFont('Segoe UI', 10))
        config = ConfigManager(output / 'settings.json')
        config.save({'restore_root': str(output / 'evidence')})
        for org, success in [('contoso.example', True), ('fabrikam.example', False)]:
            sample = output / 'samples' / org / 'sample.txt'
            sample.parent.mkdir(parents=True)
            sample.write_text('Synthetic offline diagnostic sample. No tenant data.\n', encoding='utf-8')
            report = dict(AllSuccessful=success, BackupExecuted=False, BackupStatus='SkippedByUser',
                          Exchange=dict(Status='SUCCESS', LocalFile=str(sample), SizeBytes=sample.stat().st_size,
                                        SHA256=hashlib.sha256(sample.read_bytes()).hexdigest(), Attempts=1,
                                        SourceMailbox='demo@' + org, Subject='Synthetic diagnostic evidence'),
                          OneDrive=dict(Status='NOT_CONFIGURED' if success else 'FAILED', Error='' if success else 'Simulated export failure'),
                          SharePoint=dict(Status='NOT_CONFIGURED'), CleanupErrors=[],
                          DurationSeconds=12, PhaseTimings={'Preflight': 2, 'Exchange': 10})
            summary = build_batch_summary(org, [dict(organization=org, job='Daily protection', exit_code=0 if success else 2,
                                                    report=report, duration_seconds=13)], True, datetime(2026, 9, 26, 14, 30), 13)
            path = write_batch_report(output / 'evidence', summary)
            assert not summary['ReportErrors'], summary['ReportErrors']
            assert path.with_suffix('.pdf').read_bytes().startswith(b'%PDF-')
        window = MainWindow(auto_discover=False, config=config)
        window.resize(1380, 920)
        window.run_page._inventory_loaded({'Organizations': [dict(Name=org, Jobs=[dict(Name='Daily protection')])
                                                               for org in ('contoso.example', 'fabrikam.example')]})
        window.run_page.job_tree.set_all(Qt.Checked)
        window.show()
        for index, name in enumerate(('dashboard', 'run', 'reports', 'settings')):
            window.show_page(index)
            qt.processEvents()
            assert window.grab().save(str(output / f'{name}.png'))
        assert not window.run_page.discovery.is_running()
        window.close()
        qt.processEvents()
        result = {'success': True, 'checks': ['local evidence', 'JSON', 'TXT', 'HTML', 'PDF', 'four UI pages'], 'veeam_connected': False}
        code = 0
    except Exception:
        result = {'success': False, 'error': traceback.format_exc(), 'veeam_connected': False}
        code = 1
    (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return code
