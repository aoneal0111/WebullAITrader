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
        "app.paper_order_book.composition",
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


def test_service_imports_only_local_application_modules() -> None:
    path = PRODUCTION / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imports == {
        "app.paper_order_book.composition",
        "app.paper_order_book.models",
        "app.paper_order_book.orchestrator",
    }


def test_facade_imports_only_models_and_service() -> None:
    path = PRODUCTION / "facade.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imports == {
        "app.paper_order_book.composition",
        "app.paper_order_book.models",
    }


def test_composition_imports_only_functools_and_local_modules() -> None:
    path = PRODUCTION / "composition.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imports == {
        "functools",
        "app.paper_order_book.orchestrator",
        "app.paper_order_book.runtime",
        "app.paper_order_book.service",
    }


def test_factories_import_only_permitted_construction_boundaries() -> None:
    path = PRODUCTION / "factories.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imports == {
        "datetime",
        "app.paper_order_book.models",
        "app.paper_trading.order_book_api",
    }


def test_lifecycle_book_type_is_not_in_factory_function_annotations() -> None:
    path = PRODUCTION / "factories.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            annotations = [
                argument.annotation for argument in node.args.kwonlyargs
            ] + [node.returns]
            assert all(
                not isinstance(annotation, ast.Name)
                or annotation.id != "PaperOrderBook"
                for annotation in annotations
            )


def test_only_composition_instantiates_application_graph_types() -> None:
    graph_types = {
        "PaperOrderBookRuntime",
        "PaperOrderBookOrchestrator",
        "PaperOrderBookService",
    }
    for path in PRODUCTION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constructions = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in graph_types
        }
        if path.name == "composition.py":
            assert constructions == graph_types
        else:
            assert constructions == set(), path


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


def test_package_all_is_a_literal_tuple_for_static_api_review() -> None:
    path = PRODUCTION / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    assert isinstance(assignments[0].value, ast.Tuple)
    assert all(
        isinstance(item, ast.Constant) and isinstance(item.value, str)
        for item in assignments[0].value.elts
    )
