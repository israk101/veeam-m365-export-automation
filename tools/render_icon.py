from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


def main() -> int:
    QApplication([])
    root = Path(__file__).resolve().parents[1]
    renderer = QSvgRenderer(str(root / "assets" / "app-icon.svg"))
    image = QImage(QSize(256, 256), QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return 0 if image.save(str(root / "assets" / "app-icon.ico"), "ICO") else 1


if __name__ == "__main__":
    raise SystemExit(main())
