from types import SimpleNamespace

import pytest

from app.catalysts.sec_shadow_parity import SecCatalystShadowEvaluator
from app.composition.sec_shadow_runtime import (
    SecShadowRuntimeState,
    create_sec_shadow_runtime,
)
from app.configuration.models import TradingEnvironment
from app.symbol_intelligence.composition import SymbolIntelligenceRepositoryComposition


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
