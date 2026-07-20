from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.ai.parser import parse_response
from app.ai.prompt_builder import build_prompt_package
from app.backtesting.models import BacktestOrderIntent, HistoricalCandle, ReplayEvent, ReplayEventType, ReplayJournal
from app.indicators.market_snapshot import MarketSnapshot, build_market_snapshot
from app.strategy.scoring import MarketAnalysis, StrategyScore, analyze_snapshot, score_snapshot


def build_pipeline_state(
    candles: tuple[HistoricalCandle, ...], timestamp: datetime, strategy_version: str, prompt_version: str
) -> tuple[MarketSnapshot, StrategyScore, MarketAnalysis, object]:
    snapshot = build_market_snapshot(
        candles[-1].symbol,
        tuple(candle.high for candle in candles), tuple(candle.low for candle in candles),
        tuple(candle.close for candle in candles), tuple(candle.volume for candle in candles),
    )
    score = score_snapshot(snapshot)
    analysis = analyze_snapshot(snapshot)
    prompt = build_prompt_package(snapshot, analysis, score, timestamp=timestamp,
                                  strategy_version=strategy_version, prompt_version=prompt_version)
    return snapshot, score, analysis, prompt


def parse_supplied_response(raw_json: str):
    return parse_response(raw_json)


def append_replay_event(
    journal: ReplayJournal, candle_index: int, timestamp: datetime,
    event_type: ReplayEventType, status: str, details: tuple[tuple[str, str], ...] = (),
) -> ReplayJournal:
    return ReplayJournal((*journal.events, ReplayEvent(len(journal.events) + 1, candle_index, timestamp, event_type, status, details)))


def intent_matches_action(intent: BacktestOrderIntent, action: str) -> bool:
    return intent.symbol.strip().upper() != "" and intent.order_type.value != "" and action == intent.side.value
