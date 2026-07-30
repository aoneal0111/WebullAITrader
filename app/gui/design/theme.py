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
    QLabel#sectionTitle {{ font-size: {Typography.SM}px; font-weight: 700; color: {Colors.TEXT_MUTED}; letter-spacing: 1px; }}
    QLabel#eyebrow {{ color: {Colors.ACCENT}; font-size: {Typography.XS}px; font-weight: 800; letter-spacing: 1px; }}
    QLabel#monoValue {{ font-family: "{Typography.MONO}"; color: {Colors.TEXT_STRONG}; font-weight: 650; }}
    QFrame#panel, QFrame#metricCard, QFrame#statusCard {{
        background: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 8px;
    }}
    QFrame#metricCard:hover, QFrame#statusCard:hover {{ border-color: {Colors.BORDER_STRONG}; background: {Colors.SURFACE_ALT}; }}
    QLabel#metricTitle {{ color: {Colors.TEXT_MUTED}; font-size: {Typography.XS}px; font-weight: 750; letter-spacing: 0.5px; }}
    QLabel#metricValue {{ font-family: "{Typography.MONO}"; font-size: {Typography.XL}px; font-weight: 700; color: {Colors.TEXT_STRONG}; }}
    QPushButton {{ border: 0; border-radius: 6px; padding: 8px 12px; font-weight: 650; }}
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
    QPushButton#navButton {{ text-align: left; padding: 10px 12px; color: {Colors.TEXT_MUTED}; background: transparent; border-left: 2px solid transparent; }}
    QPushButton#navButton:hover {{ color: {Colors.TEXT}; background: {Colors.SURFACE_ALT}; }}
    QPushButton#navButton:checked {{ color: {Colors.TEXT_STRONG}; background: {Colors.ACCENT_SOFT}; border-left-color: {Colors.ACCENT}; }}
    QLabel#statusBadge {{ border-radius: 7px; padding: 4px 8px; background: {Colors.SURFACE_RAISED}; color: {Colors.TEXT_MUTED}; font-weight: 750; }}
    QLabel#statusBadge[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#statusBadge[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#statusBadge[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#statusIndicator[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#statusIndicator[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#statusIndicator[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel#statusIndicator[status='neutral'] {{ color: {Colors.TEXT_FAINT}; }}
    QTableWidget {{ background: transparent; alternate-background-color: {Colors.SURFACE_ALT}; border: 0; gridline-color: {Colors.BORDER}; selection-background-color: {Colors.ACCENT_SOFT}; selection-color: {Colors.TEXT_STRONG}; }}
    QTableWidget::item {{ padding: 5px 8px; border-bottom: 1px solid {Colors.BORDER}; }}
    QHeaderView::section {{ background: {Colors.SURFACE}; color: {Colors.TEXT_MUTED}; border: 0; border-bottom: 1px solid {Colors.BORDER}; padding: 7px 8px; font-size: {Typography.XS}px; font-weight: 750; }}
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
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QStatusBar {{ background: {Colors.SIDEBAR}; color: {Colors.TEXT_MUTED}; border-top: 1px solid {Colors.BORDER}; min-height: {Dimensions.STATUS_HEIGHT}px; }}
    """
