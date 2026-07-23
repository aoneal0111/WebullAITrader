from __future__ import annotations

from typing import Protocol, Sequence, TypeVar, runtime_checkable

from app.evidence.models import Evidence


SnapshotT = TypeVar("SnapshotT", contravariant=True)


@runtime_checkable
class EvidenceProvider(Protocol[SnapshotT]):
    @property
    def name(self) -> str:
        """Stable provider name used for auditing and attribution."""
        ...

    def generate(
        self,
        snapshot: SnapshotT,
    ) -> Sequence[Evidence]:
        """Generate zero or more immutable evidence records."""
        ...
