from app.positions import PositionsPolicy,PositionsRequest
def request():return PositionsRequest("request-1","session-1",{"source":"synthetic"})
def enabled_policy():return PositionsPolicy(enabled=True)
