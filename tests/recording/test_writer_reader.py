from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.operations_core import RuntimeStarted, RuntimeStarting
from app.recording import (
    RecordedSession,
    RecordingFormatError,
    RecordingReader,
    RecordingSerializer,
    RecordingWriter,
)


NOW = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)


def session() -> RecordedSession:
    serializer = RecordingSerializer()
    events = (
        RuntimeStarting(occurred_at=NOW),
        RuntimeStarted(
            active_model="atlas",
            occurred_at=NOW,
        ),
    )
    return RecordedSession(
        session_id="session-1",
        started_at=NOW,
        ended_at=NOW,
        strategy_version="1.0",
        application_version="0.1.0",
        broker="BROKER_NEUTRAL",
        runtime_mode="PAPER",
        events=tuple(
            serializer.record_event(event, index)
            for index, event in enumerate(events, start=1)
        ),
    )


def test_writer_atomically_writes_and_reader_reconstructs_archive(
    tmp_path: Path,
) -> None:
    serializer = RecordingSerializer()
    writer = RecordingWriter(tmp_path, serializer)
    reader = RecordingReader(serializer)

    path = writer.write(session())
    archive = reader.read_archive(path)

    assert path.name == "session-1.atlas-session.json"
    assert tuple(
        type(event).__name__ for event in archive.events
    ) == ("RuntimeStarting", "RuntimeStarted")
    assert tuple(
        entry.sequence_number for entry in archive.entries
    ) == (1, 2)
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_replace_failure_preserves_existing_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    serializer = RecordingSerializer()
    writer = RecordingWriter(tmp_path, serializer)
    target = tmp_path / "existing.json"
    target.write_bytes(b"original")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(
        "app.recording.writer.os.replace",
        fail_replace,
    )

    with pytest.raises(OSError, match="replace failed"):
        writer.write(session(), target)

    assert target.read_bytes() == b"original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_reader_rejects_tampered_file(tmp_path: Path) -> None:
    serializer = RecordingSerializer()
    path = RecordingWriter(tmp_path, serializer).write(session())
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "BROKER_NEUTRAL",
            "TAMPERED",
        ),
        encoding="utf-8",
    )

    with pytest.raises(RecordingFormatError, match="checksum"):
        RecordingReader(serializer).read_archive(path)
