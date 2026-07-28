from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class OrderEntryPanel(QFrame):
    """Collect and validate paper-order input without executing trades."""

    order_validated = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("contentPanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        header = QHBoxLayout()

        title = QLabel("ORDER ENTRY")
        title.setObjectName("sectionTitle")

        self._validation_status = QLabel("Ready for paper-order input.")
        self._validation_status.setObjectName("mutedText")

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._validation_status)

        root.addLayout(header)

        fields = QGridLayout()
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(8)

        self.symbol = QLineEdit()
        self.symbol.setPlaceholderText("AAPL")
        self.symbol.setMaxLength(12)

        self.side = QComboBox()
        self.side.addItems(("BUY", "SELL"))

        self.quantity = QSpinBox()
        self.quantity.setRange(1, 1_000_000)
        self.quantity.setValue(1)

        self.order_type = QComboBox()
        self.order_type.addItems(
            (
                "MARKET",
                "LIMIT",
                "STOP",
                "STOP_LIMIT",
            )
        )

        self.limit_price = QDoubleSpinBox()
        self.limit_price.setRange(0.0, 10_000_000.0)
        self.limit_price.setDecimals(4)
        self.limit_price.setSingleStep(0.01)
        self.limit_price.setPrefix("$")

        self.stop_price = QDoubleSpinBox()
        self.stop_price.setRange(0.0, 10_000_000.0)
        self.stop_price.setDecimals(4)
        self.stop_price.setSingleStep(0.01)
        self.stop_price.setPrefix("$")

        self.time_in_force = QComboBox()
        self.time_in_force.addItems(("DAY", "GTC"))

        fields.addWidget(self._label("Symbol"), 0, 0)
        fields.addWidget(self.symbol, 1, 0)

        fields.addWidget(self._label("Side"), 0, 1)
        fields.addWidget(self.side, 1, 1)

        fields.addWidget(self._label("Quantity"), 0, 2)
        fields.addWidget(self.quantity, 1, 2)

        fields.addWidget(self._label("Order Type"), 0, 3)
        fields.addWidget(self.order_type, 1, 3)

        fields.addWidget(self._label("Limit Price"), 2, 0)
        fields.addWidget(self.limit_price, 3, 0)

        fields.addWidget(self._label("Stop Price"), 2, 1)
        fields.addWidget(self.stop_price, 3, 1)

        fields.addWidget(self._label("Time in Force"), 2, 2)
        fields.addWidget(self.time_in_force, 3, 2)

        fields.setColumnStretch(0, 2)
        fields.setColumnStretch(1, 1)
        fields.setColumnStretch(2, 1)
        fields.setColumnStretch(3, 1)

        root.addLayout(fields)

        actions = QHBoxLayout()
        actions.addStretch()

        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("secondaryButton")
        self.clear_button.clicked.connect(self.clear)

        self.submit_button = QPushButton("Place Paper Order")
        self.submit_button.setObjectName("primaryButton")
        self.submit_button.clicked.connect(self._validate)

        actions.addWidget(self.clear_button)
        actions.addWidget(self.submit_button)

        root.addLayout(actions)

        self.order_type.currentTextChanged.connect(
            self._update_price_controls
        )
        self._update_price_controls(self.order_type.currentText())

    def set_execution_enabled(self, enabled: bool) -> None:
        """Enable placement only when the command dependencies are available."""

        self.submit_button.setEnabled(enabled)
        if enabled:
            self.submit_button.setToolTip("")
            self._validation_status.setText(
                "Ready for paper-order input."
            )
        else:
            self.submit_button.setToolTip(
                "Paper trading service is unavailable."
            )
            self._validation_status.setText(
                "Paper trading service is unavailable."
            )

    def show_submission_success(
        self,
        *,
        broker_order_id: str,
        message: str,
    ) -> None:
        """Display an accepted paper-order acknowledgement."""

        self._validation_status.setText(
            f"Submitted: {broker_order_id} - {message}"
        )

    def show_submission_error(self, message: str) -> None:
        """Display a normalized placement failure."""

        normalized = str(message).strip() or "Unknown placement failure."
        self._validation_status.setText(
            f"Submission error: {normalized}"
        )

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("mutedText")
        return label

    def clear(self) -> None:
        self.symbol.clear()
        self.side.setCurrentIndex(0)
        self.quantity.setValue(1)
        self.order_type.setCurrentIndex(0)
        self.limit_price.setValue(0.0)
        self.stop_price.setValue(0.0)
        self.time_in_force.setCurrentIndex(0)
        self._validation_status.setText(
            "Ready for paper-order input."
        )

    def _update_price_controls(self, order_type: str) -> None:
        self.limit_price.setEnabled(
            order_type in {"LIMIT", "STOP_LIMIT"}
        )
        self.stop_price.setEnabled(
            order_type in {"STOP", "STOP_LIMIT"}
        )

    def _validate(self) -> None:
        symbol = self.symbol.text().strip().upper()
        order_type = self.order_type.currentText()

        if not symbol:
            self._show_error("Symbol is required.")
            self.symbol.setFocus()
            return

        if not symbol.replace(".", "").replace("-", "").isalnum():
            self._show_error("Symbol contains invalid characters.")
            self.symbol.setFocus()
            return

        if order_type in {"LIMIT", "STOP_LIMIT"}:
            if self.limit_price.value() <= 0:
                self._show_error(
                    "A positive limit price is required."
                )
                self.limit_price.setFocus()
                return

        if order_type in {"STOP", "STOP_LIMIT"}:
            if self.stop_price.value() <= 0:
                self._show_error(
                    "A positive stop price is required."
                )
                self.stop_price.setFocus()
                return

        request = {
            "symbol": symbol,
            "side": self.side.currentText(),
            "quantity": self.quantity.value(),
            "order_type": order_type,
            "limit_price": (
                Decimal(str(self.limit_price.value()))
                if self.limit_price.isEnabled()
                else None
            ),
            "stop_price": (
                Decimal(str(self.stop_price.value()))
                if self.stop_price.isEnabled()
                else None
            ),
            "time_in_force": self.time_in_force.currentText(),
        }

        self.symbol.setText(symbol)
        self._validation_status.setText(
            f"Validated: {request['side']} "
            f"{request['quantity']} {symbol} "
            f"{order_type}"
        )
        self.order_validated.emit(request)

    def _show_error(self, message: str) -> None:
        self._validation_status.setText(
            f"Validation error: {message}"
        )
