"""Motyw Qt Ewidentora — język Spedi / Dispatcher."""

from __future__ import annotations

from ewidentor.ui import theme as T

APP_QSS = f"""
QMainWindow, QDialog {{
    background: {T.BG_APP};
    color: {T.TEXT_PRIMARY};
    font-family: "Segoe UI";
    font-size: 16px;
}}
QToolBar {{
    background: {T.BG_CHROME};
    border: none;
    spacing: 6px;
    padding: 4px 0;
    color: {T.TEXT_ON_CHROME};
}}
QToolBar QLabel {{
    color: {T.TEXT_SECONDARY};
    font-family: "Segoe UI";
}}
QLabel#brandWordmark {{
    color: {T.BRAND};
    font-family: "Segoe UI";
    font-size: 18px;
    font-weight: 800;
    padding: 0 12px 0 4px;
}}
QToolBar QToolButton {{
    background-color: {T.BG_BUTTON};
    border: none;
    border-radius: 0px;
    padding: 8px 12px;
    color: {T.TEXT_PRIMARY};
    font-weight: 600;
}}
QToolBar QToolButton:hover {{
    background-color: {T.BG_BUTTON_HOVER};
}}
QToolBar QToolButton:disabled {{
    color: {T.TEXT_MUTED};
    background-color: {T.BG_BUTTON};
}}
QToolBar QComboBox {{
    background: white;
    color: {T.TEXT_PRIMARY};
    border: 1px solid {T.BORDER_STRONG};
    border-radius: 0px;
    padding: 6px 8px;
    min-height: 22px;
}}
QToolBar QComboBox QAbstractItemView {{
    background: {T.BG_ELEVATED};
    color: {T.TEXT_PRIMARY};
    selection-background-color: {T.SELECTION};
}}
QLabel#kpiTitle {{
    color: {T.TEXT_MUTED};
    font-size: 12px;
}}
QLabel#kpiValue {{
    color: {T.BRAND};
    font-family: "Segoe UI";
    font-size: 16px;
    font-weight: 700;
}}
QFrame#kpiStrip {{
    background: transparent;
    border: none;
}}
QLabel#kpiSep {{
    color: {T.BORDER};
    padding: 0 2px;
}}
QFrame#inspectorFrame, QFrame#checklistFrame {{
    background: {T.BG_PANEL};
    border: 1px solid {T.BORDER};
    border-radius: 0px;
}}
QPushButton#stepButton {{
    background: {T.BG_BUTTON};
    border: none;
    border-radius: 0px;
    padding: 8px 12px;
    min-height: 22px;
    color: {T.TEXT_SECONDARY};
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#stepButton:hover {{
    color: {T.TEXT_PRIMARY};
    background: {T.BG_BUTTON_HOVER};
}}
QPushButton#stepButton[stepDone="true"] {{
    background: {T.ACCENT};
    border: none;
    color: {T.TEXT_ON_ACCENT};
}}
QPushButton#stepButton[stepDone="true"]:hover {{
    background: {T.ACCENT_HOVER};
    border: none;
    color: {T.TEXT_ON_ACCENT};
}}
QTabWidget::pane {{
    border: 1px solid {T.BORDER};
    border-radius: 0px;
    background: {T.BG_ELEVATED};
    top: -1px;
}}
QTabBar::tab {{
    background: white;
    border: 1px solid {T.BORDER};
    padding: 8px 14px;
    margin-right: 4px;
    color: {T.TEXT_PRIMARY};
    font-weight: 600;
}}
QTabBar::tab:selected {{
    background: {T.BRAND};
    color: white;
    border-color: {T.BRAND};
}}
QTableWidget {{
    background: {T.BG_ELEVATED};
    border: 1px solid {T.BORDER};
    border-radius: 0px;
    gridline-color: {T.GRIDLINE};
    selection-background-color: {T.SELECTION};
    selection-color: {T.SELECTION_TEXT};
    alternate-background-color: {T.ROW_ALT};
}}
QHeaderView::section {{
    background: {T.BG_MUTED};
    color: {T.BRAND};
    border: 1px solid {T.BORDER};
    padding: 6px 8px;
    font-weight: 700;
}}
QPushButton {{
    background: {T.BG_BUTTON};
    border: none;
    border-radius: 0px;
    padding: 8px 12px;
    color: {T.TEXT_PRIMARY};
    min-height: 22px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {T.BG_BUTTON_HOVER};
}}
QPushButton#primaryButton {{
    background: #e11d48;
    color: {T.TEXT_ON_ACCENT};
    border: none;
}}
QPushButton#primaryButton:hover {{
    background: #be123c;
}}
QPushButton:disabled {{
    color: {T.TEXT_MUTED};
    background: {T.BG_APP};
    border: none;
}}
QComboBox, QSpinBox, QDateEdit, QLineEdit, QDoubleSpinBox {{
    background: white;
    border: 1px solid {T.BORDER};
    border-radius: 0px;
    padding: 6px 8px;
    min-height: 22px;
    color: {T.TEXT_PRIMARY};
}}
QComboBox:focus, QSpinBox:focus, QDateEdit:focus, QLineEdit:focus, QDoubleSpinBox:focus {{
    border: 1px solid {T.FOCUS_RING};
}}
QCheckBox {{
    spacing: 6px;
}}
QSplitter::handle {{
    background: {T.BORDER};
    width: 6px;
}}
QLabel#sectionHint {{
    color: {T.TEXT_MUTED};
    font-size: 12px;
}}
QLabel#miloSuggest {{
    color: {T.SUGGEST};
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#viewToggle {{
    background: {T.BG_BUTTON};
    border: none;
    border-radius: 0px;
    padding: 8px 12px;
}}
QPushButton#viewToggle:checked {{
    background: {T.BRAND};
    border: none;
    color: white;
    font-weight: 600;
}}
"""


def apply_app_style(app) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
