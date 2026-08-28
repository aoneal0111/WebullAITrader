from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.gui.formatters.prices import format_price
from app.gui.formatters.warrior_paper import format_warrior_paper
from app.gui.formatters.watchlist import format_watchlist
from app.momentum_scanner.models import CatalystStatus, CatalystType, ScannerObservation
from app.read_models.watchlist import WatchlistEntry, WatchlistState
from app.strategies.warrior_momentum.desktop_sidecar import (
    WarriorCaptureHealth,
    WarriorFocusItem,
    WarriorPaperSnapshot,
)
from app.strategies.warrior_momentum.forward_models import FloatProvenance
from app.strategies.warrior_momentum.models import (
    MinuteBar,
    SetupDetection,
    SetupState,
    SetupType,
    StopModel,
)
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime


def _bars() -> tuple[MinuteBar, ...]:
    values = (
        ("18:47", "2.671", "2.671", "2.512", "2.530", "132248"),
        ("18:48", "2.540", "2.550", "2.510", "2.550", "54319"),
        ("18:49", "2.540", "2.545", "2.510", "2.530", "50806"),
        ("18:50", "2.525", "2.539", "2.519", "2.539", "16349"),
        ("18:51", "2.540", "2.560", "2.532", "2.560", "36510"),
        ("18:52", "2.565", "2.590", "2.551", "2.569", "28644"),
    )
    return tuple(
        MinuteBar(
            "AEMD",
            datetime.fromisoformat(f"2026-08-28T{minute}:00+00:00"),
            *(Decimal(value) for value in fields),
        )
        for minute, *fields in values
    )


def _candidate():
    observation = ScannerObservation(
        symbol="AEMD",
        timestamp=datetime(2026, 8, 28, 18, 53, 0, 779000, tzinfo=UTC),
        price=Decimal("2.545"),
        previous_close=Decimal("2.17"),
        current_volume=Decimal("43405307"),
        average_30_day_volume=Decimal("600490.3533333333333333333333"),
        float_shares=Decimal("711136"),
        bid=Decimal("2.540"),
        ask=Decimal("2.550"),
        catalyst=CatalystType.NONE,
        catalyst_headline=None,
        tradable=True,
        halted=False,
        catalyst_status=CatalystStatus.FALSE,
    )
    return WarriorMomentumRuntime().discover(
        observation,
        _bars(),
        session="REGULAR",
    )


def test_adaptive_price_policy_preserves_meaningful_precision() -> None:
    assert format_price(Decimal("2.545")) == "2.545"
    assert format_price(Decimal("2.540")) == "2.54"
    assert format_price(Decimal("6.93")) == "6.93"
    assert format_price(Decimal("25.17")) == "25.17"
    assert format_price(Decimal("2.7113550")) == "2.7114"


def test_opportunity_last_bid_and_ask_use_adaptive_precision() -> None:
    state = WatchlistState(
        ordered_symbols=("AEMD",),
        entries=(WatchlistEntry(
            symbol="AEMD",
        latest_price="2.545",
        bid="2.540",
        ask="2.550",
        metadata=(("scanner_market_timestamp", "2026-08-28T22:32:22.206000+00:00"),),
        ),),
    )

    row = format_watchlist(state).rows[0]

    assert row.latest_price == "2.545"
    assert row.bid == "2.54"
    assert row.ask == "2.55"
    assert row.market_timestamp == "2026-08-28T22:32:22.206000+00:00"


def test_warrior_price_trigger_and_stop_share_price_policy() -> None:
    candidate = replace(
        _candidate(),
        setup=SetupDetection(
            SetupType.MICRO_PULLBACK,
            SetupState.FORMING,
            Decimal("70"),
            Decimal("2.5613550"),
            Decimal("2.5190"),
            StopModel.MICRO_PULLBACK_LOW,
        ),
    )
    snapshot = WarriorPaperSnapshot(
        enabled=True,
        health=WarriorCaptureHealth.RUNNING,
        configuration_fingerprint="test",
        items=(WarriorFocusItem(
            candidate,
            FloatProvenance.MARKET_CAP_PRICE_PROXY,
            candidate.setup.trigger,
            candidate.setup.stop_price,
            ("NO_SETUP",),
        ),),
    )

    row = format_warrior_paper(snapshot).focus.rows[0]

    assert row.latest_price == "2.545"
    assert row.entry_trigger == "2.5614"
    assert row.stop_price == "2.519"
