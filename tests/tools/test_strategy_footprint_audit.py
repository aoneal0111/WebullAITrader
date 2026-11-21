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
