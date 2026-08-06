"""Deterministic calculations over authoritative portfolio projections."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import combinations
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from .configuration import PortfolioIntelligenceConfiguration, PortfolioRiskLimits
from .models import (
    AttributionEntry, AttributionSummary, ConcentrationSummary, CorrelationPair,
    CorrelationSummary, ExposureSummary, OrderSide, PerformanceSummary,
    PortfolioFill, PortfolioIntelligenceInput, PortfolioIntelligenceSnapshot,
    PortfolioPosition, PortfolioRiskBudgetStatus, PriceObservation,
    RiskBudgetClassification, RiskBudgetMetric, ZERO,
)


HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class _ClosedTrade:
    symbol: str
    opened_at: datetime
    closed_at: datetime
    realized_pnl: Decimal
    strategy_id: str
    decision_type: str
    asset_class: str
    session_id: str


class PearsonCorrelationAnalyzer:
    """Pairwise Pearson correlation using aligned consecutive price returns."""

    def __init__(self, configuration: PortfolioIntelligenceConfiguration = PortfolioIntelligenceConfiguration()) -> None:
        self.configuration = configuration

    def analyze(
        self,
        positions: tuple[PortfolioPosition, ...],
        history: Mapping[str, tuple[PriceObservation, ...]],
    ) -> CorrelationSummary:
        pairs: list[CorrelationPair] = []
        excluded = 0
        symbols = sorted({position.symbol for position in positions})
        for first, second in combinations(symbols, 2):
            first_returns = _returns(history.get(first, ()), self.configuration.correlation_lookback)
            second_returns = _returns(history.get(second, ()), self.configuration.correlation_lookback)
            overlap = sorted(set(first_returns) & set(second_returns))
            if len(overlap) < self.configuration.minimum_correlation_observations:
                excluded += 1
                continue
            correlation = _pearson(
                tuple(first_returns[key] for key in overlap),
                tuple(second_returns[key] for key in overlap),
            )
            if correlation is None:
                excluded += 1
                continue
            pairs.append(CorrelationPair(first, second, correlation, len(overlap)))
        pairs.sort(key=lambda pair: (pair.first_symbol, pair.second_symbol))
        highest = max(pairs, key=lambda pair: (abs(pair.correlation), pair.first_symbol, pair.second_symbol), default=None)
        average = sum((pair.correlation for pair in pairs), ZERO) / Decimal(len(pairs)) if pairs else None
        high = tuple(pair for pair in pairs if abs(pair.correlation) >= self.configuration.high_correlation_threshold)
        market_values = {position.symbol: position.market_value for position in positions}
        gross = _complete_sum(abs(value) if value is not None else None for value in market_values.values())
        clustered = {symbol for pair in high for symbol in (pair.first_symbol, pair.second_symbol)}
        cluster_percentage = (
            sum((abs(market_values[symbol]) for symbol in clustered if market_values[symbol] is not None), ZERO) / gross
            if clustered and gross not in (None, ZERO)
            else ZERO if gross == ZERO
            else None
        )
        return CorrelationSummary(highest, average, high, cluster_percentage, len(pairs), excluded)


class PortfolioIntelligenceService:
    """Pure snapshot builder; it never queries a broker or mutates source records."""

    def __init__(
        self,
        configuration: PortfolioIntelligenceConfiguration = PortfolioIntelligenceConfiguration(),
        limits: PortfolioRiskLimits = PortfolioRiskLimits(),
        correlation_analyzer: PearsonCorrelationAnalyzer | None = None,
    ) -> None:
        self.configuration = configuration
        self.limits = limits
        self.correlation_analyzer = correlation_analyzer or PearsonCorrelationAnalyzer(configuration)

    def build(self, source: PortfolioIntelligenceInput) -> PortfolioIntelligenceSnapshot:
        if not isinstance(source, PortfolioIntelligenceInput):
            raise TypeError("source must be PortfolioIntelligenceInput")
        generated_at = source.generated_at or _latest_time(source) or datetime.now(timezone.utc)
        exposure = _exposure(source)
        concentration = _concentration(source.positions, exposure)
        correlation = self.correlation_analyzer.analyze(source.positions, source.price_history)
        trades = _group_trades(source.fills)
        performance = _performance(source, trades, generated_at, self.configuration)
        attribution = _attribution(source, trades)
        realized = performance.cumulative_realized_pnl
        unrealized = _complete_sum(position.unrealized_pnl for position in source.positions)
        total = realized + unrealized if realized is not None and unrealized is not None else None
        risk = _risk_budget(exposure, performance, self.limits, self.configuration.risk_budget_warning_percentage)
        observations = _observations(concentration, correlation, risk, self.configuration)
        return PortfolioIntelligenceSnapshot(
            account=source.account,
            generated_at=generated_at,
            positions=tuple(sorted(source.positions, key=lambda position: position.symbol)),
            working_orders=tuple(sorted(source.working_orders, key=lambda order: order.order_id)),
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_pnl=total,
            exposure=exposure,
            concentration=concentration,
            correlation=correlation,
            performance=performance,
            attribution=attribution,
            risk_budget=risk,
            observations=observations,
        )


def _exposure(source: PortfolioIntelligenceInput) -> ExposureSummary:
    values = {position.symbol: position.market_value for position in source.positions}
    long_exposure = _complete_sum(value if value is not None and value > ZERO else ZERO if value is not None else None for value in values.values())
    short_exposure = _complete_sum(abs(value) if value is not None and value < ZERO else ZERO if value is not None else None for value in values.values())
    gross = _complete_sum(abs(value) if value is not None else None for value in values.values())
    net = _complete_sum(values.values())
    equity = source.account.equity
    weights = tuple(
        (symbol, abs(value) / equity if value is not None and equity not in (None, ZERO) else None)
        for symbol, value in sorted(values.items())
    )
    known_weights = tuple(value for _, value in weights if value is not None)
    weights_complete = len(known_weights) == len(weights) and equity not in (None, ZERO)
    cash_percentage = source.account.cash / equity if source.account.cash is not None and equity not in (None, ZERO) else None
    buying_power_utilization = (
        gross / (gross + source.account.buying_power)
        if gross is not None and source.account.buying_power is not None and gross + source.account.buying_power > ZERO
        else ZERO if gross == ZERO and source.account.buying_power == ZERO
        else None
    )
    order_notionals = tuple(
        order.remaining_quantity * order.price if order.price is not None else None
        for order in source.working_orders
    )
    pending = _complete_sum(order_notionals)
    projected = dict(values)
    determinable = all(value is not None for value in projected.values()) and all(value is not None for value in order_notionals)
    if determinable:
        for order in source.working_orders:
            signed = order.remaining_quantity * order.price * (Decimal("1") if order.side is OrderSide.BUY else Decimal("-1"))
            projected[order.symbol] = (projected.get(order.symbol) or ZERO) + signed
        gross_after = sum((abs(value) for value in projected.values()), ZERO)
        net_after = sum(projected.values(), ZERO)
    else:
        gross_after = net_after = None
    sorted_weights = sorted(known_weights, reverse=True)
    return ExposureSummary(
        gross, net, long_exposure, short_exposure, cash_percentage,
        buying_power_utilization, weights,
        max(sorted_weights) if weights_complete and sorted_weights else ZERO if weights_complete else None,
        sum(sorted_weights[:5], ZERO) if weights_complete else None,
        len(source.positions), pending, gross_after, net_after,
    )


def _concentration(positions: tuple[PortfolioPosition, ...], exposure: ExposureSummary) -> ConcentrationSummary:
    allocations = [weight for _, weight in exposure.position_weights if weight is not None]
    complete = len(allocations) == len(positions)
    allocations.sort(reverse=True)
    gross = exposure.gross_exposure
    shares = [abs(position.market_value) / gross for position in positions if position.market_value is not None] if gross not in (None, ZERO) else []
    hhi = sum((share * share for share in shares), ZERO) if complete and shares else ZERO if complete and not positions else None
    imbalance = (
        abs(exposure.long_exposure - exposure.short_exposure) / gross
        if exposure.long_exposure is not None and exposure.short_exposure is not None and gross not in (None, ZERO)
        else ZERO if gross == ZERO else None
    )
    strategy = _dimension_concentration(positions, "strategy_id", gross)
    asset_class = _dimension_concentration(positions, "asset_class", gross)
    sector = None if any(position.sector is None for position in positions) else _dimension_concentration(positions, "sector", gross)
    return ConcentrationSummary(
        allocations[0] if complete and allocations else ZERO if complete else None,
        sum(allocations[:3], ZERO) if complete else None,
        sum(allocations[:5], ZERO) if complete else None,
        hhi, imbalance, strategy, asset_class, sector,
    )


def _dimension_concentration(positions: tuple[PortfolioPosition, ...], name: str, gross: Decimal | None) -> tuple[tuple[str, Decimal], ...]:
    if gross in (None, ZERO) or any(position.market_value is None for position in positions):
        return ()
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for position in positions:
        key = getattr(position, name) or "Unattributed"
        totals[str(key)] += abs(position.market_value)  # type: ignore[arg-type]
    return tuple(sorted(((key, value / gross) for key, value in totals.items()), key=lambda item: (-item[1], item[0])))


def _returns(points: tuple[PriceObservation, ...], lookback: int) -> dict[datetime, Decimal]:
    ordered = sorted(points, key=lambda point: point.timestamp)[-(lookback + 1):]
    result: dict[datetime, Decimal] = {}
    for previous, current in zip(ordered, ordered[1:]):
        if previous.price != ZERO:
            result[current.timestamp] = current.price / previous.price - Decimal("1")
    return result


def _pearson(first: tuple[Decimal, ...], second: tuple[Decimal, ...]) -> Decimal | None:
    count = Decimal(len(first))
    mean_first = sum(first, ZERO) / count
    mean_second = sum(second, ZERO) / count
    deviations = tuple((a - mean_first, b - mean_second) for a, b in zip(first, second))
    covariance = sum((a * b for a, b in deviations), ZERO)
    variance_first = sum((a * a for a, _ in deviations), ZERO)
    variance_second = sum((b * b for _, b in deviations), ZERO)
    denominator_squared = variance_first * variance_second
    if denominator_squared == ZERO:
        return None
    value = covariance / denominator_squared.sqrt()
    return max(Decimal("-1"), min(Decimal("1"), value))


def _group_trades(fills: tuple[PortfolioFill, ...]) -> tuple[_ClosedTrade, ...]:
    unique = {fill.fill_id: fill for fill in fills}
    quantities: dict[str, Decimal] = defaultdict(lambda: ZERO)
    opened: dict[str, datetime] = {}
    pnl: dict[str, Decimal] = defaultdict(lambda: ZERO)
    complete: dict[str, bool] = defaultdict(lambda: True)
    attributes: dict[str, PortfolioFill] = {}
    trades: list[_ClosedTrade] = []
    for fill in sorted(unique.values(), key=lambda item: (item.timestamp, item.fill_id)):
        old = quantities[fill.symbol]
        delta = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        new = old + delta
        if old == ZERO:
            opened[fill.symbol] = fill.timestamp
            attributes[fill.symbol] = fill
        if fill.realized_pnl is None:
            complete[fill.symbol] = False
        else:
            pnl[fill.symbol] += fill.realized_pnl
        closes = old != ZERO and (new == ZERO or (old > ZERO) != (new > ZERO))
        if closes:
            if complete[fill.symbol]:
                attribution = attributes[fill.symbol]
                trades.append(_ClosedTrade(
                    fill.symbol, opened[fill.symbol], fill.timestamp, pnl[fill.symbol],
                    attribution.strategy_id or "Unattributed",
                    attribution.decision_type or "Unattributed",
                    attribution.asset_class,
                    attribution.session_id or "Unattributed",
                ))
            pnl[fill.symbol] = ZERO
            complete[fill.symbol] = True
            if new != ZERO:
                opened[fill.symbol] = fill.timestamp
                attributes[fill.symbol] = fill
        quantities[fill.symbol] = new
    return tuple(trades)


def _performance(source: PortfolioIntelligenceInput, trades: tuple[_ClosedTrade, ...], now: datetime, config: PortfolioIntelligenceConfiguration) -> PerformanceSummary:
    wins = tuple(trade.realized_pnl for trade in trades if trade.realized_pnl > ZERO)
    losses = tuple(trade.realized_pnl for trade in trades if trade.realized_pnl < ZERO)
    classified = len(wins) + len(losses)
    gross_profit = sum(wins, ZERO) if trades else None
    gross_loss = sum(losses, ZERO) if trades else None
    win_rate = Decimal(len(wins)) / Decimal(classified) if classified else None
    loss_rate = Decimal(len(losses)) / Decimal(classified) if classified else None
    average_win = sum(wins, ZERO) / Decimal(len(wins)) if wins else None
    average_loss = sum(losses, ZERO) / Decimal(len(losses)) if losses else None
    profit_factor = gross_profit / abs(gross_loss) if gross_profit is not None and gross_loss not in (None, ZERO) else None
    expectancy = sum((trade.realized_pnl for trade in trades), ZERO) / Decimal(len(trades)) if trades else None
    holding = sum((Decimal(str((trade.closed_at - trade.opened_at).total_seconds())) for trade in trades), ZERO) / Decimal(len(trades)) if trades else None
    realized_values = tuple(fill.realized_pnl for fill in {fill.fill_id: fill for fill in source.fills}.values())
    cumulative = _complete_sum(realized_values)
    day = _trading_day(now, config)
    daily_values = tuple(fill.realized_pnl for fill in {fill.fill_id: fill for fill in source.fills}.values() if _trading_day(fill.timestamp, config) == day)
    daily_realized = _complete_sum(daily_values)
    maximum_drawdown, current_drawdown = _drawdowns(source.equity_history)
    starting = min(source.equity_history, key=lambda point: point.timestamp).equity if source.equity_history else None
    ending = max(source.equity_history, key=lambda point: point.timestamp).equity if source.equity_history else source.account.equity
    roe = (ending - starting) / starting if starting not in (None, ZERO) and ending is not None else None
    return PerformanceSummary(
        daily_realized, None, None, cumulative, gross_profit, gross_loss,
        win_rate, loss_rate, profit_factor, average_win, average_loss, expectancy,
        holding, maximum_drawdown, current_drawdown, roe, len(trades),
    )


def _trading_day(value: datetime, config: PortfolioIntelligenceConfiguration):
    local = value.astimezone(ZoneInfo(config.performance_reporting_timezone)) - timedelta(hours=config.trading_day_boundary_hour)
    return local.date()


def _drawdowns(points) -> tuple[Decimal | None, Decimal | None]:
    if not points:
        return None, None
    peak: Decimal | None = None
    maximum = ZERO
    current = ZERO
    for point in sorted(points, key=lambda item: item.timestamp):
        peak = point.equity if peak is None or point.equity > peak else peak
        current = (peak - point.equity) / peak if peak > ZERO else ZERO
        maximum = max(maximum, current)
    return maximum, current


def _attribution(source: PortfolioIntelligenceInput, trades: tuple[_ClosedTrade, ...]) -> AttributionSummary:
    realized_symbol: dict[str, Decimal] = defaultdict(lambda: ZERO)
    dimensions: dict[str, dict[str, Decimal]] = {name: defaultdict(lambda: ZERO) for name in ("strategy", "decision", "asset", "session")}
    for trade in trades:
        realized_symbol[trade.symbol] += trade.realized_pnl
        dimensions["strategy"][trade.strategy_id] += trade.realized_pnl
        dimensions["decision"][trade.decision_type] += trade.realized_pnl
        dimensions["asset"][trade.asset_class] += trade.realized_pnl
        dimensions["session"][trade.session_id] += trade.realized_pnl
    unrealized_symbol: dict[str, Decimal] = defaultdict(lambda: ZERO)
    unrealized_dimensions: dict[str, dict[str, Decimal]] = {name: defaultdict(lambda: ZERO) for name in dimensions}
    for position in source.positions:
        if position.unrealized_pnl is None:
            continue
        unrealized_symbol[position.symbol] += position.unrealized_pnl
        unrealized_dimensions["strategy"][position.strategy_id or "Unattributed"] += position.unrealized_pnl
        unrealized_dimensions["decision"][position.decision_type or "Unattributed"] += position.unrealized_pnl
        unrealized_dimensions["asset"][position.asset_class] += position.unrealized_pnl
        unrealized_dimensions["session"]["Unattributed"] += position.unrealized_pnl
    def entries(realized, unrealized):
        return tuple(AttributionEntry(key, realized[key], unrealized[key], realized[key] + unrealized[key]) for key in sorted(set(realized) | set(unrealized)))
    return AttributionSummary(
        entries(realized_symbol, unrealized_symbol),
        entries(dimensions["strategy"], unrealized_dimensions["strategy"]),
        entries(dimensions["decision"], unrealized_dimensions["decision"]),
        entries(dimensions["asset"], unrealized_dimensions["asset"]),
        entries(dimensions["session"], unrealized_dimensions["session"]),
        _complete_sum(fill.realized_pnl for fill in {fill.fill_id: fill for fill in source.fills}.values()),
        _complete_sum(position.unrealized_pnl for position in source.positions),
    )


def _risk_budget(exposure: ExposureSummary, performance: PerformanceSummary, limits: PortfolioRiskLimits, warning: Decimal) -> PortfolioRiskBudgetStatus:
    specs = (
        ("Gross Exposure", exposure.gross_exposure, limits.maximum_gross_exposure),
        ("Net Exposure", abs(exposure.net_exposure) if exposure.net_exposure is not None else None, limits.maximum_net_exposure),
        ("Largest Position", exposure.largest_position_weight, limits.maximum_largest_position),
        ("Daily Loss", abs(min(performance.daily_realized_pnl, ZERO)) if performance.daily_realized_pnl is not None else None, limits.maximum_daily_loss),
        ("Drawdown", performance.current_drawdown, limits.maximum_drawdown),
        ("Open Positions", exposure.open_positions, limits.maximum_open_positions),
        ("Buying Power Utilization", exposure.buying_power_utilization, limits.maximum_buying_power_utilization),
    )
    metrics = tuple(RiskBudgetMetric(name, current, limit, _classify(current, limit, warning)) for name, current, limit in specs)
    rank = {RiskBudgetClassification.UNKNOWN: 0, RiskBudgetClassification.WITHIN_LIMITS: 1, RiskBudgetClassification.APPROACHING_LIMIT: 2, RiskBudgetClassification.AT_LIMIT: 3, RiskBudgetClassification.EXCEEDED: 4}
    known = tuple(metric.classification for metric in metrics if metric.classification is not RiskBudgetClassification.UNKNOWN)
    overall = max(known, key=rank.get) if known else RiskBudgetClassification.UNKNOWN
    return PortfolioRiskBudgetStatus(overall, metrics)


def _classify(current, limit, warning: Decimal) -> RiskBudgetClassification:
    if current is None or limit is None:
        return RiskBudgetClassification.UNKNOWN
    current_decimal, limit_decimal = Decimal(current), Decimal(limit)
    if current_decimal > limit_decimal:
        return RiskBudgetClassification.EXCEEDED
    if current_decimal == limit_decimal:
        return RiskBudgetClassification.AT_LIMIT
    if current_decimal >= limit_decimal * warning:
        return RiskBudgetClassification.APPROACHING_LIMIT
    return RiskBudgetClassification.WITHIN_LIMITS


def _observations(concentration, correlation, risk, config) -> tuple[str, ...]:
    result: list[str] = []
    if concentration.top_three_allocation is not None and concentration.top_three_allocation >= config.concentration_warning_threshold:
        result.append("Portfolio is concentrated in three positions.")
    if correlation.highly_correlated_pairs:
        pair = correlation.highly_correlated_pairs[0]
        result.append(f"{pair.first_symbol} and {pair.second_symbol} have high return correlation.")
    buying_power = next((metric for metric in risk.metrics if metric.name == "Buying Power Utilization"), None)
    if buying_power is not None and buying_power.classification is RiskBudgetClassification.APPROACHING_LIMIT:
        result.append("Buying-power utilization is approaching its configured limit.")
    return tuple(result)


def _complete_sum(values: Iterable[Decimal | None]) -> Decimal | None:
    values = tuple(values)
    if any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), ZERO)


def _latest_time(source: PortfolioIntelligenceInput) -> datetime | None:
    values: list[datetime] = []
    values.extend(fill.timestamp for fill in source.fills)
    values.extend(point.timestamp for point in source.equity_history)
    values.extend(point.timestamp for points in source.price_history.values() for point in points)
    return max(values, default=None)


__all__ = ["PearsonCorrelationAnalyzer", "PortfolioIntelligenceService"]
