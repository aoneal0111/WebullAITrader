from app.order_cancellation import OrderCancellationPolicy,OrderCancellationRequest
def request(client_order_id="client-1"):return OrderCancellationRequest("request-1","session-1","account-1","broker-1",client_order_id,{"source":"synthetic"})
def enabled_policy():return OrderCancellationPolicy(enabled=True)
