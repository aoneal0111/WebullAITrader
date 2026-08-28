from PySide6.QtCore import QDate, QDateTime, QTime, QTimeZone

from app.gui.widgets.runtime_control_header import format_local_clock


def _central(year: int, month: int, day: int) -> QDateTime:
    zone = QTimeZone(b"America/Chicago")
    assert zone.isValid()
    return QDateTime(QDate(year, month, day), QTime(12, 34, 56), zone)


def test_local_clock_uses_daylight_abbreviation_from_explicit_timezone() -> None:
    assert format_local_clock(_central(2026, 8, 28)) == "12:34:56 CDT"


def test_local_clock_uses_standard_abbreviation_across_dst() -> None:
    assert format_local_clock(_central(2026, 1, 28)) == "12:34:56 CST"
