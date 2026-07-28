import os
from pathlib import Path
from tempfile import NamedTemporaryFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter

from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow


APPLICATION = QApplication.instance() or QApplication([])


def _window(composition, settings: QSettings) -> MainWindow:
    return MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.decision_projector,
        composition.runtime_health_projector,
        composition.timeline_projector,
        composition.trade_lifecycle_projector,
        composition.operator_workspace_projector,
        composition.replay_controller,
        composition.replay_projections,
        composition.recording_controller,
        composition.event_store_controller,
        composition.analytics_controller,
        composition.backtesting_controller,
        settings=settings,
    )


def _settings_path() -> Path:
    handle = NamedTemporaryFile(
        prefix="atlas-x-",
        suffix=".ini",
        delete=False,
    )
    handle.close()
    return Path(handle.name)


def test_main_window_builds_workstation_shell() -> None:
    path = _settings_path()
    settings = QSettings(
        str(path),
        QSettings.Format.IniFormat,
    )
    composition = create_desktop_composition()
    window = _window(composition, settings)
    try:
        assert isinstance(window, QMainWindow)
        assert isinstance(window.shell_splitter, QSplitter)
        assert window.workspace_splitter.count() == 2
        assert window.statusBar() is window.persistent_status_bar
        assert window.start_button.text().endswith("Start Runtime")
        assert window.stop_button.text().endswith("Stop Runtime")
    finally:
        window.close()
        composition.close(timeout_seconds=1.0)
        settings.clear()
        settings.sync()
        path.unlink(missing_ok=True)


def test_splitters_geometry_and_sidebar_restore_safely() -> None:
    path = _settings_path()
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    first_composition = create_desktop_composition()
    first = _window(first_composition, settings)
    first.resize(1320, 820)
    first.show()
    APPLICATION.processEvents()
    first.workspace_splitter.setSizes([620, 380])
    first.shell_splitter.setSizes([210, 1110])
    first.sidebar.set_collapsed(True)
    first.pages.setCurrentIndex(2)
    first._save_workspace_state()
    saved_workspace = first.workspace_splitter.saveState()
    saved_shell = first.shell_splitter.saveState()
    first.close()
    first_composition.close(timeout_seconds=1.0)

    restored_settings = QSettings(
        str(path),
        QSettings.Format.IniFormat,
    )
    second_composition = create_desktop_composition()
    second = _window(second_composition, restored_settings)
    try:
        assert second.workspace_splitter.saveState() == saved_workspace
        assert second.shell_splitter.saveState() == saved_shell
        assert second.sidebar.is_collapsed is True
        assert second.pages.currentIndex() == 2
        assert second.geometry().isValid()
    finally:
        second.close()
        second_composition.close(timeout_seconds=1.0)
        restored_settings.clear()
        restored_settings.sync()
        path.unlink(missing_ok=True)
