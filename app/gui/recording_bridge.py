from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.recording import RecordingController, RecordingSnapshot


class QtRecordingBridge(QObject):
    """Deliver immutable recording snapshots on the Qt boundary."""

    recording_changed = Signal(object)

    def __init__(
        self,
        controller: RecordingController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(controller, RecordingController):
            raise TypeError(
                "controller must be a RecordingController"
            )
        self._controller = controller
        self._listener_id = controller.subscribe(self._forward)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._controller.unsubscribe(self._listener_id)
        self._closed = True

    def _forward(self, snapshot: RecordingSnapshot) -> None:
        self.recording_changed.emit(snapshot)
