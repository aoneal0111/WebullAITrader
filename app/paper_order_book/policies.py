"""Immutable validation policy for Paper Order Book coordination."""

from dataclasses import dataclass

from app.paper_order_book.exceptions import PaperOrderBookValidationError


@dataclass(frozen=True, slots=True)
class PaperOrderBookPolicy:
    reject_duplicate_command_ids: bool = True
    reject_non_monotonic_timestamps: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.reject_duplicate_command_ids, bool):
            raise PaperOrderBookValidationError(
                "reject_duplicate_command_ids must be boolean"
            )
        if not isinstance(self.reject_non_monotonic_timestamps, bool):
            raise PaperOrderBookValidationError(
                "reject_non_monotonic_timestamps must be boolean"
            )


__all__ = ("PaperOrderBookPolicy",)
