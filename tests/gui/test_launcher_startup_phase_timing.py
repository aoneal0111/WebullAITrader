"""Atlas launcher startup checkpoints are bounded and failure-contained."""
from unittest.mock import Mock, patch

from app.gui import app


def test_startup_phase_emits_only_known_phase_and_duration(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(app, "monotonic", lambda: 9.250)
    # Never patch the global logging module across pytest's own log hooks.
    with patch.object(app.logging, "getLogger", return_value=logger):
        app._startup_phase(8.0, "desktop_composition_created")
    logger.info.assert_called_once_with(
        "ATLAS_STARTUP_PHASE phase=%s elapsed_ms=%d",
        "desktop_composition_created", 1250,
    )


def test_startup_phase_is_nonblocking_when_observability_fails(monkeypatch):
    def failure(_):
        raise OSError("synthetic diagnostic failure")
    monkeypatch.setattr(app, "monotonic", lambda: 7.0)
    with patch.object(app.logging, "getLogger", side_effect=failure):
        assert app._startup_phase(5.0, "window_show_requested") is None
