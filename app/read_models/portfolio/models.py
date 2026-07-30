from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PortfolioHighlight:
    symbol: str
    value: str

    def __post_init__(self) -> None:
        for field_name in ("symbol", "value"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"portfolio highlight {field_name} is required"
                )


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    total_market_value: str | None
    total_cost_basis: str
    realized_pnl: str | None
    unrealized_pnl: str | None
    total_pnl: str | None
    gross_exposure: str | None
    long_exposure: str | None
    short_exposure: str | None
    open_positions: int
    working_orders: int
    winning_positions: int | None
    losing_positions: int | None
    largest_position: PortfolioHighlight | None
    largest_unrealized_gain: PortfolioHighlight | None
    largest_unrealized_loss: PortfolioHighlight | None

    def __post_init__(self) -> None:
        optional_values = (
            self.total_market_value,
            self.realized_pnl,
            self.unrealized_pnl,
            self.total_pnl,
            self.gross_exposure,
            self.long_exposure,
            self.short_exposure,
        )
        if not isinstance(self.total_cost_basis, str):
            raise TypeError("total_cost_basis must be text")
        if not self.total_cost_basis.strip():
            raise ValueError("total_cost_basis must not be empty")
        if any(
            value is not None
            and (not isinstance(value, str) or not value.strip())
            for value in optional_values
        ):
            raise ValueError(
                "optional portfolio values must be None or non-empty text"
            )
        required_counts = (
            self.open_positions,
            self.working_orders,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in required_counts
        ):
            raise ValueError("portfolio counts must be nonnegative integers")
        for value in (self.winning_positions, self.losing_positions):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    "portfolio win/loss counts must be nonnegative or None"
                )
        for highlight in (
            self.largest_position,
            self.largest_unrealized_gain,
            self.largest_unrealized_loss,
        ):
            if highlight is not None and not isinstance(
                highlight,
                PortfolioHighlight,
            ):
                raise TypeError(
                    "portfolio highlights must be PortfolioHighlight or None"
                )

    @classmethod
    def initial(cls) -> "PortfolioSummary":
        return cls(
            total_market_value="0",
            total_cost_basis="0",
            realized_pnl="0",
            unrealized_pnl="0",
            total_pnl="0",
            gross_exposure="0",
            long_exposure="0",
            short_exposure="0",
            open_positions=0,
            working_orders=0,
            winning_positions=0,
            losing_positions=0,
            largest_position=None,
            largest_unrealized_gain=None,
            largest_unrealized_loss=None,
        )
