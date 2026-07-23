from pathlib import Path

def test_broker_facade_depends_only_on_public_order_placement():
    files=(Path("app/broker/__init__.py"),Path("app/broker/interfaces.py"),Path("app/broker/serializers.py"),Path("app/broker/exceptions.py"))
    text="\n".join(path.read_text(encoding="utf-8").lower() for path in files)
    assert "app.order_placement." not in text
    prohibited=("research_","analytics","strategy","execution_orchestrator","persistence","storage","live_trading","webull","broker_adapter","broker_protocol")
    assert not [value for value in prohibited if value in text]

def test_facade_defines_no_duplicate_order_models():
    text="\n".join(path.read_text(encoding="utf-8") for path in Path("app/broker").glob("*.py") if path.name in {"__init__.py","interfaces.py","serializers.py","exceptions.py"})
    assert "@dataclass" not in text and "class BrokerOrderRequest" not in text and "class BrokerOrderResult" not in text
