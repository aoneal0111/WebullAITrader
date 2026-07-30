from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.operations_core import ApplicationState
from app.replay_workspace import ReplayWorkspace


class QtReplayStateBridge(QObject):
    """Marshal workspace snapshots onto Qt's event thread."""

    state_changed = Signal(object)

    def __init__(
        self,
        workspace: ReplayWorkspace,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace = workspace
        self._listener_id = workspace.subscribe(self._emit)

    def _emit(self, state: ApplicationState) -> None:
        self.state_changed.emit(state)

    def close(self) -> None:
        if self._listener_id is not None:
            self._workspace.unsubscribe(self._listener_id)
            self._listener_id = None


__all__ = ["QtReplayStateBridge"]
