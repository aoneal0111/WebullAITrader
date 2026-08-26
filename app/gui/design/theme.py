from __future__ import annotations

import os
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from app.gui.design.tokens import Colors, Dimensions, Typography


def _font_families() -> tuple[str, str]:
    """Install a known Windows UI font when Qt's runtime has no font database.

    Some packaged/offscreen Qt deployments expose an empty font database; a
    QSS family name then produces tofu glyphs instead of using platform
    fallback.  Registering the installed Segoe UI file makes text rendering
    deterministic while generic Qt families remain the non-Windows fallback.
    """
    app = QApplication.instance()
    if app is not None and "Segoe UI" not in QFontDatabase.families():
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf")
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)
    family = "Segoe UI" if "Segoe UI" in QFontDatabase.families() else "Sans Serif"
    mono = "Consolas" if "Consolas" in QFontDatabase.families() else "Monospace"
    if app is not None:
        app.setFont(QFont(family, Typography.MD))
    return family, mono


def application_stylesheet() -> str:
    family, mono = _font_families()
    return f"""
    * {{ font-family: "{family}"; font-size: {Typography.MD}px; }}
    QMainWindow, QWidget#appRoot {{ background: {Colors.BACKGROUND}; color: {Colors.TEXT}; }}
    QWidget#navigationRail {{ background: {Colors.SIDEBAR}; border-right: 1px solid {Colors.BORDER}; }}
    QWidget#contentArea, QStackedWidget {{ background: {Colors.BACKGROUND}; }}
    QLabel {{ color: {Colors.TEXT}; }}
    QLabel#muted {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#faint {{ color: {Colors.TEXT_FAINT}; }}
    QLabel#brand {{ font-size: {Typography.XL}px; font-weight: 800; letter-spacing: 2px; color: {Colors.TEXT_STRONG}; }}
    QLabel#brandMark {{ background: {Colors.ACCENT}; color: white; border-radius: 8px; font-size: 17px; font-weight: 900; }}
    QLabel#pageTitle {{ font-size: {Typography.XXL}px; font-weight: 750; color: {Colors.TEXT_STRONG}; }}
    QLabel#missionTitle {{ font-size: {Typography.XL}px; font-weight: 750; color: {Colors.TEXT_STRONG}; }}
    QLabel#headerMetricValue {{ font-family: "{mono}"; color: {Colors.TEXT_STRONG}; font-size: 14px; font-weight: 700; }}
    QLabel#headerMetricValue[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#headerMetricValue[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#headerMetricValue[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#headerMetricValue[status='neutral'] {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#sectionTitle {{ font-size: {Typography.PANEL_TITLE}px; font-weight: 700; color: {Colors.TEXT_STRONG}; }}
    QLabel#sectionEyebrow {{ font-size: {Typography.XS}px; font-weight: 750; color: {Colors.TEXT_MUTED}; letter-spacing: 0.8px; }}
    QLabel#eyebrow {{ color: {Colors.ACCENT}; font-size: {Typography.XS}px; font-weight: 800; letter-spacing: 1px; }}
    QLabel#monoValue {{ font-family: "{mono}"; color: {Colors.TEXT_STRONG}; font-weight: 650; }}
    QLabel#quotePrice {{ font-family: "{mono}"; color: {Colors.TEXT_STRONG}; font-size: {Typography.PRIMARY_METRIC}px; font-weight: 800; }}
    QLabel#candidateSymbol {{ font-size: 24px; font-weight: 800; color: {Colors.TEXT_STRONG}; }}
    QLabel#candidatePrice {{ font-family: "{mono}"; font-size: 23px; font-weight: 800; color: {Colors.TEXT_STRONG}; }}
    QLabel#candidateChange {{ font-family: "{mono}"; font-size: 19px; font-weight: 750; }}
    QLabel#candidateChange[tone='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#candidateChange[tone='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#candidateChange[tone='neutral'] {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#monoValue[emphasis='primary'] {{ font-size: {Typography.LG}px; font-weight: 750; }}
    QFrame#panel, QFrame#metricCard, QFrame#statusCard {{
        background: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 5px;
    }}
    QFrame#terminalHeader {{ background: {Colors.SIDEBAR}; border-bottom: 1px solid {Colors.BORDER_STRONG}; }}
    QFrame#workstationHeader {{ background: {Colors.SIDEBAR}; border: 1px solid {Colors.BORDER_STRONG}; border-radius: 5px; }}
    QFrame#workstationFooter {{ background: {Colors.SURFACE}; border-top: 1px solid {Colors.BORDER}; }}
    QLabel#tableValue {{ color: {Colors.TEXT}; font-family: "{Typography.MONO}"; }}
    QFrame#headerSeparator {{ color: {Colors.BORDER}; max-width: 1px; margin: 4px 3px; }}
    QFrame#missionStatusCard, QFrame#activityMetric {{
        background: {Colors.SURFACE_ALT};
        border: 1px solid {Colors.BORDER_SOFT};
        border-radius: 6px;
    }}
    QFrame#tradeIntelligenceHeader, QFrame#intelligenceSection {{
        background: {Colors.SURFACE_ALT};
        border: 1px solid {Colors.BORDER_SOFT};
        border-radius: 6px;
    }}
    QLabel#intelligenceExplanation {{ color: {Colors.TEXT_STRONG}; font-size: {Typography.LG}px; font-weight: 650; }}
    QLabel#decisionState {{ font-family: "{mono}"; color: {Colors.TEXT_STRONG}; font-size: {Typography.XXL}px; font-weight: 850; padding: 8px; }}
    QLabel#decisionState[tone='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#decisionState[tone='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#decisionState[tone='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#decisionState[tone='neutral'] {{ color: {Colors.TEXT_MUTED}; }}
    QFrame#activityMetric:hover {{ border-color: {Colors.BORDER_STRONG}; }}
    QFrame#metricCard:hover, QFrame#statusCard:hover {{ border-color: {Colors.BORDER_STRONG}; background: {Colors.SURFACE_ALT}; }}
    QFrame#metricCard[emphasis='primary'] {{ border-color: {Colors.BORDER_STRONG}; background: {Colors.SURFACE_ALT}; }}
    QFrame#metricCard[emphasis='compact'] {{ background: {Colors.SURFACE}; border-color: {Colors.BORDER_SOFT}; }}
    QLabel#metricTitle {{ color: {Colors.TEXT_MUTED}; font-size: {Typography.SM}px; font-weight: 700; letter-spacing: 0.35px; }}
    QLabel#aiObjective {{ color: {Colors.TEXT_STRONG}; font-size: {Typography.XL}px; font-weight: 750; }}
    QLabel#aiReasoning {{ color: {Colors.TEXT}; font-size: {Typography.MD}px; }}
    QLabel#aiFact {{ color: {Colors.TEXT_STRONG}; font-weight: 650; }}
    QLabel#metricValue {{ font-family: "{mono}"; font-size: {Typography.LG}px; font-weight: 700; color: {Colors.TEXT_STRONG}; }}
    QLabel#metricValue[emphasis='primary'] {{ font-size: {Typography.PRIMARY_METRIC}px; font-weight: 750; }}
    QLabel#metricValue[emphasis='medium'] {{ font-size: {Typography.XL}px; }}
    QLabel#compactMetricValue {{ font-family: "{mono}"; font-size: 14px; font-weight: 700; color: {Colors.TEXT_STRONG}; }}
    QLabel#metricValue[tone='good'], QLabel#compactMetricValue[tone='good'], QLabel#monoValue[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#metricValue[tone='danger'], QLabel#compactMetricValue[tone='danger'], QLabel#monoValue[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#metricValue[tone='warn'], QLabel#compactMetricValue[tone='warn'], QLabel#monoValue[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#metricValue[tone='neutral'], QLabel#compactMetricValue[tone='neutral'], QLabel#monoValue[status='neutral'] {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#quotePrice[tone='good'], QLabel#monoValue[tone='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#quotePrice[tone='danger'], QLabel#monoValue[tone='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#quotePrice[tone='neutral'], QLabel#monoValue[tone='neutral'] {{ color: {Colors.TEXT_MUTED}; }}
    QPushButton {{ border: 0; border-radius: 4px; padding: 6px 10px; min-height: 20px; font-weight: 650; }}
    QPushButton:focus, QComboBox:focus, QLineEdit:focus, QTreeView:focus, QTableView:focus, QTableWidget:focus {{ border: 1px solid {Colors.CYAN}; }}
    QPushButton#primaryButton {{ background: {Colors.ACCENT}; color: white; }}
    QPushButton#primaryButton:hover {{ background: {Colors.ACCENT_HOVER}; }}
    QPushButton#secondaryButton {{ background: {Colors.SURFACE_RAISED}; color: {Colors.TEXT}; border: 1px solid {Colors.BORDER}; }}
    QPushButton#secondaryButton:hover {{ border-color: {Colors.BORDER_STRONG}; background: {Colors.SURFACE_ALT}; }}
    QPushButton#ghostButton {{ color: {Colors.TEXT_MUTED}; background: transparent; border: 1px solid {Colors.BORDER}; }}
    QPushButton#ghostButton:hover {{ color: {Colors.TEXT}; background: {Colors.SURFACE_RAISED}; }}
    QPushButton#dangerButton {{ background: {Colors.DANGER_SOFT}; color: {Colors.DANGER}; border: 1px solid {Colors.DANGER}; }}
    QPushButton#dangerButton:hover {{ background: {Colors.DANGER}; color: white; }}
    QPushButton#dangerButton:disabled {{ color: {Colors.TEXT_FAINT}; background: {Colors.SURFACE}; border-color: {Colors.BORDER}; }}
    QPushButton:disabled {{ color: {Colors.TEXT_FAINT}; background: {Colors.SURFACE}; border-color: {Colors.BORDER}; }}
    QPushButton#navButton {{ text-align: left; padding: 9px 10px; min-height: 24px; color: {Colors.TEXT_MUTED}; background: transparent; border-left: 3px solid transparent; }}
    QPushButton#navButton:hover {{ color: {Colors.TEXT_STRONG}; background: {Colors.SURFACE_HOVER}; border-left-color: {Colors.BORDER_STRONG}; }}
    QPushButton#navButton:checked {{ color: {Colors.ACCENT_HOVER}; background: {Colors.ACCENT_SOFT}; border-left-color: {Colors.ACCENT}; }}
    QPushButton#scannerFilter {{ color: {Colors.TEXT_MUTED}; background: transparent; border: 1px solid {Colors.BORDER}; }}
    QPushButton#scannerFilter:checked {{ color: {Colors.ACCENT_HOVER}; background: {Colors.ACCENT_SOFT}; border-color: {Colors.ACCENT}; }}
    QPushButton#sidebarToggle {{ color: {Colors.TEXT_MUTED}; background: transparent; border: 1px solid {Colors.BORDER}; }}
    QToolButton#focusChartButton, QToolButton#collapseButton, QToolButton#overflowButton {{ color: {Colors.TEXT_MUTED}; background: transparent; border: 1px solid {Colors.BORDER}; border-radius: 4px; padding: 5px 8px; }}
    QToolButton#focusChartButton:hover, QToolButton#collapseButton:hover, QToolButton#overflowButton:hover {{ color: {Colors.TEXT_STRONG}; border-color: {Colors.BORDER_STRONG}; background: {Colors.SURFACE_RAISED}; }}
    QLabel#statusBadge {{ border-radius: 7px; padding: 4px 8px; background: {Colors.SURFACE_RAISED}; color: {Colors.TEXT_MUTED}; font-weight: 750; }}
    QLabel#statusBadge[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#statusBadge[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#statusBadge[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#statusIndicator[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#statusIndicator[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#statusIndicator[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#statusIndicator[status='neutral'] {{ color: {Colors.TEXT_FAINT}; }}
    QFrame#statusSeparator {{ color: {Colors.BORDER_STRONG}; max-height: 12px; }}
    QAbstractItemView, QTreeView, QTableView, QTableWidget {{
        background: {Colors.SURFACE};
        alternate-background-color: {Colors.SURFACE_ALT};
        color: {Colors.TEXT};
        border: 0;
        gridline-color: {Colors.BORDER_SOFT};
        selection-background-color: {Colors.ACCENT_SOFT};
        selection-color: {Colors.TEXT_STRONG};
        outline: 0;
    }}
    QTreeView::item, QTableView::item, QTableWidget::item {{ padding: 3px 7px; border-bottom: 1px solid {Colors.BORDER_SOFT}; }}
    QTreeView::item:selected, QTableView::item:selected, QTableWidget::item:selected {{ border-top: 1px solid {Colors.ACCENT}; border-bottom: 1px solid {Colors.ACCENT}; }}
    QTreeView::branch {{ background: {Colors.SURFACE}; }}
    QTableCornerButton::section {{ background: {Colors.SURFACE_RAISED}; border: 0; border-bottom: 1px solid {Colors.BORDER_STRONG}; }}
    QHeaderView::section {{ background: {Colors.SURFACE_RAISED}; color: {Colors.TEXT}; border: 0; border-bottom: 1px solid {Colors.BORDER_STRONG}; padding: 6px 7px; font-size: {Typography.SM}px; font-weight: 700; }}
    QLabel#emptyState {{ color: {Colors.TEXT_MUTED}; font-size: {Typography.MD}px; background: transparent; }}
    QTabWidget::pane {{ border: 1px solid {Colors.BORDER}; background: {Colors.SURFACE}; border-radius: 7px; top: -1px; }}
    QTabBar::tab {{ color: {Colors.TEXT_MUTED}; background: transparent; padding: 9px 14px; border-bottom: 2px solid transparent; }}
    QTabBar::tab:hover {{ color: {Colors.TEXT}; }}
    QTabBar::tab:selected {{ color: {Colors.TEXT_STRONG}; border-bottom-color: {Colors.ACCENT}; }}
    QComboBox, QLineEdit, QSpinBox, QDateTimeEdit {{ background: {Colors.SURFACE_ALT}; color: {Colors.TEXT}; border: 1px solid {Colors.BORDER}; border-radius: 5px; padding: 6px 8px; }}
    QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDateTimeEdit:hover {{ border-color: {Colors.BORDER_STRONG}; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QScrollArea {{ border: 0; background: transparent; }}
    QWidget#healthWorkspace, QWidget#healthPanel, QWidget#healthMetrics,
    QScrollArea#healthScrollArea, QWidget#healthScrollViewport {{
        background: {Colors.SURFACE};
        color: {Colors.TEXT};
        border: 0;
    }}
    QScrollBar:vertical {{ background: {Colors.BACKGROUND}; width: 9px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {Colors.BORDER_STRONG}; min-height: 28px; border-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: {Colors.TEXT_FAINT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: {Colors.BACKGROUND}; height: 9px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {Colors.BORDER_STRONG}; min-width: 28px; border-radius: 4px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QSplitter::handle {{ background: {Colors.BORDER_SOFT}; }}
    QSplitter::handle:hover {{ background: {Colors.BORDER_STRONG}; }}
    QStatusBar {{ background: {Colors.SIDEBAR}; color: {Colors.TEXT}; border-top: 1px solid {Colors.BORDER_STRONG}; min-height: {Dimensions.STATUS_HEIGHT}px; max-height: {Dimensions.STATUS_HEIGHT}px; }}
    """
