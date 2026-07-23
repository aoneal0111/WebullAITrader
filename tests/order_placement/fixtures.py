from app.order_placement import *
def order(order_type=OrderType.LIMIT,limit_price="10.25",stop_price=None):return OrderRequestModel("request-1","account-1","aapl",OrderSide.BUY,order_type,"2.5",limit_price,stop_price,TimeInForce.DAY,"client-1",{"source":"synthetic"})
def request():return OrderPlacementRequest("session-1",order())
def enabled_policy():return OrderPlacementPolicy(enabled=True)
