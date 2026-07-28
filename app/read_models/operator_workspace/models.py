from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkspaceSelectionSource(StrEnum):
    TIMELINE = "TIMELINE"
    DECISION = "DECISION"
    TRADE = "TRADE"
    POSITION = "POSITION"
    ORDER = "ORDER"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class OperatorWorkspaceSnapshot:
    selected_symbol: str | None = None
    selected_trade: str | None = None
    selected_timeline_entry: str | None = None
    selected_decision: str | None = None
    selected_position: str | None = None
    selected_order: str | None = None
    selection_source: WorkspaceSelectionSource = (
        WorkspaceSelectionSource.NONE
    )

    def __post_init__(self) -> None:
        if self.selected_symbol is not None:
            _validate_symbol(self.selected_symbol)
        for field_name in (
            "selected_trade",
            "selected_timeline_entry",
            "selected_decision",
            "selected_position",
            "selected_order",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_text(value, field_name)
        if not isinstance(
            self.selection_source,
            WorkspaceSelectionSource,
        ):
            raise TypeError(
                "selection_source must be a WorkspaceSelectionSource"
            )
        required_fields = {
            WorkspaceSelectionSource.TIMELINE: "selected_timeline_entry",
            WorkspaceSelectionSource.DECISION: "selected_decision",
            WorkspaceSelectionSource.TRADE: "selected_trade",
            WorkspaceSelectionSource.POSITION: "selected_position",
            WorkspaceSelectionSource.ORDER: "selected_order",
        }
        required = required_fields.get(self.selection_source)
        if required is not None and getattr(self, required) is None:
            raise ValueError(
                f"{self.selection_source.value} selection requires {required}"
            )
        if (
            self.selection_source
            in {
                WorkspaceSelectionSource.DECISION,
                WorkspaceSelectionSource.TRADE,
                WorkspaceSelectionSource.POSITION,
                WorkspaceSelectionSource.ORDER,
            }
            and self.selected_symbol is None
        ):
            raise ValueError(
                f"{self.selection_source.value} selection requires "
                "selected_symbol"
            )
        if self.selection_source is WorkspaceSelectionSource.NONE and any(
            getattr(self, field_name) is not None
            for field_name in required_fields.values()
        ):
            raise ValueError(
                "NONE selection cannot contain a specific selection"
            )
        if self.selected_trade is not None:
            _validate_symbol(self.selected_trade)
            if self.selected_trade != self.selected_symbol:
                raise ValueError(
                    "selected_trade must match selected_symbol"
                )
        if self.selected_position is not None:
            _validate_symbol(self.selected_position)
            if self.selected_position != self.selected_symbol:
                raise ValueError(
                    "selected_position must match selected_symbol"
                )

    @classmethod
    def initial(cls) -> "OperatorWorkspaceSnapshot":
        return cls()


def _validate_symbol(value: str) -> None:
    _validate_text(value, "symbol")
    if value != value.upper():
        raise ValueError("symbol must be uppercase")


def _validate_text(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise ValueError(f"{field_name} must be stripped non-empty text")
