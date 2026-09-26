import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    # Chromium and Qt objects must share one QApplication for the whole suite.
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
