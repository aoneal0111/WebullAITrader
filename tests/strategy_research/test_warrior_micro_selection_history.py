"""Regression tests for read-only normalized-bar entry timing adapter."""
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from app.strategy_research.warrior_micro_selection_history import run
from tests.warrior_momentum.test_strategy import micro_bars


def write_partition(tmp_path: Path, bars, *, symbol="XYZ", day="2026-08-10"):
    source = tmp_path / f"{symbol}_{day}.jsonl"
    with source.open("x", encoding="utf-8") as output:
        for bar in bars:
            record = asdict(bar)
            record["timestamp"] = bar.timestamp.isoformat()
            for key in ("open", "high", "low", "close", "volume"):
                record[key] = str(record[key])
            output.write(json.dumps(record) + "\n")
    return source


def test_normalized_session_read_only_and_source_provenance(tmp_path):
    source = write_partition(tmp_path, micro_bars())
    before = source.read_bytes()
    report_path = tmp_path / "timing-report.json"
    result = run(source, symbol="XYZ", day="2026-08-10", output=report_path)
    assert source.read_bytes() == before
    assert result["bars"] == 6
    assert sum(result["micro_states"].values()) == 6
    assert result["gap_boundaries"] == 0
    assert result["longest_contiguous_run"] == 6
    assert len(result["observations"]) == 1
    assert result["observations"][0]["at"] == "2026-08-10T13:36:00+00:00"
    assert result["orders"] == result["network_requests"] == 0
    assert result["promotion"] == "NONE"
    assert json.loads(report_path.read_text()) == result
    with pytest.raises(ValueError, match="OUTPUT_ALREADY_EXISTS"):
        run(source, symbol="XYZ", day="2026-08-10", output=report_path)


def test_reject_wrong_day_and_process_entire_long_session(tmp_path):
    source = write_partition(tmp_path, micro_bars())
    with pytest.raises(ValueError, match="EXPLICIT_NORMALIZED_PARTITION_REQUIRED"):
        run(source, symbol="XYZ", day="2026-08-11", output=tmp_path / "bad.json")
    extended = tuple(micro_bars())
    from dataclasses import replace
    from datetime import timedelta
    bars = list(extended)
    for i in range(6, 601):
        bars.append(replace(extended[-1], timestamp=extended[-1].timestamp + timedelta(minutes=i - 5)))
    many = tmp_path / "many"
    many.mkdir()
    huge = write_partition(many, bars)
    result = run(huge, symbol="XYZ", day="2026-08-10", output=many / "full.json")
    assert result["bars"] == 601
    assert sum(result["micro_states"].values()) == 601
    assert (many / "full.json").exists()
