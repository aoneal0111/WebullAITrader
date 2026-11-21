"""Conservative strategy surface classification regression tests."""
from pathlib import Path

from tools.strategy_execution_surface_inventory import inspect

ROOT = Path(__file__).resolve().parents[2]


def test_current_strategy_surface_matches_repo_and_remains_read_only():
    result = inspect(ROOT)
    assert all(value["import_present"] for value in result["desktop_surfaces"].values())
    assert result["equity_replacement_registry_empty"] is True
    assert result["crypto_discovery_research_only_defaults"] is True
    assert result["review_required"] is False
    assert result["deletions_authorized"] is False


def test_missing_surface_fails_closed(tmp_path):
    (tmp_path / "app" / "composition").mkdir(parents=True)
    try:
        inspect(tmp_path)
    except ValueError as exc:
        assert str(exc).startswith("MISSING_OR_LINKED_SOURCE:")
    else:
        raise AssertionError("Missing sources should fail closed")
