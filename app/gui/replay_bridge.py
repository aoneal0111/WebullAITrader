from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.replay import ReplayController, ReplaySnapshot


class QtReplayBridge(QObject):
    """Deliver immutable replay snapshots on the Qt signal boundary."""

    replay_changed = Signal(object)

    def __init__(
        self,
        controller: ReplayController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(controller, ReplayController):
            raise TypeError("controller must be a ReplayController")
        self._controller = controller
        self._listener_id = controller.subscribe(self._forward)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._controller.unsubscribe(self._listener_id)
        self._closed = True

    def _forward(self, snapshot: ReplaySnapshot) -> None:
        self.replay_changed.emit(snapshot)
