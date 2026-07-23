from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import StorageConfigurationError, StoragePaths


def test_storage_paths_resolve_expected_directories(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_root(tmp_path / "WebullData")

    assert paths.root == (tmp_path / "WebullData").resolve()
    assert paths.market_data_raw == paths.root / "MarketData" / "Raw"
    assert paths.market_data_parquet == paths.root / "MarketData" / "Parquet"
    assert paths.market_data_tick == paths.root / "MarketData" / "Tick"
    assert paths.replay_runs == paths.root / "ReplayRuns"
    assert paths.database == paths.root / "Database"


def test_storage_paths_read_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "WebullData"
    monkeypatch.setenv("WEBULL_DATA_ROOT", str(root))

    paths = StoragePaths.from_env()

    assert paths.root == root.resolve()


def test_blank_storage_root_is_rejected() -> None:
    with pytest.raises(
        StorageConfigurationError,
        match="cannot be blank",
    ):
        StoragePaths.from_root("   ")


def test_relative_storage_root_is_rejected() -> None:
    with pytest.raises(
        StorageConfigurationError,
        match="absolute path",
    ):
        StoragePaths.from_root("data/WebullData")


def test_drive_or_filesystem_root_is_rejected() -> None:
    root = Path.cwd().anchor

    with pytest.raises(
        StorageConfigurationError,
        match="drive root",
    ):
        StoragePaths.from_root(root)


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    paths = StoragePaths.from_root(tmp_path / "WebullData")

    with pytest.raises(
        StorageConfigurationError,
        match="escapes configured root",
    ):
        paths.resolve_within_root("..", "outside")


def test_safe_child_path_is_allowed(tmp_path: Path) -> None:
    paths = StoragePaths.from_root(tmp_path / "WebullData")

    result = paths.resolve_within_root(
        "Evidence",
        "AAPL",
        "record.json",
    )

    assert result == (
        paths.root / "Evidence" / "AAPL" / "record.json"
    ).resolve()
