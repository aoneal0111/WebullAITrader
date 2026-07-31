from decimal import Decimal
from types import SimpleNamespace

from app.broker_protocol.models import BrokerAccount, BrokerCash
from app.configuration import TradingConfiguration, TradingEnvironment
from app.webull.startup_validation import RuntimeStartupValidator, TradingProbeState


class Broker:
    def __init__(self, calls, *, account_error=None):
        self.calls = calls
        self.account_error = account_error

    def get_account(self):
        self.calls.append("trading.account")
        if self.account_error:
            raise self.account_error
        return BrokerAccount("******ount", "PAPER", "ACTIVE")

    def get_cash(self):
        self.calls.append("trading.buying_power")
        return BrokerCash(
            Decimal("1000"), Decimal("0"), "USD", buying_power=Decimal("900")
        )

    def get_positions(self):
        self.calls.append("trading.positions")
        return ()


class MarketProbe:
    def __init__(self, calls, result):
        self.calls = calls
        self.result = result

    def run(self):
        self.calls.append("market_data.probe")
        return self.result


def trading(environment=TradingEnvironment.TEST):
    return TradingConfiguration(
        environment, "account", "trade-key", "trade-secret",
        "https://trade.example", "wss://trade.example/mqtt",
    )


def test_startup_validation_orders_trading_before_market_data():
    calls = []
    market_result = SimpleNamespace(scanner_ready=True, reason=None)
    result = RuntimeStartupValidator(
        Broker(calls), trading(), MarketProbe(calls, market_result)
    ).run()

    assert calls == [
        "trading.account", "trading.buying_power", "trading.positions",
        "market_data.probe",
    ]
    assert result.trading.authentication is TradingProbeState.CONNECTED
    assert result.trading.buying_power is TradingProbeState.OK
    assert result.trading.paper_trading is TradingProbeState.ENABLED
    assert result.scanner_ready is True
    assert result.trading.fingerprint.startswith("fp_")
    assert "trade-key" not in result.trading.fingerprint


def test_trading_failure_is_fail_closed_but_market_probe_still_reports():
    calls = []
    market_result = SimpleNamespace(scanner_ready=True, reason=None)
    result = RuntimeStartupValidator(
        Broker(calls, account_error=PermissionError("secret token")),
        trading(),
        MarketProbe(calls, market_result),
    ).run()

    assert result.trading.authentication is TradingProbeState.AUTH_FAILED
    assert result.scanner_ready is False
    assert calls == ["trading.account", "market_data.probe"]


def test_non_paper_environment_disables_order_capability():
    result = RuntimeStartupValidator(
        Broker([]),
        trading(TradingEnvironment.PRODUCTION),
        MarketProbe([], SimpleNamespace(scanner_ready=True, reason=None)),
    ).run()
    assert result.trading.paper_trading is TradingProbeState.DISABLED
    assert result.scanner_ready is False
