from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Iterable, Sequence, TypeVar

from app.evidence.exceptions import (
    EvidenceCollectionError,
    EvidenceValidationError,
)
from app.evidence.models import Evidence
from app.evidence.provider import EvidenceProvider


SnapshotT = TypeVar("SnapshotT")


@dataclass(frozen=True, slots=True)
class EvidenceCollection:
    evidence: tuple[Evidence, ...]
    provider_names: tuple[str, ...]

    def for_symbol(self, symbol: str) -> tuple[Evidence, ...]:
        normalized = symbol.strip().upper()

        if not normalized:
            raise EvidenceValidationError(
                "symbol cannot be blank"
            )

        return tuple(
            item
            for item in self.evidence
            if item.symbol == normalized
        )


class EvidenceCollector(Generic[SnapshotT]):
    def __init__(
        self,
        providers: Iterable[EvidenceProvider[SnapshotT]],
    ) -> None:
        self._providers = tuple(providers)

        names = tuple(
            _validate_provider_name(provider.name)
            for provider in self._providers
        )

        if len(set(names)) != len(names):
            raise EvidenceValidationError(
                "Evidence provider names must be unique"
            )

        self._provider_names = names

    @property
    def provider_names(self) -> tuple[str, ...]:
        return self._provider_names

    def collect(
        self,
        snapshot: SnapshotT,
    ) -> EvidenceCollection:
        collected: list[Evidence] = []

        for provider in self._providers:
            try:
                generated = provider.generate(snapshot)
            except Exception as exc:
                raise EvidenceCollectionError(
                    f"Evidence provider failed: {provider.name}"
                ) from exc

            if generated is None:
                raise EvidenceCollectionError(
                    f"Evidence provider returned None: {provider.name}"
                )

            for item in generated:
                if not isinstance(item, Evidence):
                    raise EvidenceCollectionError(
                        "Evidence provider returned an invalid item: "
                        f"{provider.name}"
                    )

                if item.source != provider.name:
                    raise EvidenceCollectionError(
                        "Evidence source does not match provider name: "
                        f"{provider.name}"
                    )

                collected.append(item)

        return EvidenceCollection(
            evidence=tuple(collected),
            provider_names=self._provider_names,
        )


def _validate_provider_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceValidationError(
            "Evidence provider name cannot be blank"
        )

    return value.strip()
