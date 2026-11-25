"""PAPER composition startup phase logging must never impact reconciliation."""
from unittest.mock import Mock, patch

from app.composition import desktop


def test_composition_checkpoint_contains_only_phase_and_elapsed(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(desktop, "monotonic", lambda: 16.5)
    with patch.object(desktop.logging, "getLogger", return_value=logger):
        desktop._composition_startup_phase(14.0, "protection_reconciliation_completed")
    logger.info.assert_called_once_with(
        "ATLAS_COMPOSITION_PHASE phase=%s elapsed_ms=%d",
        "protection_reconciliation_completed", 2500,
    )


def test_composition_checkpoint_is_failure_contained(monkeypatch):
    def failed(_):
        raise OSError("synthetic diagnostic failure")
    monkeypatch.setattr(desktop, "monotonic", lambda: 1.0)
    with patch.object(desktop.logging, "getLogger", side_effect=failed):
        assert desktop._composition_startup_phase(0.0, "paper_gateway_created") is None
