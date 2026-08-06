from datetime import datetime, timezone
from decimal import Decimal

from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import ApplicationStateStore, OperationsBus
from app.paper_trading.models import PaperFill


def _event(sequence, fill=None, mark=None):
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=datetime(2026, 8, 6, 15, sequence, tzinfo=timezone.utc),
        event_type="fill" if fill else "mark",
        message="portfolio fact",
        cycle=sequence,
        symbol="ABC",
        fill=fill,
        mark_price=mark,
    )


def test_projection_replay_is_deterministic_and_deduplicates_fills():
    fill = PaperFill("fill-1", "ABC", "BUY", Decimal("2"), Decimal("10"), Decimal("20"), Decimal("0"), datetime(2026, 8, 6, 15, 1, tzinfo=timezone.utc))
    events = (_event(1, fill, Decimal("10")), _event(2, mark=Decimal("11")))
    snapshots = []
    for _ in range(2):
        bus = OperationsBus()
        store = ApplicationStateStore(bus)
        pipeline = create_runtime_projection_pipeline(operations_bus=bus, account_id="paper")
        for event in events + (events[0],):
            pipeline.sink(event)
        snapshots.append(store.snapshot().portfolio_intelligence)
        store.close()
    assert snapshots[0] == snapshots[1]
    assert snapshots[0].positions[0].market_value == Decimal("22")
    assert snapshots[0].performance.cumulative_realized_pnl == Decimal("0")


def test_broker_reconciliation_compatible_account_source():
    from app.portfolio_intelligence import PortfolioAccount

    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    pipeline = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id="broker-neutral",
        portfolio_account_source=lambda: PortfolioAccount("broker-neutral", Decimal("100"), Decimal("50"), Decimal("40")),
    )
    fill = PaperFill("fill", "ABC", "BUY", Decimal("1"), Decimal("10"), Decimal("10"), Decimal("0"), datetime(2026, 8, 6, 15, 1, tzinfo=timezone.utc))
    pipeline.sink(_event(1, fill, Decimal("10")))
    assert store.snapshot().portfolio_intelligence.account.equity == Decimal("100")
    assert store.snapshot().portfolio_intelligence.exposure.gross_exposure == Decimal("10")
    store.close()
