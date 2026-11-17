from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from tests.test_desktop_broker_runtime import configuration
from app.configuration import TradingEnvironment
from app.webull.stream_connect_review import check
from app.webull.errors import NetworkError
import app.webull.stream_connect_review as module


def test_explicit_market_data_probe_has_no_desktop_account_or_order_dependency(monkeypatch):
    calls = []
    transport = NS(connect=lambda:calls.append('connect'),
        subscribe=lambda symbols:calls.append(('subscribe', symbols)),
        request_stop=lambda:calls.append('stop'),
        disconnect=lambda **kw:calls.append(('disconnect', kw)),
        read_event=lambda:None)
    clock = iter([0, 1, 3])
    monkeypatch.setattr(module, 'monotonic', lambda:next(clock))
    report = check(configuration(), seconds=2, factory=lambda config:transport)
    assert report['status'] == 'SUBSCRIBED_NO_PAYLOADS'
    assert report['orders'] == 0 and report['trading_runtime_started'] is False
    assert calls == ['connect', ('subscribe', ('AAPL',)), 'stop', ('disconnect', {'timeout_seconds':5})]


def test_connect_failure_retains_safe_cause_and_always_disconnects():
    class OriginalFailure(Exception):
        error_code = 'INVALID_SESSION'
        status_code = 417
    calls = []
    def connect():
        try: raise OriginalFailure('app_secret=DO_NOT_EXPORT')
        except OriginalFailure as error: raise NetworkError('connection failed') from error
    report = check(configuration(), factory=lambda cfg:NS(connect=connect,
        request_stop=lambda:calls.append('stop'), disconnect=lambda **kw:calls.append('disconnect')))
    assert report['status'] == 'FAILED' and report['stage'] == 'CONNECT'
    assert report['cause']['sdk_error_code'] == 'INVALID_SESSION'
    assert calls == ['stop', 'disconnect']
    assert 'DO_NOT_EXPORT' not in str(report)


@pytest.mark.parametrize('config', [replace(configuration(), live_trading_enabled=True),
                                  replace(configuration(), environment=TradingEnvironment.PRODUCTION)])
def test_unsafe_mode_is_refused_before_factory(config):
    with pytest.raises(ValueError):
        check(config, factory=lambda _:pytest.fail('must not construct provider'))
