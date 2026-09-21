from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from core.paths import resource_path
from core.reports import human_size
from ui.theme import COLORS


class PageHeading(QFrame):
    def __init__(self, eyebrow: str, title: str, subtitle: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(4)
        eye = QLabel(eyebrow.upper())
        eye.setObjectName("Eyebrow")
        heading = QLabel(title)
        heading.setObjectName("Title")
        desc = QLabel(subtitle)
        desc.setObjectName("Subtitle")
        desc.setWordWrap(True)
        layout.addWidget(eye)
        layout.addWidget(heading)
        layout.addWidget(desc)


class StatusCard(QFrame):
    def __init__(self, name: str, glyph: str = "", icon_path: str | Path | None = None) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumHeight(152)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(10)

        self.icon_label = QLabel()
        resolved_icon = None
        if icon_path:
            p = resource_path(str(icon_path))
            if p.is_file():
                resolved_icon = p
            elif Path(icon_path).is_file():
                resolved_icon = Path(icon_path)

        if resolved_icon:
            pixmap = QPixmap(str(resolved_icon))
            if not pixmap.isNull():
                self.icon_label.setPixmap(pixmap.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.icon_label.setFixedSize(28, 28)
            else:
                self.icon_label.setText(glyph)
                self.icon_label.setStyleSheet(f"font-size: 20px; color: {COLORS['text_secondary']};")
        elif glyph:
            self.icon_label.setText(glyph)
            self.icon_label.setStyleSheet(f"font-size: 20px; color: {COLORS['text_secondary']};")

        title = QLabel(name)
        title.setObjectName("CardTitle")

        self.badge = QLabel("NOT RUN")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setMinimumWidth(74)

        top.addWidget(self.icon_label)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.badge)

        self.primary = QLabel("No result available")
        self.primary.setWordWrap(True)
        self.primary.setStyleSheet(f"color: {COLORS['text']}; font-weight: 600; font-size: 13px;")

        self.detail = QLabel("Run a restore test to populate this card.")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)

        layout.addLayout(top)
        layout.addStretch()
        layout.addWidget(self.primary)
        layout.addWidget(self.detail)
        self.update_data({})

    def update_data(self, data: dict[str, Any] | None) -> None:
        data = data or {}
        status = str(data.get("Status", "NOT RUN")).upper()
        success = status == "SUCCESS"
        is_not_run = status in ("NOT RUN", "N/A", "")

        if success:
            color = COLORS["green"]
            bg = COLORS["green_dark"]
            border = COLORS["green_border"]
        elif not is_not_run:
            color = COLORS["red"]
            bg = COLORS["red_dark"]
            border = COLORS["red_border"]
        else:
            color = COLORS["muted"]
            bg = COLORS["surface_alt"]
            border = COLORS["border"]

        self.badge.setText(status or "N/A")
        # Crisp pro desktop badge: 2px radius, 1px hairline border
        self.badge.setStyleSheet(
            f"background: {bg}; color: {color}; border: 1px solid {border}; "
            f"border-radius: 2px; padding: 3px 8px; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;"
        )

        source = data.get("SourceMailbox") or data.get("SourceUser") or data.get("Site") or "No result available"
        filename = data.get("Subject") or data.get("FileName") or "Run a restore test to populate this card."
        size = human_size(data.get("SizeBytes"))
        self.primary.setText(str(source))
        self.detail.setText(f"{filename}  ·  {size}" if data else str(filename))


class LogConsole(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumBlockCount(2500)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setStyleSheet(
            'font-family: "Cascadia Code", "Consolas", "Courier New", monospace; '
            'font-size: 12px; line-height: 1.4;'
        )

    def append_line(self, text: str, level: str = "info") -> None:
        colors = {
            "success": COLORS["green"],
            "warning": COLORS["amber"],
            "error": COLORS["red"],
            "info": COLORS["text_secondary"],
        }
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colors.get(level, colors["info"])))
        cursor.insertText(text + "\n", fmt)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()


class OrgJobTree(QTreeWidget):
    """Checkable tree: root items are organizations, children are individual jobs."""

    selection_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setIndentation(18)
        self.setAnimated(True)
        self.itemChanged.connect(self._on_item_changed)

    def _update_org_text(self, org_item: QTreeWidgetItem) -> None:
        org_name = str(org_item.data(0, Qt.UserRole) or "")
        total = org_item.childCount()
        if total == 0:
            return
        checked_count = sum(1 for i in range(total) if org_item.child(i).checkState(0) == Qt.Checked)
        if checked_count == total:
            org_item.setText(0, f"{org_name}  ({total} job{'s' if total != 1 else ''})")
        elif checked_count == 0:
            org_item.setText(0, f"{org_name}  (0 of {total} jobs selected)")
        else:
            org_item.setText(0, f"{org_name}  ({checked_count} of {total} jobs selected)")

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        self.blockSignals(True)
        try:
            if item.parent() is None:
                state = item.checkState(0)
                if state in (Qt.Checked, Qt.Unchecked):
                    for i in range(item.childCount()):
                        item.child(i).setCheckState(0, state)
                self._update_org_text(item)
            else:
                parent = item.parent()
                checked_count = sum(1 for i in range(parent.childCount()) if parent.child(i).checkState(0) == Qt.Checked)
                total = parent.childCount()
                if checked_count == total and total > 0:
                    parent.setCheckState(0, Qt.Checked)
                elif checked_count == 0:
                    parent.setCheckState(0, Qt.Unchecked)
                else:
                    parent.setCheckState(0, Qt.PartiallyChecked)
                self._update_org_text(parent)
        finally:
            self.blockSignals(False)
        self.selection_changed.emit()

    def load_inventory(
        self,
        inventory: dict[str, list[dict[str, str]]],
        preselected: dict[str, list[str]] | None = None,
    ) -> None:
        self.blockSignals(True)
        self.clear()
        preselected = preselected or {}
        has_any_selection = any(bool(jobs) for jobs in preselected.values())

        for org_name, jobs in inventory.items():
            org_item = QTreeWidgetItem(self)
            count_label = f"({len(jobs)} job{'s' if len(jobs) != 1 else ''})"
            org_item.setText(0, f"{org_name}  {count_label}")
            org_item.setData(0, Qt.UserRole, org_name)
            font = org_item.font(0)
            font.setBold(True)
            org_item.setFont(0, font)
            org_item.setFlags(org_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)

            org_selected = set(preselected.get(org_name, []))
            checked_count = 0
            for job in jobs:
                job_name = str(job.get("Name", ""))
                if not job_name:
                    continue
                job_item = QTreeWidgetItem(org_item)
                job_item.setText(0, job_name)
                job_item.setData(0, Qt.UserRole, job_name)
                job_item.setFlags(job_item.flags() | Qt.ItemIsUserCheckable)
                job_item.setToolTip(0, f"Veeam Job ID: {job.get('Id', '—')}")

                is_checked = job_name in org_selected if has_any_selection else True
                if is_checked:
                    job_item.setCheckState(0, Qt.Checked)
                    checked_count += 1
                else:
                    job_item.setCheckState(0, Qt.Unchecked)

            total = org_item.childCount()
            if total > 0 and checked_count == total:
                org_item.setCheckState(0, Qt.Checked)
            elif checked_count > 0:
                org_item.setCheckState(0, Qt.PartiallyChecked)
            else:
                org_item.setCheckState(0, Qt.Unchecked)
            self._update_org_text(org_item)

        self.expandAll()
        self.blockSignals(False)
        self.selection_changed.emit()

    def filter_items(self, query: str) -> tuple[int, int]:
        """Show matching organizations/jobs without rebuilding or changing checks."""
        needle = query.strip().casefold()
        visible_organizations = 0
        visible_jobs = 0
        for i in range(self.topLevelItemCount()):
            org_item = self.topLevelItem(i)
            org_name = str(org_item.data(0, Qt.UserRole) or "")
            organization_matches = not needle or needle in org_name.casefold()
            matching_children = 0
            for j in range(org_item.childCount()):
                child = org_item.child(j)
                job_name = str(child.data(0, Qt.UserRole) or child.text(0))
                child_matches = organization_matches or needle in job_name.casefold()
                child.setHidden(not child_matches)
                matching_children += int(child_matches)
            organization_visible = organization_matches or matching_children > 0
            org_item.setHidden(not organization_visible)
            if organization_visible:
                visible_organizations += 1
                visible_jobs += matching_children
                org_item.setExpanded(bool(needle) or org_item.isExpanded())
        return visible_organizations, visible_jobs

    def selected_jobs(self) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for i in range(self.topLevelItemCount()):
            org_item = self.topLevelItem(i)
            org_name = str(org_item.data(0, Qt.UserRole) or "")
            for j in range(org_item.childCount()):
                child = org_item.child(j)
                if child.checkState(0) == Qt.Checked:
                    result.append((org_name, str(child.data(0, Qt.UserRole) or child.text(0))))
        return result

    def selected_dict(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for org_name, job_name in self.selected_jobs():
            result.setdefault(org_name, []).append(job_name)
        return result

    def set_all(self, state: Qt.CheckState) -> None:
        self.blockSignals(True)
        for i in range(self.topLevelItemCount()):
            org_item = self.topLevelItem(i)
            org_item.setCheckState(0, state)
            for j in range(org_item.childCount()):
                org_item.child(j).setCheckState(0, state)
            self._update_org_text(org_item)
        self.blockSignals(False)
        self.selection_changed.emit()

    def set_visible(self, state: Qt.CheckState) -> None:
        """Apply a check state only to jobs currently visible after filtering."""
        self.blockSignals(True)
        for i in range(self.topLevelItemCount()):
            org_item = self.topLevelItem(i)
            if org_item.isHidden():
                continue
            for j in range(org_item.childCount()):
                child = org_item.child(j)
                if not child.isHidden():
                    child.setCheckState(0, state)
            checked_count = sum(
                org_item.child(j).checkState(0) == Qt.Checked
                for j in range(org_item.childCount())
            )
            total = org_item.childCount()
            if checked_count == total and total:
                org_item.setCheckState(0, Qt.Checked)
            elif checked_count:
                org_item.setCheckState(0, Qt.PartiallyChecked)
            else:
                org_item.setCheckState(0, Qt.Unchecked)
            self._update_org_text(org_item)
        self.blockSignals(False)
        self.selection_changed.emit()
