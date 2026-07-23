from app.portfolio import PortfolioPolicy,PortfolioRequest
def request():return PortfolioRequest("portfolio-request","account-1",{"source":"synthetic"})
def enabled_policy():return PortfolioPolicy(enabled=True)
