"""Deterministic daily funnel and paper-performance reporting."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from statistics import mean
from zoneinfo import ZoneInfo

from .forward_models import CaptureRecordType, ForwardTransition
from .forward_store import ForwardCaptureStore

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class DailyForwardReport:
    trading_date: date
    funnel: tuple[tuple[str, int], ...]
    paper_trades: int
    wins: int | None
    losses: int | None
    scratches: int | None
    total_r: Decimal | None
    expectancy_r: Decimal | None
    profit_factor: Decimal | None
    maximum_intraday_drawdown_r: Decimal | None
    average_mae_r: Decimal | None
    average_mfe_r: Decimal | None
    rejection_counts: tuple[tuple[str, int], ...]
    missing_data_counts: tuple[tuple[str, int], ...]
    setups_detected: int = 0
    open_paper_positions: int = 0
    counterfactual_starts: int = 0
    tracked_counterfactuals: int = 0
    configuration_fingerprint: str | None = None


class EvidenceMaturity(StrEnum):
    NO_TRADES = "NO_TRADES"
    EARLY_SAMPLE = "EARLY_SAMPLE"
    DEVELOPING_SAMPLE = "DEVELOPING_SAMPLE"
    MEANINGFUL_SAMPLE = "MEANINGFUL_SAMPLE"


@dataclass(frozen=True, slots=True)
class CumulativeBreakdown:
    category: str
    bucket: str
    sample_size: int
    expectancy_r: Decimal
    profit_factor: Decimal | None


@dataclass(frozen=True, slots=True)
class CumulativeForwardReport:
    configuration_fingerprint: str
    trading_days: int
    paper_trades: int
    wins: int
    losses: int
    win_rate: Decimal | None
    total_r: Decimal | None
    expectancy_r: Decimal | None
    profit_factor: Decimal | None
    maximum_drawdown_r: Decimal | None
    maturity: EvidenceMaturity
    breakdowns: tuple[CumulativeBreakdown, ...]


def build_daily_report(
    store: ForwardCaptureStore, trading_date: date,
    *, configuration_fingerprint: str | None = None,
) -> DailyForwardReport:
    all_records = store.records()
    if configuration_fingerprint is not None:
        records = tuple(
            record for record, fingerprint in _records_with_fingerprint(all_records)
            if fingerprint == configuration_fingerprint
            and record.timestamp.astimezone(EASTERN).date() == trading_date
        )
    else:
        records = tuple(
            record for record in all_records
            if record.timestamp.astimezone(EASTERN).date() == trading_date
        )
    transition_records = [
        item for item in records
        if item.record_type is CaptureRecordType.STATE_TRANSITION
    ]
    transition_counts = Counter(
        (item.payload.get("to"), item.symbol) for item in transition_records
    )
    stage_count = lambda stage: sum(key[0] == stage for key in transition_counts)
    discovered = {
        item.symbol for item in records
        if item.record_type is CaptureRecordType.DISCOVERY
    }
    in_play = {
        item.symbol for item in records
        if item.record_type is CaptureRecordType.DISCOVERY
        and item.payload.get("stocks_in_play")
    }
    funnel = (
        ("DISCOVERED", len(discovered)),
        ("STOCKS_IN_PLAY", len(in_play)),
        ("NEAR", stage_count(ForwardTransition.NEAR.value)),
        ("QUALIFIED", stage_count(ForwardTransition.QUALIFIED.value)),
        ("SETUP", stage_count(ForwardTransition.SETUP_FORMING.value)),
        ("TRIGGERED", stage_count(ForwardTransition.SETUP_TRIGGERED.value)),
        ("ENTRY_READY", stage_count(ForwardTransition.ENTRY_READY.value)),
        ("PAPER_TRADE", stage_count(ForwardTransition.PAPER_ENTRY.value)),
    )
    exits = [
        item.payload for item in transition_records
        if item.payload.get("to") == ForwardTransition.PAPER_EXIT.value
    ]
    realized = [Decimal(item["realized_r"]) for item in exits]
    maes = [Decimal(item["mae_r"]) for item in exits]
    mfes = [Decimal(item["mfe_r"]) for item in exits]
    rejection_counts: Counter[str] = Counter()
    for record in transition_records:
        item = record.payload
        if item.get("to") != ForwardTransition.ENTRY_BLOCKED.value:
            continue
        for gate in item.get("blocking_gates", ()):
            rejection_counts[str(gate["gate"])] += 1
    missing_counts: Counter[str] = Counter()
    for item in records:
        if item.record_type is CaptureRecordType.DATA_QUALITY:
            missing_counts.update(key for key, value in item.payload.items() if value)
    paper_entries = sum(
        item.record_type is CaptureRecordType.PAPER_FILL
        and item.payload.get("action") == "ENTRY" for item in records
    )
    counter_starts = sum(
        item.record_type is CaptureRecordType.COUNTERFACTUAL
        and item.payload.get("action") == "START" for item in records
    )
    setups_detected = len({
        item.symbol for item in transition_records
        if item.payload.get("to") in {
            ForwardTransition.SETUP_FORMING.value,
            ForwardTransition.SETUP_TRIGGERED.value,
        }
    })
    through = tuple(
        record for record, fingerprint in _records_with_fingerprint(all_records)
        if record.timestamp.astimezone(EASTERN).date() <= trading_date
        and (
            configuration_fingerprint is None
            or fingerprint == configuration_fingerprint
        )
    )
    all_entries = sum(
        item.record_type is CaptureRecordType.PAPER_FILL
        and item.payload.get("action") == "ENTRY" for item in through
    )
    all_exits = sum(
        item.record_type is CaptureRecordType.PAPER_FILL
        and item.payload.get("action") == "EXIT" for item in through
    )
    all_counter_starts = sum(
        item.record_type is CaptureRecordType.COUNTERFACTUAL
        and item.payload.get("action") == "START" for item in through
    )
    all_counter_ends = sum(
        item.record_type is CaptureRecordType.COUNTERFACTUAL
        and item.payload.get("action") == "END" for item in through
    )
    if not realized:
        wins = losses = scratches = None
        total = expectancy = factor = drawdown = average_mae = average_mfe = None
    else:
        wins = sum(value > 0 for value in realized)
        losses = sum(value < 0 for value in realized)
        scratches = len(realized) - wins - losses
        total = sum(realized, Decimal("0"))
        expectancy = total / len(realized)
        gains = sum((value for value in realized if value > 0), Decimal("0"))
        losses_r = -sum((value for value in realized if value < 0), Decimal("0"))
        factor = None if losses_r == 0 else gains / losses_r
        equity = peak = Decimal("0")
        drawdown = Decimal("0")
        for value in realized:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        average_mae = Decimal(str(mean(maes)))
        average_mfe = Decimal(str(mean(mfes)))
    return DailyForwardReport(
        trading_date, funnel, paper_entries,
        wins, losses, scratches, total, expectancy, factor, drawdown,
        average_mae, average_mfe, tuple(sorted(rejection_counts.items())),
        tuple(sorted(missing_counts.items())),
        setups_detected, max(0, all_entries - all_exits),
        counter_starts, max(0, all_counter_starts - all_counter_ends),
        configuration_fingerprint,
    )


def persist_daily_report(store: ForwardCaptureStore, report: DailyForwardReport) -> tuple[int, int]:
    from datetime import datetime, time
    from .forward_models import CaptureRecord

    same_day = tuple(
        record.timestamp for record in store.records()
        if record.record_type is not CaptureRecordType.DAILY_REPORT
        and record.timestamp.astimezone(EASTERN).date() == report.trading_date
    )
    timestamp = max(
        same_day,
        default=datetime.combine(report.trading_date, time(0), tzinfo=EASTERN),
    )
    record = CaptureRecord.create(
        CaptureRecordType.DAILY_REPORT, "WARRIOR_MOMENTUM_V1", timestamp,
        {
            "trading_date": report.trading_date,
            "funnel": report.funnel, "paper_trades": report.paper_trades,
            "wins": report.wins, "losses": report.losses,
            "scratches": report.scratches, "total_r": report.total_r,
            "expectancy_r": report.expectancy_r,
            "profit_factor": report.profit_factor,
            "maximum_intraday_drawdown_r": report.maximum_intraday_drawdown_r,
            "average_mae_r": report.average_mae_r,
            "average_mfe_r": report.average_mfe_r,
            "rejection_counts": report.rejection_counts,
            "missing_data_counts": report.missing_data_counts,
            "setups_detected": report.setups_detected,
            "open_paper_positions": report.open_paper_positions,
            "counterfactual_starts": report.counterfactual_starts,
            "tracked_counterfactuals": report.tracked_counterfactuals,
            "configuration_fingerprint": report.configuration_fingerprint,
            "empty_sample_metrics_are_na": report.paper_trades == 0,
        },
        identity_parts=(
            report.trading_date.isoformat(),
            report.configuration_fingerprint or "LEGACY",
        ),
    )
    return store.append_batch((record,))


def evidence_maturity(trades: int) -> EvidenceMaturity:
    if trades < 0:
        raise ValueError("trade count cannot be negative")
    if trades == 0:
        return EvidenceMaturity.NO_TRADES
    if trades < 20:
        return EvidenceMaturity.EARLY_SAMPLE
    if trades < 100:
        return EvidenceMaturity.DEVELOPING_SAMPLE
    return EvidenceMaturity.MEANINGFUL_SAMPLE


def build_cumulative_reports(
    store: ForwardCaptureStore,
) -> tuple[CumulativeForwardReport, ...]:
    grouped_records: dict[str, list] = {}
    trading_days: dict[str, set[date]] = {}
    for record, fingerprint in _records_with_fingerprint(store.records()):
        if fingerprint is None:
            continue
        grouped_records.setdefault(fingerprint, []).append(record)
        if (
            record.record_type is CaptureRecordType.OBSERVATION_SESSION
            and record.payload.get("action") == "START"
        ):
            trading_days.setdefault(fingerprint, set()).add(
                date.fromisoformat(record.payload["trading_date"])
            )
    reports = []
    for fingerprint in sorted(grouped_records):
        trades = _completed_trades(grouped_records[fingerprint])
        values = [item["realized_r"] for item in trades]
        wins = sum(value > 0 for value in values)
        losses = sum(value < 0 for value in values)
        total, expectancy, factor, drawdown = _aggregate_r(values)
        breakdowns = []
        for category, bucket_source in (
            ("setup", lambda item: item["setup"]),
            ("score", lambda item: _score_bucket(item["momentum_score"])),
            ("rvol", lambda item: _rvol_bucket(item["relative_volume"])),
            ("float_provenance", lambda item: item["float_provenance"]),
            ("float_bucket", lambda item: _float_bucket(item["float_shares"])),
            ("price", lambda item: _price_bucket(item["price"])),
            ("catalyst", lambda item: item["catalyst_state"]),
            ("session", lambda item: item["session"]),
        ):
            buckets: dict[str, list[Decimal]] = {}
            for item in trades:
                buckets.setdefault(bucket_source(item), []).append(item["realized_r"])
            for bucket in sorted(buckets):
                bucket_values = buckets[bucket]
                _total, bucket_expectancy, bucket_factor, _drawdown = _aggregate_r(bucket_values)
                breakdowns.append(CumulativeBreakdown(
                    category, bucket, len(bucket_values),
                    bucket_expectancy or Decimal("0"), bucket_factor,
                ))
        count = len(trades)
        reports.append(CumulativeForwardReport(
            fingerprint, len(trading_days.get(fingerprint, set())), count,
            wins, losses,
            None if count == 0 else Decimal(wins) / count * Decimal("100"),
            total, expectancy, factor, drawdown, evidence_maturity(count),
            tuple(breakdowns),
        ))
    return tuple(reports)


def _records_with_fingerprint(records):
    current = None
    for record in records:
        if record.record_type is CaptureRecordType.OBSERVATION_SESSION:
            payload = record.payload
            if payload.get("action") == "START":
                current = payload.get("configuration_fingerprint")
                yield record, current
                continue
            yield record, current
            if payload.get("action") == "END":
                current = None
            continue
        yield record, current


def _completed_trades(records):
    opened: dict[str, list[dict]] = {}
    completed = []
    for record in records:
        payload = record.payload
        if record.record_type is CaptureRecordType.PAPER_FILL and payload.get("action") == "ENTRY":
            opened.setdefault(record.symbol, []).append(payload)
        elif (
            record.record_type is CaptureRecordType.STATE_TRANSITION
            and payload.get("to") == ForwardTransition.PAPER_EXIT.value
            and opened.get(record.symbol)
        ):
            entry = opened[record.symbol].pop(0)
            completed.append({
                **entry,
                "realized_r": Decimal(payload["realized_r"]),
                "momentum_score": Decimal(entry["momentum_score"]),
                "relative_volume": Decimal(entry["relative_volume"]),
                "float_shares": None if entry.get("float_shares") is None else Decimal(entry["float_shares"]),
                "price": Decimal(entry.get("price", entry["fill_price"])),
                "float_provenance": entry.get("float_provenance", "UNKNOWN"),
            })
    return completed


def _aggregate_r(values):
    if not values:
        return None, None, None, None
    total = sum(values, Decimal("0"))
    gains = sum((value for value in values if value > 0), Decimal("0"))
    losses = -sum((value for value in values if value < 0), Decimal("0"))
    equity = peak = drawdown = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return total, total / len(values), None if losses == 0 else gains / losses, drawdown


def _score_bucket(value):
    for low, high in ((25, 44), (45, 59), (60, 69), (70, 79), (80, 89), (90, 100)):
        if Decimal(low) <= value <= Decimal(high) + Decimal("0.999"):
            return f"{low}-{high}"
    return "OTHER"


def _rvol_bucket(value):
    if value < 2: return "<2x"
    if value < 5: return "2-5x"
    if value < 10: return "5-10x"
    if value < 25: return "10-25x"
    return "25x+"


def _float_bucket(value):
    if value is None: return "UNKNOWN"
    if value <= 5_000_000: return "<=5M"
    if value <= 10_000_000: return "5-10M"
    if value <= 20_000_000: return "10-20M"
    if value <= 50_000_000: return "20-50M"
    return ">50M"


def _price_bucket(value):
    if value < 2: return "$1-$2"
    if value < 5: return "$2-$5"
    if value < 10: return "$5-$10"
    return "$10-$20"


__all__ = [
    "CumulativeBreakdown", "CumulativeForwardReport", "DailyForwardReport",
    "EvidenceMaturity", "build_cumulative_reports", "build_daily_report",
    "evidence_maturity", "persist_daily_report",
]
