from app.account_information import BrokerNeutralAccountInformation
from app.session import Session, SessionIdentifier, SessionSnapshot, SessionStatus


def active_snapshot(session_id="session-1"):
    identifier = SessionIdentifier(session_id)
    return SessionSnapshot(SessionStatus.ACTIVE, Session(identifier, "account access", SessionStatus.ACTIVE), (), 2)


class FakeSessionManager:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot if snapshot is not None else active_snapshot()
        self.error = error
        self.calls = 0
    def state(self):
        self.calls += 1
        if self.error: raise self.error
        return self.snapshot


class FakeGateway:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else BrokerNeutralAccountInformation(
            "account-1", "CASH", "ACTIVE", "1000.25", "500.00", "1500.25", "usd")
        self.error = error
        self.requests = []
    def get_account_information(self, request):
        self.requests.append(request)
        if self.error: raise self.error
        return self.response
