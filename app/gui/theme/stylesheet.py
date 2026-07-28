from __future__ import annotations

from .colors import Colors
from .sizing import Radius
from .typography import Typography


def application_stylesheet() -> str:
    """Return the application-wide stylesheet generated from theme tokens."""

    return f"""
    * {{
        font-family: {Typography.FAMILY};
        font-size: {Typography.BODY}px;
    }}
    QMainWindow, QWidget#appRoot {{
        background: {Colors.BACKGROUND};
        color: {Colors.TEXT};
    }}
    QWidget#workspaceSurface {{
        background: {Colors.BACKGROUND};
    }}
    QLabel {{ color: {Colors.TEXT}; }}
    QLabel#muted {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#brand {{
        font-size: {Typography.LG}px;
        font-weight: {Typography.WEIGHT_BOLD};
        letter-spacing: 2px;
    }}
    QLabel#pageTitle {{
        font-size: {Typography.XL}px;
        font-weight: {Typography.WEIGHT_BOLD};
    }}
    QLabel#sectionTitle, QLabel#panelHeader {{
        color: {Colors.TEXT_MUTED};
        font-size: {Typography.SM}px;
        font-weight: {Typography.WEIGHT_BOLD};
        letter-spacing: 1px;
    }}
    QFrame#panel, QFrame#metricCard, QFrame#statusCard,
    QFrame#runtimeRibbon, QFrame#operatorWorkspace {{
        background: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.LG}px;
    }}
    QFrame#metricCard:hover, QFrame#statusCard:hover {{
        border-color: {Colors.BORDER_STRONG};
        background: {Colors.SURFACE_RAISED};
    }}
    QLabel#metricTitle {{
        color: {Colors.TEXT_MUTED};
        font-size: {Typography.XS + 1}px;
        font-weight: {Typography.WEIGHT_BOLD};
    }}
    QLabel#metricValue {{
        font-size: {Typography.LG}px;
        font-weight: {Typography.WEIGHT_BOLD};
    }}
    QLabel#metricNote {{
        color: {Colors.TEXT_SUBTLE};
        font-size: {Typography.XS + 1}px;
    }}
    QPushButton, QToolButton {{
        border: 0;
        border-radius: {Radius.MD}px;
        padding: 8px 13px;
        font-weight: {Typography.WEIGHT_SEMIBOLD};
    }}
    QPushButton#primaryButton, QToolButton#primaryButton {{
        background: {Colors.ACCENT};
        color: white;
    }}
    QPushButton#primaryButton:hover, QToolButton#primaryButton:hover {{
        background: {Colors.ACCENT_HOVER};
    }}
    QPushButton#secondaryButton, QToolButton#secondaryButton,
    QToolButton#iconButton {{
        background: {Colors.SURFACE_RAISED};
        color: {Colors.TEXT};
        border: 1px solid {Colors.BORDER};
    }}
    QPushButton#dangerButton, QToolButton#dangerButton {{
        background: {Colors.DANGER};
        color: white;
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {Colors.TEXT_SUBTLE};
        background: {Colors.SURFACE_RAISED};
    }}
    QPushButton#navButton {{
        text-align: left;
        padding: 10px 13px;
        color: {Colors.TEXT_MUTED};
        background: transparent;
    }}
    QPushButton#navButton:hover {{
        color: {Colors.TEXT};
        background: {Colors.SURFACE_RAISED};
    }}
    QPushButton#navButton:checked {{
        color: white;
        background: {Colors.ACCENT};
    }}
    QLabel#statusPill, QLabel#statusBadge, QLabel#runtimeBadge {{
        border-radius: {Radius.MD}px;
        padding: 4px 8px;
        background: {Colors.SURFACE_RAISED};
        color: {Colors.TEXT_MUTED};
        font-weight: {Typography.WEIGHT_BOLD};
    }}
    QLabel[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel[status='danger'] {{ color: {Colors.DANGER}; }}
    QLabel[status='info'] {{ color: {Colors.INFO}; }}
    QSplitter::handle {{
        background: {Colors.BACKGROUND};
        width: 5px;
        height: 5px;
    }}
    QSplitter::handle:hover {{ background: {Colors.BORDER_STRONG}; }}
    QScrollArea {{ border: 0; background: transparent; }}
    QTableWidget {{
        background: transparent;
        alternate-background-color: {Colors.SURFACE_RAISED};
        border: 0;
        gridline-color: {Colors.BORDER};
    }}
    QHeaderView::section {{
        background: {Colors.SURFACE};
        color: {Colors.TEXT_MUTED};
        border: 0;
        border-bottom: 1px solid {Colors.BORDER};
        padding: 8px;
        font-weight: {Typography.WEIGHT_BOLD};
    }}
    QStatusBar {{
        background: {Colors.SURFACE};
        color: {Colors.TEXT_MUTED};
        border-top: 1px solid {Colors.BORDER};
    }}
    """

