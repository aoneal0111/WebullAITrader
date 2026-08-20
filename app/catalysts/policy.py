from __future__ import annotations

from dataclasses import dataclass

from app.momentum_scanner.models import CatalystType


@dataclass(frozen=True, slots=True)
class CatalystPriorityPolicy:
    """Immutable highest-to-lowest ordering for catalyst selection."""

    ordered_types: tuple[CatalystType, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.ordered_types, tuple):
            raise TypeError("ordered_types must be a tuple")
        if any(not isinstance(item, CatalystType) for item in self.ordered_types):
            raise TypeError("ordered_types must contain only CatalystType values")
        if len(set(self.ordered_types)) != len(self.ordered_types):
            raise ValueError("ordered_types must not contain duplicates")
        if set(self.ordered_types) != set(CatalystType):
            raise ValueError("ordered_types must contain every CatalystType")

    def priority(self, catalyst_type: CatalystType) -> int:
        """Return a larger number for a more preferred catalyst type."""

        return len(self.ordered_types) - self.ordered_types.index(catalyst_type)


_NEUTRAL_TYPES = tuple(
    sorted(
        (
            item
            for item in CatalystType
            if item
            not in {
                CatalystType.EARNINGS,
                CatalystType.SEC_FILING,
                CatalystType.NONE,
            }
        ),
        key=lambda item: item.value,
    )
)


DEFAULT_CATALYST_PRIORITY_POLICY = CatalystPriorityPolicy(
    (
        CatalystType.EARNINGS,
        CatalystType.SEC_FILING,
        *_NEUTRAL_TYPES,
        CatalystType.NONE,
    )
)


__all__ = [
    "CatalystPriorityPolicy",
    "DEFAULT_CATALYST_PRIORITY_POLICY",
]
