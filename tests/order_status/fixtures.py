from app.order_status import OrderStatusPolicy,OrderStatusRequest
def request(client_order_id="client-1"):return OrderStatusRequest("request-1","session-1","account-1","broker-1",client_order_id,{"source":"synthetic"})
def enabled_policy():return OrderStatusPolicy(enabled=True)
