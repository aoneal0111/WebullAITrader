from __future__ import annotations

from app.gui.design.tokens import Colors


def application_stylesheet() -> str:
    return f"""
    * {{ font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px; }}
    QMainWindow, QWidget#appRoot {{ background: {Colors.BACKGROUND}; color: {Colors.TEXT}; }}
    QLabel {{ color: {Colors.TEXT}; }}
    QLabel#muted {{ color: {Colors.TEXT_MUTED}; }}
    QLabel#brand {{ font-size: 16px; font-weight: 700; letter-spacing: 1px; }}
    QLabel#pageTitle {{ font-size: 24px; font-weight: 700; }}
    QLabel#sectionTitle {{ font-size: 12px; font-weight: 700; color: {Colors.TEXT_MUTED}; letter-spacing: 1px; }}
    QFrame#panel, QFrame#metricCard {{ background: {Colors.SURFACE}; border: 1px solid {Colors.BORDER}; border-radius: 10px; }}
    QLabel#metricTitle {{ color: {Colors.TEXT_MUTED}; font-size: 11px; font-weight: 700; }}
    QLabel#metricValue {{ font-size: 20px; font-weight: 700; }}
    QPushButton {{ border: 0; border-radius: 7px; padding: 9px 14px; font-weight: 600; }}
    QPushButton#primaryButton {{ background: {Colors.ACCENT}; color: white; }}
    QPushButton#primaryButton:hover {{ background: {Colors.ACCENT_HOVER}; }}
    QPushButton#secondaryButton {{ background: {Colors.SURFACE_RAISED}; color: {Colors.TEXT}; border: 1px solid {Colors.BORDER}; }}
    QPushButton#dangerButton {{ background: {Colors.DANGER}; color: white; padding: 11px 18px; }}
    QPushButton:disabled {{ color: #626b78; background: #191e27; }}
    QPushButton#navButton {{ text-align: left; padding: 11px 14px; color: {Colors.TEXT_MUTED}; background: transparent; }}
    QPushButton#navButton:hover {{ color: {Colors.TEXT}; background: {Colors.SURFACE_RAISED}; }}
    QPushButton#navButton:checked {{ color: white; background: {Colors.ACCENT}; }}
    QLabel#statusBadge {{ border-radius: 8px; padding: 5px 9px; background: {Colors.SURFACE_RAISED}; color: {Colors.TEXT_MUTED}; font-weight: 700; }}
    QLabel#statusBadge[status='good'] {{ color: {Colors.SUCCESS}; }}
    QLabel#statusBadge[status='warn'] {{ color: {Colors.WARNING}; }}
    QLabel#statusBadge[status='danger'] {{ color: {Colors.DANGER}; }}
    QTableWidget {{ background: transparent; alternate-background-color: {Colors.SURFACE_RAISED}; border: 0; gridline-color: {Colors.BORDER}; }}
    QHeaderView::section {{ background: {Colors.SURFACE}; color: {Colors.TEXT_MUTED}; border: 0; border-bottom: 1px solid {Colors.BORDER}; padding: 8px; font-weight: 700; }}
    QStatusBar {{ background: {Colors.SURFACE}; color: {Colors.TEXT_MUTED}; border-top: 1px solid {Colors.BORDER}; }}
    """
