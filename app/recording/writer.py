from __future__ import annotations

import os
from pathlib import Path
import tempfile

from .models import RecordedSession
from .serializer import RecordingSerializer


class RecordingWriter:
    def __init__(
        self,
        directory: Path,
        serializer: RecordingSerializer,
    ) -> None:
        if not isinstance(directory, Path):
            raise TypeError("directory must be a Path")
        if not isinstance(serializer, RecordingSerializer):
            raise TypeError(
                "serializer must be a RecordingSerializer"
            )
        self._directory = directory
        self._serializer = serializer

    @property
    def directory(self) -> Path:
        return self._directory

    def write(
        self,
        session: RecordedSession,
        path: Path | None = None,
    ) -> Path:
        if not isinstance(session, RecordedSession):
            raise TypeError("session must be a RecordedSession")
        if path is not None and not isinstance(path, Path):
            raise TypeError("path must be a Path or None")
        target = (
            self._directory / f"{session.session_id}.atlas-session.json"
            if path is None
            else path
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        data = self._serializer.serialize(session)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
            temporary_path = None
            return target
        finally:
            if (
                temporary_path is not None
                and temporary_path.exists()
            ):
                temporary_path.unlink()
