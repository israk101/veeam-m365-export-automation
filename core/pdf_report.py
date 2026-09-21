"""PDF report generator using PySide6's QWebEngine (Chromium-based).

Uses the full Chromium rendering engine to produce a pixel-perfect PDF
that matches the HTML report exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def write_pdf_report(root: Path | str, summary: dict[str, Any]) -> Path:
    """Render the HTML summary report to a PDF using Chromium via QWebEngine.

    Parameters
    ----------
    root : Path | str
        Directory where *Report_Summary.pdf* will be written.
    summary : dict
        The batch summary dictionary (same as for HTML/txt).

    Returns
    -------
    Path
        The path to the generated PDF file.
    """
    import sys

    from PySide6.QtCore import QEventLoop, QMarginsF, QTimer, QUrl
    from PySide6.QtGui import QPageLayout, QPageSize
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    from core.html_report import render_html_report

    dest = Path(root).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    pdf_path = dest / "Report_Summary.pdf"

    # Ensure a QApplication exists (required for Qt rendering)
    app = QApplication.instance()
    app_created = False
    if app is None:
        app = QApplication(sys.argv[:1] or [""])
        app_created = True

    try:
        html_content = render_html_report(summary)

        # A4 page with zero extra margins so CSS @page controls margins
        page_layout = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
        )

        view = QWebEngineView()
        view.setHtml(html_content, QUrl("file:///"))

        loop = QEventLoop()
        pdf_data: list[bytes] = []
        error_occurred: list[bool] = [False]

        def _on_load_finished(ok: bool) -> None:
            if not ok:
                error_occurred[0] = True
                loop.quit()
                return
            # Page is fully loaded — print to PDF
            view.page().printToPdf(
                _on_pdf_ready,
                page_layout,
            )

        def _on_pdf_ready(data: bytes) -> None:
            pdf_data.append(bytes(data))
            loop.quit()

        view.loadFinished.connect(_on_load_finished)

        # Safety timeout: 30 seconds
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(30000)

        loop.exec()
        timeout.stop()

        if pdf_data and pdf_data[0]:
            pdf_path.write_bytes(pdf_data[0])

        view.close()
        view.deleteLater()

    finally:
        if app_created:
            del app

    return pdf_path
