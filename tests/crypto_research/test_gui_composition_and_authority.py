from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import app.composition.desktop as desktop_module
from app.configuration import load_configuration
from app.crypto_research import (
    CryptoResearchRuntime,
    CryptoResearchStatus,
    CryptoResearchViewStore,
)
from app.gui.pages.crypto_research import CryptoResearchPage

from .test_models_and_analysis import T0, observation


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_configuration_is_disabled_by_default_and_bounded(tmp_path) -> None:
    default = load_configuration({})
    assert default.crypto_discovery_enabled is False
    assert default.crypto_discovery_symbols == ()
    configured = load_configuration({
        "CRYPTO_DISCOVERY_ENABLED": "true",
        "CRYPTO_DISCOVERY_SYMBOLS": "BTC/USD,ETH-USD",
        "CRYPTO_DISCOVERY_REFRESH_SECONDS": "30",
        "CRYPTO_DISCOVERY_QUEUE_CAPACITY": "64",
        "CRYPTO_DISCOVERY_PATH": str(tmp_path / "external.jsonl"),
    })
    assert configured.crypto_discovery_symbols == ("BTC/USD", "ETH-USD")
    assert configured.crypto_discovery_refresh_seconds == 30
    assert configured.crypto_discovery_queue_capacity == 64


def test_disabled_composition_does_not_initialize_provider_or_output(
    monkeypatch, tmp_path,
) -> None:
    output = tmp_path / "crypto.jsonl"
    configuration = load_configuration({"CRYPTO_DISCOVERY_PATH": str(output)})
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    composition = desktop_module.create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3"
    )
    try:
        runtime = composition.crypto_research_runtime
        assert runtime is not None and runtime.enabled is False
        assert runtime._worker is None and runtime._poller is None
        assert not output.exists()
        assert "crypto_research" in composition.memory_observability._providers
    finally:
        composition.close(timeout_seconds=1)
    assert not output.exists()


def test_gui_is_segmented_and_has_no_execution_controls(application) -> None:
    del application
    item = observation(0, "100", "10")
    class Store:
        def append(self, _record):
            return None

        def close(self):
            return None

    runtime = CryptoResearchRuntime(enabled=True, store=Store(), clock=lambda: T0)
    runtime._accepting = True
    runtime._evaluate_and_persist(item)

    class Source:
        def snapshot(self):
            return runtime.latest()

    page = CryptoResearchPage(Source())
    page.refresh()
    assert page.findChild(type(page.empty_label), "cryptoResearchDisclosure").text() == (
        "24/7 | NO EXECUTION AUTHORITY"
    )
    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "BTC/USD"
    assert page.table.item(0, 10).text() == "CRYPTO"
    assert page.table.item(0, 11).text() == "RESEARCH ONLY"
    assert not page.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)
    page.close()
    runtime.close()


def test_gui_renders_unavailable_volume_and_all_research_statuses(application) -> None:
    del application
    store = CryptoResearchViewStore()
    page = CryptoResearchPage(store)
    item = observation(0, "100", None)

    class MemoryStore:
        def append(self, _record):
            return None

        def close(self):
            return None

    runtime = CryptoResearchRuntime(
        enabled=True, store=MemoryStore(), view_store=store, clock=lambda: T0,
    )
    runtime._accepting = True
    runtime._evaluate_and_persist(item)
    page.refresh()
    assert page.table.item(0, 3).text() == "--"
    assert page.table.item(0, 4).text() == "--"

    for status in CryptoResearchStatus:
        store.publish_status(
            status, "PROVIDER_ERROR" if status is CryptoResearchStatus.PROVIDER_ERROR else None
        )
        page.refresh()
        assert status.value in page.status_label.text()
        if status is CryptoResearchStatus.PROVIDER_ERROR:
            assert "PROVIDER_ERROR)" in page.status_label.text()
    page.close()
    runtime.close()


def test_crypto_package_has_no_execution_or_equity_decision_imports() -> None:
    root = Path(__file__).parents[2] / "app" / "crypto_research"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in root.glob("*.py")
    )
    forbidden = (
        "app.paper_trading", "app.live_execution", "app.order_placement",
        "app.order_cancellation", "app.momentum_scanner", "warrior_momentum",
        "adaptive_entry", "entry_opportunity_value", "TradingClient",
    )
    assert not any(value in source for value in forbidden)
    assert "supports_crypto=False" in (
        Path(__file__).parents[2] / "app" / "broker_plugins" / "webull" / "plugin.py"
    ).read_text(encoding="utf-8")
    assert '"instrument_type": "EQUITY"' in (
        Path(__file__).parents[2] / "app" / "webull" / "serializers.py"
    ).read_text(encoding="utf-8")
