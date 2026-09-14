from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.autonomous_paper import opportunity_identity
from app.trade_intelligence.opportunity_memory import OpportunityMemoryState
from app.strategies.warrior_momentum.models import SetupDetection, SetupState, SetupType
from app.strategies.warrior_momentum.setups import LegacySetupEpisodeTracker


UTC = timezone.utc
DAY = date(2026, 9, 14)


def signal(*, setup: str, trigger: str, stop: str, minute: int = 0,
           structural_episode_id: str | None = None,
           taxonomy_opportunity_id: str | None = None):
    values = dict(
        strategy_id="WARRIOR_MOMENTUM_V1", symbol="VNCE",
        timestamp=datetime(2026, 9, 14, 16, minute, tzinfo=UTC),
        setup_type=setup, entry_trigger=Decimal(trigger),
        stop_price=Decimal(stop),
    )
    if structural_episode_id is not None:
        values["structural_episode_id"] = structural_episode_id
    if taxonomy_opportunity_id is not None:
        values["taxonomy_opportunity_id"] = taxonomy_opportunity_id
    return SimpleNamespace(**values)


def service(tmp_path):
    store = ForwardCaptureStore(tmp_path / "capture.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    return WarriorForwardCaptureService(store, writer), writer


def test_vnce_like_expired_geometry_is_superseded_by_new_structure(tmp_path):
    service_instance, writer = service(tmp_path)
    try:
        old = signal(setup="FLAT_TOP_BREAKOUT", trigger="6.65", stop="6.61",
                     structural_episode_id="VNCE-EPISODE-1")
        new = signal(setup="HIGH_OF_DAY_BREAKOUT", trigger="7.18", stop="7.02", minute=9,
                     structural_episode_id="VNCE-EPISODE-2")
        old_id = opportunity_identity(old)
        new_id = opportunity_identity(new)
        assert service_instance._reconcile_structural_opportunity("VNCE", old) == old_id
        service_instance.opportunity_memory.observe(
            opportunity_id=old_id, symbol="VNCE", trading_date=DAY,
            observed_at=old.timestamp, price=Decimal("6.65"), qualified=True,
            setup="FLAT_TOP_BREAKOUT", setup_state="TRIGGERED",
            entry_anchor=Decimal("6.65"),
        )
        selected = service_instance._reconcile_structural_opportunity("VNCE", new)

        assert selected == new_id
        assert selected != old_id
        assert service_instance._memory_opportunity_ids["VNCE"] == new_id
        old_record = service_instance.opportunity_memory.get(DAY, "VNCE", old_id)
        assert old_record is not None
        assert old_record.opportunity_state is OpportunityMemoryState.INVALIDATED
        assert old_record.latest_blocking_reason == "STRUCTURE_SUPERSEDED"
    finally:
        writer.close()


def test_changed_geometry_does_not_rebase_active_lifecycle(tmp_path):
    service_instance, writer = service(tmp_path)
    try:
        old = signal(setup="FLAT_TOP_BREAKOUT", trigger="6.65", stop="6.61",
                     structural_episode_id="VNCE-EPISODE-1")
        new = signal(setup="HIGH_OF_DAY_BREAKOUT", trigger="7.18", stop="7.02", minute=9,
                     structural_episode_id="VNCE-EPISODE-2")
        old_id = opportunity_identity(old)
        assert service_instance._reconcile_structural_opportunity("VNCE", old) == old_id
        state = SimpleNamespace(
            remaining=100, signal=old,
        )
        service_instance._paper["VNCE"] = state
        assert service_instance._reconcile_structural_opportunity("VNCE", new) == old_id
        assert service_instance._memory_opportunity_ids["VNCE"] == old_id
    finally:
        writer.close()


def test_terminal_unfilled_state_allows_fast_new_structure(tmp_path):
    service_instance, writer = service(tmp_path)
    try:
        old = signal(setup="FLAT_TOP_BREAKOUT", trigger="6.65", stop="6.61",
                     structural_episode_id="VNCE-EPISODE-1")
        new = signal(setup="HIGH_OF_DAY_BREAKOUT", trigger="7.18", stop="7.02", minute=9,
                     structural_episode_id="VNCE-EPISODE-2")
        old_id = opportunity_identity(old)
        assert service_instance._reconcile_structural_opportunity("VNCE", old) == old_id
        service_instance._paper["VNCE"] = SimpleNamespace(remaining=0, signal=old)
        assert service_instance._reconcile_structural_opportunity("VNCE", new) == opportunity_identity(new)
    finally:
        writer.close()


def test_identity_ignores_decimal_formatting_and_non_structural_jitter():
    base = signal(setup="FLAT_TOP_BREAKOUT", trigger="6.65", stop="6.61",
                  structural_episode_id="VNCE-EPISODE-1")
    equivalent = signal(setup="HIGH_OF_DAY_BREAKOUT", trigger="6.650", stop="6.610",
                        minute=1, structural_episode_id="VNCE-EPISODE-1")
    assert opportunity_identity(base) == opportunity_identity(equivalent)


def test_confluence_uses_one_normalized_opportunity_identity():
    members = [
        signal(setup=setup, trigger=trigger, stop=stop,
               taxonomy_opportunity_id="NORMALIZED-VNCE-1")
        for setup, trigger, stop in (
            ("HIGH_OF_DAY_BREAKOUT", "7.10", "6.95"),
            ("FLAT_TOP_BREAKOUT", "7.10", "6.95"),
            ("ASCENDING_BASE_BREAKOUT", "7.10", "6.95"),
        )
    ]
    assert {opportunity_identity(member) for member in members} == {"NORMALIZED-VNCE-1"}


def test_expiry_without_new_structural_identity_does_not_reset(tmp_path):
    service_instance, writer = service(tmp_path)
    try:
        first = signal(setup="FLAT_TOP_BREAKOUT", trigger="6.65", stop="6.61",
                       structural_episode_id="VNCE-EPISODE-1")
        later = signal(setup="FLAT_TOP_BREAKOUT", trigger="6.650", stop="6.610",
                       minute=3, structural_episode_id="VNCE-EPISODE-1")
        assert service_instance._reconcile_structural_opportunity("VNCE", first) == opportunity_identity(first)
        assert service_instance._reconcile_structural_opportunity("VNCE", later) == opportunity_identity(first)
    finally:
        writer.close()


def test_memory_identity_hints_are_bounded(tmp_path):
    service_instance, writer = service(tmp_path)
    try:
        service_instance.opportunity_memory.max_active = 2
        for index in range(5):
            item = signal(
                setup="FLAT_TOP_BREAKOUT", trigger="6.65", stop="6.61",
                structural_episode_id=f"EPISODE-{index}",
            )
            item.symbol = f"V{index}"
            service_instance._reconcile_structural_opportunity(item.symbol, item)
        assert len(service_instance._memory_opportunity_ids) == 2
        assert len(service_instance._memory_geometry_keys) == 2
        assert list(service_instance._memory_opportunity_ids) == ["V3", "V4"]
    finally:
        writer.close()


def test_restart_does_not_restore_superseded_geometry_as_current(tmp_path):
    first_service, first_writer = service(tmp_path)
    old = signal(setup="FLAT_TOP_BREAKOUT", trigger="6.65", stop="6.61",
                 structural_episode_id="VNCE-EPISODE-1")
    new = signal(setup="HIGH_OF_DAY_BREAKOUT", trigger="7.18", stop="7.02",
                 minute=9, structural_episode_id="VNCE-EPISODE-2")
    try:
        old_id = first_service._reconcile_structural_opportunity("VNCE", old)
        assert first_service._reconcile_structural_opportunity("VNCE", new) == opportunity_identity(new)
        assert old_id != opportunity_identity(new)
    finally:
        first_writer.close()

    restarted, restarted_writer = service(tmp_path)
    try:
        assert restarted._reconcile_structural_opportunity("VNCE", new) == opportunity_identity(new)
        assert opportunity_identity(new) == "VNCE-EPISODE-2"
    finally:
        restarted_writer.close()


def test_detector_owned_episode_continues_then_resets_for_lower_same_type_structure():
    tracker = LegacySetupEpisodeTracker(maximum_symbols=2)
    setup = SetupDetection(SetupType.FLAT_TOP_BREAKOUT, SetupState.TRIGGERED,
                           Decimal("86"), Decimal("6.65"), Decimal("6.61"),
                           structural_anchor="FLAT_TOP_BREAKOUT|2026-09-14T16:00:00+00:00")
    continued = tracker.observe("VNCE", setup, session="REGULAR")
    later_bar = tracker.observe(
        "VNCE", replace(setup, structural_anchor=setup.structural_anchor),
        session="REGULAR",
    )
    fresh = tracker.observe(
        "VNCE", replace(setup, structural_anchor="FLAT_TOP_BREAKOUT|2026-09-14T16:20:00+00:00"),
        session="REGULAR",
    )
    assert continued.structural_episode_id == later_bar.structural_episode_id
    assert fresh.structural_episode_id != continued.structural_episode_id
