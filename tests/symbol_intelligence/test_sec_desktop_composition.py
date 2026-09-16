from types import SimpleNamespace

import pytest

from app.symbol_intelligence.composition import create_symbol_intelligence_composition
from app.symbol_intelligence.repository import SymbolIntelligenceRepository
from app.symbol_intelligence.providers.sec_transport import SecEdgarResponse
from app.symbol_intelligence.providers.sec_identity import SecIssuerIdentityResolver


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

    def close(self, *, timeout_seconds):
        self.closed += 1
        return True


def cfg(*, enabled=False, legacy=None):
    sec = SimpleNamespace(enabled=enabled, user_agent="offline", requests_per_second=2.0)
    return SimpleNamespace(symbol_intelligence_sec_edgar=sec, sec_edgar=legacy, environment=SimpleNamespace(value="TEST"))


def test_disabled_composition_has_no_side_effects(tmp_path):
    called = []
    result = create_symbol_intelligence_composition(
        cfg(), repository_factory=lambda path: called.append(path),
    )
    assert result is None
    assert called == []


def test_legacy_conflict_fails_closed_before_construction(tmp_path):
    with pytest.raises(RuntimeError, match="conflicts with legacy"):
        create_symbol_intelligence_composition(
            cfg(enabled=True, legacy=object()), activate=True,
            repository_factory=lambda path: pytest.fail("repository must not be built"),
        )


def test_explicit_fake_activation_constructs_and_closes_once(tmp_path):
    composition = create_symbol_intelligence_composition(
        cfg(enabled=True), environment="TEST", activate=True,
        repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "si.sqlite3"),
        transport_factory=FakeTransport, service_factory=FakeService,
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
        )
