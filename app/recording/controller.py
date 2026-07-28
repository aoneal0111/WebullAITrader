from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import RLock

from app.replay import ReplayController

from .models import (
    RecordedSession,
    RecordingSnapshot,
    RecordingStatus,
)
from .reader import RecordingReader
from .writer import RecordingWriter


RecordingListener = Callable[[RecordingSnapshot], None]


class RecordingController:
    def __init__(
        self,
        recorder,
        writer: RecordingWriter,
        reader: RecordingReader,
        replay_controller: ReplayController,
    ) -> None:
        from .recorder import SessionRecorder

        if not isinstance(recorder, SessionRecorder):
            raise TypeError("recorder must be a SessionRecorder")
        if not isinstance(writer, RecordingWriter):
            raise TypeError("writer must be a RecordingWriter")
        if not isinstance(reader, RecordingReader):
            raise TypeError("reader must be a RecordingReader")
        if not isinstance(replay_controller, ReplayController):
            raise TypeError(
                "replay_controller must be a ReplayController"
            )
        self._lock = RLock()
        self._recorder = recorder
        self._writer = writer
        self._reader = reader
        self._replay_controller = replay_controller
        self._path: Path | None = None
        self._size_bytes = 0
        self._error: str | None = None
        self._listeners: dict[int, RecordingListener] = {}
        self._next_listener_id = 1
        self._closed = False
        self._completion_listener_id = (
            recorder.subscribe_completed(self._persist_completed)
        )

    def snapshot(self) -> RecordingSnapshot:
        with self._lock:
            snapshot = self._recorder.snapshot()
            active = snapshot.status is RecordingStatus.ACTIVE
            return replace(
                snapshot,
                status=(
                    RecordingStatus.ERROR
                    if self._error is not None and not active
                    else snapshot.status
                ),
                size_bytes=0 if active else self._size_bytes,
                file_path=(
                    None
                    if active or self._path is None
                    else str(self._path)
                ),
                error=None if active else self._error,
            )

    def save(self, path: Path) -> Path:
        if not isinstance(path, Path):
            raise TypeError("path must be a Path")
        with self._lock:
            self._ensure_open()
            if (
                self._recorder.snapshot().status
                is RecordingStatus.ACTIVE
            ):
                raise RuntimeError(
                    "cannot save while recording is active"
                )
            session = self._recorder.completed_session()
            if session is None:
                raise RuntimeError(
                    "no completed recording is available"
                )
        result = self._writer.write(session, path)
        self._set_persisted(result)
        return result

    def open(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be a Path")
        with self._lock:
            self._ensure_open()
        session = self._reader.read_session(path)
        archive = self._reader.read_archive(path)
        self._replay_controller.load(
            archive,
            session_id=session.session_id,
        )
        self._set_persisted(path)

    def subscribe(self, listener: RecordingListener) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._ensure_open()
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener
            snapshot = self.snapshot()
        listener(snapshot)
        return listener_id

    def unsubscribe(self, listener_id: int) -> bool:
        with self._lock:
            return self._listeners.pop(listener_id, None) is not None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._recorder.unsubscribe_completed(
                self._completion_listener_id
            )
            self._listeners.clear()
            self._closed = True

    def _persist_completed(self, session: RecordedSession) -> None:
        try:
            path = self._writer.write(session)
        except OSError as exc:
            with self._lock:
                self._error = str(exc)
            self._notify()
            return
        self._set_persisted(path)

    def _set_persisted(self, path: Path) -> None:
        with self._lock:
            self._path = path
            self._size_bytes = path.stat().st_size
            self._error = None
        self._notify()

    def _notify(self) -> None:
        with self._lock:
            snapshot = self.snapshot()
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            listener(snapshot)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("recording controller is closed")
