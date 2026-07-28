from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QStatusBar

from app.gui.theme import Sizing


class PersistentStatusBar(QStatusBar):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("persistentStatusBar")
        self.setMinimumHeight(Sizing.STATUS_BAR_HEIGHT)
        self.runtime = self._field("Runtime", "STOPPED")
        self.recorder = self._field("Recorder", "READY")
        self.events = self._field("Events/sec", "0")
        self.memory = self._field("Memory", "—")
        self.clock = self._field("Clock", "--:--:--")
        self.addWidget(self.runtime)
        self.addPermanentWidget(self.recorder)
        self.addPermanentWidget(self.events)
        self.addPermanentWidget(self.memory)
        self.addPermanentWidget(self.clock)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._update_clock()

    @staticmethod
    def _field(label: str, value: str) -> QLabel:
        widget = QLabel(f"{label}  {value}")
        widget.setObjectName("muted")
        widget.setProperty("fieldLabel", label)
        return widget

    @staticmethod
    def _set_field(widget: QLabel, value: object) -> None:
        widget.setText(f"{widget.property('fieldLabel')}  {value}")

    def set_runtime(self, value: str) -> None:
        self._set_field(self.runtime, value)

    def set_recorder(self, value: str) -> None:
        self._set_field(self.recorder, value)

    def set_events_per_second(self, value: object) -> None:
        self._set_field(self.events, value)

    def set_memory(self, value: str) -> None:
        self._set_field(self.memory, value)

    def _update_clock(self) -> None:
        self._set_field(self.clock, datetime.now().astimezone().strftime("%H:%M:%S"))

