"""Replay adapter for decisions made solely from durable forward evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, ScannerObservation,
)

from .configuration import WarriorMomentumConfig
from .forward_models import CaptureRecord, CaptureRecordType
from .forward_store import ForwardCaptureStore
from .models import MinuteBar, StockInPlayType
from .runtime import WarriorMomentumRuntime


@dataclass(frozen=True, slots=True)
class ReplayEquivalenceResult:
    decision_record_id: str
    equivalent: bool
    expected_score: Decimal
    actual_score: Decimal
    expected_status: str
    actual_status: str
    expected_setup: dict | None
    actual_setup: dict | None
    expected_reason_codes: tuple[str, ...]
    actual_reason_codes: tuple[str, ...]


def replay_captured_decision(
    store: ForwardCaptureStore, decision_record_id: str,
    config: WarriorMomentumConfig = WarriorMomentumConfig(),
) -> ReplayEquivalenceResult:
    decisions = {
        record.record_id: record
        for record in store.records(record_type=CaptureRecordType.DECISION)
    }
    try:
        record = decisions[decision_record_id]
    except KeyError as exc:
        raise KeyError(f"unknown decision record: {decision_record_id}") from exc
    payload = record.payload
    raw = payload["observation"]
    observation = ScannerObservation(
        symbol=record.symbol,
        timestamp=datetime.fromisoformat(payload["decision_timestamp"]),
        price=Decimal(raw["price"]),
        previous_close=Decimal(raw["previous_close"]),
        current_volume=Decimal(raw["current_volume"]),
        average_30_day_volume=Decimal(raw["average_30_day_volume"]),
        float_shares=(
            None if raw["float_shares"] is None else Decimal(raw["float_shares"])
        ),
        bid=None if raw["bid"] is None else Decimal(raw["bid"]),
        ask=None if raw["ask"] is None else Decimal(raw["ask"]),
        catalyst=CatalystType(raw["catalyst"]),
        catalyst_headline=None,
        tradable=bool(raw["tradable"]),
        halted=bool(raw["halted"]),
        asset_class=AssetClass(raw["asset_class"]),
        catalyst_status=CatalystStatus(raw["catalyst_status"]),
    )
    required = set(payload["bar_timestamps"])
    bars = tuple(
        _bar_from_record(item)
        for item in store.records(
            symbol=record.symbol, record_type=CaptureRecordType.MINUTE_BAR,
        )
        if item.payload["bar_timestamp"] in required
    )
    replayed = WarriorMomentumRuntime(config).discover(
        observation, bars, session=payload["session"],
        top_gapper=StockInPlayType.TOP_GAPPER.value in payload["stocks_in_play"],
    )
    assessed, _signal = WarriorMomentumRuntime(config).assess_entry(replayed)
    actual_setup = _setup_payload(assessed.setup)
    expected_setup = payload["setup"]
    expected_reasons = tuple(payload["reason_codes"])
    actual_reasons = tuple(code.value for code in assessed.reason_codes)
    expected_score = Decimal(payload["score"])
    return ReplayEquivalenceResult(
        decision_record_id=record.record_id,
        equivalent=(
            expected_score == assessed.score.total
            and payload["status"] == assessed.status.value
            and expected_setup == actual_setup
            and expected_reasons == actual_reasons
        ),
        expected_score=expected_score,
        actual_score=assessed.score.total,
        expected_status=payload["status"],
        actual_status=assessed.status.value,
        expected_setup=expected_setup,
        actual_setup=actual_setup,
        expected_reason_codes=expected_reasons,
        actual_reason_codes=actual_reasons,
    )


def _bar_from_record(record: CaptureRecord) -> MinuteBar:
    payload = record.payload
    return MinuteBar(
        symbol=record.symbol,
        timestamp=datetime.fromisoformat(payload["bar_timestamp"]),
        open=Decimal(payload["open"]), high=Decimal(payload["high"]),
        low=Decimal(payload["low"]), close=Decimal(payload["close"]),
        volume=Decimal(payload["volume"]),
    )


def _setup_payload(setup) -> dict | None:
    if setup is None:
        return None
    return {
        "type": setup.setup_type.value, "state": setup.state.value,
        "score": str(setup.score),
        "trigger": None if setup.trigger is None else str(setup.trigger),
        "stop_price": None if setup.stop_price is None else str(setup.stop_price),
        "stop_model": None if setup.stop_model is None else setup.stop_model.value,
        "resistance": None if setup.resistance is None else str(setup.resistance),
    }


__all__ = ["ReplayEquivalenceResult", "replay_captured_decision"]
