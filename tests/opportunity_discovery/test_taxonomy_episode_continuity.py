from datetime import UTC, date, datetime, timedelta
import base64
from decimal import Decimal
import json
import sqlite3

from app.opportunity_discovery import (
    DetectionState, StrategyDetection, StrategyFamily, TaxonomyEpisodeRecovery,
    TaxonomyEpisodeTracker,
)


T0 = datetime(2026, 9, 15, 13, 27, tzinfo=UTC)


def detection(strategy, state, at, *, anchor=None, trigger="1.30", stop="1.23", symbol="BDRX", provenance=None):
    return StrategyDetection(
        strategy, "TEST", StrategyFamily.BREAKOUT, symbol, "PREMARKET",
        date(2026, 9, 15), at, state,
        anchor or f"raw|{at.isoformat()}", anchor or f"raw|{at.isoformat()}",
        Decimal("1.28"), Decimal(trigger) if trigger else None,
        Decimal(stop) if stop else None, (), (), (), (), (), True,
        provenance or f"taxonomy-structural-provenance-v1|{symbol}|2026-09-15|PREMARKET|PULLBACK_ROOT|2026-09-15T13:00:00+00:00",
    )


def durable_anchor(label, provenance=None):
    provenance = provenance or "taxonomy-structural-provenance-v1|MYSZ|2026-09-15|PREMARKET|PULLBACK_ROOT|2026-09-15T13:00:00+00:00"
    encoded = base64.urlsafe_b64encode(provenance.encode()).decode().rstrip("=")
    return f"TAXONOMY_EPISODE|{encoded}|{label}"


def test_taxonomy_episode_keeps_identity_through_geometry_and_membership_evolution():
    tracker = TaxonomyEpisodeTracker(maximum_contexts=2)
    first = tracker.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED, T0),))
    second = tracker.stabilize((
        detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED, T0 + timedelta(minutes=1),
                   anchor="raw|rolling-window|08:04", trigger="1.31", stop="1.20"),
        detection("POST_GAP_RECLAIM", DetectionState.FORMING, T0 + timedelta(minutes=1),
                   anchor="raw|rolling-window|08:04", trigger=None, stop=None),
    ))
    assert first[0].opportunity_anchor == second[0].opportunity_anchor
    assert first[0].detector_episode_id == second[0].detector_episode_id
    assert {row.strategy_id for row in second} == {"HIGH_OF_DAY_BREAKOUT", "POST_GAP_RECLAIM"}


def test_trigger_latch_requires_completed_cross_and_survives_recalculation():
    tracker = TaxonomyEpisodeTracker()
    armed = tracker.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED, T0),))
    crossed = tracker.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.DETECTED, T0 + timedelta(minutes=1),
                                               anchor="raw|new", trigger="1.30"),))
    later = tracker.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED, T0 + timedelta(minutes=2),
                                             anchor="raw|later", trigger="1.40"),))
    assert armed[0].state is DetectionState.TRIGGER_ARMED
    assert crossed[0].state is DetectionState.DETECTED
    assert later[0].state is DetectionState.DETECTED
    assert crossed[0].opportunity_anchor == armed[0].opportunity_anchor


def test_invalidated_context_resets_and_is_bounded():
    tracker = TaxonomyEpisodeTracker(maximum_contexts=1)
    first = tracker.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED, T0),))[0]
    tracker.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.INVALIDATED, T0 + timedelta(minutes=1)),))
    second = tracker.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED, T0 + timedelta(minutes=2),
                                                    anchor="new-independent", provenance="taxonomy-structural-provenance-v1|BDRX|2026-09-15|PREMARKET|BREAKOUT_ROOT|2026-09-15T13:29:00+00:00"),))[0]
    assert first.opportunity_anchor != second.opportunity_anchor
    assert tracker.memory_metrics()["context_count"] == 1


def test_session_boundary_is_a_distinct_taxonomy_context():
    tracker = TaxonomyEpisodeTracker()
    first = tracker.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED, T0),))[0]
    regular = StrategyDetection(
        first.strategy_id, first.strategy_version, first.family, first.symbol, "REGULAR",
        first.session_date, T0 + timedelta(minutes=2), DetectionState.TRIGGER_ARMED,
        "raw|regular", "raw|regular", first.reference_price, first.trigger_level,
        first.structural_stop, (), (), (), (), (), True,
    )
    second = tracker.stabilize((regular,))[0]
    assert second.opportunity_anchor != first.opportunity_anchor


def test_durable_recovery_restores_evolved_active_episode_once(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE opportunity_state (opportunity_id TEXT PRIMARY KEY, symbol TEXT,
                last_stage TEXT);
            CREATE TABLE opportunity_timeline (event_id TEXT PRIMARY KEY, opportunity_id TEXT,
                event_type TEXT, source TEXT, observed_at TEXT, payload_json TEXT,
                trigger_price TEXT);
        """)
        anchor = durable_anchor("durable-mysz")
        connection.execute("INSERT INTO opportunity_state VALUES (?,?,?)", ("opp-1", "MYSZ", "TRIGGER_READY"))
        connection.execute("INSERT INTO opportunity_timeline VALUES (?,?,?,?,?,?,?)", (
            "event-1", "opp-1", "TRIGGER_READY", "TAXONOMY", T0.isoformat(),
            json.dumps({"strategy": "HIGH_OF_DAY_BREAKOUT", "payload": {
                "structural_anchor": anchor, "session": "PREMARKET", "session_date": "2026-09-15",
            }}), "1.30",
        ))
    recovery = __import__("app.opportunity_discovery", fromlist=["load_taxonomy_episode_recovery"]).load_taxonomy_episode_recovery(path)
    restarted = TaxonomyEpisodeTracker(recovery=recovery)
    later = restarted.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED,
                                             T0 + timedelta(minutes=20), anchor="raw|evolved", trigger="1.40",
                                             symbol="MYSZ"),))[0]
    assert later.opportunity_anchor == anchor


def test_durable_triggered_recovery_restores_trigger_and_prefers_current_geometry(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE opportunity_state (opportunity_id TEXT PRIMARY KEY, symbol TEXT,
                last_stage TEXT);
            CREATE TABLE opportunity_timeline (event_id TEXT PRIMARY KEY, opportunity_id TEXT,
                event_type TEXT, source TEXT, observed_at TEXT, payload_json TEXT,
                trigger_price TEXT);
        """)
        payload = json.dumps({"strategy": "HIGH_OF_DAY_BREAKOUT", "payload": {
            "structural_anchor": durable_anchor("triggered"), "session": "PREMARKET",
            "session_date": "2026-09-15",
        }})
        connection.execute("INSERT INTO opportunity_state VALUES (?,?,?)",
                           ("opp-triggered", "MYSZ", "TRIGGERED"))
        connection.execute("INSERT INTO opportunity_timeline VALUES (?,?,?,?,?,?,?)",
                           ("event-triggered", "opp-triggered", "TRIGGERED", "TAXONOMY",
                            T0.isoformat(), payload, "1.30"))
    recovery = __import__("app.opportunity_discovery", fromlist=["load_taxonomy_episode_recovery"]).load_taxonomy_episode_recovery(path)
    restarted = TaxonomyEpisodeTracker(recovery=recovery)
    current = restarted.stabilize((detection("HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED,
                                              T0 + timedelta(minutes=20), anchor="raw|evolved",
                                              trigger="1.40", symbol="MYSZ"),))[0]
    assert current.state is DetectionState.DETECTED
    assert current.trigger_level == Decimal("1.40")
    assert current.opportunity_anchor == durable_anchor("triggered")


def test_recovered_incompatible_same_session_provenance_creates_new_episode():
    provenance_a = "taxonomy-structural-provenance-v1|MYSZ|2026-09-15|PREMARKET|PULLBACK_ROOT|2026-09-15T13:00:00+00:00"
    provenance_b = "taxonomy-structural-provenance-v1|MYSZ|2026-09-15|PREMARKET|PULLBACK_ROOT|2026-09-15T13:20:00+00:00"
    old_anchor = durable_anchor("episode-a", provenance_a)
    restarted = TaxonomyEpisodeTracker(recovery=(TaxonomyEpisodeRecovery(
        "MYSZ", "2026-09-15", "PREMARKET", old_anchor,
        ("HIGH_OF_DAY_BREAKOUT",), False, Decimal("1.30"), provenance_a,
    ),))
    current = restarted.stabilize((detection(
        "HIGH_OF_DAY_BREAKOUT", DetectionState.TRIGGER_ARMED, T0 + timedelta(minutes=20),
        anchor="raw|episode-b", symbol="MYSZ", provenance=provenance_b,
    ),))[0]
    assert current.opportunity_anchor != old_anchor


def test_old_row_without_versioned_provenance_fails_closed(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE opportunity_state (opportunity_id TEXT PRIMARY KEY, symbol TEXT,
                last_stage TEXT);
            CREATE TABLE opportunity_timeline (event_id TEXT PRIMARY KEY, opportunity_id TEXT,
                event_type TEXT, source TEXT, observed_at TEXT, payload_json TEXT,
                trigger_price TEXT);
        """)
        connection.execute("INSERT INTO opportunity_state VALUES (?,?,?)", ("old", "MYSZ", "TRIGGER_READY"))
        connection.execute("INSERT INTO opportunity_timeline VALUES (?,?,?,?,?,?,?)", (
            "old-event", "old", "TRIGGER_READY", "TAXONOMY", T0.isoformat(),
            json.dumps({"strategy": "HIGH_OF_DAY_BREAKOUT", "payload": {
                "structural_anchor": "TAXONOMY_EPISODE|legacy-only",
                "session": "PREMARKET", "session_date": "2026-09-15",
            }}), "1.30",
        ))
    from app.opportunity_discovery import load_taxonomy_episode_recovery
    assert load_taxonomy_episode_recovery(path) == ()


def test_recovery_excludes_stale_non_active_state(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE opportunity_state (opportunity_id TEXT PRIMARY KEY, symbol TEXT,
                last_stage TEXT);
            CREATE TABLE opportunity_timeline (event_id TEXT PRIMARY KEY, opportunity_id TEXT,
                event_type TEXT, source TEXT, observed_at TEXT, payload_json TEXT,
                trigger_price TEXT);
        """)
        connection.execute("INSERT INTO opportunity_state VALUES ('old','MYSZ','FORMING')")
        connection.execute("INSERT INTO opportunity_timeline VALUES (?,?,?,?,?,?,?)", (
            "old-event", "old", "TRIGGER_READY", "TAXONOMY", T0.isoformat(),
            json.dumps({"strategy": "HIGH_OF_DAY_BREAKOUT", "payload": {
            "structural_anchor": durable_anchor("stale"), "session": "PREMARKET",
                "session_date": "2026-09-15",
            }}), "1.30",
        ))
    assert __import__("app.opportunity_discovery", fromlist=["load_taxonomy_episode_recovery"]).load_taxonomy_episode_recovery(path) == ()


def test_recovery_excludes_invalidated_or_consumed_opportunity(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE opportunity_state (opportunity_id TEXT PRIMARY KEY, symbol TEXT,
                last_stage TEXT);
            CREATE TABLE opportunity_timeline (event_id TEXT PRIMARY KEY, opportunity_id TEXT,
                event_type TEXT, source TEXT, observed_at TEXT, payload_json TEXT,
                trigger_price TEXT);
        """)
        for suffix, terminal in (("invalidated", "INVALIDATED"), ("consumed", "CONSUMED")):
            opportunity_id = f"old-{suffix}"
            connection.execute("INSERT INTO opportunity_state VALUES (?,?,?)",
                               (opportunity_id, "MYSZ", "TRIGGER_READY"))
            payload = json.dumps({"strategy": "HIGH_OF_DAY_BREAKOUT", "payload": {
                "structural_anchor": durable_anchor(suffix), "session": "PREMARKET",
                "session_date": "2026-09-15",
            }})
            connection.execute("INSERT INTO opportunity_timeline VALUES (?,?,?,?,?,?,?)",
                               (f"ready-{suffix}", opportunity_id, "TRIGGER_READY", "TAXONOMY",
                                T0.isoformat(), payload, "1.30"))
            connection.execute("INSERT INTO opportunity_timeline VALUES (?,?,?,?,?,?,?)",
                               (f"terminal-{suffix}", opportunity_id, terminal, "TAXONOMY",
                                (T0 + timedelta(minutes=1)).isoformat(), payload, "1.30"))
    assert __import__("app.opportunity_discovery", fromlist=["load_taxonomy_episode_recovery"]).load_taxonomy_episode_recovery(path) == ()
