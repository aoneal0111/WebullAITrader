from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.momentum_scanner.models import (
    AssetClass,
    CatalystStatus,
    CatalystType,
    ScannerDecision,
    ScannerMetrics,
    ScannerObservation,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class MomentumScannerConfig:
    minimum_price: Decimal = Decimal("1")
    maximum_price: Decimal = Decimal("20")
    minimum_percentage_change: Decimal = Decimal("10")
    minimum_relative_volume: Decimal = Decimal("5")
    maximum_float_shares: Decimal = Decimal("20000000")
    minimum_dollar_volume: Decimal = Decimal("5000000")
    maximum_spread_percent: Decimal = Decimal("1")
    require_catalyst: bool = True


def calculate_metrics(observation: ScannerObservation) -> ScannerMetrics:
    if observation.previous_close <= ZERO:
        raise ValueError("previous_close must be positive")

    if observation.average_30_day_volume <= ZERO:
        raise ValueError("average_30_day_volume must be positive")

    percentage_change = (
        (observation.price - observation.previous_close)
        / observation.previous_close
        * HUNDRED
    )

    relative_volume = (
        observation.current_volume / observation.average_30_day_volume
    )

    dollar_volume = observation.price * observation.current_volume

    spread_percent: Decimal | None = None
    if observation.bid is not None and observation.ask is not None:
        if observation.bid <= ZERO or observation.ask <= ZERO:
            raise ValueError("bid and ask must be positive")

        if observation.ask < observation.bid:
            raise ValueError("ask cannot be lower than bid")

        midpoint = (observation.bid + observation.ask) / Decimal("2")
        spread_percent = (
            (observation.ask - observation.bid) / midpoint * HUNDRED
        )

    return ScannerMetrics(
        percentage_change=percentage_change,
        relative_volume=relative_volume,
        dollar_volume=dollar_volume,
        spread_percent=spread_percent,
    )


def evaluate_candidate(
    observation: ScannerObservation,
    config: MomentumScannerConfig = MomentumScannerConfig(),
) -> ScannerDecision:
    symbol = observation.symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol is required")

    if observation.timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")

    metrics = calculate_metrics(observation)
    passed: list[str] = []
    failed: list[str] = []

    def check(condition: bool, label: str) -> None:
        (passed if condition else failed).append(label)

    check(
        observation.asset_class is AssetClass.CRYPTO
        or config.minimum_price <= observation.price <= config.maximum_price,
        "price_range",
    )
    check(
        metrics.percentage_change >= config.minimum_percentage_change,
        "percentage_change",
    )
    check(
        metrics.relative_volume >= config.minimum_relative_volume,
        "relative_volume",
    )
    check(
        observation.float_shares is not None
        and observation.float_shares <= config.maximum_float_shares,
        "low_float",
    )
    check(
        not config.require_catalyst
        or observation.catalyst is not CatalystType.NONE,
        "news_catalyst",
    )
    check(observation.tradable, "tradable")
    check(not observation.halted, "not_halted")
    check(
        metrics.dollar_volume >= config.minimum_dollar_volume,
        "dollar_volume",
    )
    check(
        metrics.spread_percent is not None
        and metrics.spread_percent <= config.maximum_spread_percent,
        "spread",
    )

    score = _score(observation, metrics)
    technical_passed = tuple(rule for rule in passed if rule != "news_catalyst")
    technical_failed = tuple(rule for rule in failed if rule != "news_catalyst")
    technical_qualifies = not technical_failed
    cohorts: list[str] = []
    if not failed:
        cohorts.append("A_STRICT_CATALYST")
    if technical_qualifies:
        cohorts.append("B_TECHNICAL_ONLY")
        if (
            observation.catalyst_status is CatalystStatus.FALSE
            or observation.catalyst is CatalystType.NONE
        ):
            cohorts.append("C_NO_CATALYST")
        if len(set(observation.corroborating_sources)) >= 2:
            cohorts.append("D_CORROBORATED_CATALYST")
        if (
            observation.catalyst_status is CatalystStatus.TRUE
            and observation.catalyst
            in {CatalystType.EARNINGS, CatalystType.SEC_FILING}
        ):
            cohorts.append("E_STRONG_PRIMARY_CATALYST")

    return ScannerDecision(
        symbol=symbol,
        qualified=not failed,
        score=score,
        metrics=metrics,
        passed_rules=tuple(passed),
        failed_rules=tuple(failed),
        timestamp=observation.timestamp,
        price=observation.price,
        current_volume=observation.current_volume,
        catalyst=observation.catalyst,
        catalyst_headline=observation.catalyst_headline,
        catalyst_status=observation.catalyst_status,
        diagnostic_rule_values=(
            ("price_range", format(observation.price, "f")),
            ("percentage_change", format(metrics.percentage_change, "f")),
            ("relative_volume", format(metrics.relative_volume, "f")),
            (
                "low_float",
                "missing"
                if observation.float_shares is None
                else format(observation.float_shares, "f"),
            ),
            ("news_catalyst", observation.catalyst_status.value),
            ("tradable", str(observation.tradable).lower()),
            ("not_halted", str(not observation.halted).lower()),
            ("dollar_volume", format(metrics.dollar_volume, "f")),
            (
                "spread",
                "missing"
                if metrics.spread_percent is None
                else format(metrics.spread_percent, "f"),
            ),
        ),
        technical_qualifies_without_catalyst=technical_qualifies,
        technical_passed_rules=technical_passed,
        technical_failed_rules=technical_failed,
        cohort_flags=tuple(cohorts),
        previous_close=observation.previous_close,
        average_30_day_volume=observation.average_30_day_volume,
        float_shares=observation.float_shares,
        bid=observation.bid,
        ask=observation.ask,
        tradable=observation.tradable,
        halted=observation.halted,
        catalyst_source=observation.catalyst_source,
        catalyst_published_at=observation.catalyst_published_at,
        catalyst_source_url=observation.catalyst_source_url,
        corroborating_sources=observation.corroborating_sources,
        catalyst_evidence_count=observation.catalyst_evidence_count,
        catalyst_event_count=observation.catalyst_event_count,
    )


def _score(
    observation: ScannerObservation,
    metrics: ScannerMetrics,
) -> int:
    score = 0

    score += min(20, int(metrics.percentage_change))
    score += min(20, int(metrics.relative_volume * Decimal("2")))
    score += 15 if observation.catalyst is not CatalystType.NONE else 0
    score += 10 if metrics.dollar_volume >= Decimal("5000000") else 0

    if observation.float_shares is not None:
        if observation.float_shares <= Decimal("5000000"):
            score += 20
        elif observation.float_shares <= Decimal("10000000"):
            score += 17
        elif observation.float_shares <= Decimal("20000000"):
            score += 12

    if metrics.spread_percent is not None:
        if metrics.spread_percent <= Decimal("0.25"):
            score += 15
        elif metrics.spread_percent <= Decimal("0.50"):
            score += 12
        elif metrics.spread_percent <= Decimal("1"):
            score += 8

    return min(score, 100)

