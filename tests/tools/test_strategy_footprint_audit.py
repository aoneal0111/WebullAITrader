"""Regression tests for conservative import footprint inventory."""
from pathlib import Path

from tools.strategy_footprint_audit import inspect


def write(root: Path, rel: str, text: str):
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def test_reports_direct_relative_and_test_import_consumers(tmp_path):
    write(tmp_path, "app/strategies/__init__.py", "")
    write(tmp_path, "app/strategies/alpha.py", "VALUE = 1\n")
    write(tmp_path, "app/strategies/beta.py", "from .alpha import VALUE\n")
    write(tmp_path, "tests/test_alpha.py", "from app.strategies.alpha import VALUE\n")
    write(tmp_path, "tools/run.py", "import app.strategies.beta\n")
    report = inspect(tmp_path)
    rows = {item["path"]: item for item in report["candidates"]}
    assert rows["app/strategies/alpha.py"]["app_consumers"] == 1
    assert rows["app/strategies/alpha.py"]["test_consumers"] == 1
    assert rows["app/strategies/beta.py"]["tool_consumers"] == 1
    assert report["deletions_authorized"] is False
    assert report["parse_errors"] == []


def test_unreferenced_is_review_candidate_not_deletion_proof(tmp_path):
    write(tmp_path, "app/strategy_lab/single.py", "def main():\n    pass\n")
    write(tmp_path, "tests/test_placeholder.py", "assert True\n")
    report = inspect(tmp_path)
    assert report["strategy_files"] == 1
    assert report["no_static_import_consumers"] == 1
    assert report["candidates"][0]["review_only"] is True


def test_syntax_errors_reported_without_guessing(tmp_path):
    write(tmp_path, "app/strategy/malformed.py", "def broken(:\n")
    write(tmp_path, "tests/test_placeholder.py", "assert True\n")
    report = inspect(tmp_path)
    assert report["parse_errors"] == ["app/strategy/malformed.py: SyntaxError"]


def test_detects_direct_cli_and_freqtrade_loader_without_python_imports(tmp_path):
    write(tmp_path, "app/strategy_lab/my_cli.py", "if __name__ == '__main__':\n    pass\n")
    write(tmp_path, "app/strategy_lab/MyStrategy.py",
          "from freqtrade.strategy import IStrategy\nclass MyStrategy(IStrategy):\n    pass\n")
    write(tmp_path, "tests/test_placeholder.py",
          "SELECTED_STRATEGY = 'MyStrategy.py'\n")
    report = inspect(tmp_path)
    rows = {row["path"]: row for row in report["candidates"]}
    assert rows["app/strategy_lab/my_cli.py"]["direct_cli_entrypoint"]
    assert rows["app/strategy_lab/MyStrategy.py"]["freqtrade_istrategy_subclass"]
    assert rows["app/strategy_lab/MyStrategy.py"]["literal_consumer_count"] == 1
    assert report["no_static_import_but_direct_entrypoint"] == 1
    assert report["no_static_import_but_freqtrade_strategy"] == 1
    assert report["deletions_authorized"] is False
