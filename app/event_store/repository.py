from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from app.recording import RecordedSession, RecordingReader
from app.replay import ReplayEventArchive

from .index import EventStoreIndex, build_index


class EventStoreRepositoryError(ValueError):
    pass


class DuplicateSessionError(EventStoreRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class _LoadedRecording:
    fingerprint: tuple[int, int]
    session: RecordedSession
    archive: ReplayEventArchive


class EventStoreRepository:
    """Incrementally index checksum-validated immutable recordings."""

    def __init__(
        self,
        directory: Path,
        reader: RecordingReader,
    ) -> None:
        if not isinstance(directory, Path):
            raise TypeError("directory must be a Path")
        if not isinstance(reader, RecordingReader):
            raise TypeError("reader must be a RecordingReader")
        self._directory = directory
        self._reader = reader
        self._lock = RLock()
        self._loaded: dict[Path, _LoadedRecording] = {}
        self._index = EventStoreIndex()
        self._files_read = 0
        self._closed = False

    @property
    def index(self) -> EventStoreIndex:
        with self._lock:
            return self._index

    @property
    def files_read(self) -> int:
        with self._lock:
            return self._files_read

    def refresh(self) -> EventStoreIndex:
        with self._lock:
            self._ensure_open()
            paths = (
                tuple(
                    sorted(
                        self._directory.glob(
                            "*.atlas-session.json"
                        )
                    )
                )
                if self._directory.exists()
                else ()
            )
            candidate: dict[Path, _LoadedRecording] = {}
            for path in paths:
                stat = path.stat()
                fingerprint = (stat.st_size, stat.st_mtime_ns)
                cached = self._loaded.get(path)
                if (
                    cached is not None
                    and cached.fingerprint == fingerprint
                ):
                    candidate[path] = cached
                    continue
                session = self._reader.read_session(path)
                archive = self._reader.to_archive(session)
                candidate[path] = _LoadedRecording(
                    fingerprint,
                    session,
                    archive,
                )
                self._files_read += 1
            by_session: dict[str, Path] = {}
            for path, loaded in candidate.items():
                existing = by_session.get(loaded.session.session_id)
                if existing is not None:
                    raise DuplicateSessionError(
                        "duplicate session_id "
                        f"{loaded.session.session_id}: "
                        f"{existing} and {path}"
                    )
                by_session[loaded.session.session_id] = path
            index = build_index(
                tuple(
                    (
                        loaded.session,
                        loaded.archive,
                        str(path),
                    )
                    for path, loaded in candidate.items()
                )
            )
            self._loaded = candidate
            self._index = index
            return index

    def archive(self, session_id: str) -> ReplayEventArchive:
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or session_id != session_id.strip()
        ):
            raise ValueError(
                "session_id must be stripped non-empty text"
            )
        with self._lock:
            self._ensure_open()
            for loaded in self._loaded.values():
                if loaded.session.session_id == session_id:
                    return loaded.archive
        raise KeyError(session_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._loaded.clear()
            self._index = EventStoreIndex()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("event store repository is closed")
