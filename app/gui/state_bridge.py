from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.operations_core import ApplicationState, ApplicationStateStore


class QtStateBridge(QObject):
    """
    Delivers ApplicationState snapshots to Qt widgets safely.

    ApplicationStateStore listeners may run on a worker thread. Emitting a Qt
    signal allows Qt to queue delivery to widgets on the GUI thread.
    """

    state_changed = Signal(object)

    def __init__(
        self,
        state_store: ApplicationStateStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._state_store = state_store
        self._listener_id = state_store.subscribe(self._forward_state)

    def close(self) -> None:
        self._state_store.unsubscribe(self._listener_id)

    def _forward_state(self, state: ApplicationState) -> None:
        self.state_changed.emit(state)
