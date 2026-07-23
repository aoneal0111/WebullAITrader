from datetime import UTC, datetime
from decimal import Decimal

from app.momentum_scanner import (
    AssetClass,
    CatalystType,
    ScannerObservation,
    evaluate_candidate,
    rank_candidates,
)

D = Decimal


def observation(**overrides) -> ScannerObservation:
    values = {
        "symbol": "TEST",
        "timestamp": datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        "price": D("5"),
        "previous_close": D("4"),
        "current_volume": D("6000000"),
        "average_30_day_volume": D("1000000"),
        "float_shares": D("8000000"),
        "bid": D("4.99"),
        "ask": D("5.01"),
        "catalyst": CatalystType.EARNINGS,
        "catalyst_headline": "Company reports earnings results",
        "tradable": True,
        "halted": False,
    }
    values.update(overrides)
    return ScannerObservation(**values)


def test_strong_candidate_qualifies() -> None:
    decision = evaluate_candidate(observation())

    assert decision.qualified is True
    assert decision.symbol == "TEST"
    assert decision.metrics.percentage_change == D("25.00")
    assert decision.metrics.relative_volume == D("6")
    assert decision.score >= 80
    assert decision.failed_rules == ()


def test_candidate_without_news_fails() -> None:
    decision = evaluate_candidate(
        observation(
            catalyst=CatalystType.NONE,
            catalyst_headline=None,
        )
    )

    assert decision.qualified is False
    assert "news_catalyst" in decision.failed_rules


def test_high_float_candidate_fails() -> None:
    decision = evaluate_candidate(
        observation(float_shares=D("50000000"))
    )

    assert decision.qualified is False
    assert "low_float" in decision.failed_rules


def test_wide_spread_candidate_fails() -> None:
    decision = evaluate_candidate(
        observation(bid=D("4.80"), ask=D("5.20"))
    )

    assert decision.qualified is False
    assert "spread" in decision.failed_rules


def test_ranking_excludes_failed_candidates() -> None:
    strong = evaluate_candidate(observation(symbol="AAA"))
    failed = evaluate_candidate(
        observation(
            symbol="BBB",
            catalyst=CatalystType.NONE,
            catalyst_headline=None,
        )
    )

    ranked = rank_candidates((failed, strong))

    assert tuple(item.symbol for item in ranked) == ("AAA",)


def test_scanner_has_no_execution_dependency() -> None:
    import app.momentum_scanner.rules as rules

    source_names = set(rules.__dict__)
    assert "submit_order" not in source_names
    assert "place_order" not in source_names
    assert "cancel_order" not in source_names

def test_stock_at_one_dollar_can_qualify() -> None:
    decision = evaluate_candidate(
        observation(
            price=D("1"),
            previous_close=D("0.80"),
            current_volume=D("6000000"),
            bid=D("0.995"),
            ask=D("1.005"),
            asset_class=AssetClass.STOCK,
        )
    )

    assert decision.qualified is True
    assert "price_range" in decision.passed_rules


def test_stock_below_one_dollar_fails_price_range() -> None:
    decision = evaluate_candidate(
        observation(
            price=D("0.99"),
            previous_close=D("0.75"),
            current_volume=D("6000000"),
            bid=D("0.985"),
            ask=D("0.995"),
            asset_class=AssetClass.STOCK,
        )
    )

    assert decision.qualified is False
    assert "price_range" in decision.failed_rules


def test_stock_above_twenty_dollars_fails_price_range() -> None:
    decision = evaluate_candidate(
        observation(
            price=D("20.01"),
            previous_close=D("16"),
            bid=D("19.99"),
            ask=D("20.03"),
            asset_class=AssetClass.STOCK,
        )
    )

    assert decision.qualified is False
    assert "price_range" in decision.failed_rules


def test_crypto_has_no_maximum_price_restriction() -> None:
    decision = evaluate_candidate(
        observation(
            symbol="BTCUSD",
            price=D("100000"),
            previous_close=D("80000"),
            current_volume=D("100"),
            average_30_day_volume=D("10"),
            float_shares=D("10000000"),
            bid=D("99990"),
            ask=D("100010"),
            asset_class=AssetClass.CRYPTO,
        )
    )

    assert decision.qualified is True
    assert "price_range" in decision.passed_rules


def test_crypto_has_no_minimum_price_restriction() -> None:
    decision = evaluate_candidate(
        observation(
            symbol="DOGEUSD",
            price=D("0.10"),
            previous_close=D("0.08"),
            current_volume=D("100000000"),
            average_30_day_volume=D("10000000"),
            float_shares=D("10000000"),
            bid=D("0.0999"),
            ask=D("0.1001"),
            asset_class=AssetClass.CRYPTO,
        )
    )

    assert decision.qualified is True
    assert "price_range" in decision.passed_rules

