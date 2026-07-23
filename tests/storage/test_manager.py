from __future__ import annotations

from pathlib import Path

from app.storage import StoragePaths, initialize_storage


def test_initialize_storage_creates_all_directories(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_root(tmp_path / "WebullData")

    result = initialize_storage(paths)

    assert result.root == paths.root
    assert set(result.created) == set(paths.directories())
    assert all(path.is_dir() for path in paths.directories())


def test_initialize_storage_is_idempotent(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_root(tmp_path / "WebullData")

    initialize_storage(paths)
    second_result = initialize_storage(paths)

    assert second_result.created == ()
    assert set(second_result.existing) == set(paths.directories())


def test_initialize_storage_removes_write_probe(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_root(tmp_path / "WebullData")

    initialize_storage(paths)

    assert not (paths.root / ".atp-storage-write-test").exists()
