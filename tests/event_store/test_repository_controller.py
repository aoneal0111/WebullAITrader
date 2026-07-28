from pathlib import Path

from app.composition import create_desktop_composition
from app.composition.desktop_runtime_config import (
    DesktopRuntimeConfiguration,
)
from app.event_store import (
    EventStoreController,
    EventStoreQueryEngine,
    EventStoreRepository,
    EventStoreStatus,
)
from app.recording import (
    RecordingReader,
    RecordingSerializer,
    RecordingWriter,
)
from app.replay import (
    ReplayClock,
    ReplayController,
    ReplayEngine,
    ReplayEventArchive,
)
from app.composition.desktop import ReplayProjectionGraph


def test_repository_incrementally_indexes_only_changed_files(
    tmp_path: Path,
    event_store_sessions,
) -> None:
    serializer = RecordingSerializer()
    writer = RecordingWriter(tmp_path, serializer)
    repository = EventStoreRepository(
        tmp_path,
        RecordingReader(serializer),
    )
    first, _ = event_store_sessions("one")
    second, _ = event_store_sessions("two", 10)
    writer.write(first)

    assert len(repository.refresh().sessions) == 1
    assert repository.files_read == 1
    repository.refresh()
    assert repository.files_read == 1

    writer.write(second)
    assert len(repository.refresh().sessions) == 2
    assert repository.files_read == 2
    repository.close()
    repository.close()


def test_controller_detects_duplicates_and_checksum_corruption(
    tmp_path: Path,
    event_store_sessions,
) -> None:
    serializer = RecordingSerializer()
    writer = RecordingWriter(tmp_path, serializer)
    session, _ = event_store_sessions("duplicate")
    writer.write(session, tmp_path / "first.atlas-session.json")
    writer.write(session, tmp_path / "second.atlas-session.json")
    graph = ReplayProjectionGraph.create()
    clock = ReplayClock()
    replay = ReplayController(
        archive=ReplayEventArchive(),
        clock=clock,
        engine=ReplayEngine(
            graph.bus,
            clock,
            reset_sink=graph.reset,
        ),
    )
    repository = EventStoreRepository(
        tmp_path,
        RecordingReader(serializer),
    )
    controller = EventStoreController(
        repository,
        EventStoreQueryEngine(),
        replay,
    )
    try:
        assert controller.snapshot().status is EventStoreStatus.ERROR
        assert "duplicate session_id" in controller.snapshot().errors[0]
    finally:
        controller.close()
        replay.close()
        graph.close()


def test_controller_reports_checksum_validation_failure(
    tmp_path: Path,
    event_store_sessions,
) -> None:
    serializer = RecordingSerializer()
    session, _ = event_store_sessions("corrupt")
    path = RecordingWriter(tmp_path, serializer).write(session)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "BROKER_NEUTRAL",
            "TAMPERED",
        ),
        encoding="utf-8",
    )
    graph = ReplayProjectionGraph.create()
    clock = ReplayClock()
    replay = ReplayController(
        ReplayEventArchive(),
        clock,
        ReplayEngine(
            graph.bus,
            clock,
            reset_sink=graph.reset,
        ),
    )
    controller = EventStoreController(
        EventStoreRepository(
            tmp_path,
            RecordingReader(serializer),
        ),
        EventStoreQueryEngine(),
        replay,
    )
    try:
        assert controller.snapshot().status is EventStoreStatus.ERROR
        assert "checksum" in controller.snapshot().errors[0]
    finally:
        controller.close()
        replay.close()
        graph.close()


def test_composed_controller_refreshes_queries_and_opens_replay(
    tmp_path: Path,
    event_store_sessions,
) -> None:
    serializer = RecordingSerializer()
    session, _ = event_store_sessions("session-1")
    RecordingWriter(tmp_path, serializer).write(session)
    composition = create_desktop_composition(
        configuration=DesktopRuntimeConfiguration(
            recording_directory=tmp_path,
        )
    )
    try:
        snapshot = composition.event_store_controller.snapshot()
        assert snapshot.status is EventStoreStatus.READY
        assert snapshot.statistics.total_events == 3
        result = composition.event_store_controller.query(
            "order",
            "order-session-1",
        )
        assert result.statistics.matched_events == 2

        composition.event_store_controller.open_replay("session-1")
        assert (
            composition.replay_controller.snapshot().session.event_count
            == 3
        )
    finally:
        composition.close(timeout_seconds=1.0)
