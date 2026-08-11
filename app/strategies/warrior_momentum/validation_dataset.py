"""Reproducible, sanitized historical dataset capture and loading."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from .models import MinuteBar


@dataclass(frozen=True, slots=True)
class SessionReference:
    symbol: str
    session_date: str
    previous_close: Decimal
    average_prior_30_day_volume: Decimal


@dataclass(frozen=True, slots=True)
class ValidationDataset:
    dataset_id: str
    captured_at: datetime
    source: str
    selection_method: str
    bars: tuple[MinuteBar, ...]
    references: tuple[SessionReference, ...]
    catalyst_evidence: str
    spread_evidence: str
    float_evidence: str
    halt_evidence: str
    tradability_evidence: str
    sha256: str


def write_dataset(
    directory: Path,
    *,
    captured_at: datetime,
    bars: Iterable[MinuteBar],
    references: Iterable[SessionReference],
    symbols: tuple[str, ...],
) -> dict[str, object]:
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    directory.mkdir(parents=True, exist_ok=True)
    ordered_bars = tuple(sorted(bars, key=lambda item: (item.timestamp, item.symbol)))
    ordered_references = tuple(sorted(references, key=lambda item: (item.session_date, item.symbol)))
    lines = tuple(json.dumps({
        "symbol": item.symbol, "timestamp": item.timestamp.isoformat(),
        "open": str(item.open), "high": str(item.high), "low": str(item.low),
        "close": str(item.close), "volume": str(item.volume),
    }, sort_keys=True, separators=(",", ":")) for item in ordered_bars)
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    digest = sha256(payload).hexdigest()
    (directory / "bars.jsonl").write_bytes(payload)
    dates = tuple(sorted({item.timestamp.date().isoformat() for item in ordered_bars}))
    symbol_coverage = {}
    for symbol in symbols:
        symbol_bars = tuple(item for item in ordered_bars if item.symbol == symbol)
        symbol_dates = tuple(sorted({item.timestamp.date().isoformat() for item in symbol_bars}))
        symbol_coverage[symbol] = {
            "bar_count": len(symbol_bars), "session_count": len(symbol_dates),
            "date_start": symbol_dates[0] if symbol_dates else None,
            "date_end": symbol_dates[-1] if symbol_dates else None,
        }
    manifest = {
        "schema_version": 1,
        "dataset_id": f"warrior-v1-{digest[:16]}",
        "captured_at": captured_at.isoformat(),
        "source": "WEBULL_OPENAPI_READ_ONLY_HISTORY",
        "selection_method": "fixed symbols from 2026-08-10 Atlas production momentum observation",
        "symbols": list(symbols), "symbol_count": len(symbols),
        "symbol_coverage": symbol_coverage,
        "dates": list(dates), "date_start": dates[0] if dates else None,
        "date_end": dates[-1] if dates else None, "bar_count": len(ordered_bars),
        "bar_interval": "1 minute", "bar_timestamp_semantics": "interval open",
        "references": [
            {"symbol": item.symbol, "session_date": item.session_date,
             "previous_close": str(item.previous_close),
             "average_prior_30_day_volume": str(item.average_prior_30_day_volume)}
            for item in ordered_references
        ],
        "evidence": {
            "catalyst": "UNAVAILABLE: no point-in-time earnings/SEC archive",
            "spread": "UNAVAILABLE: OHLCV history has no bid/ask",
            "float": "UNKNOWN: current market-cap proxy would introduce lookahead",
            "halt": "UNAVAILABLE: history response has no halt lifecycle",
            "tradability": "UNKNOWN historically; dataset inclusion is not historical tradability proof",
        },
        "limitations": [
            "symbols selected from a later scanner snapshot; survivorship/selection bias",
            "regular-session bars only in returned history",
            "no point-in-time scanner rank snapshots",
        ],
        "bars_sha256": digest,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def load_dataset(directory: Path) -> ValidationDataset:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload = (directory / "bars.jsonl").read_bytes()
    digest = sha256(payload).hexdigest()
    if digest != manifest["bars_sha256"]:
        raise ValueError("validation dataset hash mismatch")
    bars = tuple(MinuteBar(
        item["symbol"], datetime.fromisoformat(item["timestamp"]),
        Decimal(item["open"]), Decimal(item["high"]), Decimal(item["low"]),
        Decimal(item["close"]), Decimal(item["volume"]),
    ) for item in (json.loads(line) for line in payload.decode("utf-8").splitlines() if line))
    references = tuple(SessionReference(
        item["symbol"], item["session_date"], Decimal(item["previous_close"]),
        Decimal(item["average_prior_30_day_volume"]),
    ) for item in manifest["references"])
    evidence = manifest["evidence"]
    return ValidationDataset(
        dataset_id=manifest["dataset_id"], captured_at=datetime.fromisoformat(manifest["captured_at"]),
        source=manifest["source"], selection_method=manifest["selection_method"],
        bars=bars, references=references, catalyst_evidence=evidence["catalyst"],
        spread_evidence=evidence["spread"], float_evidence=evidence["float"],
        halt_evidence=evidence["halt"], tradability_evidence=evidence["tradability"],
        sha256=digest,
    )


__all__ = ["SessionReference", "ValidationDataset", "write_dataset", "load_dataset"]
