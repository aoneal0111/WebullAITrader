from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.backtesting.models import PendingExecution, ReplayCheckpoint, ReplayEvent, ReplayEventType, ReplayJournal
from app.market_history import MarketObservation
from app.compliance.models import FundingSource, PurchaseLot
from app.paper_trading.models import (
    EquityPoint, JournalEvent, JournalEventType, PaperJournal, PaperPortfolio, PaperPosition,
)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    start_timestamp: datetime
    end_timestamp: datetime
    number_of_candles: int
    number_of_proposals: int
    number_approved: int
    number_rejected: int
    number_filled: int
    ending_cash: Decimal
    ending_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal
    win_rate: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal | None
    checkpoint: ReplayCheckpoint

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("checkpoint")
        for key, value in tuple(result.items()):
            if isinstance(value, Decimal):
                result[key] = format(value, "f")
            elif isinstance(value, datetime):
                result[key] = value.isoformat()
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def checkpoint_from_json(payload: str) -> ReplayCheckpoint:
    try:
        value = json.loads(payload)
        portfolio = _portfolio(value["portfolio"])
        paper_journal = PaperJournal(tuple(_paper_event(item) for item in value["paper_journal"]["events"]))
        replay_journal = ReplayJournal(tuple(_replay_event(item) for item in value["replay_journal"]["events"]))
        equity_curve = tuple(EquityPoint(_dt(item["timestamp"]), Decimal(item["equity"])) for item in value["equity_curve"])
        portfolio_history = tuple(_portfolio(item) for item in value["portfolio_history"])
        lots = tuple(_lot(item) for item in value["purchase_lots"])
        observations = tuple(sorted((_market_observation(item) for item in value.get("market_observations", ())),
                                    key=lambda item: (item.timestamp, item.symbol)))
        pending_data = value["pending_execution"]
        pending = None if pending_data is None else PendingExecution(pending_data["proposal_json"], pending_data["compliance_json"])
        return ReplayCheckpoint(
            int(value["schema_version"]), value["dataset_fingerprint"], value["response_fingerprint"],
            value["intent_fingerprint"], value["config_fingerprint"], int(value["next_candle_index"]),
            portfolio, paper_journal, replay_journal, equity_curve, portfolio_history, lots, pending,
            int(value["proposals"]), int(value["approved"]), int(value["rejected"]), int(value["filled"]),
            observations,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("checkpoint JSON is malformed") from exc


def _portfolio(value: dict[str, Any]) -> PaperPortfolio:
    positions = tuple(
        PaperPosition(item["symbol"], Decimal(item["quantity"]), Decimal(item["average_cost"]),
                      Decimal(item["current_mark"]), Decimal(item["market_value"]), Decimal(item["unrealized_pnl"]))
        for item in value["positions"]
    )
    return PaperPortfolio(Decimal(value["initial_cash"]), Decimal(value["cash"]), positions,
                          Decimal(value["realized_pnl"]), Decimal(value["unrealized_pnl"]),
                          Decimal(value["equity"]), _dt(value["timestamp"]))


def _paper_event(value: dict[str, Any]) -> JournalEvent:
    return JournalEvent(int(value["sequence"]), JournalEventType(value["event_type"]), value["request_id"],
                        _dt(value["timestamp"]), value["message"], tuple(tuple(item) for item in value["details"]))


def _replay_event(value: dict[str, Any]) -> ReplayEvent:
    return ReplayEvent(int(value["sequence"]), int(value["candle_index"]), _dt(value["timestamp"]),
                       ReplayEventType(value["event_type"]), value["status"], tuple(tuple(item) for item in value["details"]))


def _lot(value: dict[str, Any]) -> PurchaseLot:
    settlement = date.fromisoformat(value["funding_settlement_date"]) if value["funding_settlement_date"] else None
    return PurchaseLot(value["symbol"], Decimal(value["quantity"]), _dt(value["purchase_timestamp"]),
                       FundingSource(value["funding_source"]), settlement, Decimal(value["remaining_quantity"]))


def _dt(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("checkpoint timestamps must be timezone-aware")
    return result


def _market_observation(value: dict[str, Any]) -> MarketObservation:
    decimal_fields = ("open", "high", "low", "close", "volume", "bid", "ask", "observed_slippage")
    parsed = {key: None if value.get(key) is None else Decimal(value[key]) for key in decimal_fields}
    return MarketObservation(
        _dt(value["timestamp"]), value["symbol"], parsed["open"], parsed["high"], parsed["low"],
        parsed["close"], parsed["volume"], parsed["bid"], parsed["ask"], value.get("session"),
        value.get("market_status"), parsed["observed_slippage"], value.get("volatility_regime"),
        value.get("trend_regime"),
    )
