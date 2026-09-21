from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import MainWindow


def main() -> int:
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    if window.run_page.discovery.is_running():
        window.run_page.discovery.process.kill()
        window.run_page.discovery.process.waitForFinished(1000)
    window.run_page._inventory_loaded({
        "Server": "localhost",
        "Organizations": [
            {"Name": "contoso.onmicrosoft.com", "Id": "org-1", "Jobs": [
                {"Name": "Contoso - Exchange", "Id": "job-1"},
                {"Name": "Contoso - OneDrive", "Id": "job-2"},
                {"Name": "Contoso - SharePoint", "Id": "job-3"},
            ]},
            {"Name": "fabrikam.onmicrosoft.com", "Id": "org-2", "Jobs": [
                {"Name": "Fabrikam - Complete", "Id": "job-4"},
            ]},
        ],
    })
    window.run_page._set_all_jobs(Qt.Checked)
    window.run_page.log.clear()
    base = Path(__file__).resolve().parents[1]
    ok = True
    names = ("dashboard", "run", "reports", "settings")
    for index, name in enumerate(names):
        window.show_page(index)
        app.processEvents()
        ok = window.grab().save(str(base / f"ui-{name}.png"), "PNG") and ok
    window.close()
    print(base)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
