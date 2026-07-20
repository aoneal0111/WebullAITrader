from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtesting import (
    HistoricalExitMethod,
    compare_historical_exit_methods,
    evaluate_fixed_target_entries,
    evaluate_trailing_entries,
)
from app.backtesting.models import (
    HistoricalCandle,
    HistoricalFrame,
)
from app.order_compliance.models import (
    MarketComplianceState,
    MarketStatus,
    SymbolStatus,
)
from app.trade_management import TrailingExitConfig


D = Decimal
START = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)


def _frames(
    prices: tuple[Decimal, ...],
) -> tuple[HistoricalFrame, ...]:
    frames = []

    for index, price in enumerate(prices):
        opened = START + timedelta(minutes=index * 2)
        closed = opened + timedelta(minutes=1)

        candle = HistoricalCandle(
            symbol="TEST",
            open_timestamp=opened,
            close_timestamp=closed,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=D("1000"),
        )

        market = MarketComplianceState(
            "TEST",
            MarketStatus.OPEN,
            SymbolStatus.TRADABLE,
            opened - timedelta(hours=1),
            closed + timedelta(hours=5),
            opened - timedelta(hours=2),
            closed + timedelta(hours=8),
            D("0.01"),
            closed,
            price,
        )

        frames.append(
            HistoricalFrame(
                candle=candle,
                market_state=market,
                execution_bid=price,
                execution_ask=price,
                execution_last=price,
            )
        )

    return tuple(frames)


def test_trailing_exit_outperforms_fixed_target_on_long_trend() -> None:
    frames = _frames((
        D("100"),
        D("103"),
        D("105"),
        D("110"),
        D("118"),
        D("116"),
        D("115"),
    ))

    result = compare_historical_exit_methods(
        frames,
        (0,),
    )

    assert result.fixed_target.number_of_trades == 1
    assert result.trailing_stop.number_of_trades == 1

    fixed_trade = result.fixed_target.trades[0]
    trailing_trade = result.trailing_stop.trades[0]

    assert fixed_trade.exit_method is HistoricalExitMethod.FIXED_TARGET
    assert fixed_trade.exit_price == D("105")

    assert (
        trailing_trade.exit_method
        is HistoricalExitMethod.TRAILING_STOP
    )
    assert trailing_trade.exit_price > fixed_trade.exit_price
    assert result.pnl_improvement > D("0")
    assert result.return_improvement_percent > D("0")


def test_break_even_stop_protects_trade() -> None:
    frames = _frames((
        D("100"),
        D("102"),
        D("101"),
        D("100"),
        D("99"),
    ))

    result = evaluate_trailing_entries(
        frames,
        (0,),
    )

    trade = result.trades[0]

    assert trade.exit_method is HistoricalExitMethod.TRAILING_STOP
    assert trade.exit_price == D("100")
    assert trade.realized_pnl == D("0")
    assert result.break_even_trades == 1


def test_fixed_target_metrics_cover_multiple_entries() -> None:
    frames = _frames((
        D("100"),
        D("105"),
        D("110"),
        D("100"),
        D("95"),
        D("90"),
    ))

    result = evaluate_fixed_target_entries(
        frames,
        (0, 2),
        target_gain_percent=D("5"),
    )

    assert result.number_of_trades == 2
    assert result.winning_trades == 1
    assert result.losing_trades == 1
    assert result.win_rate == D("50")
    assert result.profit_factor is not None
    assert result.expectancy is not None
    assert result.maximum_drawdown_percent > D("0")
    assert result.average_holding_candles > D("0")


def test_wider_trailing_stop_holds_through_small_pullback() -> None:
    frames = _frames((
        D("100"),
        D("105"),
        D("110"),
        D("108"),
        D("112"),
        D("107"),
    ))

    config = TrailingExitConfig(
        activation_gain_percent=D("3"),
        trailing_distance_percent=D("5"),
        break_even_trigger_percent=D("1.5"),
        minimum_stop_raise_percent=D("0.1"),
    )

    result = evaluate_trailing_entries(
        frames,
        (0,),
        config=config,
    )

    trade = result.trades[0]

    assert trade.exit_method is HistoricalExitMethod.END_OF_DATA
    assert trade.exit_price == D("107")
    assert trade.highest_price == D("112")
    assert trade.realized_pnl == D("7")


def test_invalid_final_entry_index_is_rejected() -> None:
    frames = _frames((
        D("100"),
        D("101"),
    ))

    with pytest.raises(
        ValueError,
        match="at least one future frame",
    ):
        evaluate_trailing_entries(
            frames,
            (1,),
        )


def test_empty_entry_list_returns_empty_metrics() -> None:
    frames = _frames((
        D("100"),
        D("101"),
    ))

    result = evaluate_trailing_entries(
        frames,
        (),
    )

    assert result.number_of_trades == 0
    assert result.total_realized_pnl == D("0")
    assert result.win_rate == D("0")
    assert result.profit_factor is None
    assert result.expectancy is None
