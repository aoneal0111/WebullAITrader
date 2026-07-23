from __future__ import annotations

from app.execution_coordinator.proposal_factory import create_proposed_order

import json
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal

from app.ai.response_models import ResponseAction
from app.backtesting.datasource import frames_fingerprint, validate_frames
from app.backtesting.models import (
    BacktestConfig, BacktestOrderIntent, HistoricalFrame, PendingExecution, ReplayCheckpoint,
    ReplayEventType, ReplayJournal, SuppliedAIResponse, canonical_fingerprint,
)
from app.backtesting.replay import append_replay_event, build_pipeline_state, intent_matches_action, parse_supplied_response
from app.backtesting.results import BacktestResult
from app.compliance.gfv_validator import evaluate_sell_compliance
from app.compliance.models import AccountType, FundingSource, PurchaseLot
from app.order_compliance.models import (
    OrderComplianceDecision, OrderSide, ProposedOrder,
)
from app.order_compliance.validator import evaluate_order_compliance
from app.order_compliance.account_state_builder import build_account_state
from app.paper_trading.metrics import calculate_metrics
from app.paper_trading.models import EquityPoint, ExecutionStatus, PaperJournal, PaperMarketQuote
from app.paper_trading.portfolio import create_portfolio
from app.paper_trading.simulator import simulate_proposal
from app.risk.validator import evaluate_risk
from app.market_history import MarketObservation


def run_backtest(
    frames: tuple[HistoricalFrame, ...], responses: tuple[SuppliedAIResponse, ...],
    intents: tuple[BacktestOrderIntent, ...], config: BacktestConfig, *,
    checkpoint: ReplayCheckpoint | None = None,
) -> BacktestResult:
    final = _run(frames, responses, intents, config, checkpoint, len(frames))
    metrics = calculate_metrics(final.paper_journal, final.equity_curve)
    return BacktestResult(
        frames[0].candle.open_timestamp, frames[-1].candle.close_timestamp, len(frames),
        final.proposals, final.approved, final.rejected, final.filled,
        final.portfolio.cash, final.portfolio.equity, final.portfolio.realized_pnl,
        final.portfolio.unrealized_pnl, metrics.total_return, metrics.maximum_drawdown,
        metrics.win_rate, metrics.profit_factor, metrics.expectancy, final,
    )


def run_until(
    frames: tuple[HistoricalFrame, ...], responses: tuple[SuppliedAIResponse, ...],
    intents: tuple[BacktestOrderIntent, ...], config: BacktestConfig, stop_before_index: int,
    *, checkpoint: ReplayCheckpoint | None = None,
) -> ReplayCheckpoint:
    return _run(frames, responses, intents, config, checkpoint, stop_before_index)


def resume_backtest(frames, responses, intents, config, checkpoint: ReplayCheckpoint) -> BacktestResult:
    return run_backtest(frames, responses, intents, config, checkpoint=checkpoint)


def _run(frames, responses, intents, config, checkpoint, stop):
    validate_frames(frames)
    fingerprints = (
        frames_fingerprint(frames), canonical_fingerprint(responses),
        canonical_fingerprint(intents), canonical_fingerprint(config),
    )
    if checkpoint is None:
        portfolio = create_portfolio(config.initial_cash, frames[0].candle.open_timestamp)
        state = ReplayCheckpoint(config.checkpoint_schema_version, *fingerprints, 0, portfolio, PaperJournal(),
                                 ReplayJournal(), (EquityPoint(portfolio.timestamp, portfolio.equity),),
                                 (portfolio,), (), None, 0, 0, 0, 0)
    else:
        state = checkpoint
        if (state.schema_version != config.checkpoint_schema_version or
                (state.dataset_fingerprint, state.response_fingerprint, state.intent_fingerprint, state.config_fingerprint) != fingerprints):
            raise ValueError("checkpoint fingerprint or schema mismatch")
    if not 0 <= stop <= len(frames) or stop < state.next_candle_index:
        raise ValueError("invalid replay stop index")
    response_map = {item.candle_timestamp: item for item in responses}
    intent_map = {item.candle_timestamp: item for item in intents}
    for index in range(state.next_candle_index, stop):
        frame = frames[index]
        timestamp = frame.candle.close_timestamp
        observation = MarketObservation(
            timestamp, frame.candle.symbol.upper(), frame.candle.open, frame.candle.high,
            frame.candle.low, frame.candle.close, frame.candle.volume, frame.execution_bid,
            frame.execution_ask, frame.session.value if frame.session is not None else None,
            frame.market_state.market_status.value if frame.market_state.market_status is not None else None,
            frame.observed_slippage, frame.volatility_regime, frame.trend_regime,
        )
        observations = tuple(sorted((*state.market_observations, observation),
                                    key=lambda item: (item.timestamp, item.symbol)))
        journal = append_replay_event(state.replay_journal, index, timestamp, ReplayEventType.CANDLE, "PROCESSED")
        state = replace(state, replay_journal=journal, market_observations=observations)
        if state.pending_execution is not None:
            proposal = _proposal_from_json(state.pending_execution.proposal_json)
            decision = _decision_from_json(state.pending_execution.compliance_json)
            quote = PaperMarketQuote(frame.candle.symbol, frame.execution_bid, frame.execution_ask,
                                     frame.execution_last, frame.candle.open_timestamp)
            simulation = simulate_proposal(state.portfolio, proposal, decision, quote,
                                           config.paper_execution_config, state.paper_journal, state.equity_curve)
            filled = state.filled + (simulation.execution.status is ExecutionStatus.FILLED)
            rejected = state.rejected + (simulation.execution.status is ExecutionStatus.REJECTED)
            lots = _update_lots(state.purchase_lots, simulation.execution.fill)
            journal = append_replay_event(state.replay_journal, index, timestamp, ReplayEventType.PAPER_EXECUTION,
                                          simulation.execution.status.value)
            state = replace(state, portfolio=simulation.portfolio, paper_journal=simulation.journal,
                            equity_curve=simulation.equity_curve,
                            portfolio_history=((*state.portfolio_history, simulation.portfolio)
                                               if simulation.execution.status is ExecutionStatus.FILLED
                                               else state.portfolio_history),
                            purchase_lots=lots, pending_execution=None,
                            filled=int(filled), rejected=int(rejected), replay_journal=journal)
        candles = tuple(item.candle for item in frames[: index + 1])
        snapshot, score, analysis, prompt = build_pipeline_state(candles, timestamp, config.strategy_version, config.prompt_version)
        journal = append_replay_event(state.replay_journal, index, timestamp, ReplayEventType.INDICATORS, "BUILT")
        journal = append_replay_event(journal, index, timestamp, ReplayEventType.STRATEGY, score.action.value)
        journal = append_replay_event(journal, index, timestamp, ReplayEventType.PROMPT, "BUILT",
                                      (("prompt_version", prompt.metadata.prompt_version),))
        state = replace(state, replay_journal=journal)
        if index + 1 < config.warmup_candles:
            state = replace(state, next_candle_index=index + 1)
            continue
        supplied = response_map.get(timestamp)
        intent = intent_map.get(timestamp)
        if supplied is None:
            journal = append_replay_event(state.replay_journal, index, timestamp, ReplayEventType.AI_REJECTION, "MISSING")
            state = replace(state, replay_journal=journal, next_candle_index=index + 1)
            continue
        try:
            response = parse_supplied_response(supplied.raw_json)
        except ValueError as exc:
            journal = append_replay_event(state.replay_journal, index, timestamp, ReplayEventType.AI_REJECTION, "INVALID", (("reason", str(exc)),))
            state = replace(state, replay_journal=journal, next_candle_index=index + 1, rejected=state.rejected + 1)
            continue
        journal = append_replay_event(state.replay_journal, index, timestamp, ReplayEventType.AI_RESPONSE, response.action.value)
        risk = evaluate_risk(response, snapshot, config.risk_limits)
        journal = append_replay_event(journal, index, timestamp, ReplayEventType.RISK, "APPROVED" if risk.approved else "REJECTED")
        state = replace(state, replay_journal=journal)
        if response.action is ResponseAction.HOLD:
            state = replace(state, next_candle_index=index + 1)
            continue
        if intent is None or not intent_matches_action(intent, response.action.value):
            journal = append_replay_event(state.replay_journal, index, timestamp, ReplayEventType.ORDER_COMPLIANCE, "REJECTED_INTENT")
            state = replace(state, replay_journal=journal, next_candle_index=index + 1, rejected=state.rejected + 1)
            continue
        proposal = create_proposed_order(
            intent,
            created_timestamp=timestamp,
        )
        gfv = None
        if intent.side is OrderSide.SELL and config.account_type is AccountType.CASH:
            gfv = evaluate_sell_compliance(intent.symbol, intent.quantity, config.account_type, timestamp, state.purchase_lots)
            state = replace(state, replay_journal=append_replay_event(state.replay_journal, index, timestamp,
                                ReplayEventType.GFV, "APPROVED" if gfv.approved else "REJECTED"))
        account = build_account_state(
            portfolio=state.portfolio,
            account_type=config.account_type,
            filled_orders=state.filled,
            symbol=intent.symbol,
            timestamp=timestamp,
        )
        decision = evaluate_order_compliance(proposal, account, frame.market_state, config.compliance_limits,
                                             config.kill_switch, gfv_decision=gfv, risk_decision=risk)
        journal = append_replay_event(state.replay_journal, index, timestamp, ReplayEventType.ORDER_COMPLIANCE,
                                      "APPROVED" if decision.approved else "REJECTED")
        pending = PendingExecution(_proposal_json(proposal), json.dumps(decision.to_dict(), sort_keys=True)) if decision.approved else None
        state = replace(state, replay_journal=journal, pending_execution=pending,
                        proposals=state.proposals + 1, approved=state.approved + int(decision.approved),
                        rejected=state.rejected + int(not decision.approved), next_candle_index=index + 1)
    return state


def _proposal_json(value):
    return json.dumps({"request_id": value.request_id, "symbol": value.symbol, "side": value.side.value,
                       "order_type": value.order_type.value, "quantity": str(value.quantity),
                       "limit_price": None if value.limit_price is None else str(value.limit_price),
                       "stop_price": None if value.stop_price is None else str(value.stop_price),
                       "requested_session": value.requested_session.value, "created_timestamp": value.created_timestamp.isoformat()}, sort_keys=True)


def _proposal_from_json(payload):
    from datetime import datetime
    from app.order_compliance.models import OrderSide, OrderType, TradingSession
    value = json.loads(payload)
    return ProposedOrder(value["request_id"], value["symbol"], OrderSide(value["side"]), OrderType(value["order_type"]),
                         Decimal(value["quantity"]), Decimal(value["limit_price"]) if value["limit_price"] else None,
                         Decimal(value["stop_price"]) if value["stop_price"] else None,
                         TradingSession(value["requested_session"]), datetime.fromisoformat(value["created_timestamp"]))


def _decision_from_json(payload):
    value = json.loads(payload)
    decimal = lambda key: Decimal(value[key]) if value[key] is not None else None
    return OrderComplianceDecision(value["approved"], value["approval_reason"], value["request_id"],
        tuple(value["checks_passed"]), tuple(value["checks_failed"]), tuple(value["warnings"]),
        decimal("maximum_compliant_quantity"), decimal("normalized_limit_price"), decimal("normalized_stop_price"),
        decimal("lower_valid_tick"), decimal("upper_valid_tick"))


def _update_lots(lots, fill):
    if fill is None:
        return lots
    if fill.side == "BUY":
        return (*lots, PurchaseLot(fill.symbol, fill.quantity, fill.timestamp, FundingSource.SETTLED_CASH, None, fill.quantity))
    remaining = fill.quantity
    updated = []
    for lot in lots:
        if lot.symbol == fill.symbol and remaining > 0:
            used = min(remaining, lot.remaining_quantity)
            remaining -= used
            lot = replace(lot, remaining_quantity=lot.remaining_quantity - used)
        if lot.remaining_quantity > 0:
            updated.append(lot)
    return tuple(updated)





