"""Read-only crypto research surface with deliberately no execution controls."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.crypto_research import (
    CryptoResearchDecision,
    CryptoResearchStatus,
    default_crypto_research_view,
)


_EMPTY_MESSAGES = {
    CryptoResearchStatus.DISABLED: "Crypto discovery is disabled.",
    CryptoResearchStatus.DISCOVERING: "Discovering supported crypto pairs...",
    CryptoResearchStatus.ACTIVE: "Crypto research is active.",
    CryptoResearchStatus.NO_SUPPORTED_PAIRS: "No supported crypto pairs were discovered.",
    CryptoResearchStatus.PROVIDER_ERROR: "Crypto provider request failed.",
    CryptoResearchStatus.AWAITING_DATA: "Supported pairs found; awaiting research data.",
}


class CryptoResearchPage(QWidget):
    COLUMNS = (
        "Pair",
        "Price",
        "% Change",
        "Volume",
        "Notional Volume",
        "Score",
        "Rank",
        "Event",
        "Trend",
        "Spread",
        "Asset Type",
        "Authority",
    )

    def __init__(self, source=None) -> None:
        super().__init__()
        self.setObjectName("cryptoResearchPage")
        self._source = source or default_crypto_research_view()
        layout = QVBoxLayout(self)
        title = QLabel("CRYPTO RESEARCH")
        title.setObjectName("sectionTitle")
        disclosure = QLabel("24/7 | NO EXECUTION AUTHORITY")
        disclosure.setObjectName("cryptoResearchDisclosure")
        disclosure.setAccessibleName("Crypto research authority disclosure")
        self.status_label = QLabel("Status: DISABLED")
        self.status_label.setObjectName("cryptoResearchStatus")
        self.empty_label = QLabel("Crypto discovery is disabled or awaiting research data.")
        self.empty_label.setObjectName("muted")
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setObjectName("cryptoResearchTable")
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(title)
        layout.addWidget(disclosure)
        layout.addWidget(self.status_label)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.table, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        rows = tuple(self._source.snapshot())
        status_getter = getattr(self._source, "status_snapshot", None)
        if callable(status_getter):
            state = status_getter()
            status = state.status
            failure = state.last_failure_category
        else:
            status = (
                CryptoResearchStatus.ACTIVE
                if rows
                else CryptoResearchStatus.AWAITING_DATA
            )
            failure = None
        self.render(rows, status=status, last_failure_category=failure)

    def render(
        self,
        rows: tuple[CryptoResearchDecision, ...],
        *,
        status: CryptoResearchStatus = CryptoResearchStatus.AWAITING_DATA,
        last_failure_category: str | None = None,
    ) -> None:
        status_text = f"Status: {status.value}"
        if last_failure_category:
            status_text += f" ({last_failure_category})"
        self.status_label.setText(status_text)
        self.empty_label.setText(_EMPTY_MESSAGES[status])
        self.table.setRowCount(len(rows))
        self.empty_label.setVisible(not rows)
        self.table.setVisible(bool(rows))
        for row_index, decision in enumerate(rows):
            features = decision.features
            event = decision.event_types[0].value if decision.event_types else "OBSERVATION"
            trend = _trend(features.trend_velocity)
            values = (
                decision.pair.canonical_symbol,
                str(decision.price),
                _display(features.percentage_change),
                _display(decision.volume),
                _display(features.notional_volume),
                str(decision.score),
                str(decision.rank),
                event,
                trend,
                _display(decision.spread),
                decision.asset_type.value,
                "RESEARCH ONLY",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {1, 2, 3, 4, 5, 6, 9}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row_index, column, item)


def _display(value: Decimal | None) -> str:
    return "--" if value is None else str(value)


def _trend(value: Decimal | None) -> str:
    if value is None:
        return "INSUFFICIENT DATA"
    if value > 0:
        return "RISING"
    if value < 0:
        return "FALLING"
    return "FLAT"


__all__ = ["CryptoResearchPage"]
