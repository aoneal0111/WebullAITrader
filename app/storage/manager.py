from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.storage.exceptions import StorageConfigurationError
from app.storage.paths import StoragePaths


@dataclass(frozen=True, slots=True)
class StorageInitializationResult:
    root: Path
    created: tuple[Path, ...]
    existing: tuple[Path, ...]


def initialize_storage(
    paths: StoragePaths | None = None,
) -> StorageInitializationResult:
    storage_paths = paths or StoragePaths.from_env()

    created: list[Path] = []
    existing: list[Path] = []

    for directory in storage_paths.directories():
        if directory.exists():
            if not directory.is_dir():
                raise StorageConfigurationError(
                    f"Storage path exists but is not a directory: {directory}"
                )
            existing.append(directory)
            continue

        try:
            directory.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise StorageConfigurationError(
                f"Unable to create storage directory: {directory}"
            ) from exc

        created.append(directory)

    _verify_write_access(storage_paths.root)

    return StorageInitializationResult(
        root=storage_paths.root,
        created=tuple(created),
        existing=tuple(existing),
    )


def _verify_write_access(root: Path) -> None:
    probe = root / ".atp-storage-write-test"

    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise StorageConfigurationError(
            f"Storage root is not writable: {root}"
        ) from exc
