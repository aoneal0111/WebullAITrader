from decimal import Decimal

from app.trade_management import (
    ExitMethod,
    TrailingExitConfig,
    compare_exit_methods,
    simulate_trailing_stop,
)


D = Decimal


def test_trailing_stop_holds_beyond_fixed_five_percent() -> None:
    prices = (
        D("100"),
        D("101"),
        D("103"),
        D("105"),
        D("108"),
        D("112"),
        D("118"),
        D("116"),
        D("115.50"),
    )

    comparison = compare_exit_methods(prices)

    assert (
        comparison.fixed_target.exit_method
        is ExitMethod.FIXED_TARGET
    )
    assert comparison.fixed_target.exit_price == D("105")
    assert comparison.fixed_target.gain_percent == D("5.00")

    assert (
        comparison.trailing_stop.exit_method
        is ExitMethod.TRAILING_STOP
    )
    assert comparison.trailing_stop.exit_price == D("115.50")
    assert comparison.trailing_stop.gain_percent == D("15.50")
    assert comparison.trailing_improvement_percent == D("10.50")


def test_trailing_stop_protects_break_even() -> None:
    prices = (
        D("100"),
        D("102"),
        D("101"),
        D("100"),
    )

    result = simulate_trailing_stop(prices)

    assert result.exit_method is ExitMethod.TRAILING_STOP
    assert result.exit_price == D("100")
    assert result.gain_percent == D("0.00")


def test_trailing_stop_does_not_claim_exit_without_stop_hit() -> None:
    prices = (
        D("100"),
        D("101"),
        D("102"),
        D("103"),
        D("104"),
    )

    result = simulate_trailing_stop(prices)

    assert result.exit_method is ExitMethod.END_OF_DATA
    assert result.exit_price == D("104")
    assert result.gain_percent == D("4.00")


def test_wider_trailing_stop_holds_longer() -> None:
    prices = (
        D("100"),
        D("105"),
        D("110"),
        D("108"),
        D("112"),
        D("107"),
    )

    config = TrailingExitConfig(
        activation_gain_percent=D("3"),
        trailing_distance_percent=D("5"),
        break_even_trigger_percent=D("1.5"),
        minimum_stop_raise_percent=D("0.1"),
    )

    result = simulate_trailing_stop(
        prices,
        config=config,
    )

    assert result.exit_method is ExitMethod.END_OF_DATA
    assert result.highest_price == D("112")
    assert result.exit_price == D("107")
    assert result.gain_percent == D("7.00")

