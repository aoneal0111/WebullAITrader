from __future__ import annotations

from pathlib import Path

from app.replay import ReplayEventArchive

from .models import RecordedSession
from .serializer import RecordingSerializer


class RecordingReader:
    def __init__(self, serializer: RecordingSerializer) -> None:
        if not isinstance(serializer, RecordingSerializer):
            raise TypeError(
                "serializer must be a RecordingSerializer"
            )
        self._serializer = serializer

    def read_session(self, path: Path) -> RecordedSession:
        if not isinstance(path, Path):
            raise TypeError("path must be a Path")
        return self._serializer.deserialize(path.read_bytes())

    def read_archive(self, path: Path) -> ReplayEventArchive:
        session = self.read_session(path)
        return ReplayEventArchive.from_events(
            self._serializer.restore_event(event)
            for event in session.events
        )
