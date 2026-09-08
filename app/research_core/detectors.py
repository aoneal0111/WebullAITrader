"""Generic detector typing and an ordered, immutable registry."""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar


ContextT = TypeVar("ContextT", contravariant=True)
DetectionT = TypeVar("DetectionT", covariant=True)
RegistryContextT = TypeVar("RegistryContextT")
RegistryDetectionT = TypeVar("RegistryDetectionT")
DEFAULT_MAXIMUM_DETECTORS = 128


class DetectorDefinition(Protocol):
    strategy_id: str
    research_only: bool


class Detector(Protocol[ContextT, DetectionT]):
    definition: DetectorDefinition

    def detect(self, context: ContextT) -> DetectionT: ...


class DetectorRegistry(Generic[RegistryContextT, RegistryDetectionT]):
    """Preserve caller-supplied detector order without dynamic registration."""

    def __init__(
        self,
        detectors: tuple[Detector[RegistryContextT, RegistryDetectionT], ...],
        *,
        maximum_detectors: int = DEFAULT_MAXIMUM_DETECTORS,
    ) -> None:
        if maximum_detectors <= 0 or len(detectors) > maximum_detectors:
            raise ValueError("detector registry exceeds its positive explicit bound")
        identities = [item.definition.strategy_id for item in detectors]
        if len(identities) != len(set(identities)):
            raise ValueError("detector strategy IDs must be unique")
        if any(not item.definition.research_only for item in detectors):
            raise ValueError("all registered detectors must be research-only")
        self._detectors = detectors

    @property
    def detectors(self) -> tuple[Detector[RegistryContextT, RegistryDetectionT], ...]:
        return self._detectors

    def evaluate(self, context: RegistryContextT) -> tuple[RegistryDetectionT, ...]:
        return tuple(detector.detect(context) for detector in self._detectors)
