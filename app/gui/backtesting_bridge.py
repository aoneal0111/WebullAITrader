from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.backtesting.controller import BacktestingController
from app.backtesting.models import ExperimentSnapshot


class QtBacktestingBridge(QObject):
    experiments_changed = Signal(object)

    def __init__(
        self,
        controller: BacktestingController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(controller, BacktestingController):
            raise TypeError("controller must be BacktestingController")
        self._controller = controller
        self._listener_id = controller.subscribe(self._forward)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._controller.unsubscribe(self._listener_id)
        self._closed = True

    def _forward(self, snapshot: ExperimentSnapshot) -> None:
        self.experiments_changed.emit(snapshot)
