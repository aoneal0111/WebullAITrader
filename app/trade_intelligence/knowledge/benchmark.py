"""Isolated point-in-time benchmark acquisition and regime side-table build."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from app.market.calendar import EASTERN, NYSE

from .acquisition import AcquisitionConfig, AlpacaHistoricalClient, download_partition, partition_paths

BENCHMARK_REGIME_VERSION = "ATLAS_BENCHMARK_REGIME_V1"
BENCHMARK_SYMBOLS = ("SPY", "QQQ", "IWM")


def trading_dates(start: date, end: date) -> tuple[date, ...]:
    schedule = NYSE.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    return tuple(item.date() for item in schedule.index)


def _write_failure(path: Path, symbol: str, day: date, error: Exception, attempts: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"provider": "ALPACA", "feed": "IEX", "symbol": symbol, "trading_date": day.isoformat(),
             "status": "FAILED_TRANSIENT_EXHAUSTED", "failure_reason": str(error)[:240],
             "attempts": attempts, "derivation_version": BENCHMARK_REGIME_VERSION}
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def acquire_benchmarks(client: AlpacaHistoricalClient, *, root: Path, start: date, end: date) -> dict[str, object]:
    """Acquire only the three explicit benchmark symbols into an isolated root."""
    config = AcquisitionConfig(start_date=start, end_date=end, raw_root=root / "raw",
                               normalized_root=root / "normalized", manifest_root=root / "manifests")
    days = trading_dates(start, end)
    planned = [(symbol, day) for symbol in BENCHMARK_SYMBOLS for day in days]
    manifests: list[dict[str, object]] = []
    for symbol, day in planned:
        try:
            manifest = download_partition(client, config, symbol, day)
            manifests.append(manifest.to_dict())
        except Exception as exc:
            _, _, manifest_path = partition_paths(config, symbol, day)
            _write_failure(manifest_path, symbol, day, exc, config.max_retries + 1)
            manifests.append({"symbol": symbol, "trading_date": day.isoformat(),
                              "status": "FAILED_TRANSIENT_EXHAUSTED", "failure_reason": str(exc)[:240]})
    root.mkdir(parents=True, exist_ok=True)
    (root / "acquisition_manifest.json").write_text(json.dumps({
        "schema_version": 1, "derivation_version": BENCHMARK_REGIME_VERSION,
        "provider": "ALPACA", "feed": "IEX", "symbols": BENCHMARK_SYMBOLS,
        "start_date": start.isoformat(), "end_date": end.isoformat(), "partitions": manifests,
        "requests": dict(client.request_counters)}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"symbol_days_planned": len(planned),
            "complete": sum(item.get("status") == "COMPLETE" for item in manifests),
            "empty": sum(item.get("status") == "EMPTY_CONFIRMED" for item in manifests),
            "failed": sum(str(item.get("status", "")).startswith("FAILED") for item in manifests),
            "manifests": manifests, "requests": dict(client.request_counters)}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_benchmark_context(root: Path, *, start: date, end: date) -> Path:
    """Build a compact, timestamped bar-derived benchmark side table."""
    database = root / "benchmark_context.sqlite3"
    temporary = database.with_suffix(".sqlite3.tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    connection.execute("""CREATE TABLE benchmark_context (
        benchmark_symbol TEXT, trading_date TEXT, effective_timestamp TEXT, session TEXT,
        price REAL, return_from_open REAL, return_1m REAL, return_5m REAL, return_10m REAL,
        return_30m REAL, vwap REAL, above_vwap INTEGER, hod_distance REAL, lod_distance REAL,
        range_percent REAL, volatility REAL, momentum REAL, volume_acceleration REAL,
        opening_range_position TEXT, derivation_version TEXT,
        PRIMARY KEY(benchmark_symbol, effective_timestamp))""")
    connection.execute("CREATE INDEX benchmark_context_asof ON benchmark_context(trading_date, benchmark_symbol, effective_timestamp)")
    for symbol in BENCHMARK_SYMBOLS:
        for day in trading_dates(start, end):
            path = root / "normalized" / f"{symbol}_{day.isoformat()}.jsonl"
            if not path.exists():
                continue
            bars = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    timestamp = datetime.fromisoformat(str(item["timestamp"]))
                    bars.append((timestamp, item))
            bars.sort(key=lambda value: value[0])
            regular = [item for timestamp, item in bars if str(item.get("session")).upper() == "REGULAR"]
            opening = regular[:5]
            open_price = _num(opening[0].get("open")) if opening else None
            prior_closes: list[float] = []
            cumulative_volume = 0.0
            cumulative_dollar = 0.0
            for index, (timestamp, item) in enumerate(bars):
                price = _num(item.get("close")); high = _num(item.get("high")); low = _num(item.get("low"))
                volume = _num(item.get("volume")) or 0.0
                if price is None or high is None or low is None:
                    continue
                cumulative_volume += volume; cumulative_dollar += price * volume
                core_before = [(_num(value.get("high")), _num(value.get("low"))) for _, value in bars[:index + 1]
                               if str(value.get("session")).upper() == "REGULAR"]
                rolling = [(_num(value.get("high")) or 0) - (_num(value.get("low")) or 0)
                           for _, value in bars[max(0, index - 10):index] if _num(value.get("high")) is not None]
                prior_volume = [_num(value.get("volume")) for _, value in bars[max(0, index - 10):index]
                                if _num(value.get("volume")) is not None]
                vwap = cumulative_dollar / cumulative_volume if cumulative_volume else None
                recent_mean = sum(prior_volume) / len(prior_volume) if prior_volume else None
                volatility = (sum(rolling) / len(rolling)) / price * 100 if rolling and price else None
                ret = lambda offset: None if index < offset or _num(bars[index - offset][1].get("close")) in (None, 0) else (price / _num(bars[index - offset][1].get("close")) - 1) * 100
                opening_high = max((pair[0] for pair in core_before[:5] if pair[0] is not None), default=None) if len(core_before) >= 5 else None
                opening_low = min((pair[1] for pair in core_before[:5] if pair[1] is not None), default=None) if len(core_before) >= 5 else None
                position = None if opening_high is None or opening_low is None else ("ABOVE" if price > opening_high else "BELOW" if price < opening_low else "INSIDE")
                asof_high = max((pair[0] for pair in core_before if pair[0] is not None), default=None)
                asof_low = min((pair[1] for pair in core_before if pair[1] is not None), default=None)
                connection.execute("INSERT INTO benchmark_context VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    symbol, day.isoformat(), timestamp.isoformat(), item.get("session"), price,
                    None if open_price in (None, 0) else (price / open_price - 1) * 100,
                    ret(1), ret(5), ret(10), ret(30), vwap, None if vwap is None else int(price >= vwap),
                    None if asof_high in (None, 0) else (asof_high - price) / asof_high * 100,
                    None if asof_low in (None, 0) else (price - asof_low) / asof_low * 100,
                    (high - low) / price * 100, volatility, ret(5),
                    None if recent_mean in (None, 0) else volume / recent_mean,
                    position, BENCHMARK_REGIME_VERSION))
    connection.commit(); connection.close()
    temporary.replace(database)
    return database
