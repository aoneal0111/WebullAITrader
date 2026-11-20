"""NAUT observed-signal gateway exercise, strictly hypothetical PAPER orders.

This replays executable gateway *mechanics*, not the October historical order:
the 2026-10-09 capture contains no actual 0.40R/50% exit submission.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.strategies.warrior_momentum import (
    ForwardCaptureStore, ForwardCaptureWriter, WarriorForwardCaptureService,
)
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperExecutionBridge, PaperExitSubmissionState,
    lifecycle_identity,
)
from tests.test_support.session_clock import (
    FixedClock, create_session_paper_composition,
)
from tests.warrior_momentum.test_forward_capture import point

# Historical marks, not proof of actual executable order acknowledgements.
ENTRY_AT = datetime(2026, 10, 9, 16, 53, 0, tzinfo=UTC)
OBSERVED_SIGNAL_AT = datetime(2026, 10, 9, 16, 54, 15, 199777, tzinfo=UTC)
ENTRY = D("2.40")
STOP = D("2.29")
SIGNAL_BID = D("2.42")
QUANTITY = 384
CANDIDATE_PARTIAL = 96
IDENTITY = "WARRIOR_MOMENTUM_V1|NAUT|LEGACY_EPISODE|gateway-replay-only"


def _quote(composition, clock, sequence, bid, ask, size):
    clock.value = OBSERVED_SIGNAL_AT + timedelta(seconds=sequence)
    return composition.gateway.process_market_event(MarketEvent(
        sequence, clock.value, "NAUT", "naut-observed-signal-gateway-replay",
        MarketEventType.QUOTE,
        QuotePayload(D(bid), D(ask), D(size), D(size)),
    ))


def _active(composition, kind):
    return tuple(
        o for o in composition.order_book.open_orders_for_symbol("NAUT")
        if o.request.side.value == "SELL"
        and o.request.order_type.value == kind
    )


def _seed(tmp_path):
    clock = FixedClock(ENTRY_AT)
    position = {"NAUT": D("0")}
    composition = create_session_paper_composition(
        clock=clock, at=ENTRY_AT,
        persistence_path=str(tmp_path / "naut-gateway.sqlite3"),
        position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        protection_amender=composition.gateway.amend_protective_stop,
        position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    store = ForwardCaptureStore(tmp_path / "naut-capture.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    seed = WarriorForwardCaptureService(store, writer)
    candidate = seed.runtime.discover(
        point().observation, point().bars, session="REGULAR",
    )
    _candidate, reference = seed.runtime.assess_entry(candidate)
    assert reference is not None
    signal = replace(
        reference, symbol="NAUT", timestamp=ENTRY_AT,
        entry_trigger=ENTRY, reference_price=ENTRY,
        stop_price=STOP, structural_stop_price=STOP,
        risk_per_share=ENTRY - STOP,
        target_levels=(D("2.51"), D("2.62"), D("2.73")),
        taxonomy_execution_identity=IDENTITY,
        structural_episode_id="naut-replay-episode",
    )
    assert bridge.submit_entry(signal, QUANTITY, D("42.24"))
    _quote(composition, clock, -65, "2.39", "2.40", "500")
    entry_order = next(o for o in composition.order_book.history()
                       if o.request.side.value == "BUY")
    assert entry_order.filled_quantity == D(QUANTITY)
    assert entry_order.average_fill_price == ENTRY
    position["NAUT"] = D(QUANTITY)
    stop = bridge.ensure_exit("NAUT", QUANTITY, STOP, "STOP", lifecycle_identity(signal))
    assert stop.protection_active and stop.requested_order_active
    assert len(_active(composition, "STOP")) == 1
    return composition, bridge, writer, clock, position, signal


@pytest.mark.parametrize("first_fill", [30, 96])
def test_naut_observed_signal_can_submit_market_partial_and_keep_stop(
    tmp_path, first_fill,
):
    composition, bridge, writer, clock, position, signal = _seed(tmp_path)
    try:
        # The *research overlay*, not production Warrior, requests this
        # hypothetical 25% sale after the sampled 2.42 BID.
        clock.value = OBSERVED_SIGNAL_AT
        result = bridge.ensure_exit(
            "NAUT", CANDIDATE_PARTIAL, SIGNAL_BID,
            "PROFIT_HARVEST_1", lifecycle_identity(signal),
        )
        assert result.state in {
            PaperExitSubmissionState.SUBMITTED,
            PaperExitSubmissionState.WORKING,
        }
        assert result.requested_order_active
        harvest = next(o for o in composition.order_book.history()
                       if o.request.execution_reason == "PROFIT_HARVEST_1")
        assert harvest.request.order_type.value == "MARKET"
        assert harvest.filled_quantity == 0
        assert len(_active(composition, "STOP")) == 1

        # Quoted signal BID is not an order fill. The *subsequent* test quote
        # supplies the actual price and finite liquidity.
        _quote(composition, clock, 1, "2.41", "2.42", first_fill)
        partial = composition.order_book.get(harvest.order_id)
        assert partial.filled_quantity == D(first_fill)
        assert partial.average_fill_price == D("2.41")
        position["NAUT"] = D(QUANTITY - first_fill)
        assert len(_active(composition, "STOP")) == 1
        if first_fill < CANDIDATE_PARTIAL:
            _quote(composition, clock, 2, "2.40", "2.41", 100)
            assert composition.order_book.get(harvest.order_id).filled_quantity == D(96)
            position["NAUT"] = D(288)
        assert all(
            o.remaining_quantity <= D(QUANTITY)
            for o in composition.order_book.open_orders_for_symbol("NAUT")
            if o.request.side.value == "SELL"
        )
        assert len(_active(composition, "STOP")) == 1
        assert bridge.ensure_exit(
            "NAUT", 288, STOP, "STOP", lifecycle_identity(signal),
        ).protection_active
    finally:
        writer.close()
        composition.close()


def test_naut_signal_does_not_claim_fill_without_next_market_quote(tmp_path):
    composition, bridge, writer, clock, _position, signal = _seed(tmp_path)
    try:
        clock.value = OBSERVED_SIGNAL_AT
        result = bridge.ensure_exit(
            "NAUT", CANDIDATE_PARTIAL, SIGNAL_BID,
            "PROFIT_HARVEST_1", lifecycle_identity(signal),
        )
        assert result.requested_order_active
        harvest = next(o for o in composition.order_book.history()
                       if o.request.execution_reason == "PROFIT_HARVEST_1")
        assert harvest.filled_quantity == 0
        assert len(_active(composition, "STOP")) == 1
    finally:
        writer.close()
        composition.close()
