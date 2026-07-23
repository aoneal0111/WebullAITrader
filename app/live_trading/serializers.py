from app.live_trading.exceptions import LiveTradingSerializationError
from app.live_trading.models import *
def _serialize(value,expected):
    if not isinstance(value,expected):raise LiveTradingSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()
serialize_identity=lambda v:_serialize(v,LiveTradingIdentity)
serialize_order_identity=lambda v:_serialize(v,LiveTradingOrderIdentity)
serialize_policy=lambda v:_serialize(v,LiveTradingPolicy)
serialize_order_request=lambda v:_serialize(v,LiveTradingOrderRequest)
serialize_request=lambda v:_serialize(v,LiveTradingRequest)
serialize_criteria=lambda v:_serialize(v,LiveTradingCriteriaResult)
serialize_research_record=lambda v:_serialize(v,LiveTradingResearchRecord)
serialize_order_record=lambda v:_serialize(v,LiveTradingOrderRecord)
serialize_summary=lambda v:_serialize(v,LiveTradingSummary)
serialize_result=lambda v:_serialize(v,LiveTradingResult)
