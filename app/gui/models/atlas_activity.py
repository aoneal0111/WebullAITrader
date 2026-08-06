from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AtlasActivityRow:
    label: str
    value: str
    tone: str = "neutral"


@dataclass(frozen=True, slots=True)
class AtlasActivitySnapshot:
    """Runtime/read-model facts shown beside Atlas Focus."""

    rows: tuple[AtlasActivityRow, ...] = ()

    @classmethod
    def initial(cls) -> "AtlasActivitySnapshot":
        return cls()


__all__ = ["AtlasActivityRow", "AtlasActivitySnapshot"]
