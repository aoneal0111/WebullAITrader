from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.momentum_scanner.models import (
    AssetClass,
    CatalystStatus,
    CatalystType,
    FloatProvenance,
    ScannerDecision,
    ScannerMetrics,
    ScannerObservation,
)
from app.momentum_scanner.quality import (
    momentum_priority,
    reevaluation_cadence_ms,
    rvol_quality,
    spread_quality,
    velocity_attention,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
BALANCED_POLICY_VERSION = "BALANCED_V1"
CONSERVATIVE_POLICY_VERSION = "CONSERVATIVE_V1"


@dataclass(frozen=True, slots=True)
class MomentumScannerConfig:
    minimum_price: Decimal = Decimal("1")
    maximum_price: Decimal = Decimal("100")
    minimum_percentage_change: Decimal = Decimal("5")
    minimum_relative_volume: Decimal = Decimal("2")
    maximum_float_shares: Decimal = Decimal("50000000")
    minimum_dollar_volume: Decimal = Decimal("250000")
    maximum_spread_percent: Decimal = Decimal("1.50")
    require_catalyst: bool = False
    policy_version: str = BALANCED_POLICY_VERSION

    @classmethod
    def conservative_v1(cls) -> "MomentumScannerConfig":
        """Return the pre-Balanced scanner policy for research reconstruction."""
        return cls(
            maximum_price=Decimal("20"),
            minimum_percentage_change=Decimal("10"),
            minimum_relative_volume=Decimal("5"),
            maximum_float_shares=Decimal("20000000"),
            minimum_dollar_volume=Decimal("5000000"),
            maximum_spread_percent=Decimal("1"),
            require_catalyst=True,
            policy_version=CONSERVATIVE_POLICY_VERSION,
        )


def calculate_metrics(observation: ScannerObservation) -> ScannerMetrics:
    if observation.previous_close <= ZERO:
        raise ValueError("previous_close must be positive")

    percentage_change = (
        (observation.price - observation.previous_close)
        / observation.previous_close
        * HUNDRED
    )

    relative_volume_available = observation.average_30_day_volume > ZERO
    relative_volume = (
        observation.current_volume / observation.average_30_day_volume
        if relative_volume_available else ZERO
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

    rvol_score, rvol_band = rvol_quality(
        relative_volume if relative_volume_available else None,
    )
    spread_score, execution_quality, _block_reason = spread_quality(
        spread_percent, normal_percent=MomentumScannerConfig().maximum_spread_percent,
    )
    velocity_score = velocity_attention(None, None)
    priority, _components = momentum_priority(
        percentage_change=percentage_change,
        rvol_score=rvol_score,
        dollar_volume=dollar_volume,
        velocity_score=velocity_score,
        spread_score=spread_score,
        catalyst_present=observation.catalyst is not CatalystType.NONE,
    )
    return ScannerMetrics(
        percentage_change=percentage_change,
        relative_volume=relative_volume,
        dollar_volume=dollar_volume,
        spread_percent=spread_percent,
        rvol_score=rvol_score,
        rvol_band=rvol_band,
        spread_quality=execution_quality,
        spread_quality_score=spread_score,
        momentum_priority=priority,
        reevaluation_cadence_ms=reevaluation_cadence_ms(priority, velocity_score),
        relative_volume_available=relative_volume_available,
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
    # RVOL is participation evidence, not opportunity admission.
    check(metrics.relative_volume >= ZERO, "relative_volume")
    float_value_is_low = (
        observation.float_shares is not None
        and observation.float_shares <= config.maximum_float_shares
    )
    float_is_authoritative = (
        observation.float_provenance
        is FloatProvenance.AUTHORITATIVE_FLOAT
    )
    float_is_upper_bound = observation.float_provenance in {
        FloatProvenance.SHARES_OUTSTANDING,
        FloatProvenance.MARKET_CAP_PRICE_PROXY,
    }
    float_verified = (
        float_is_authoritative
        or (float_is_upper_bound and float_value_is_low)
    )

    check(float_verified, "float_verified")

    if float_is_authoritative:
        check(float_value_is_low, "low_float")
    elif float_is_upper_bound and float_value_is_low:
        check(True, "low_float")
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
    # A missing/elevated spread lowers execution quality but does not erase a
    # real momentum opportunity. Malformed quotes already fail in
    # calculate_metrics().
    check(True, "spread")

    rvol_score, rvol_band = rvol_quality(
        metrics.relative_volume if metrics.relative_volume_available else None,
    )
    spread_score, execution_quality, execution_block_reason = spread_quality(
        metrics.spread_percent,
        normal_percent=config.maximum_spread_percent,
    )
    velocity_score = velocity_attention(
        metrics.price_velocity_cents_1m,
        metrics.price_velocity_percent_1m,
    )
    priority, priority_components = momentum_priority(
        percentage_change=metrics.percentage_change,
        rvol_score=rvol_score,
        dollar_volume=metrics.dollar_volume,
        velocity_score=velocity_score,
        spread_score=spread_score,
        catalyst_present=observation.catalyst is not CatalystType.NONE,
    )
    metrics = replace(
        metrics,
        rvol_score=rvol_score,
        rvol_band=rvol_band,
        spread_quality=execution_quality,
        spread_quality_score=spread_score,
        momentum_priority=priority,
        reevaluation_cadence_ms=reevaluation_cadence_ms(priority, velocity_score),
    )

    score = _score(observation, metrics)
    technical_passed = tuple(rule for rule in passed if rule != "news_catalyst")
    technical_failed = tuple(rule for rule in failed if rule != "news_catalyst")
    technical_qualifies = not technical_failed
    observation_failed = tuple(
        rule for rule, passed_rule in (
            ("price_range", config.minimum_price <= observation.price <= config.maximum_price),
            ("tradable", observation.tradable),
            ("not_halted", not observation.halted),
        )
        if not passed_rule
    )
    cohorts: list[str] = []
    strict_catalyst_qualifies = (
        technical_qualifies
        and observation.catalyst is not CatalystType.NONE
    )
    if strict_catalyst_qualifies:
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
        policy_version=config.policy_version,
        last_price_timestamp=observation.last_price_timestamp,
        quote_timestamp=observation.quote_timestamp,
        trade_timestamp=observation.trade_timestamp,
        last_price_received_timestamp=observation.last_price_received_timestamp,
        quote_received_timestamp=observation.quote_received_timestamp,
        observation_eligible=not observation_failed,
        observation_failed_rules=observation_failed,
        participation_quality=rvol_band,
        execution_quality=execution_quality,
        execution_block_reason=execution_block_reason,
        momentum_priority_components=priority_components,
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
