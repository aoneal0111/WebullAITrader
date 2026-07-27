from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPERATIONAL_MAIN = PROJECT_ROOT / "app" / "operational_main.py"

FORBIDDEN_CONSTRUCTORS = {
    "AuthorizationRegistry",
    "DurableExecutionJournal",
    "DurableMarketEventStore",
    "EmergencyStopStore",
}


def _tree() -> ast.Module:
    return ast.parse(
        OPERATIONAL_MAIN.read_text(encoding="utf-8"),
        filename=str(OPERATIONAL_MAIN),
    )


def test_operational_main_does_not_construct_runtime_infrastructure() -> None:
    constructed_names = {
        node.func.id
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
    }

    assert constructed_names.isdisjoint(FORBIDDEN_CONSTRUCTORS)


def test_operational_main_does_not_manage_broker_lifecycle_directly() -> None:
    direct_broker_lifecycle_calls = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"connect", "disconnect"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "broker"
    ]

    assert direct_broker_lifecycle_calls == []


def test_operational_main_builds_runtime_through_composition_root() -> None:
    composition_calls = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_operational_runtime_composition"
    ]

    assert composition_calls


def test_operational_workflows_use_runtime_session_context() -> None:
    session_contexts = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "OperationalRuntimeSession"
            for item in node.items
        )
    ]

    assert len(session_contexts) == 2


def test_operational_workflows_connect_through_session() -> None:
    session_connect_calls = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "session"
    ]

    assert len(session_connect_calls) == 2
