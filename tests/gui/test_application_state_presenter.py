from types import SimpleNamespace

from app.gui.presenters.application_state_presenter import (
    PresentationCoordinator,
    RuntimeStatusPresenter,
)


class _LabelStub:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, value: str) -> None:
        self.text = value


def test_runtime_status_presenter_prefers_health_market_data_status() -> None:
    label = _LabelStub()
    presenter = RuntimeStatusPresenter(label)

    state = SimpleNamespace(
        runtime=SimpleNamespace(
            environment="PAPER",
            phase=SimpleNamespace(value="RUNNING"),
            market_feed_status="REST_ONLY",
            cycles_completed=7,
        ),
        health_projection=SimpleNamespace(
            market_data_status="CONNECTED",
        ),
    )

    presenter.render(state)

    assert label.text == (
        "PAPER  |  Runtime RUNNING  |  Feed CONNECTED  |  Cycles 7"
    )


def test_runtime_status_presenter_falls_back_to_runtime_feed_status() -> None:
    label = _LabelStub()
    presenter = RuntimeStatusPresenter(label)

    state = SimpleNamespace(
        runtime=SimpleNamespace(
            environment="PAPER",
            phase=SimpleNamespace(value="RUNNING"),
            market_feed_status="REST_ONLY",
            cycles_completed=3,
        ),
        health_projection=SimpleNamespace(
            market_data_status=None,
        ),
    )

    presenter.render(state)

    assert label.text == (
        "PAPER  |  Runtime RUNNING  |  Feed REST_ONLY  |  Cycles 3"
    )


def test_presentation_coordinator_skips_hidden_expensive_routes() -> None:
    calls: list[str] = []

    class Presenter:
        def __init__(self, name: str) -> None:
            self.name = name

        def render(self, _state) -> None:
            calls.append(self.name)

    dashboard = Presenter("dashboard")
    timeline = Presenter("timeline")
    health = Presenter("health")
    coordinator = PresentationCoordinator(
        (dashboard, timeline, health),
        active_routes=({0}, {5}, None),
    )

    coordinator.render(object(), active_route=0)
    assert calls == ["dashboard", "health"]
    calls.clear()
    coordinator.render(object(), active_route=5)
    assert calls == ["timeline", "health"]
