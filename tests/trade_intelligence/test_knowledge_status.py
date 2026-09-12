import json
from pathlib import Path

from app.trade_intelligence.knowledge.accounting import status_for_root


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_status_aggregates_partition_states_and_sizes(tmp_path):
    for name, state in (("a", "COMPLETE"), ("b", "EMPTY_CONFIRMED"),
                        ("c", "FAILED_TRANSIENT"), ("d", "CORRUPT")):
        _write(tmp_path / "manifests" / f"{name}.json", {
            "provider": "ALPACA", "feed": "IEX", "symbol": name.upper(),
            "trading_date": "2026-01-02", "status": state,
            "row_count": 3 if state == "COMPLETE" else 0,
            "downloaded_at": "2026-01-03T00:00:00+00:00",
        })
    (tmp_path / "raw").mkdir(); (tmp_path / "raw" / "a.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "normalized").mkdir(); (tmp_path / "normalized" / "A_2026-01-02.jsonl").write_text("{}\n", encoding="utf-8")
    value = status_for_root(tmp_path)
    assert value["partitions"] == {"planned": 4, "in_progress": 0, "complete": 1,
                                    "empty_confirmed": 1, "failed_transient": 1,
                                    "failed_permanent": 0, "corrupt": 1}
    assert value["rows"] == {"raw": 3, "normalized": 1}
    assert value["provider"] == "ALPACA" and value["feed"] == "IEX"


def test_status_reconstructs_sample_without_credentials_or_network(tmp_path):
    _write(tmp_path / "manifests" / "a.json", {
        "provider": "ALPACA", "feed": "IEX", "symbol": "AAPL",
        "trading_date": "2026-01-02", "status": "COMPLETE", "row_count": 2,
        "downloaded_at": "2026-01-03T00:00:00+00:00", "content_hash": "x",
    })
    value = status_for_root(tmp_path)
    assert value["run_id"] and value["run_status"] == "SUCCEEDED"
    assert len(value["quota"]) == 23
    assert (tmp_path / "run_manifest.json").exists()


def test_status_reads_quota_from_corpus_episodes(tmp_path):
    _write(tmp_path / "episodes.jsonl", {"episode_id": "e", "symbol": "A",
                                          "trading_date": "2026-01-02",
                                          "strategy_memberships": ["MICRO_PULLBACK"]})
    value = status_for_root(tmp_path, persist_reconstruction=False)
    assert value["quota"]["MICRO_PULLBACK"]["accepted"] == 1
    assert value["quota"]["MICRO_PULLBACK"]["remaining"] == 4999
