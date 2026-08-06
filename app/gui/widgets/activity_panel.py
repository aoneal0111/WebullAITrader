from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import ActivitySnapshot, TimelineFilter
from app.gui.design.tokens import Colors
from app.gui.widgets.data_table import StyledDataTable


class ActivityPanel(QWidget):
    """Render immutable timeline rows and emit structured filter intent."""

    filters_changed = Signal(object)

    def __init__(self, *, show_filters: bool = True) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        filter_bar = QWidget()
        filters = QHBoxLayout(filter_bar)
        filters.setContentsMargins(0, 0, 0, 0)
        self._severity = QComboBox()
        self._category = QComboBox()
        self._symbol = QComboBox()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search mission timeline")
        for widget in (
            self._severity,
            self._category,
            self._symbol,
            self._search,
        ):
            filters.addWidget(widget)
        filter_bar.setVisible(show_filters)
        layout.addWidget(filter_bar)

        self._table = StyledDataTable(
            ("Time", "Severity", "Category", "Symbol", "Source", "Event")
        )
        self._table.set_empty_state(
            "No mission events",
            "Projected runtime activity will appear here.",
            icon="\u25f7",
        )
        layout.addWidget(self._table)

        self._severity.currentTextChanged.connect(self._emit_filters)
        self._category.currentTextChanged.connect(self._emit_filters)
        self._symbol.currentTextChanged.connect(self._emit_filters)
        self._search.textChanged.connect(self._emit_filters)

    def render(self, snapshot: ActivitySnapshot) -> None:
        self._set_options(
            self._severity,
            snapshot.severity_options,
            snapshot.filters.severity,
        )
        self._set_options(
            self._category,
            snapshot.category_options,
            snapshot.filters.category,
        )
        self._set_options(
            self._symbol,
            snapshot.symbol_options,
            snapshot.filters.symbol,
        )
        self._search.blockSignals(True)
        self._search.setText(snapshot.filters.search)
        self._search.blockSignals(False)

        self._table.setRowCount(len(snapshot.entries))
        for row_index, entry in enumerate(snapshot.entries):
            values = (
                entry.occurred_at.astimezone().strftime("%H:%M:%S"),
                entry.severity,
                entry.category,
                entry.related_symbol or "--",
                entry.source,
                entry.message,
            )
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index == 1:
                    item.setForeground(
                        QBrush(QColor(_severity_color(value)))
                    )
                elif column_index == 2:
                    item.setForeground(
                        QBrush(QColor(_category_color(value)))
                    )
                self._table.setItem(row_index, column_index, item)

    @staticmethod
    def _set_options(
        combo: QComboBox,
        options: tuple[str, ...],
        selected: str,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(options)
        combo.setCurrentText(selected)
        combo.blockSignals(False)

    def _emit_filters(self) -> None:
        self.filters_changed.emit(
            TimelineFilter(
                severity=self._severity.currentText() or "ALL",
                category=self._category.currentText() or "ALL",
                symbol=self._symbol.currentText() or "ALL",
                search=self._search.text(),
            )
        )


def _severity_color(value: str) -> str:
    normalized = value.upper()
    if normalized in {"SUCCESS", "INFO"}:
        return Colors.SUCCESS
    if normalized in {"ERROR", "CRITICAL"}:
        return Colors.DANGER
    if normalized in {"WARNING", "WARN"}:
        return Colors.WARNING
    return Colors.TEXT_MUTED


def _category_color(value: str) -> str:
    normalized = value.upper()
    if normalized in {"ORDER", "EXECUTION", "TRADE"}:
        return Colors.SUCCESS
    if normalized == "RISK":
        return Colors.WARNING
    if normalized in {"AI", "SCANNER"}:
        return Colors.CYAN
    if normalized in {"BROKER", "MARKET_DATA"}:
        return Colors.ACCENT_HOVER
    return Colors.TEXT_MUTED
