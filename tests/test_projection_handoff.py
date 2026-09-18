from __future__ import annotations

from app.strategies.warrior_momentum.projection_handoff import (
    BoundedProjectionHandoff,
)


def test_projection_handoff_is_bounded_and_coalesces_latest_state():
    values: list[object] = []
    handoff = BoundedProjectionHandoff(values.append, maximum_keys=2)
    handoff.start(worker=False)
    try:
        assert handoff.submit("AEMD", 1)
        assert handoff.submit("AEMD", 2)
        assert handoff.submit("CPOP", 3)
        assert handoff.submit("SSM", 4) is False
        metrics = handoff.metrics()
        assert metrics.pending <= 2
        assert metrics.coalesced == 1
        assert metrics.dropped == 1
    finally:
        handoff.stop(drain=False)


def test_projection_handoff_deterministic_drain_preserves_distinct_symbols():
    values: list[object] = []
    handoff = BoundedProjectionHandoff(values.append, maximum_keys=8)
    handoff.start(worker=False)
    try:
        for index in range(8):
            assert handoff.submit(f"SYM{index}", index)
        assert handoff.submit("OVERFLOW", 300) is False
        metrics = handoff.metrics()
        assert metrics.high_water <= 8
        assert metrics.pending <= 8
    finally:
        handoff.stop(drain=False)


def test_projection_handoff_drain_once_supports_offline_determinism():
    values: list[object] = []
    handoff = BoundedProjectionHandoff(values.append, maximum_keys=8)
    handoff.start(worker=False)
    handoff.submit("AEMD", "first")
    handoff.submit("AEMD", "latest")
    assert handoff.drain_once()
    handoff.stop(drain=False)
    assert values == ["latest"]
