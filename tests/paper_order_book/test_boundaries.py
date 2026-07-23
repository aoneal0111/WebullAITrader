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


def test_no_runtime_transition_matching_execution_or_market_data_logic() -> None:
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
        assert path.name != "runtime.py"
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert forbidden_names.isdisjoint(imported_names), path


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
