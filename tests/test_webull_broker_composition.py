from types import SimpleNamespace

from app import operational_main


def test_build_broker_uses_configured_plugin_runtime(monkeypatch):
    configuration = SimpleNamespace(
        broker_provider="webull",
    )
    execution = object()
    captured = {}

    def fake_create_broker_runtime(
        *,
        provider,
        configuration,
        webull_broker_factory,
    ):
        captured.update(
            {
                "provider": provider,
                "configuration": configuration,
                "webull_broker_factory": webull_broker_factory,
            }
        )
        return SimpleNamespace(execution=execution)

    monkeypatch.setattr(
        operational_main,
        "create_broker_runtime",
        fake_create_broker_runtime,
    )

    assert operational_main.build_broker(configuration) is execution
    assert captured == {
        "provider": "webull",
        "configuration": configuration,
        "webull_broker_factory": operational_main.build_webull_broker,
    }
