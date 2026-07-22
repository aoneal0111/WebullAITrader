from app import broker
from app.order_placement import NormalizedOrderStatus,OrderPlacementDependencyError,OrderPlacementError,OrderPlacementRequest,OrderPlacementResult,OrderPlacementRuntime,OrderPlacementSerializationError,OrderPlacementValidationError,serialize_request,serialize_result

def test_public_aliases_preserve_contract_identity():
    assert broker.BrokerOrderRequest is OrderPlacementRequest
    assert broker.BrokerOrderResult is OrderPlacementResult
    assert broker.BrokerOrderStatus is NormalizedOrderStatus
    assert broker.BrokerOrderExecutor is OrderPlacementRuntime

def test_exception_aliases_preserve_identity():
    assert broker.BrokerOrderError is OrderPlacementError
    assert broker.BrokerOrderValidationError is OrderPlacementValidationError
    assert broker.BrokerOrderDependencyError is OrderPlacementDependencyError
    assert broker.BrokerOrderSerializationError is OrderPlacementSerializationError

def test_serializer_aliases_delegate_by_identity():
    assert broker.serialize_broker_order_request is serialize_request
    assert broker.serialize_broker_order_result is serialize_result

def test_public_surface_is_intentional():
    expected={"BrokerOrderRequest","BrokerOrderResult","BrokerOrderStatus","BrokerOrderExecutor","BrokerOrderError","BrokerOrderValidationError","BrokerOrderDependencyError","BrokerOrderSerializationError","serialize_broker_order_request","serialize_broker_order_result"}
    assert set(broker.__all__)==expected
