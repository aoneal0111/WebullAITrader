from __future__ import annotations

from .models import ExperimentResult


class ExperimentRepository:
    def __init__(self) -> None:
        self._results: dict[str, ExperimentResult] = {}
        self._closed = False

    def save(self, result: ExperimentResult) -> None:
        self._ensure_open()
        if not isinstance(result, ExperimentResult):
            raise TypeError("result must be ExperimentResult")
        identifier = result.experiment.experiment_id
        if identifier in self._results:
            raise ValueError(f"duplicate experiment_id: {identifier}")
        self._results[identifier] = result

    def get(self, experiment_id: str) -> ExperimentResult:
        self._ensure_open()
        _identifier(experiment_id)
        try:
            return self._results[experiment_id]
        except KeyError as exc:
            raise KeyError(f"unknown experiment_id: {experiment_id}") from exc

    def list(self) -> tuple[ExperimentResult, ...]:
        self._ensure_open()
        return tuple(
            self._results[key]
            for key in sorted(self._results)
        )

    def clear(self) -> None:
        self._ensure_open()
        self._results.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("experiment repository is closed")


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("experiment_id must be stripped non-empty text")
