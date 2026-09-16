from types import SimpleNamespace

import pytest

from datetime import UTC, datetime, timedelta
from pathlib import Path
import os

from app.catalysts.models import CatalystEvidence
from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.catalysts.sec_shadow_parity import (
    MismatchKind,
    ParityMetrics,
    ParityState,
    ReadinessReason,
    SecCatalystParityObservation,
    SecCatalystParityStore,
    SecCatalystShadowEvaluator,
)
from app.catalysts.sec_symbol_intelligence_adapter import (
    AdapterReadiness,
    AdapterReadinessReason,
    SecCatalystAdapterEvaluation,
)
from app.catalysts.sec_shadow_provider import SecShadowingCatalystProvider
from app.composition.sec_shadow_runtime import (
    SecShadowRuntimeState,
    create_sec_shadow_runtime,
)
from app.configuration.models import TradingEnvironment
from app.symbol_intelligence.composition import SymbolIntelligenceRepositoryComposition
from app.configuration.loader import load_configuration
from app.configuration.environment import resolve_runtime_environment
from app.composition.desktop import create_desktop_composition


@pytest.fixture(autouse=True)
def _offline_guards(monkeypatch):
    """Fail this module immediately if a real SEC/network path is touched."""
    import httpx
    def fail_network(*args, **kwargs):
        raise AssertionError("real HTTP is forbidden in C2B2 tests")
    monkeypatch.setattr(httpx.Client, "send", fail_network)
    monkeypatch.setattr(httpx.Client, "request", fail_network)
    from app.symbol_intelligence.acquisition import SecSymbolIntelligenceAcquisitionService
    from app.symbol_intelligence.network_lease import SecNetworkOwnershipLease
    from app.symbol_intelligence.providers.sec_transport import SecEdgarTransport
    def fail_forbidden(*args, **kwargs):
        raise AssertionError("SEC acquisition/transport/lease is forbidden in C2B2 tests")
    monkeypatch.setattr(SecSymbolIntelligenceAcquisitionService, "__init__", fail_forbidden)
    monkeypatch.setattr(SecEdgarTransport, "__init__", fail_forbidden)
    monkeypatch.setattr(SecNetworkOwnershipLease, "try_acquire", fail_forbidden)


def _configuration(*, enabled=True, environment=TradingEnvironment.PAPER, legacy=True):
    return SimpleNamespace(
        environment=environment,
        sec_edgar=(object() if legacy else None),
        symbol_intelligence_sec_edgar=SimpleNamespace(
            enabled=True, shadow_parity_enabled=enabled, freshness_days=3,
        ),
    )


class _Repo:
    pass


class _Adapter:
    def __init__(self, repository, **kwargs):
        self.repository = repository


class _Store:
    pass


class _Evaluator:
    pass


def _factories(repository, calls):
    def repo_factory(configuration, **kwargs):
        calls.append("repository")
        return SymbolIntelligenceRepositoryComposition(repository)

    def adapter_factory(value, **kwargs):
        calls.append(("adapter", value))
        return _Adapter(value, **kwargs)

    def store_factory(path, **kwargs):
        calls.append(("store", path))
        return _Store()

    def evaluator_factory(**kwargs):
        calls.append(("evaluator", kwargs["adapter"], kwargs["store"]))
        return _Evaluator()

    return repo_factory, adapter_factory, store_factory, evaluator_factory


def test_flag_false_gates_all_shadow_factories():
    calls = []
    factories = _factories(_Repo(), calls)
    result = create_sec_shadow_runtime(_configuration(enabled=False), repository_composition_factory=factories[0], adapter_factory=factories[1], store_factory=factories[2], evaluator_factory=factories[3])
    assert result.state is SecShadowRuntimeState.DISABLED
    assert calls == []


@pytest.mark.parametrize("environment", list(TradingEnvironment)[:])
def test_only_paper_is_eligible(environment):
    calls = []
    factories = _factories(_Repo(), calls)
    result = create_sec_shadow_runtime(_configuration(environment=environment), repository_composition_factory=factories[0], adapter_factory=factories[1], store_factory=factories[2], evaluator_factory=factories[3])
    if environment is TradingEnvironment.PAPER:
        assert result.state is SecShadowRuntimeState.ACTIVE
        assert result.evaluator is not None
    else:
        assert result.state is SecShadowRuntimeState.INELIGIBLE_ENVIRONMENT
        assert calls == []


def test_repository_identity_and_single_construction():
    repository = _Repo()
    calls = []
    factories = _factories(repository, calls)
    result = create_sec_shadow_runtime(_configuration(), repository_composition_factory=factories[0], adapter_factory=factories[1], store_factory=factories[2], evaluator_factory=factories[3])
    assert result.state is SecShadowRuntimeState.ACTIVE
    assert result.adapter.repository is repository
    assert [item[0] if isinstance(item, tuple) else item for item in calls] == ["repository", "adapter", "store", "evaluator"]


def test_legacy_or_repository_absence_gates_before_factories():
    calls = []
    factories = _factories(_Repo(), calls)
    result = create_sec_shadow_runtime(_configuration(legacy=False), repository_composition_factory=factories[0], adapter_factory=factories[1], store_factory=factories[2], evaluator_factory=factories[3])
    assert result.state is SecShadowRuntimeState.NO_LEGACY_PROVIDER
    assert calls == []

    result = create_sec_shadow_runtime(_configuration(), repository_composition_factory=lambda *a, **k: None, adapter_factory=factories[1], store_factory=factories[2], evaluator_factory=factories[3])
    assert result.state is SecShadowRuntimeState.NO_SYMBOL_INTELLIGENCE
    assert calls == []


@pytest.mark.parametrize("which", ["adapter", "store", "evaluator"])
def test_construction_failure_is_shadow_nonfatal(which):
    calls = []
    factories = list(_factories(_Repo(), calls))
    index = {"adapter": 1, "store": 2, "evaluator": 3}[which]
    def fail(*args, **kwargs):
        raise RuntimeError("must not escape")
    factories[index] = fail
    result = create_sec_shadow_runtime(_configuration(), repository_composition_factory=factories[0], adapter_factory=factories[1], store_factory=factories[2], evaluator_factory=factories[3])
    assert result.state is SecShadowRuntimeState.CONSTRUCTION_ERROR
    assert result.evaluator is None


def test_process_environment_activation_is_consumed(monkeypatch):
    monkeypatch.setenv("ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED", "true")
    monkeypatch.setenv("ATLAS_SEC_EDGAR_ENABLED", "true")
    monkeypatch.setenv("ATLAS_SEC_EDGAR_USER_AGENT", "test contact@example.invalid")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test contact@example.invalid")
    monkeypatch.setenv("ATLAS_SYMBOL_INTELLIGENCE_SEC_EDGAR_ENABLED", "true")
    monkeypatch.setenv("WEBULL_TRADING_ENVIRONMENT", "PAPER")
    configuration = load_configuration()
    assert configuration.symbol_intelligence_sec_edgar.shadow_parity_enabled is True


@pytest.mark.parametrize(
    ("process_value", "dotenv_value", "expected"),
    [("true", "false", True), ("false", "true", False)],
)
def test_desktop_process_precedence_is_consumed(monkeypatch, tmp_path, process_value, dotenv_value, expected):
    monkeypatch.setenv("ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED", process_value)
    monkeypatch.setenv("ATLAS_SEC_EDGAR_ENABLED", "true")
    monkeypatch.setenv("ATLAS_SEC_EDGAR_USER_AGENT", "test contact@example.invalid")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test contact@example.invalid")
    monkeypatch.setenv("ATLAS_SYMBOL_INTELLIGENCE_SEC_EDGAR_ENABLED", "true")
    monkeypatch.setenv("WEBULL_TRADING_ENVIRONMENT", "PAPER")
    # The process value wins over the repository .env value without editing .env.
    (tmp_path / ".env").write_text(
        f"ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED={dotenv_value}\n",
        encoding="utf-8",
    )
    resolved = resolve_runtime_environment(dict(os.environ), dotenv_path=tmp_path / ".env")
    configuration = load_configuration(env=resolved)
    assert configuration.symbol_intelligence_sec_edgar.shadow_parity_enabled is expected
    calls = []
    factories = _factories(_Repo(), calls)
    runtime = create_sec_shadow_runtime(configuration, repository_composition_factory=factories[0], adapter_factory=factories[1], store_factory=factories[2], evaluator_factory=factories[3])
    assert (runtime.state is SecShadowRuntimeState.ACTIVE) is expected


def test_desktop_composition_exposes_active_holder(monkeypatch):
    from app.composition import desktop as desktop_module
    configuration = load_configuration(env={
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": "test contact@example.invalid",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "true",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_EDGAR_ENABLED": "true",
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    evaluator = SimpleNamespace(metrics=SimpleNamespace(snapshot=lambda: ParityMetrics()))
    store = SimpleNamespace(summary=lambda **kwargs: "summary")
    holder = SimpleNamespace(state="ACTIVE", evaluator=evaluator, store=store)
    composition = create_desktop_composition(
        driver_factory=lambda: SimpleNamespace(start=lambda: True, stop=lambda: None),
        shadow_runtime_factory=lambda config: holder,
    )
    try:
        assert composition.sec_shadow_runtime is holder
        assert composition.sec_shadow_runtime.evaluator.metrics.snapshot().evaluations == 0
        assert composition.sec_shadow_runtime.store.summary(since=datetime.now(UTC)) == "summary"
    finally:
        composition.close()


def test_d2b_flags_consume_paper_acquisition_activation(monkeypatch):
    from app.composition import desktop as desktop_module

    configuration = load_configuration(env={
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
        "SEC_EDGAR_USER_AGENT": "test contact@example.invalid",
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": "test contact@example.invalid",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "true",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED": "true",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED": "true",
        "ATLAS_MEMORY_OBSERVABILITY_ENABLED": "false",
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    calls = []

    def acquisition_factory(config, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        desktop_module,
        "create_symbol_intelligence_composition",
        acquisition_factory,
    )
    shadow_holder = SimpleNamespace(state="ACTIVE", evaluator=None, store=None)
    composition = create_desktop_composition(
        driver_factory=lambda: SimpleNamespace(start=lambda: True, stop=lambda: None),
        shadow_runtime_factory=lambda config: shadow_holder,
    )
    try:
        assert len(calls) == 1
        assert calls[0]["activate"] is True
        assert calls[0]["start"] is False
        assert callable(calls[0]["activation_diagnostics_callback"])
        assert composition.symbol_intelligence is None
    finally:
        composition.close()


def test_process_false_disables_without_sidecar_factories(monkeypatch):
    monkeypatch.setenv("ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED", "false")
    configuration = load_configuration(env={
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": "test contact@example.invalid",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "false",
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
    })
    calls = []
    factories = _factories(_Repo(), calls)
    result = create_sec_shadow_runtime(
        configuration, repository_composition_factory=factories[0],
        adapter_factory=factories[1], store_factory=factories[2], evaluator_factory=factories[3],
    )
    assert result.state is SecShadowRuntimeState.DISABLED
    assert calls == []


def test_production_path_contracts_are_value_only():
    assert str(Path("data/symbol_intelligence/paper/symbol_intelligence.sqlite3")) == str(
        Path("data") / "symbol_intelligence" / "paper" / "symbol_intelligence.sqlite3"
    )
    assert SecCatalystParityStore.production_path("PAPER") == str(
        Path("data") / "symbol_intelligence" / "paper" / "sec_shadow_parity.sqlite3"
    )


def _observation(when: datetime, state: ParityState = ParityState.MATCH):
    return SecCatalystParityObservation(
        1, "PAPER", "ABC", when, state,
        mismatch_kinds=((MismatchKind.STATUS_MISMATCH,) if state is ParityState.MISMATCH else ()),
        legacy_status=CatalystStatus.FALSE,
        shadow_status=CatalystStatus.FALSE,
        readiness_reason=(ReadinessReason.SOURCE_STATE_MISSING if state is ParityState.SHADOW_NOT_READY else None),
    )


def test_parity_store_restart_and_disable_preserve_history(tmp_path):
    path = tmp_path / "parity.sqlite3"
    when = datetime.now(UTC).replace(microsecond=0)
    store = SecCatalystParityStore(str(path))
    assert store.record(_observation(when)) is True
    del store
    reopened = SecCatalystParityStore(str(path))
    summary = reopened.summary(since=when - timedelta(seconds=1))
    assert summary.matches == 1
    # Disabling the runtime does not delete or rewrite the diagnostic history.
    disabled = create_sec_shadow_runtime(_configuration(enabled=False))
    assert disabled.state is SecShadowRuntimeState.DISABLED
    assert reopened.summary(since=when - timedelta(seconds=1)).total == 1


def test_active_disabled_reenabled_same_sidecar_preserves_history(tmp_path):
    path = tmp_path / "cycle.sqlite3"
    when = datetime.now(UTC).replace(microsecond=0)
    first = SecCatalystParityStore(str(path))
    assert first.record(_observation(when)) is True
    disabled = create_sec_shadow_runtime(_configuration(enabled=False))
    assert disabled.state is SecShadowRuntimeState.DISABLED
    second = SecCatalystParityStore(str(path))
    assert second.summary(since=when - timedelta(seconds=1)).matches == 1
    assert second.record(_observation(when + timedelta(minutes=1))) is True
    assert second.summary(since=when - timedelta(seconds=1)).matches == 2


def test_summary_is_bounded_and_secret_safe(tmp_path):
    when = datetime.now(UTC).replace(microsecond=0)
    store = SecCatalystParityStore(str(tmp_path / "summary.sqlite3"))
    store.record(_observation(when, ParityState.MISMATCH))
    summary = store.summary(since=when - timedelta(seconds=1), limit_examples=50)
    assert len(summary.examples) == 1
    assert not hasattr(summary.examples[0], "headline")
    assert not hasattr(summary.examples[0], "source_url")
    with pytest.raises(ValueError):
        store.summary(since=when, limit_examples=51)


class _TypedAdapter:
    def __init__(self, evaluation):
        self.evaluation = evaluation
        self.calls = 0

    def evaluate(self, symbol, *, as_of=None):
        self.calls += 1
        if isinstance(self.evaluation, BaseException):
            raise self.evaluation
        return self.evaluation


class _FailingStore:
    def record(self, observation):
        raise RuntimeError("storage failure")


def _evidence(status):
    return CatalystEvidence("ABC", CatalystType.NONE, status, source="SEC_EDGAR")


def test_runtime_adapter_and_store_failures_are_isolated(tmp_path):
    legacy = _evidence(CatalystStatus.FALSE)
    adapter = _TypedAdapter(SecCatalystAdapterEvaluation(_evidence(CatalystStatus.FALSE), AdapterReadiness.READY))
    evaluator = SecCatalystShadowEvaluator(adapter, store=_FailingStore(), environment="PAPER")
    provider = SecShadowingCatalystProvider(
        SimpleNamespace(name="SEC_EDGAR", get_evidence=lambda symbol, as_of=None: legacy), evaluator,
    )
    assert provider.get_evidence("ABC", as_of=datetime.now(UTC)) is legacy
    assert evaluator.metrics.snapshot().storage_failures == 1

    failing = SecCatalystShadowEvaluator(_TypedAdapter(RuntimeError("adapter failure")), store=None)
    provider = SecShadowingCatalystProvider(
        SimpleNamespace(name="SEC_EDGAR", get_evidence=lambda symbol, as_of=None: legacy), failing,
    )
    assert provider.get_evidence("ABC", as_of=datetime.now(UTC)) is legacy
    assert failing.metrics.snapshot().shadow_errors == 1


@pytest.mark.parametrize(
    ("readiness", "reason", "expected_state"),
    [
        (AdapterReadiness.NOT_READY, AdapterReadinessReason.SOURCE_STATE_MISSING, ParityState.SHADOW_NOT_READY),
        (AdapterReadiness.NOT_READY, AdapterReadinessReason.SOURCE_STATE_UNAVAILABLE, ParityState.SHADOW_NOT_READY),
        (AdapterReadiness.READY, None, ParityState.MATCH),
        (AdapterReadiness.ERROR, AdapterReadinessReason.REPOSITORY_ERROR, ParityState.SHADOW_ERROR),
    ],
)
def test_composed_shadow_paths_keep_denominator_exclusive(readiness, reason, expected_state):
    from app.momentum_scanner.models import CatalystStatus
    legacy = _evidence(CatalystStatus.FALSE)
    adapter = _TypedAdapter(SecCatalystAdapterEvaluation(
        _evidence(CatalystStatus.FALSE), readiness, reason,
    ))
    evaluator = SecCatalystShadowEvaluator(adapter, store=None, environment="PAPER")
    provider = SecShadowingCatalystProvider(
        SimpleNamespace(name="SEC_EDGAR", get_evidence=lambda symbol, as_of=None: legacy), evaluator,
    )
    assert provider.get_evidence("ABC", as_of=datetime.now(UTC)) is legacy
    metrics = evaluator.metrics.snapshot()
    if expected_state is ParityState.MATCH:
        assert metrics.matches == 1 and metrics.mismatches == 0
    elif expected_state is ParityState.SHADOW_NOT_READY:
        assert metrics.shadow_not_ready == 1 and metrics.mismatches == 0
    else:
        assert metrics.shadow_errors == 1 and metrics.mismatches == 0
