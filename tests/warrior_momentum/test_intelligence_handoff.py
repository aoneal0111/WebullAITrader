from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from math import ceil
from threading import Event
from time import monotonic, sleep

from app.strategies.warrior_momentum.intelligence_handoff import (
    BoundedIntelligenceHandoff,
)
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.forward_runtime import (
    WarriorForwardCaptureService, _IntelligenceResult,
)
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.models import SetupState, SetupType
from app.paper_trade_experiment.harness import PaperExperimentJournal
from app.performance_diagnostics import performance_diagnostics
from app.trade_intelligence.decision_intelligence.entry_timing import (
    EntryIntelligenceConfig, HistoricalPaperEntryTimingPolicy, PAPER_TREATMENT,
)
from app.trade_intelligence.decision_intelligence.service import (
    HistoricalDecisionIntelligence,
)
from app.trade_intelligence.taxonomy_paper_bridge import TaxonomyPaperExecutionBridge
from tests.warrior_momentum.test_forward_capture import (
    T0, account, bar, bars, point, scanner,
)


def test_inflight_completion_cannot_overwrite_newer_generation() -> None:
    entered = Event()
    release = Event()

    def handler(value):
        if value == "old":
            entered.set()
            assert release.wait(2.0)
        return value

    handoff = BoundedIntelligenceHandoff(handler, maximum_keys=2, autostart=True)
    try:
        assert handoff.submit("XYZ", ("episode-1", "setup-a"), "old")
        assert entered.wait(1.0)
        assert handoff.submit("XYZ", ("episode-2", "setup-a"), "new")
        release.set()
        assert handoff.wait_idle(2.0)
        publication = handoff.lookup("XYZ")
        assert publication is not None
        assert publication.identity == ("episode-2", "setup-a")
        assert publication.value == "new"
        metrics = handoff.metrics()
        assert metrics.accepted == 2
        assert metrics.completed == 2
        assert metrics.superseded >= 1
        assert metrics.failures == 0
    finally:
        release.set()
        assert handoff.stop(timeout_seconds=2.0)


def test_same_symbol_different_setup_retains_only_latest_identity() -> None:
    handoff = BoundedIntelligenceHandoff(lambda value: value, autostart=True)
    try:
        assert handoff.submit("XYZ", ("episode-1", "flat-top"), "first")
        assert handoff.wait_idle(1.0)
        assert handoff.submit("XYZ", ("episode-1", "hod"), "second")
        assert handoff.wait_idle(1.0)
        publication = handoff.lookup("XYZ")
        assert publication is not None
        assert publication.identity == ("episode-1", "hod")
        assert publication.value == "second"
    finally:
        assert handoff.stop(timeout_seconds=2.0)


def test_worker_exception_is_contained_and_counted() -> None:
    def fail(_value):
        raise RuntimeError("research failure")

    handoff = BoundedIntelligenceHandoff(fail, autostart=True)
    try:
        assert handoff.submit("XYZ", "v1", object())
        assert handoff.wait_idle(1.0)
        assert handoff.lookup("XYZ") is None
        metrics = handoff.metrics()
        assert metrics.failures == 1
        assert metrics.completed == 0
    finally:
        assert handoff.stop(timeout_seconds=2.0)


def test_queue_saturation_rejects_new_key_without_unbounded_fifo() -> None:
    entered = Event()
    release = Event()

    def handler(value):
        if value == "active":
            entered.set()
            assert release.wait(2.0)
        return value

    handoff = BoundedIntelligenceHandoff(handler, maximum_keys=1, autostart=True)
    try:
        assert handoff.submit("AAA", "v1", "active")
        assert entered.wait(1.0)
        assert handoff.submit("BBB", "v1", "pending")
        assert not handoff.submit("CCC", "v1", "rejected")
        metrics = handoff.metrics()
        assert metrics.rejected == 1
        assert metrics.depth == 1
        assert metrics.high_water == 1
    finally:
        release.set()
        assert handoff.stop(timeout_seconds=2.0)


def test_slow_intelligence_worker_cannot_block_treatment_lookup(tmp_path) -> None:
    entered = Event()
    release = Event()

    def slow_callback(**_kwargs):
        entered.set()
        assert release.wait(2.0)
        return None, None, None

    store = ForwardCaptureStore(tmp_path / "slow-worker.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        taxonomy_execution_bridge=TaxonomyPaperExecutionBridge(),
        decision_intelligence_entry_observer=slow_callback,
        async_decision_intelligence=True,
    )
    forming = point(
        observation=scanner(price=D("9.95"), bid=D("9.94"), ask=D("9.96")),
        bars=(*bars()[:-1], bar(4, "9.96", "10", "9.94", "9.95", "300")),
    )
    try:
        service.observe(point(bars=()), account=account())
        assert entered.wait(1.0)
        started = monotonic()
        _candidate, signal = service.observe(forming, account=account())
        elapsed_ms = (monotonic() - started) * 1000.0
        assert signal is None
        assert elapsed_ms < 10.0
    finally:
        release.set()
        service.close_intelligence_worker(timeout_seconds=2.0)
        writer.close()


def test_shutdown_drains_pending_work_within_bound() -> None:
    completed = []

    def handler(value):
        sleep(0.01)
        completed.append(value)
        return value

    handoff = BoundedIntelligenceHandoff(handler, maximum_keys=4, autostart=True)
    assert handoff.submit("AAA", "v1", "a")
    assert handoff.submit("BBB", "v1", "b")
    started = monotonic()
    assert handoff.stop(drain=True, timeout_seconds=2.0)
    assert monotonic() - started < 2.0
    assert set(completed) == {"a", "b"}
    assert handoff.metrics().depth == 0


def test_cached_treatment_rejects_stale_episode_setup_and_market_state(tmp_path) -> None:
    store = ForwardCaptureStore(tmp_path / "stale-treatment.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    try:
        value = point()
        completed = tuple(bars())
        candidate = service.runtime.discover(
            value.observation, completed, session=value.session,
        )
        assert candidate.setup is not None
        setup = replace(
            candidate.setup,
            state=SetupState.FORMING,
            structural_episode_id="episode-current",
        )
        candidate = replace(
            candidate,
            price=setup.trigger - D("0.01"),
            setup=setup,
        )
        cached = _IntelligenceResult(None, object(), setup, None)
        assert service._current_treatment_signal(cached, candidate, stale=False) is not None

        newer_episode = replace(
            candidate,
            setup=replace(setup, structural_episode_id="episode-new"),
        )
        assert service._current_treatment_signal(
            cached, newer_episode, stale=False,
        ) is None

        other_type = (
            SetupType.FLAT_TOP_BREAKOUT
            if setup.setup_type is not SetupType.FLAT_TOP_BREAKOUT
            else SetupType.HIGH_OF_DAY_BREAKOUT
        )
        other_setup = replace(candidate, setup=replace(setup, setup_type=other_type))
        assert service._current_treatment_signal(
            cached, other_setup, stale=False,
        ) is None
        assert service._current_treatment_signal(cached, candidate, stale=True) is None

        first_version = service._completed_bar_version(value)
        first_identity = service._intelligence_identity(
            value, candidate, first_version,
        )
        changed_value = point(
            observation=scanner(timestamp=value.observation.timestamp),
            bars=(*bars(), replace(bars()[-1], close=D("10.01"))),
        )
        changed_version = service._completed_bar_version(changed_value)
        changed_identity = service._intelligence_identity(
            changed_value, candidate, changed_version,
        )
        assert changed_identity != first_identity
    finally:
        writer.close()


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, ceil(len(ordered) * percentile) - 1))]


def test_production_shaped_real_intelligence_fast_path_latency(tmp_path) -> None:
    """Measure the real DI, treatment policy, and taxonomy bridge in one worker."""
    store = ForwardCaptureStore(tmp_path / "production-fast-path.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    intelligence = HistoricalDecisionIntelligence(
        journal_path=tmp_path / "production-intelligence.sqlite3",
    )
    intelligence.start("PAPER")
    journal = PaperExperimentJournal(tmp_path / "production-experiment.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(
            enabled=True,
            mode=PAPER_TREATMENT,
            allocation_percent=50,
        ),
        journal=journal,
    )
    service_ref = {}

    def real_decision_and_entry(**kwargs):
        result = intelligence.observe_decision(
            value=kwargs["value"],
            candidate=kwargs["candidate"],
            signal=kwargs.get("signal"),
            taxonomy_candidate=kwargs.get("taxonomy_candidate"),
            legacy_candidate=kwargs.get("legacy_candidate"),
        )
        _decision, treatment = policy.assess(
            result=result,
            candidate=kwargs["candidate"],
            environment="PAPER",
            signal_factory=service_ref["service"].runtime.entry_signal,
            decision_timestamp=kwargs.get("decision_timestamp"),
            existing_signal=kwargs.get("signal"),
        )
        return result, treatment, _decision

    service = WarriorForwardCaptureService(
        store,
        writer,
        taxonomy_execution_bridge=TaxonomyPaperExecutionBridge(),
        decision_intelligence_entry_observer=real_decision_and_entry,
        paper_entry_intelligence=policy.assess,
        async_observation_records=True,
        async_decision_intelligence=True,
    )
    service_ref["service"] = service
    pretrigger = (*bars()[:-1], bar(4, "9.96", "10", "9.94", "9.95", "300"))
    flat_history = tuple(
        replace(
            bars()[0],
            timestamp=T0 - timedelta(minutes=64 - index),
            open=D("8"), high=D("8.01"), low=D("7.99"), close=D("8"),
        )
        for index in range(64)
    )
    no_setup = point(bars=())
    production_sized = point(
        observation=scanner(price=D("8.10"), bid=D("8.09"), ask=D("8.11")),
        bars=flat_history,
    )
    forming = point(
        observation=scanner(price=D("9.95"), bid=D("9.94"), ask=D("9.96")),
        bars=pretrigger,
    )
    blocked = point(quote_freshness_seconds=D("99"))
    entry_ready = point()
    durations = []
    observed_states = set()
    try:
        # Warm the real worker/artifact and immutable completed-bar state.
        service.observe(no_setup, account=account())
        assert service.wait_for_intelligence(timeout_seconds=5.0)
        sequence = [no_setup] * 500 + [production_sized, forming, blocked, entry_ready]
        for value in sequence:
            started = monotonic()
            candidate, signal = service.observe(value, account=account())
            durations.append((monotonic() - started) * 1000.0)
            if candidate.setup is None:
                observed_states.add("NO_SETUP")
            else:
                observed_states.add(candidate.setup.state.value)
            if candidate.status.value == "AWAITING_EXECUTION_DATA":
                observed_states.add("EXECUTION_BLOCKED")
            if signal is not None:
                observed_states.add("ENTRY_READY")
        # Missing treatment state is intentionally fail-closed.  Once the
        # exact assignment/lifecycle publication completes, the same canonical
        # event must produce the unchanged entry decision without blocking.
        assert service.wait_for_intelligence(timeout_seconds=5.0)
        started = monotonic()
        candidate, signal = service.observe(entry_ready, account=account())
        durations.append((monotonic() - started) * 1000.0)
        if signal is not None:
            observed_states.add("ENTRY_READY")
        p50 = _percentile(durations, 0.50)
        p90 = _percentile(durations, 0.90)
        p99 = _percentile(durations, 0.99)
        timings = performance_diagnostics.snapshot().component_timings
        fast_stage_p99 = {
            name: timings[name]["p99_ms"]
            for name in (
                "warrior.service.treatment_lookup",
                "warrior.service.taxonomy_fast_path",
                "warrior.service.decision_intelligence_async_submit",
            )
        }
        treatment_timing = timings["warrior.service.treatment_lookup"]
        print(
            "PRODUCTION_FAST_PATH_MS",
            {
                "p50": p50, "p90": p90, "p99": p99,
                "max": max(durations), "states": sorted(observed_states),
                "treatment": treatment_timing,
                **fast_stage_p99,
            },
        )
        assert {
            "NO_SETUP", "FORMING", "TRIGGERED", "EXECUTION_BLOCKED", "ENTRY_READY",
        } <= observed_states
        assert p50 < 10.0
        assert p90 < 25.0
        assert p99 < 50.0
        assert treatment_timing["max_ms"] < 10.0
    finally:
        service.close_intelligence_worker(timeout_seconds=5.0)
        service.close_observation_records()
        intelligence.close()
        policy.close()
        journal.close()
        writer.close()


def test_real_intelligence_sync_and_async_decisions_are_equivalent(tmp_path) -> None:
    def run(name: str, *, asynchronous: bool):
        root = tmp_path / name
        root.mkdir()
        store = ForwardCaptureStore(root / "forward.sqlite3")
        writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
        intelligence = HistoricalDecisionIntelligence(
            journal_path=root / "intelligence.sqlite3",
        )
        intelligence.start("PAPER")
        journal = PaperExperimentJournal(root / "experiment.sqlite3")
        policy = HistoricalPaperEntryTimingPolicy(
            config=EntryIntelligenceConfig(
                enabled=True, mode=PAPER_TREATMENT, allocation_percent=0,
            ),
            journal=journal,
        )
        service_ref = {}

        def callback(**kwargs):
            result = intelligence.observe_decision(
                value=kwargs["value"], candidate=kwargs["candidate"],
                signal=kwargs.get("signal"),
                taxonomy_candidate=kwargs.get("taxonomy_candidate"),
                legacy_candidate=kwargs.get("legacy_candidate"),
            )
            _decision, treatment = policy.assess(
                result=result, candidate=kwargs["candidate"], environment="PAPER",
                signal_factory=service_ref["service"].runtime.entry_signal,
                decision_timestamp=kwargs.get("decision_timestamp"),
                existing_signal=kwargs.get("signal"),
            )
            return result, treatment, _decision

        service = WarriorForwardCaptureService(
            store, writer,
            taxonomy_execution_bridge=TaxonomyPaperExecutionBridge(),
            decision_intelligence_entry_observer=callback,
            paper_entry_intelligence=policy.assess,
            async_decision_intelligence=asynchronous,
        )
        service_ref["service"] = service
        pretrigger = (*bars()[:-1], bar(4, "9.96", "10", "9.94", "9.95", "300"))
        sequence = (
            point(bars=()),
            point(
                observation=scanner(price=D("9.95"), bid=D("9.94"), ask=D("9.96")),
                bars=pretrigger,
            ),
            point(quote_freshness_seconds=D("99")),
            point(),
            point(bars=()),
            point(),
        )
        outcomes = []
        try:
            for value in sequence:
                candidate, signal = service.observe(value, account=account())
                if asynchronous:
                    assert service.wait_for_intelligence(timeout_seconds=5.0)
                setup = candidate.setup
                outcomes.append((
                    candidate.status.value,
                    tuple(code.value for code in candidate.reason_codes),
                    None if setup is None else setup.state.value,
                    None if setup is None else setup.setup_type.value,
                    None if setup is None else setup.structural_episode_id,
                    signal is not None,
                    None if signal is None else signal.entry_trigger,
                    None if signal is None else signal.stop_price,
                    None if signal is None else signal.risk_per_share,
                    None if signal is None else signal.taxonomy_opportunity_id,
                ))
            return tuple(outcomes)
        finally:
            service.close_intelligence_worker(timeout_seconds=5.0)
            intelligence.close()
            policy.close()
            journal.close()
            writer.close()

    assert run("sync", asynchronous=False) == run("async", asynchronous=True)
