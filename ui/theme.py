from __future__ import annotations

from core.paths import resource_path

# Figma Combination 56: Salt and Pepper Palette
# Primary Neutral Spectrum: White (#FFFFFF) & Gray shades (#D4D4D4, #B3B3B3, #2B2B2B)
# High-contrast, minimal aesthetic for focus and professional clarity.
COLORS = {
    "window": "#181818",            # Base background (deep pepper canvas)
    "sidebar": "#1D1D1D",           # Sidebar background
    "surface": "#242424",           # Cards, panels, elevated sections
    "surface_alt": "#2A2A2A",       # Nested frames, secondary buttons, rows
    "surface_elevated": "#333333",  # Elevated popups, tooltips, dialogs
    "border": "#363636",            # Crisp 1px hairline border
    "border_strong": "#4E4E4E",     # Active/hover border
    "text": "#FFFFFF",              # Salt white (primary text, headings)
    "text_secondary": "#D4D4D4",    # Salt mist (card titles, subheadings)
    "muted": "#969696",             # Pepper gray (secondary text, labels, hints)
    "green": "#4ADE80",             # Verified status accent
    "green_dark": "#183324",        # Verified status badge background
    "green_border": "#2B523B",      # Verified border stroke
    "red": "#F87171",               # Failed status accent
    "red_dark": "#381A1E",          # Failed badge background
    "red_border": "#5E2C33",        # Failed border stroke
    "amber": "#FBBF24",             # Warning/in-progress accent
    "amber_dark": "#362714",        # Warning badge background
    "amber_border": "#59401C",      # Warning border stroke
    "blue": "#60A5FA",              # Informational accent / focus link
}


def stylesheet() -> str:
    c = COLORS
    check_svg = resource_path("assets/checkbox_checked.svg").as_posix()
    return f"""
        QMainWindow, QWidget#Root {{
            background: {c['window']};
            color: {c['text']};
            font-family: "Segoe UI Variable Text", "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
        }}
        QWidget {{
            color: {c['text']};
            font-size: 13px;
            font-family: "Segoe UI Variable Text", "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
        }}
        QFrame#Sidebar {{
            background: {c['sidebar']};
            border-right: 1px solid {c['border']};
        }}
        /* Pro desktop cards and panels: crisp micro-radius 4px (no bubbly rounded corners) */
        QFrame#Card, QFrame#Panel {{
            background: {c['surface']};
            border: 1px solid {c['border']};
            border-radius: 4px;
        }}
        QLabel#Eyebrow {{
            color: {c['text_secondary']};
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.8px;
            text-transform: uppercase;
        }}
        QLabel#Title {{
            color: {c['text']};
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.3px;
        }}
        QLabel#Subtitle, QLabel#Muted {{
            color: {c['muted']};
            font-size: 12px;
        }}
        QLabel#CardTitle {{
            color: {c['text_secondary']};
            font-size: 14px;
            font-weight: 600;
        }}
        QLabel#TimerBadge {{
            color: {c['text_secondary']};
            background: {c['surface_alt']};
            border: 1px solid {c['border']};
            border-radius: 3px;
            padding: 3px 8px;
            font-size: 11px;
            font-weight: 600;
            font-family: "Cascadia Mono", Consolas, monospace;
        }}
        QLabel#Metric {{
            color: {c['text']};
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.5px;
        }}
        /* Standard buttons: crisp 3px micro-radius */
        QPushButton {{
            background: {c['surface_alt']};
            color: {c['text_secondary']};
            border: 1px solid {c['border']};
            border-radius: 3px;
            padding: 7px 14px;
            font-weight: 500;
            text-align: center;
        }}
        QPushButton:hover {{
            background: #343434;
            color: {c['text']};
            border-color: {c['border_strong']};
        }}
        QPushButton:pressed {{
            background: #222222;
        }}
        QPushButton:focus {{
            border: 1px solid {c['text']};
        }}
        QPushButton:disabled {{
            color: #606060;
            background: #202020;
            border-color: #2C2C2C;
        }}
        /* Primary button: Salt white CTA with deep dark text for high contrast */
        QPushButton#Primary {{
            background: #FFFFFF;
            color: #141414;
            border: 1px solid #FFFFFF;
            border-radius: 3px;
            font-weight: 650;
            padding: 8px 18px;
        }}
        QPushButton#Primary:hover {{
            background: #E5E5E5;
            border-color: #E5E5E5;
            color: #0F0F0F;
        }}
        QPushButton#Primary:pressed {{
            background: #CCCCCC;
            border-color: #CCCCCC;
        }}
        QPushButton#Primary:disabled {{
            background: #3A3A3A;
            color: #707070;
            border-color: #3A3A3A;
        }}
        QPushButton#Danger {{
            background: #26181A;
            color: {c['red']};
            border: 1px solid {c['red_border']};
            border-radius: 3px;
            font-weight: 600;
        }}
        QPushButton#Danger:hover {{
            background: {c['red_dark']};
            border-color: {c['red']};
        }}
        QPushButton#Danger:disabled {{
            background: #1F1B1C;
            color: #6A4449;
            border-color: #2D2022;
        }}
        /* Sidebar Navigation buttons */
        QPushButton#Nav {{
            border: none;
            border-radius: 0px;
            background: transparent;
            text-align: left;
            padding: 10px 14px;
            color: {c['muted']};
            font-weight: 500;
            font-size: 13px;
            border-left: 3px solid transparent;
        }}
        QPushButton#Nav:hover {{
            background: {c['surface']};
            color: {c['text']};
        }}
        QPushButton#Nav:checked {{
            background: {c['surface_alt']};
            color: {c['text']};
            font-weight: 600;
            border-left: 3px solid #FFFFFF;
        }}
        /* Inputs and dropdowns: crisp 3px micro-radius */
        QLineEdit, QSpinBox {{
            background: #1C1C1C;
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 3px;
            padding: 7px 10px;
            selection-background-color: #3A3A3A;
            selection-color: #FFFFFF;
        }}
        QLineEdit:focus, QSpinBox:focus {{
            border: 1px solid #FFFFFF;
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            background: {c['surface_alt']};
            border: none;
            width: 18px;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background: #383838;
        }}
        QComboBox {{
            background: #1C1C1C;
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 3px;
            padding: 7px 10px;
            min-height: 20px;
        }}
        QComboBox:focus {{
            border: 1px solid #FFFFFF;
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox QAbstractItemView {{
            background: {c['surface']};
            color: {c['text']};
            border: 1px solid {c['border_strong']};
            border-radius: 3px;
            selection-background-color: {c['surface_alt']};
            selection-color: {c['text']};
            padding: 4px;
        }}
        QCheckBox {{
            spacing: 8px;
            color: {c['text_secondary']};
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {c['border_strong']};
            border-radius: 2px;
            background: #1C1C1C;
        }}
        QCheckBox::indicator:hover {{
            border-color: #888888;
        }}
        QCheckBox::indicator:checked {{
            background: #FFFFFF;
            border-color: #FFFFFF;
            image: url({check_svg});
        }}
        QCheckBox::indicator:indeterminate {{
            background: #FFFFFF;
            border-color: #FFFFFF;
            image: url({check_svg});
        }}
        /* Progress bar: clean 2px radius */
        QProgressBar {{
            background: #1A1A1A;
            border: 1px solid {c['border']};
            border-radius: 2px;
            height: 6px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background: #FFFFFF;
            border-radius: 2px;
        }}
        /* Lists, logs, and trees: crisp recessed backgrounds and micro-radii */
        QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget {{
            background: #141414;
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 3px;
            padding: 6px;
        }}
        QPlainTextEdit:focus, QTextEdit:focus, QListWidget:focus, QTreeWidget:focus {{
            border-color: {c['border_strong']};
        }}
        QListWidget::item, QTreeWidget::item {{
            border-radius: 2px;
            padding: 6px 8px;
            margin: 1px 0px;
            color: {c['text_secondary']};
        }}
        QListWidget::item:hover, QTreeWidget::item:hover {{
            background: #252525;
            color: {c['text']};
        }}
        QListWidget::item:selected, QTreeWidget::item:selected {{
            background: {c['surface_alt']};
            color: #FFFFFF;
            border: 1px solid #404040;
        }}
        QTreeWidget::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {c['border_strong']};
            border-radius: 2px;
            background: #1C1C1C;
        }}
        QTreeWidget::indicator:hover {{
            border-color: #888888;
        }}
        QTreeWidget::indicator:checked {{
            background: #FFFFFF;
            border-color: #FFFFFF;
            image: url({check_svg});
        }}
        QTreeWidget::indicator:indeterminate {{
            background: #FFFFFF;
            border-color: #FFFFFF;
            image: url({check_svg});
        }}
        QTreeWidget::branch {{
            background: transparent;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: #3A3A3A;
            border-radius: 2px;
            min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: #555555;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 8px;
        }}
        QScrollBar::handle:horizontal {{
            background: #3A3A3A;
            border-radius: 2px;
            min-width: 24px;
        }}
        QToolTip {{
            background: {c['surface_elevated']};
            color: {c['text']};
            border: 1px solid {c['border_strong']};
            border-radius: 2px;
            padding: 5px 8px;
            font-size: 11px;
        }}
    """
