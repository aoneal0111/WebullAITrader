from app.webull_transport import *
from tests.webull_transport.helpers import order,policy
from tests.broker_adapter.helpers import STAMP
def test_limit_day_buy_mapping_deterministic():
 r=WebullTransportRequest(order(),STAMP,policy(),WebullTransportState(STAMP));a=WebullOrderMapper().map(r);assert a==WebullOrderMapper().map(r) and a.action is WebullOrderAction.BUY and a.order_type is WebullOrderType.LMT and a.time_in_force is WebullTimeInForce.DAY
