"""Broker-named aliases for public Order Placement serializers."""
from app.order_placement import serialize_request,serialize_result

serialize_broker_order_request=serialize_request
serialize_broker_order_result=serialize_result

__all__=("serialize_broker_order_request","serialize_broker_order_result")
