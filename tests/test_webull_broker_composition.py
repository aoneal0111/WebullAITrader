from app import operational_main
from app.live_execution.broker_factory import build_webull_broker


def test_operational_main_preserves_build_broker_compatibility_alias():
    assert operational_main.build_broker is build_webull_broker
