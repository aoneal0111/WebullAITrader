from __future__ import annotations

import app.composition.configured_paper_runtime as composition_module
from app.composition.configured_paper_runtime import (
    create_configured_paper_runtime_dependencies,
)


def test_create_configured_paper_runtime_dependencies_composes_graph(
    monkeypatch,
) -> None:
    calls: dict[str, dict[str, object]] = {}

    scanner_coordinator = object()
    snapshot_resolver = object()
    quantity_provider = object()
    request_id_provider = object()
    runtime_context_configuration = object()
    timestamp_source = object()
    market_state_source = object()
    market_quote_source = object()
    gfv_decision_source = object()
    clock = object()
    strategy_engine = object()
    inference_adapter = object()
    scanner_cycle_sink = object()

    snapshot_source = object()
    context_provider = object()
    order_intent_factory = object()
    request_builder = object()
    coordinator = object()
    dependencies = object()

    def fake_create_live_snapshot_source(**kwargs):
        calls["snapshot_source"] = kwargs
        return snapshot_source

    def fake_create_runtime_context_provider(**kwargs):
        calls["context_provider"] = kwargs
        return context_provider

    def fake_order_intent_factory(**kwargs):
        calls["order_intent_factory"] = kwargs
        return order_intent_factory

    def fake_paper_request_builder(**kwargs):
        calls["request_builder"] = kwargs
        return request_builder

    def fake_create_paper_execution_pipeline():
        calls["execution_pipeline"] = {}
        return coordinator

    def fake_create_paper_runtime_dependencies(**kwargs):
        calls["dependencies"] = kwargs
        return dependencies

    monkeypatch.setattr(
        composition_module,
        "create_live_snapshot_source",
        fake_create_live_snapshot_source,
    )
    monkeypatch.setattr(
        composition_module,
        "create_runtime_context_provider",
        fake_create_runtime_context_provider,
    )
    monkeypatch.setattr(
        composition_module,
        "RuntimeOrderIntentFactory",
        fake_order_intent_factory,
    )
    monkeypatch.setattr(
        composition_module,
        "PaperRequestBuilder",
        fake_paper_request_builder,
    )
    monkeypatch.setattr(
        composition_module,
        "create_paper_execution_pipeline",
        fake_create_paper_execution_pipeline,
    )
    monkeypatch.setattr(
        composition_module,
        "create_paper_runtime_dependencies",
        fake_create_paper_runtime_dependencies,
    )

    result = create_configured_paper_runtime_dependencies(
        scanner_coordinator=scanner_coordinator,
        snapshot_resolver=snapshot_resolver,
        quantity_provider=quantity_provider,
        request_id_provider=request_id_provider,
        runtime_context_configuration=runtime_context_configuration,
        timestamp_source=timestamp_source,
        market_state_source=market_state_source,
        market_quote_source=market_quote_source,
        gfv_decision_source=gfv_decision_source,
        clock=clock,
        strategy_engine=strategy_engine,
        inference_adapter=inference_adapter,
        candidate_limit=12,
        maximum_events_per_cycle=345,
        scanner_cycle_sink=scanner_cycle_sink,
    )

    assert result is dependencies

    assert calls["snapshot_source"] == {
        "coordinator": scanner_coordinator,
        "snapshot_resolver": snapshot_resolver,
        "candidate_limit": 12,
        "maximum_events_per_cycle": 345,
        "cycle_sink": scanner_cycle_sink,
    }

    assert calls["context_provider"] == {
        "configuration": runtime_context_configuration,
        "timestamp_source": timestamp_source,
        "market_state_source": market_state_source,
        "market_quote_source": market_quote_source,
        "gfv_decision_source": gfv_decision_source,
    }

    assert calls["order_intent_factory"] == {
        "quantity_provider": quantity_provider,
        "request_id_provider": request_id_provider,
    }

    assert calls["request_builder"] == {
        "order_intent_factory": order_intent_factory,
        "context_provider": context_provider,
    }

    assert calls["execution_pipeline"] == {}

    assert calls["dependencies"] == {
        "snapshot_source": snapshot_source,
        "coordinator": coordinator,
        "request_builder": request_builder,
        "clock": clock,
        "strategy_engine": strategy_engine,
        "inference_adapter": inference_adapter,
    }


def test_configured_runtime_defaults_are_forwarded(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        composition_module,
        "create_live_snapshot_source",
        lambda **kwargs: captured.setdefault("snapshot", kwargs),
    )
    monkeypatch.setattr(
        composition_module,
        "create_runtime_context_provider",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        composition_module,
        "RuntimeOrderIntentFactory",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        composition_module,
        "PaperRequestBuilder",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        composition_module,
        "create_paper_execution_pipeline",
        lambda: object(),
    )
    monkeypatch.setattr(
        composition_module,
        "create_paper_runtime_dependencies",
        lambda **kwargs: kwargs,
    )

    create_configured_paper_runtime_dependencies(
        scanner_coordinator=object(),
        snapshot_resolver=object(),
        quantity_provider=object(),
        request_id_provider=object(),
        runtime_context_configuration=object(),
        timestamp_source=object(),
        market_state_source=object(),
        market_quote_source=object(),
        gfv_decision_source=object(),
        clock=object(),
    )

    assert captured["snapshot"]["candidate_limit"] == 25
    assert captured["snapshot"]["maximum_events_per_cycle"] == 1000
    assert captured["snapshot"]["cycle_sink"] is None
