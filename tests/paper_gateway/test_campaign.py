from datetime import datetime, timezone
from decimal import Decimal

from app.operations.runtime import PaperRuntimeEvent
from app.paper_gateway.campaign import start_new_paper_campaign
from app.paper_gateway.durable_store import (
    DurablePaperExecutionStore,
    LEGACY_PAPER_CAMPAIGN_ID,
)


def _event(sequence: int) -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        event_type="PAPER_TEST_EVIDENCE",
        message="historical evidence",
        cycle=0,
        symbol="SUNE",
        source="campaign-test",
    )


def test_rollover_keeps_history_but_starts_empty_active_campaign(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = DurablePaperExecutionStore(path, account_id="paper-account")
    old_campaign = store.active_campaign_id
    store.persist(_event(1))

    campaign = store.start_new_paper_campaign(
        operation_key="rollover-2026-01-01", campaign_id="paper-clean-1",
        started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert campaign == "paper-clean-1"
    assert campaign != old_campaign
    assert store.active_campaign_id == campaign
    assert store.active_campaign_capital() == {
        "starting_equity": Decimal("10000"),
        "starting_cash": Decimal("10000"),
        "buying_power_multiplier": Decimal("1"),
    }
    assert store.events() == ()
    assert len(store.historical_events()) == 1
    store.close()

    reopened = DurablePaperExecutionStore(path, account_id="paper-account")
    assert reopened.events() == ()
    assert len(reopened.historical_events()) == 1
    assert reopened.start_new_paper_campaign(
        operation_key="rollover-2026-01-01", campaign_id="different"
    ) == campaign
    reopened.close()


def test_pre_campaign_ledger_is_legacy_until_explicit_rollover(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    store = DurablePaperExecutionStore(path, account_id="paper-account")
    store.persist(_event(1))
    store.close()

    # Remove only campaign metadata in this fixture to model a pre-campaign
    # database; historical event content itself is not rewritten.
    import sqlite3
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM metadata WHERE key='active_campaign_id'")
        connection.execute("DELETE FROM paper_campaigns")

    legacy = DurablePaperExecutionStore(path, account_id="paper-account")
    assert legacy.active_campaign_id is None
    assert legacy.events() == ()
    assert len(legacy.historical_events()) == 1
    campaign = start_new_paper_campaign(
        path, operation_key="legacy-rollover", campaign_id="paper-after-legacy",
        starting_equity=Decimal("25000"), starting_cash=Decimal("25000"),
        buying_power_multiplier=Decimal("2"),
    )
    assert campaign == "paper-after-legacy"
    assert legacy.active_campaign_id == campaign
    assert legacy.active_campaign_capital() == {
        "starting_equity": Decimal("25000"),
        "starting_cash": Decimal("25000"),
        "buying_power_multiplier": Decimal("2"),
    }
    assert LEGACY_PAPER_CAMPAIGN_ID == "legacy-paper-campaign"
    legacy.close()
