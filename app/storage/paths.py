from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.storage.exceptions import StorageConfigurationError


@dataclass(frozen=True, slots=True)
class StoragePaths:
    root: Path
    market_data: Path
    market_data_raw: Path
    market_data_parquet: Path
    market_data_tick: Path
    news: Path
    evidence: Path
    replay_runs: Path
    backtests: Path
    models: Path
    logs: Path
    database: Path
    cache: Path
    archive: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "StoragePaths":
        normalized_root = _normalize_root(root)

        return cls(
            root=normalized_root,
            market_data=normalized_root / "MarketData",
            market_data_raw=normalized_root / "MarketData" / "Raw",
            market_data_parquet=normalized_root / "MarketData" / "Parquet",
            market_data_tick=normalized_root / "MarketData" / "Tick",
            news=normalized_root / "News",
            evidence=normalized_root / "Evidence",
            replay_runs=normalized_root / "ReplayRuns",
            backtests=normalized_root / "Backtests",
            models=normalized_root / "Models",
            logs=normalized_root / "Logs",
            database=normalized_root / "Database",
            cache=normalized_root / "Cache",
            archive=normalized_root / "Archive",
        )

    @classmethod
    def from_env(cls) -> "StoragePaths":
        load_dotenv()

        value = os.getenv("WEBULL_DATA_ROOT", "").strip()
        if not value:
            raise StorageConfigurationError(
                "WEBULL_DATA_ROOT is required"
            )

        return cls.from_root(value)

    def directories(self) -> tuple[Path, ...]:
        return (
            self.root,
            self.market_data,
            self.market_data_raw,
            self.market_data_parquet,
            self.market_data_tick,
            self.news,
            self.evidence,
            self.replay_runs,
            self.backtests,
            self.models,
            self.logs,
            self.database,
            self.cache,
            self.archive,
        )

    def resolve_within_root(self, *parts: str) -> Path:
        if not parts:
            raise StorageConfigurationError(
                "At least one storage path component is required"
            )

        candidate = self.root.joinpath(*parts).resolve(strict=False)

        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise StorageConfigurationError(
                f"Storage path escapes configured root: {candidate}"
            ) from exc

        return candidate


def _normalize_root(root: str | Path) -> Path:
    raw_value = str(root).strip()

    if not raw_value:
        raise StorageConfigurationError(
            "Storage root cannot be blank"
        )

    candidate = Path(raw_value).expanduser()

    if not candidate.is_absolute():
        raise StorageConfigurationError(
            "Storage root must be an absolute path"
        )

    resolved = candidate.resolve(strict=False)

    if resolved == Path(resolved.anchor):
        raise StorageConfigurationError(
            "Storage root cannot be the drive root"
        )

    return resolved
