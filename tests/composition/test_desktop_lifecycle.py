from types import SimpleNamespace

from app.composition.desktop_lifecycle import close_desktop_composition


def test_execution_worker_stops_before_durable_paper_store_closes():
    store = {"open": True}
    def stop_worker():
        # Any completion work during drain must retain its persistence boundary.
        assert store["open"]
        store["worker_stopped"] = True
    def close_store():
        assert store.get("worker_stopped")
        store["open"] = False
    composition = SimpleNamespace(
        runtime_service=SimpleNamespace(close=lambda **kwargs: True),
        warrior_forward_sidecar=SimpleNamespace(stop=stop_worker),
        paper_trading_commands=SimpleNamespace(close=close_store),
        state_store=SimpleNamespace(close=lambda: None),
    )
    assert close_desktop_composition(composition)
    assert not store["open"]
