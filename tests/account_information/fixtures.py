from app.account_information import AccountInformationPolicy, AccountInformationRequest


def request(): return AccountInformationRequest("request-1", "session-1", {"source": "synthetic"})
def enabled_policy(): return AccountInformationPolicy(enabled=True)
