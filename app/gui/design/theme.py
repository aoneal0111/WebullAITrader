from __future__ import annotations

from app.gui.design.tokens import Colors, Dimensions, Typography


def application_stylesheet() -> str:
    return f"""
    * {{ font-family: "{Typography.FAMILY}"; font-size: {Typography.MD}px; }}
    QMainWindow, QWidget#appRoot {{ background: {Colors.BACKGROUND}; color: {Colors.TEXT}; }}
    QWidget#navigationRail {{ background: {Colors.SIDEBAR}; border-right: 1px solid {Colors.BORDER}; }}
    QWidget#contentArea, QStackedWidget {{ background: {Colors.BACKGROUND}; }}
    QLabel {{ color: {Colors.TEXT}; }}
    QLabel#muted {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#faint {{ color: {Colors.TEXT_FAINT}; }}
    QLabel#brand {{ font-size: {Typography.XL}px; font-weight: 800; letter-spacing: 2px; color: {Colors.TEXT_STRONG}; }}
    QLabel#brandMark {{ background: {Colors.ACCENT}; color: white; border-radius: 8px; font-size: 17px; font-weight: 900; }}
    QLabel#pageTitle {{ font-size: {Typography.XXL}px; font-weight: 750; color: {Colors.TEXT_STRONG}; }}
    QLabel#sectionTitle {{ font-size: {Typography.PANEL_TITLE}px; font-weight: 700; color: {Colors.TEXT_STRONG}; }}
    QLabel#sectionEyebrow {{ font-size: {Typography.XS}px; font-weight: 750; color: {Colors.TEXT_MUTED}; letter-spacing: 0.8px; }}
    QLabel#eyebrow {{ color: {Colors.ACCENT}; font-size: {Typography.XS}px; font-weight: 800; letter-spacing: 1px; }}
    QLabel#monoValue {{ font-family: "{Typography.MONO}"; color: {Colors.TEXT_STRONG}; font-weight: 650; }}
    QLabel#monoValue[emphasis='primary'] {{ font-size: {Typography.LG}px; font-weight: 750; }}
    QFrame#panel, QFrame#metricCard, QFrame#statusCard {{
        background: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 8px;
    }}
    QFrame#metricCard:hover, QFrame#statusCard:hover {{ border-color: {Colors.BORDER_STRONG}; background: {Colors.SURFACE_ALT}; }}
    QFrame#metricCard[emphasis='primary'] {{ border-color: {Colors.BORDER_STRONG}; background: {Colors.SURFACE_ALT}; }}
    QFrame#metricCard[emphasis='compact'] {{ background: {Colors.SURFACE}; border-color: {Colors.BORDER_SOFT}; }}
    QLabel#metricTitle {{ color: {Colors.TEXT_MUTED}; font-size: {Typography.SM}px; font-weight: 700; letter-spacing: 0.35px; }}
    QLabel#metricValue {{ font-family: "{Typography.MONO}"; font-size: {Typography.LG}px; font-weight: 700; color: {Colors.TEXT_STRONG}; }}
    QLabel#metricValue[emphasis='primary'] {{ font-size: {Typography.PRIMARY_METRIC}px; font-weight: 750; }}
    QLabel#metricValue[emphasis='medium'] {{ font-size: {Typography.XL}px; }}
    QLabel#compactMetricValue {{ font-family: "{Typography.MONO}"; font-size: 14px; font-weight: 700; color: {Colors.TEXT_STRONG}; }}
    QLabel#metricValue[tone='good'], QLabel#compactMetricValue[tone='good'], QLabel#monoValue[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#metricValue[tone='danger'], QLabel#compactMetricValue[tone='danger'], QLabel#monoValue[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#metricValue[tone='warn'], QLabel#compactMetricValue[tone='warn'], QLabel#monoValue[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#metricValue[tone='neutral'], QLabel#compactMetricValue[tone='neutral'], QLabel#monoValue[status='neutral'] {{ color: {Colors.TEXT_MUTED}; }}
    QPushButton {{ border: 0; border-radius: 6px; padding: 7px 11px; font-weight: 650; }}
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
    QPushButton#navButton {{ text-align: left; padding: 10px 12px; color: {Colors.TEXT_MUTED}; background: transparent; border-left: 3px solid transparent; }}
    QPushButton#navButton:hover {{ color: {Colors.TEXT_STRONG}; background: {Colors.SURFACE_HOVER}; border-left-color: {Colors.BORDER_STRONG}; }}
    QPushButton#navButton:checked {{ color: {Colors.TEXT_STRONG}; background: {Colors.ACCENT_SOFT}; border-left-color: {Colors.ACCENT}; }}
    QLabel#statusBadge {{ border-radius: 7px; padding: 4px 8px; background: {Colors.SURFACE_RAISED}; color: {Colors.TEXT_MUTED}; font-weight: 750; }}
    QLabel#statusBadge[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#statusBadge[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#statusBadge[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#statusIndicator[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#statusIndicator[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#statusIndicator[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#statusIndicator[status='neutral'] {{ color: {Colors.TEXT_FAINT}; }}
    QFrame#statusSeparator {{ color: {Colors.BORDER_STRONG}; max-height: 12px; }}
    QTableWidget {{ background: {Colors.SURFACE}; alternate-background-color: {Colors.SURFACE_ALT}; border: 0; gridline-color: {Colors.BORDER_SOFT}; selection-background-color: {Colors.ACCENT_SOFT}; selection-color: {Colors.TEXT_STRONG}; }}
    QTableWidget::item {{ padding: 3px 7px; border-bottom: 1px solid {Colors.BORDER_SOFT}; }}
    QTableWidget::item:selected {{ border-top: 1px solid {Colors.ACCENT}; border-bottom: 1px solid {Colors.ACCENT}; }}
    QHeaderView::section {{ background: {Colors.SURFACE_RAISED}; color: {Colors.TEXT}; border: 0; border-bottom: 1px solid {Colors.BORDER_STRONG}; padding: 6px 7px; font-size: {Typography.SM}px; font-weight: 700; }}
    QLabel#emptyState {{ color: {Colors.TEXT_MUTED}; font-size: {Typography.MD}px; background: transparent; }}
    QTabWidget::pane {{ border: 1px solid {Colors.BORDER}; background: {Colors.SURFACE}; border-radius: 7px; top: -1px; }}
    QTabBar::tab {{ color: {Colors.TEXT_MUTED}; background: transparent; padding: 9px 14px; border-bottom: 2px solid transparent; }}
    QTabBar::tab:hover {{ color: {Colors.TEXT}; }}
    QTabBar::tab:selected {{ color: {Colors.TEXT_STRONG}; border-bottom-color: {Colors.ACCENT}; }}
    QComboBox, QLineEdit, QSpinBox, QDateTimeEdit {{ background: {Colors.SURFACE_ALT}; color: {Colors.TEXT}; border: 1px solid {Colors.BORDER}; border-radius: 5px; padding: 6px 8px; }}
    QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDateTimeEdit:hover {{ border-color: {Colors.BORDER_STRONG}; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QAbstractItemView {{ background: {Colors.SURFACE_ALT}; color: {Colors.TEXT}; selection-background-color: {Colors.ACCENT_SOFT}; border: 1px solid {Colors.BORDER}; }}
    QScrollArea {{ border: 0; background: transparent; }}
    QScrollBar:vertical {{ background: {Colors.BACKGROUND}; width: 9px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {Colors.BORDER_STRONG}; min-height: 28px; border-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: {Colors.TEXT_FAINT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QSplitter::handle {{ background: {Colors.BORDER_SOFT}; }}
    QSplitter::handle:hover {{ background: {Colors.BORDER_STRONG}; }}
    QStatusBar {{ background: {Colors.SIDEBAR}; color: {Colors.TEXT}; border-top: 1px solid {Colors.BORDER_STRONG}; min-height: {Dimensions.STATUS_HEIGHT}px; max-height: {Dimensions.STATUS_HEIGHT}px; }}
    """
