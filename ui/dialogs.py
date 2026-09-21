from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import COLORS


_TONE_STYLE = {
    "info": ("i", COLORS["blue"], "#18283D"),
    "success": ("✓", COLORS["green"], COLORS["green_dark"]),
    "warning": ("!", COLORS["amber"], COLORS["amber_dark"]),
    "error": ("×", COLORS["red"], COLORS["red_dark"]),
    "question": ("?", COLORS["blue"], "#18283D"),
}


class StyledDialog(QDialog):
    """Frameless modal dialog matching the application's dark visual language."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        tone: str = "info",
        accept_text: str = "OK",
        cancel_text: str | None = None,
        destructive: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(430)
        self.setMaximumWidth(560)

        glyph, accent, badge_background = _TONE_STYLE.get(tone, _TONE_STYLE["info"])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        card = QFrame()
        card.setObjectName("DialogCard")
        card.setStyleSheet(
            f"QFrame#DialogCard {{ background:{COLORS['surface_elevated']}; "
            f"border:1px solid {COLORS['border_strong']}; border-radius:6px; }}"
        )
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(18)

        content = QHBoxLayout()
        content.setSpacing(14)
        icon = QLabel(glyph)
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(38, 38)
        icon.setStyleSheet(
            f"background:{badge_background}; color:{accent}; border:1px solid {accent}; "
            "border-radius:19px; font-size:20px; font-weight:700;"
        )

        text_layout = QVBoxLayout()
        text_layout.setSpacing(7)
        heading = QLabel(title)
        heading.setStyleSheet(f"color:{COLORS['text']}; font-size:16px; font-weight:650;")
        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet(f"color:{COLORS['text_secondary']}; font-size:13px;")
        text_layout.addWidget(heading)
        text_layout.addWidget(body)
        content.addWidget(icon, alignment=Qt.AlignTop)
        content.addLayout(text_layout, stretch=1)
        layout.addLayout(content)

        actions = QHBoxLayout()
        actions.addStretch()
        if cancel_text:
            cancel = QPushButton(cancel_text)
            cancel.setMinimumWidth(90)
            cancel.clicked.connect(self.reject)
            actions.addWidget(cancel)
        accept = QPushButton(accept_text)
        accept.setMinimumWidth(96)
        accept.setObjectName("Danger" if destructive else "Primary")
        accept.clicked.connect(self.accept)
        accept.setDefault(True)
        actions.addWidget(accept)
        layout.addLayout(actions)

    def exec(self) -> int:
        self.adjustSize()
        if parent := self.parentWidget():
            target = parent.window().frameGeometry().center()
            geometry = self.frameGeometry()
            geometry.moveCenter(target)
            self.move(geometry.topLeft())
        return super().exec()


def show_information(parent: QWidget, title: str, message: str, *, success: bool = False) -> None:
    StyledDialog(parent, title, message, tone="success" if success else "info").exec()


def show_warning(parent: QWidget, title: str, message: str) -> None:
    StyledDialog(parent, title, message, tone="warning").exec()


def show_error(parent: QWidget, title: str, message: str) -> None:
    StyledDialog(parent, title, message, tone="error").exec()


def ask_confirmation(
    parent: QWidget,
    title: str,
    message: str,
    *,
    accept_text: str = "Continue",
    cancel_text: str = "Cancel",
    destructive: bool = False,
) -> bool:
    dialog = StyledDialog(
        parent,
        title,
        message,
        tone="warning" if destructive else "question",
        accept_text=accept_text,
        cancel_text=cancel_text,
        destructive=destructive,
    )
    return dialog.exec() == QDialog.Accepted
