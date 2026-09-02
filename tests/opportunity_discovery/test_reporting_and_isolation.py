from pathlib import Path
import ast

from app.opportunity_discovery import (
    MultiStrategyDiscoveryEngine, default_registry, strategy_discovery_report,
)
from tests.opportunity_discovery.conftest import clean_pullback, context


def test_discovery_report_has_coverage_overlap_cardinality_and_quality():
    engine = MultiStrategyDiscoveryEngine()
    batches = tuple(engine.observe(context(clean_pullback())) for _ in range(3))
    report = strategy_discovery_report(engine.registry, batches, engine.metrics())
    assert len(report["strategy_coverage"]) == 30
    assert report["top_combinations"]
    assert report["cardinality"]["market_observations"] == 3
    assert report["quality"]["complete_r_plan"] == 1


def test_discovery_has_no_execution_dependency_or_authority():
    forbidden_modules = ("broker", "orders", "account", "composition", "autonomous_paper", "execution_service")
    forbidden_calls = {"place_order", "submit_order", "authorize_order", "veto_order",
                       "resize_order", "cancel_order", "modify_order"}
    for path in Path("app/opportunity_discovery").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom): imports.append(node.module or "")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute): calls.append(node.func.attr)
                elif isinstance(node.func, ast.Name): calls.append(node.func.id)
        assert not any(any(part in module.lower() for part in forbidden_modules) for module in imports)
        assert forbidden_calls.isdisjoint(calls)


def test_existing_production_modules_do_not_import_discovery():
    hits = []
    for path in Path("app").rglob("*.py"):
        if "opportunity_discovery" in path.parts:
            continue
        if "opportunity_discovery" in path.read_text(encoding="utf-8"):
            hits.append(path)
    assert hits == []
