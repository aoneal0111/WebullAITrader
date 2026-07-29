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
    QFrame#panel, QFrame#runtimeRibbon, QFrame#operatorWorkspace {{
        background: {Colors.SURFACE_PANEL};
        border: 1px solid {Colors.BORDER_SUBTLE};
        border-radius: {Radius.LG}px;
    }}

    QFrame#metricCard, QFrame#statusCard {{
        background: {Colors.SURFACE_CARD};
        border: 1px solid {Colors.BORDER};
        border-radius: {Radius.LG}px;
    }}

    QFrame#metricCard:hover, QFrame#statusCard:hover {{
        background: {Colors.SURFACE_ACTIVE};
        border: 1px solid {Colors.BORDER_INTERACTIVE};
    }}
    QLabel#metricTitle {{
        color: {Colors.TEXT_MUTED};
        font-size: {Typography.METRIC}px;
        font-weight: {Typography.WEIGHT_BOLD};
    }}
    QLabel#metricValue {{
        font-size: {Typography.LG}px;
        font-weight: {Typography.WEIGHT_BOLD};
    }}
    QLabel#metricNote {{
        color: {Colors.TEXT_SUBTLE};
        font-size: {Typography.METRIC}px;
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
        padding: 10px 14px;
        background: transparent;
        border: 1px solid transparent;
        border-radius: {Radius.MD}px;
        color: {Colors.TEXT_MUTED};
    }}

    QPushButton#navButton:hover {{
        background: {Colors.SURFACE_HOVER};
        color: {Colors.TEXT};
        border: 1px solid {Colors.BORDER};
    }}

    QPushButton#navButton:pressed {{
        background: {Colors.ACCENT_MUTED};
        border: 1px solid {Colors.ACCENT};
    }}

    QPushButton#navButton:checked {{
        background: {Colors.ACCENT};
        color: {Colors.TEXT_INVERSE};
        border: 1px solid {Colors.ACCENT_HOVER};
    }}

    QPushButton#navButton:focus {{
        border: 1px solid {Colors.FOCUS};
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

