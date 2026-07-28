from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.event_store import EventStoreController, EventStoreSnapshot


class QtEventStoreBridge(QObject):
    event_store_changed = Signal(object)

    def __init__(
        self,
        controller: EventStoreController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(controller, EventStoreController):
            raise TypeError(
                "controller must be EventStoreController"
            )
        self._controller = controller
        self._listener_id = controller.subscribe(self._forward)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._controller.unsubscribe(self._listener_id)
        self._closed = True

    def _forward(self, snapshot: EventStoreSnapshot) -> None:
        self.event_store_changed.emit(snapshot)
