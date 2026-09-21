from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from app import MainWindow
from core.paths import resource_path


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Veeam M365 Restore Tester")
    app.setOrganizationName("LabLogos")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(resource_path("assets/app-icon.ico"))))
    font = QFont("Segoe UI Variable", 10)
    if not font.exactMatch():
        font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
