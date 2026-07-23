import ast
from pathlib import Path

import app.paper_trading.order_book_api as api

ROOT = Path(__file__).resolve().parents[3]
PRODUCTION = ROOT / "app" / "paper_trading" / "order_book_api"


def test_facade_has_no_forbidden_imports_or_side_effect_dependencies() -> None:
    forbidden_modules = {
        "app.live_trading",
        "app.research_portfolio",
        "app.strategy",
        "app.risk",
        "app.storage",
        "app.persistence",
        "app.paper_trading.matching_engine",
        "app.paper_trading.execution_engine",
        "os",
        "subprocess",
        "socket",
        "threading",
        "multiprocessing",
        "asyncio",
        "random",
        "uuid",
    }
    for path in PRODUCTION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        assert not {
            imported
            for imported in imports
            if any(
                imported == forbidden
                or imported.startswith(f"{forbidden}.")
                for forbidden in forbidden_modules
            )
        }, path


def test_facade_defines_no_lifecycle_dataclasses_or_enums() -> None:
    for path in PRODUCTION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            assert not isinstance(node, ast.ClassDef) or node.name == (
                "PaperOrderBookInterface"
            )


def test_matching_and_execution_internals_are_not_public() -> None:
    assert all("matching" not in name.lower() for name in api.__all__)
    assert all("execution" not in name.lower() for name in api.__all__)


def test_facade_does_not_depend_on_paper_order_book_application() -> None:
    for path in PRODUCTION.glob("*.py"):
        assert "app.paper_order_book" not in path.read_text(encoding="utf-8")
