import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = ROOT / "app" / "paper_order_book"


def test_production_imports_only_permitted_boundaries() -> None:
    for path in PRODUCTION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        assert alias.name.startswith(
                            ("app.paper_order_book", "app.paper_trading.order_book_api")
                        )
            if module and module.startswith("app."):
                assert module.startswith(
                    ("app.paper_order_book", "app.paper_trading.order_book_api")
                ), (path, module)


def test_no_transition_matching_execution_or_market_data_logic() -> None:
    forbidden_names = {
        "create_order",
        "accept_order",
        "reject_order",
        "cancel_order",
        "expire_order",
        "apply_fill",
        "matching_engine",
        "execution_engine",
        "market_data",
    }
    for path in PRODUCTION.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert forbidden_names.isdisjoint(imported_names), path
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert forbidden_names.isdisjoint(called_names), path


def test_runtime_has_only_local_application_imports() -> None:
    path = PRODUCTION / "runtime.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imports == {
        "app.paper_order_book.models",
        "app.paper_order_book.validation",
    }


def test_orchestrator_imports_only_application_and_public_lifecycle_api() -> None:
    path = PRODUCTION / "orchestrator.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    assert imports == {
        "app.paper_trading.order_book_api",
        "app.paper_order_book.exceptions",
        "app.paper_order_book.models",
        "app.paper_order_book.runtime",
    }


def test_orchestrator_never_accesses_private_book_state() -> None:
    path = PRODUCTION / "orchestrator.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    private_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr.startswith("_")
    }
    assert private_attributes == {"_runtime", "_dispatch"}


def test_no_lifecycle_dataclasses_or_enums_are_declared() -> None:
    lifecycle_names = {
        "PaperOrder",
        "Fill",
        "OrderStatus",
        "OrderSide",
        "OrderType",
        "TimeInForce",
        "PaperOrderBook",
    }
    for path in PRODUCTION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declared = {
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        }
        assert lifecycle_names.isdisjoint(declared), path
