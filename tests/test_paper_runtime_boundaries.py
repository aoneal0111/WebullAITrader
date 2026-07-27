from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAPER_RUNTIME = PROJECT_ROOT / "app" / "operations" / "runtime.py"
PAPER_LIFECYCLE = PROJECT_ROOT / "app" / "operations" / "paper_lifecycle.py"
PAPER_DRIVER = (
    PROJECT_ROOT
    / "app"
    / "services"
    / "runtime_drivers"
    / "paper.py"
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )


def _called_names(path: Path) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
    }


def test_paper_runtime_does_not_manage_session_primitives_directly() -> None:
    assert _called_names(PAPER_RUNTIME).isdisjoint(
        {
            "create_paper_session",
            "close_paper_session",
        }
    )


def test_paper_runtime_does_not_import_from_composition() -> None:
    composition_imports = [
        node
        for node in ast.walk(_tree(PAPER_RUNTIME))
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and (
            node.module == "app.composition"
            or node.module.startswith("app.composition.")
        )
    ]

    assert composition_imports == []


def test_paper_lifecycle_owns_session_primitives() -> None:
    called_names = _called_names(PAPER_LIFECYCLE)

    assert "create_paper_session" in called_names
    assert "close_paper_session" in called_names


def test_paper_runtime_delegates_session_lifecycle() -> None:
    lifecycle_calls = {
        node.func.attr
        for node in ast.walk(_tree(PAPER_RUNTIME))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_session_lifecycle"
    }

    assert {"start", "update", "close"}.issubset(lifecycle_calls)


def test_paper_runtime_recovery_uses_lifecycle_session() -> None:
    lifecycle_constructions = [
        node
        for node in ast.walk(_tree(PAPER_RUNTIME))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PaperRuntimeSession"
        and any(
            keyword.arg == "session"
            for keyword in node.keywords
        )
    ]

    assert lifecycle_constructions


def test_paper_driver_uses_engine_public_lifecycle() -> None:
    engine_calls = {
        node.func.attr
        for node in ast.walk(_tree(PAPER_DRIVER))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "engine"
    }

    assert {"start", "run", "stop"}.issubset(engine_calls)


def test_paper_driver_does_not_manage_paper_sessions_directly() -> None:
    assert _called_names(PAPER_DRIVER).isdisjoint(
        {
            "PaperRuntimeSession",
            "create_paper_session",
            "close_paper_session",
        }
    )
