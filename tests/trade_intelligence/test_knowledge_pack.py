from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from app.trade_intelligence.knowledge.identity import episode_id, membership_id, near_key
from app.trade_intelligence.knowledge.mining import JsonlBarProvider, _outcomes, build_corpus
from app.trade_intelligence.knowledge.models import ACTIVE_STRATEGIES
from app.trade_intelligence.knowledge.reporting import validate_corpus
from app.trade_intelligence.knowledge.storage import KnowledgeStore


def _write(path, bars):
    path.write_text("\n".join(json.dumps({
        "symbol": "XYZ", "timestamp": bar[0].isoformat(), "open": str(bar[1]),
        "high": str(bar[2]), "low": str(bar[3]), "close": str(bar[4]), "volume": "1000",
    }) for bar in bars) + "\n", encoding="utf-8")


def test_identity_and_near_keys_are_deterministic():
    at = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    first = episode_id(symbol="XYZ", trading_date="2026-01-02", session="REGULAR",
                       structural_anchor="A", setup_start=at, trigger_price="2", structural_stop="1")
    assert first == episode_id(symbol="XYZ", trading_date="2026-01-02", session="REGULAR",
                               structural_anchor="A", setup_start=at, trigger_price="2", structural_stop="1")
    assert membership_id(first, "MICRO_PULLBACK") != membership_id(first, "FIRST_PULLBACK")
    assert near_key("xyz", "2026-01-02", "regular", "MICRO_PULLBACK", "A").startswith("XYZ|")


def test_build_is_idempotent_and_no_lookahead(tmp_path):
    start = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    bars = []
    prices = [(1, 1.1, .99, 1.05), (1.05, 1.2, 1.02, 1.15), (1.15, 1.3, 1.1, 1.25),
              (1.25, 1.3, 1.2, 1.22), (1.22, 1.32, 1.2, 1.3), (1.3, 1.5, 1.29, 1.45)]
    for index, values in enumerate(prices):
        bars.append((start + timedelta(minutes=index), *(Decimal(str(item)) for item in values)))
    source = tmp_path / "bars.jsonl"; _write(source, bars)
    output = tmp_path / "pack"
    first = build_corpus(JsonlBarProvider(source), output, repository_commit="test")
    second = build_corpus(JsonlBarProvider(source), output, repository_commit="test")
    assert first.accepted_unique >= 0
    assert first.accepted_unique > 0
    assert second.accepted_unique == 0
    assert second.exact_duplicates >= 0
    assert not validate_corpus(output)


def test_store_status_separates_physical_episodes_from_memberships(tmp_path):
    store = KnowledgeStore(tmp_path / "pack")
    assert store.status()["unique_episodes"] == 0
    assert len(ACTIVE_STRATEGIES) == 23


def test_outcomes_are_forward_only_and_same_bar_order_is_unknown():
    cutoff = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    future = (type("Bar", (), {"timestamp": cutoff + timedelta(minutes=1), "high": Decimal("2.2"),
                               "low": Decimal("0.8")})(),)
    value = _outcomes(Decimal("2"), Decimal("1"), cutoff, future)
    assert value["percent_targets"]["8"]["first_plan_event"] == "INTRABAR_ORDER_UNKNOWN"
    assert value["percent_targets"]["8"]["hit"] is True
    assert value["mfe_r"] == "0.2"
    assert value["post_8"]["time_to_8"] == 60
    assert value["profit_management_research"]["2"]["target_hit"] is True


def test_malformed_source_rows_are_quarantined(tmp_path):
    source = tmp_path / "bad.jsonl"
    source.write_text('{"symbol":"XYZ","timestamp":"not-a-time"}\n', encoding="utf-8")
    provider = JsonlBarProvider(source)
    assert tuple(provider.bars()) == ()
    assert provider.errors and provider.errors[0][0] == 1
