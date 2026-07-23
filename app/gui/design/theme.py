from __future__ import annotations

from app.gui.design.tokens import Colors


def application_stylesheet() -> str:
    return f"""
    * {{
        font-family: 'Segoe UI', 'Inter', sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QWidget#appRoot {{
        background: {Colors.BACKGROUND};
        color: {Colors.TEXT};
    }}
    QWidget {{ color: {Colors.TEXT}; }}
    QLabel {{ color: {Colors.TEXT}; background: transparent; }}
    QLabel#muted {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#brand {{ font-size: 20px; font-weight: 800; letter-spacing: 2px; }}
    QLabel#brandMark {{ font-size: 11px; font-weight: 800; color: {Colors.ACCENT}; letter-spacing: 1px; }}
    QLabel#pageTitle {{ font-size: 27px; font-weight: 750; }}
    QLabel#sectionTitle {{ font-size: 11px; font-weight: 700; color: {Colors.TEXT_MUTED}; letter-spacing: 1px; }}
    QLabel#panelTitle {{ font-size: 14px; font-weight: 700; }}
    QLabel#runtimeValue {{ font-weight: 700; }}
    QLabel#activityFeed {{ color: {Colors.TEXT_MUTED}; line-height: 1.35; padding: 4px; }}

    QWidget#sidebar {{
        background: #0d121b;
        border-right: 1px solid {Colors.BORDER};
    }}
    QFrame#panel, QFrame#metricCard, QFrame#runtimeSummary {{
        background: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 12px;
    }}
    QFrame#metricCard:hover {{ border-color: #34445c; }}
    QLabel#metricTitle {{ color: {Colors.TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: .4px; }}
    QLabel#metricValue {{ font-size: 22px; font-weight: 750; }}

    QPushButton {{
        border: 0;
        border-radius: 8px;
        padding: 9px 14px;
        font-weight: 650;
    }}
    QPushButton#primaryButton {{ background: {Colors.ACCENT}; color: white; }}
    QPushButton#primaryButton:hover {{ background: {Colors.ACCENT_HOVER}; }}
    QPushButton#secondaryButton {{
        background: {Colors.SURFACE_RAISED};
        color: {Colors.TEXT};
        border: 1px solid {Colors.BORDER};
    }}
    QPushButton#secondaryButton:hover {{ border-color: #45536a; }}
    QPushButton#dangerButton {{ background: {Colors.DANGER}; color: white; padding: 10px 17px; }}
    QPushButton:disabled {{ color: #626b78; background: #191e27; }}
    QPushButton#navButton {{
        text-align: left;
        padding: 12px 14px;
        color: {Colors.TEXT_MUTED};
        background: transparent;
        border: 1px solid transparent;
    }}
    QPushButton#navButton:hover {{ color: {Colors.TEXT}; background: {Colors.SURFACE_RAISED}; }}
    QPushButton#navButton:checked {{
        color: white;
        background: #18243a;
        border: 1px solid #29456f;
    }}
    QLabel#statusBadge {{
        border-radius: 9px;
        padding: 6px 11px;
        background: {Colors.SURFACE_RAISED};
        color: {Colors.TEXT_MUTED};
        font-weight: 750;
    }}
    QLabel#statusBadge[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#statusBadge[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#statusBadge[status='danger'] {{ color: {Colors.DANGER}; }}

    QTableWidget {{
        background: transparent;
        alternate-background-color: {Colors.SURFACE_RAISED};
        border: 0;
        gridline-color: transparent;
        selection-background-color: #1d3558;
        selection-color: white;
    }}
    QTableWidget::item {{ padding: 7px; border-bottom: 1px solid {Colors.BORDER}; }}
    QHeaderView::section {{
        background: {Colors.SURFACE};
        color: {Colors.TEXT_MUTED};
        border: 0;
        border-bottom: 1px solid {Colors.BORDER};
        padding: 9px;
        font-size: 11px;
        font-weight: 700;
    }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #303a4a; min-height: 30px; border-radius: 5px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QStatusBar {{
        background: {Colors.SURFACE};
        color: {Colors.TEXT_MUTED};
        border-top: 1px solid {Colors.BORDER};
        padding: 2px 8px;
    }}
    """
