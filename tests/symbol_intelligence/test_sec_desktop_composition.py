from types import SimpleNamespace

import pytest

from app.symbol_intelligence.composition import (
    create_symbol_intelligence_composition,
    create_symbol_intelligence_repository_composition,
    SymbolIntelligenceRepositoryComposition,
)
from app.symbol_intelligence.repository import SymbolIntelligenceRepository
from app.configuration.models import TradingEnvironment


class FakeTransport:
    def __init__(self, configuration, *, limiter):
        self.closed = 0

    def close(self):
        self.closed += 1


class FakeService:
    def __init__(self, configuration, repository, transport, resolver, *, normalizer):
        self.started = 0
        self.closed = 0

    def start(self):
        self.started += 1
        return True

    def close_admission(self):
        return None

    def request_stop(self):
        return None

    def stop(self, timeout_seconds):
        self.closed += 1
        return True


def cfg(*, enabled=False, legacy=None, migration=False, acquisition=None):
    sec = SimpleNamespace(
        enabled=enabled,
        user_agent="offline",
        requests_per_second=2.0,
        acquisition_enabled=enabled if acquisition is None else acquisition,
        dual_network_migration_enabled=migration,
    )
    return SimpleNamespace(
        symbol_intelligence_sec_edgar=sec,
        sec_edgar=legacy,
        environment=TradingEnvironment.PAPER,
    )


def test_disabled_composition_has_no_side_effects(tmp_path):
    called = []
    result = create_symbol_intelligence_composition(
        cfg(), repository_factory=lambda path: called.append(path),
    )
    assert result is None
    assert called == []


def test_repository_composition_explicit_environment_preserves_baseline_override() -> None:
    captured = []
    repository = object()
    composition = create_symbol_intelligence_repository_composition(
        cfg(enabled=True),
        environment="TEST",
        repository_factory=lambda path: captured.append(path) or repository,
    )
    assert composition is not None
    assert composition.repository is repository
    assert captured == [SymbolIntelligenceRepository.production_path("TEST")]


def test_legacy_conflict_fails_closed_before_lease_or_construction(tmp_path):
    calls = []
    result = create_symbol_intelligence_composition(
        cfg(enabled=True, legacy=object()), activate=True,
        lease_factory=lambda path: calls.append("lease"),
        repository_factory=lambda path: calls.append("repository"),
    )
    assert result is None
    assert calls == []


def test_explicit_paper_migration_allows_offline_composition(tmp_path):
    composition = create_symbol_intelligence_composition(
        cfg(enabled=True, legacy=object(), migration=True),
        activate=True,
        repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "si.sqlite3"),
        transport_factory=FakeTransport,
        service_factory=FakeService,
        lease_path=tmp_path / "lease.sqlite3",
    )
    assert composition is not None
    assert composition.close()


def test_explicit_fake_activation_constructs_and_closes_once(tmp_path):
    composition = create_symbol_intelligence_composition(
        cfg(enabled=True), environment="PAPER", activate=True,
        repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "si.sqlite3"),
        transport_factory=FakeTransport, service_factory=FakeService,
        lease_path=tmp_path / "lease.sqlite3",
    )
    assert composition is not None
    assert composition.start() is True
    assert composition.close() is True
    assert composition.close() is True


def test_resolver_recovery_failure_prevents_partial_runtime(tmp_path):
    class FailingResolver:
        def recover(self):
            raise RuntimeError("recovery failed")

    with pytest.raises(RuntimeError, match="recovery failed"):
        create_symbol_intelligence_composition(
            cfg(enabled=True), activate=True,
            repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "si.sqlite3"),
            resolver_factory=lambda repository: FailingResolver(),
            lease_path=tmp_path / "lease.sqlite3",
        )


def test_worker_start_failure_is_reported(tmp_path):
    class FailingService(FakeService):
        def start(self):
            return False

    with pytest.raises(RuntimeError, match="worker failed to start"):
        create_symbol_intelligence_composition(
            cfg(enabled=True), activate=True, start=True,
            repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "si.sqlite3"),
            transport_factory=FakeTransport, service_factory=FailingService,
            lease_path=tmp_path / "lease.sqlite3",
        )


def test_worker_start_exception_closes_transport_and_releases_lease(tmp_path):
    transports = []

    class RaisingService(FakeService):
        def start(self):
            raise RuntimeError("offline start failure")

    def transport_factory(*args, **kwargs):
        transport = FakeTransport(*args, **kwargs)
        transports.append(transport)
        return transport

    lease_path = tmp_path / "lease.sqlite3"
    with pytest.raises(RuntimeError, match="offline start failure"):
        create_symbol_intelligence_composition(
            cfg(enabled=True), activate=True, start=True,
            repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "si.sqlite3"),
            transport_factory=transport_factory,
            service_factory=RaisingService,
            lease_path=lease_path,
        )
    assert transports[0].closed == 1
    import sqlite3
    connection = sqlite3.connect(lease_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
    finally:
        connection.close()


def test_activate_false_ignores_all_d1_flags_before_lease_or_network(tmp_path):
    calls = []
    result = create_symbol_intelligence_composition(
        cfg(enabled=True, legacy=object(), migration=True),
        activate=False,
        start=False,
        lease_factory=lambda path: calls.append("lease"),
        transport_factory=lambda *args, **kwargs: calls.append("transport"),
        service_factory=lambda *args, **kwargs: calls.append("service"),
    )
    assert result is None
    assert calls == []


def test_d2b1_startup_order_is_lease_before_network_factories(tmp_path):
    events = []

    class Ownership:
        admission_open = True
        def __init__(self, lease, **kwargs):
            self.lease = lease
        def authorize_construction(self, factory):
            events.append("lease_acquire")
            events.append("heartbeat_start")
            return factory()
        def configure_shutdown(self, **kwargs):
            self.callbacks = kwargs
        def close(self, **kwargs):
            return True

    class Service(FakeService):
        def __init__(self, *args, **kwargs):
            events.append("service_construct")
            super().__init__(*args, **kwargs)
        def start(self):
            events.append("worker_start")
            return super().start()

    class Transport(FakeTransport):
        def __init__(self, *args, **kwargs):
            events.append("transport_construct")
            super().__init__(*args, **kwargs)

    class Lease:
        ttl_seconds = 45.0

    def repository_factory(path):
        events.append("repository_open")
        return SymbolIntelligenceRepository(tmp_path / "si.sqlite3")

    result = create_symbol_intelligence_composition(
        cfg(enabled=True), activate=True,
        repository_factory=repository_factory,
        lease_factory=lambda path: Lease(),
        ownership_runtime_factory=Ownership,
        transport_factory=Transport,
        service_factory=Service,
    )
    assert result is not None
    events.append("shadow_construct_if_enabled")
    events.append("desktop_runtime_construct")
    events.append("desktop_finalization_complete")
    assert result.start() is True
    assert events == [
        "repository_open", "lease_acquire", "heartbeat_start",
        "transport_construct", "service_construct",
        "shadow_construct_if_enabled", "desktop_runtime_construct",
        "desktop_finalization_complete", "worker_start",
    ]
    assert result.close() is True


def test_d2b1_composition_contention_denies_before_transport(tmp_path):
    transports = []
    services = []

    def transport_factory(*args, **kwargs):
        transports.append(True)
        return FakeTransport(*args, **kwargs)

    def service_factory(*args, **kwargs):
        services.append(True)
        return FakeService(*args, **kwargs)

    lease_path = tmp_path / "lease.sqlite3"
    first = create_symbol_intelligence_composition(
        cfg(enabled=True), activate=True,
        repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "a.sqlite3"),
        transport_factory=transport_factory, service_factory=service_factory,
        lease_path=lease_path,
    )
    assert first is not None
    second = create_symbol_intelligence_composition(
        cfg(enabled=True), activate=True,
        repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "b.sqlite3"),
        transport_factory=transport_factory, service_factory=service_factory,
        lease_path=lease_path,
    )
    assert second is None
    assert len(transports) == 1
    assert len(services) == 1
    assert first.start() is True
    first.ownership._worker_stop_callback = lambda timeout_seconds: False
    assert first.close() is False

    blocked = create_symbol_intelligence_composition(
        cfg(enabled=True), activate=True,
        repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "c.sqlite3"),
        transport_factory=transport_factory, service_factory=service_factory,
        lease_path=lease_path,
    )
    assert blocked is None
    first.ownership._worker_stop_callback = lambda timeout_seconds: True
    assert first.close() is True

    third = create_symbol_intelligence_composition(
        cfg(enabled=True), activate=True,
        repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "c.sqlite3"),
        transport_factory=transport_factory, service_factory=service_factory,
        lease_path=lease_path,
    )
    assert third is not None
    assert third.start() is True
    assert third.close() is True


def test_d2b1_shared_repository_can_be_injected_into_shadow(tmp_path):
    from app.composition.sec_shadow_runtime import create_sec_shadow_runtime

    repository = SymbolIntelligenceRepository(tmp_path / "si.sqlite3")
    wrapper = SymbolIntelligenceRepositoryComposition(repository)
    captured = []

    runtime = create_sec_shadow_runtime(
        SimpleNamespace(
            environment=TradingEnvironment.PAPER,
            sec_edgar=object(),
            symbol_intelligence_sec_edgar=SimpleNamespace(
                enabled=True, shadow_parity_enabled=True, freshness_days=3,
            ),
        ),
        repository_composition=wrapper,
        adapter_factory=lambda value, **kwargs: captured.append(value) or SimpleNamespace(repository=value),
        store_factory=lambda *args, **kwargs: object(),
        evaluator_factory=lambda **kwargs: object(),
    )
    assert runtime.adapter.repository is repository
    assert captured == [repository]


def test_d2b1_finalization_failure_retains_incomplete_holder(monkeypatch):
    from app.composition import desktop as desktop_module
    from app.composition.desktop import create_desktop_composition
    from app.configuration.loader import load_configuration

    configuration = load_configuration(env={
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": "offline contact@example.invalid",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED": "true",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED": "true",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "false",
        "ATLAS_MEMORY_OBSERVABILITY_ENABLED": "false",
    })

    class Holder:
        repository = object()
        def start(self):
            return True
        def close(self, **kwargs):
            return False

    holder = Holder()
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    monkeypatch.setattr(
        desktop_module, "create_symbol_intelligence_composition",
        lambda *args, **kwargs: holder,
    )
    monkeypatch.setattr(
        desktop_module, "create_desktop_runtime_service",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("finalization")),
    )

    with pytest.raises(RuntimeError) as raised:
        create_desktop_composition(
            driver_factory=lambda: SimpleNamespace(start=lambda: True, stop=lambda: None),
            shadow_runtime_factory=lambda config, **kwargs: SimpleNamespace(evaluator=None),
        )
    assert raised.value.symbol_intelligence_holder is holder
