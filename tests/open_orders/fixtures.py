from app.open_orders import OpenOrdersPolicy,OpenOrdersRequest
def request():return OpenOrdersRequest("request-1","account-1",{"source":"synthetic"})
def enabled_policy():return OpenOrdersPolicy(enabled=True)
