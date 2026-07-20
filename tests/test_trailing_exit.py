from decimal import Decimal

from app.trade_management import (
    ExitAction,
    ManagedLongPosition,
    TrailingExitConfig,
    evaluate_long_position,
)


D = Decimal


def position(
    *,
    highest: str = "100",
    stop: str | None = None,
    activated: bool = False,
) -> ManagedLongPosition:
    return ManagedLongPosition(
        symbol="AAPL",
        entry_price=D("100"),
        quantity=D("10"),
        highest_price=D(highest),
        protective_stop=(
            D(stop) if stop is not None else None
        ),
        trailing_activated=activated,
    )


def test_holds_before_profit_protection_activates() -> None:
    decision = evaluate_long_position(
        position(),
        D("101"),
    )

    assert decision.action is ExitAction.HOLD
    assert decision.position.highest_price == D("101")
    assert decision.position.protective_stop is None
    assert not decision.position.trailing_activated


def test_moves_stop_to_break_even() -> None:
    decision = evaluate_long_position(
        position(),
        D("102"),
    )

    assert decision.action is ExitAction.RAISE_STOP
    assert decision.recommended_stop == D("100.00")
    assert not decision.position.trailing_activated


def test_activates_trailing_stop_after_three_percent_gain() -> None:
    decision = evaluate_long_position(
        position(),
        D("105"),
    )

    assert decision.action is ExitAction.RAISE_STOP
    assert decision.position.trailing_activated
    assert decision.position.highest_price == D("105")
    assert decision.recommended_stop == D("102.90")


def test_stop_rises_as_price_rises() -> None:
    first = evaluate_long_position(
        position(),
        D("105"),
    )

    second = evaluate_long_position(
        first.position,
        D("112"),
    )

    assert second.action is ExitAction.RAISE_STOP
    assert second.position.highest_price == D("112")
    assert second.recommended_stop == D("109.76")


def test_stop_never_moves_down_during_pullback() -> None:
    current = position(
        highest="112",
        stop="109.76",
        activated=True,
    )

    decision = evaluate_long_position(
        current,
        D("111"),
    )

    assert decision.action is ExitAction.HOLD
    assert decision.position.highest_price == D("112")
    assert decision.recommended_stop == D("109.76")


def test_exits_when_price_reaches_stop() -> None:
    current = position(
        highest="118",
        stop="115.64",
        activated=True,
    )

    decision = evaluate_long_position(
        current,
        D("115.64"),
    )

    assert decision.action is ExitAction.EXIT
    assert decision.recommended_stop == D("115.64")


def test_custom_trailing_distance() -> None:
    config = TrailingExitConfig(
        activation_gain_percent=D("2"),
        trailing_distance_percent=D("4"),
        break_even_trigger_percent=D("1"),
        minimum_stop_raise_percent=D("0.10"),
    )

    decision = evaluate_long_position(
        position(),
        D("110"),
        config,
    )

    assert decision.recommended_stop == D("105.60")


def test_invalid_negative_configuration_is_rejected() -> None:
    try:
        TrailingExitConfig(
            activation_gain_percent=D("-1"),
        )
    except ValueError as exc:
        assert "activation_gain_percent" in str(exc)
    else:
        raise AssertionError("expected ValueError")
