from types import SimpleNamespace

import pytest

from app.symbol_intelligence.composition import (
    create_symbol_intelligence_composition,
    create_symbol_intelligence_repository_composition,
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
