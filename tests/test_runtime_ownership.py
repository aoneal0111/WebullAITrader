from pathlib import Path

from app.gui.runtime_ownership import acquire_runtime_ownership


def test_runtime_ownership_rejects_second_and_recovers_after_release(tmp_path: Path):
    path = tmp_path / "atlas-paper-runtime.lock"
    first = acquire_runtime_ownership(path)
    assert first is not None
    try:
        assert acquire_runtime_ownership(path) is None
    finally:
        first.release()
    second = acquire_runtime_ownership(path)
    assert second is not None
    second.release()

