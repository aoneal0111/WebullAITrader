"""Offline copy selection must choose supported evidence, never invent joins."""
from copy import deepcopy

import pytest

from app.trade_intelligence.knowledge.capture_copy_selection import (
    select_capture_copies, inspect_capture_conflicts,
)


LIFE = "WARRIOR_MOMENTUM_V1|ABC|episode"


def data(*, auth=False, extra=False):
    order = {"order_id": "buy", "paper_campaign_id": "campaign",
             "request": {"side": "BUY", "symbol": "ABC",
                         "strategy_lifecycle_id": LIFE}}
    out = {"orders": [order], "capture_records": [
        {"timestamp": "2026-10-09T15:00:00+00:00",
         "payload": {"action": "QUOTE", "paper_campaign_id": "campaign",
                     "lifecycle_id": LIFE, "bid": "10", "ask": "10.01"}}
    ]}
    if auth:
        out["entry_context"] = {"records": [{
            "symbol": "ABC", "record_type": "STATE_TRANSITION",
            "payload": {"to": "AUTHORIZATION_EVALUATED",
                        "authorization_result": "AUTHORIZED",
                        "paper_campaign_id": "campaign", "lifecycle_id": LIFE},
        }, {"symbol": "ABC", "record_type": "DECISION", "payload": {}}]}
    if extra:
        out["entry_context"]["records"].append(
            {"record_type": "DATA_QUALITY", "symbol": "ABC", "payload": {}}
        )
    return out


def test_selects_complete_copy_and_ignores_file_order():
    older, newer = data(), data(auth=True)
    for files in (
        [("a-old.json", older), ("b-new.json", newer)],
        [("b-new.json", newer), ("a-old.json", older)],
    ):
        result = select_capture_copies(files)
        assert result["selected_files"] == ["b-new.json"]
        assert result["unique_lifecycles"] == 1
        assert result["matching_authorization_coverage"] == 1
        assert result["missing_matching_authorization"] == []
        assert len(result["skipped"]) == 1
        assert result["runtime_changed"] is False


def test_no_authorization_not_silently_invented():
    result = select_capture_copies([("only.json", data())])
    assert result["matching_authorization_coverage"] == 0
    assert len(result["missing_matching_authorization"]) == 1


def test_conflicting_captured_price_paths_refused():
    old, changed = data(), data(auth=True)
    changed["capture_records"][0]["payload"]["bid"] = "11"
    with pytest.raises(ValueError, match="CONFLICTING_LIFECYCLE_EVIDENCE"):
        select_capture_copies([("old", old), ("changed", changed)])


def test_conflicting_order_ledger_refused():
    old, changed = data(), data(auth=True)
    changed["orders"][0]["order_id"] = "different"
    with pytest.raises(ValueError, match="CONFLICTING_LIFECYCLE_EVIDENCE"):
        select_capture_copies([("old", old), ("changed", changed)])


def test_tie_break_is_stable_and_no_mutation():
    first, second = data(auth=True), data(auth=True, extra=True)
    original = deepcopy(second)
    result = select_capture_copies([("b.json", first), ("a.json", second)])
    assert result["selected_files"] == ["a.json"]
    assert second == original


def test_outside_scope_and_empty_input_rejected():
    with pytest.raises(ValueError, match="INVALID_CAPTURE_COUNT"):
        select_capture_copies([])
    with pytest.raises(ValueError, match="NO_WARRIOR_LIFECYCLE"):
        select_capture_copies([("non-warrior", {"orders": [
            {"paper_campaign_id": "campaign",
             "request": {"symbol": "ABC", "strategy_lifecycle_id": "OTHER"}}
        ]})])


def test_conflict_inspection_reports_exact_lifecycle_and_class():
    original, changed = data(), data(auth=True)
    changed["capture_records"][0]["payload"]["bid"] = "11"
    report = inspect_capture_conflicts([
        ("old.json", original), ("new.json", changed)
    ])
    assert report["unique_lifecycles"] == 1
    assert report["repeated_lifecycles"] == 1
    assert report["conflicting_lifecycles"] == 1
    assert report["selection_permitted"] is False
    item = report["conflicts"][0]
    assert item["symbol"] == "ABC"
    assert item["ledger_conflict"] is False
    assert item["price_path_conflict"] is True
    assert {r["file"] for r in item["files"]} == {"old.json", "new.json"}
    assert "payload" not in str(report)


def test_conflict_inspection_is_read_only_and_reports_clean_copies():
    before = data()
    newer = data(auth=True)
    original = deepcopy(newer)
    report = inspect_capture_conflicts([
        ("old.json", before), ("new.json", newer)
    ])
    assert report["selection_permitted"] is True
    assert report["conflicts"] == []
    assert newer == original


def test_conflict_inspection_catches_ledger_changes():
    old, changed = data(), data(auth=True)
    changed["orders"][0]["order_id"] = "new-order-id"
    report = inspect_capture_conflicts([
        ("old.json", old), ("new.json", changed)
    ])
    assert report["conflicts"][0]["ledger_conflict"] is True
    assert report["conflicts"][0]["price_path_conflict"] is False


def test_accepts_exact_ordered_subsequence_and_prefers_fuller_copy():
    complete = data(auth=True)
    newer_quote = deepcopy(complete["capture_records"][0])
    newer_quote["timestamp"] = "2026-10-09T15:00:02+00:00"
    newer_quote["payload"]["bid"] = "10.20"
    complete["capture_records"].append(newer_quote)
    partial = data()
    old = deepcopy(complete)
    result = select_capture_copies([
        ("old.json", partial), ("complete.json", complete)
    ])
    assert result["selected_files"] == ["complete.json"]
    assert result["matching_authorization_coverage"] == 1
    assert complete == old
    inspection = inspect_capture_conflicts([
        ("old.json", partial), ("complete.json", complete)
    ])
    assert inspection["conflicts"][0]["exact_subset_compatible"] is True
    assert inspection["selection_permitted"] is False


def test_rejects_changed_shared_quote_even_with_more_records():
    partial = data()
    complete = data(auth=True)
    complete["capture_records"][0]["payload"]["bid"] = "9.99"
    additional = deepcopy(complete["capture_records"][0])
    additional["timestamp"] = "2026-10-09T15:00:03+00:00"
    complete["capture_records"].append(additional)
    with pytest.raises(ValueError, match="CONFLICTING_LIFECYCLE_EVIDENCE"):
        select_capture_copies([("partial", partial), ("complete", complete)])


def test_rejects_reordered_observations_or_duplicate_multiplicity():
    full = data(auth=True)
    a = deepcopy(full["capture_records"][0])
    b = deepcopy(a)
    b["timestamp"] = "2026-10-09T15:00:01+00:00"
    full["capture_records"] = [a, b]
    reverse = data()
    reverse["capture_records"] = [b, a]
    with pytest.raises(ValueError, match="CONFLICTING_LIFECYCLE_EVIDENCE"):
        select_capture_copies([("full", full), ("reverse", reverse)])
    repeated = data()
    repeated["capture_records"] = [a, a]
    with pytest.raises(ValueError, match="CONFLICTING_LIFECYCLE_EVIDENCE"):
        select_capture_copies([("full", full), ("repeated", repeated)])
