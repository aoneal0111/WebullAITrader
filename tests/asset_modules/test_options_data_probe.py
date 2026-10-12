"""Verified SDK read-only calls, bounded diagnostics and no inferred quotes."""
from datetime import UTC, datetime
from types import SimpleNamespace
import json
from unittest.mock import Mock

import pytest

from app.webull.options_data_probe import OptionsDataProbe, schema_sample, supported_symbol

NOW = datetime(2026,10,12,15,tzinfo=UTC)
SYMBOL = 'QQQ261016C00500000'


def sdk(payload=None,status=200):
    response = SimpleNamespace(status_code=status,content=b'{}',json=Mock(return_value=payload or {'data':[{'symbol':SYMBOL}]}))
    client = SimpleNamespace(instrument=SimpleNamespace(get_option_contracts=Mock(return_value=response)),
        option_market_data=SimpleNamespace(get_option_snapshot=Mock(return_value=response)))
    factory = Mock(return_value=client)
    return OptionsDataProbe(factory,clock=lambda:NOW),client,factory,response


def test_lazy_discovery_and_snapshot_use_only_the_pinned_sdk_read_methods():
    p,c,f,_ = sdk()
    assert not f.called
    discovery = p.discover('QQQ')
    assert discovery.symbols == (SYMBOL,) and discovery.state=='RESPONSE_CAPTURED'
    c.instrument.get_option_contracts.assert_called_once_with(category='US_OPTION',underlying_symbols='QQQ',status='LISTING',page_size=10)
    result = p.snapshots(discovery.symbols)
    c.option_market_data.get_option_snapshot.assert_called_once_with(symbols=[SYMBOL],category='US_OPTION')
    assert f.call_count == 1 and p.calls == 2
    assert result.report()['quote_readiness']=='UNVALIDATED_RESPONSE_SCHEMA'


@pytest.mark.parametrize('value',['AAPL261016C00500000','QQQ261332P00500000','QQQ261016P00000000','QQQ','QQQ1XXXX',None])
def test_bad_or_out_of_scope_symbols_rejected_without_client(value):
    p,_,f,_=sdk()
    assert not supported_symbol(value)
    with pytest.raises(ValueError): p.snapshots((value,))
    assert not f.called and not p.calls


def test_non_spy_qqq_discovery_and_oversized_batch_do_not_initialize_client():
    p,_,f,_=sdk()
    with pytest.raises(ValueError): p.discover('AAPL')
    with pytest.raises(ValueError): p.snapshots((SYMBOL,)*21)
    with pytest.raises(ValueError): p.snapshots((SYMBOL,SYMBOL))
    assert not f.called


@pytest.mark.parametrize('status',[401,403,417,429,500])
def test_error_response_body_is_never_parsed_or_exposed(status):
    p,_,_,response=sdk({'api_key':'test-private','message':'secret-signed-request'},status)
    r=p.discover('SPY')
    assert r.state==f'HTTP_{status}' and r.schema is None
    response.json.assert_not_called()
    assert 'test-private' not in json.dumps(r.report())


def test_provider_exception_is_generic_and_failed_attempts_count():
    factory=Mock(side_effect=RuntimeError('private api_key=test-private'))
    p=OptionsDataProbe(factory,clock=lambda:NOW)
    for _ in range(3): assert p.discover('QQQ').state=='SDK_REQUEST_UNAVAILABLE'
    assert p.discover('SPY').state=='PROBE_REQUEST_LIMIT'
    assert factory.call_count==3


def test_request_budget_caps_discovery_and_quote_reads():
    p,c,_,_=sdk()
    p.discover('SPY');p.discover('QQQ');p.snapshots((SYMBOL,))
    assert p.snapshots((SYMBOL,)).state=='PROBE_REQUEST_LIMIT'
    assert c.option_market_data.get_option_snapshot.call_count==1


def test_schema_capture_keeps_market_values_and_removes_secrets_and_arbitrary_text():
    data={'data':[{'symbol':SYMBOL,'bid_price':'1.01','ask_price':'1.02',
        'bid_time':'2026-10-12T15:00:00Z','api_key':'test-private','access_token':'hidden',
        'price':'Bearer private','message':'test-private','underlying_symbol':'QQQ',
        'account':{'id':'private'},'timestamp':'1791817200000'}]}
    sample,symbols=schema_sample(data)
    encoded=json.dumps(sample)
    assert 'test-private' not in encoded and 'hidden' not in encoded and 'Bearer' not in encoded
    assert 'api_key' not in encoded and 'account' not in encoded
    assert symbols==(SYMBOL,) and '1.01' in encoded and '1791817200000' in encoded


def test_oversized_response_and_nonfinite_json_are_refused():
    p,_,_,r=sdk()
    r.content=b'x'*131073
    assert p.discover('QQQ').state=='RESPONSE_TOO_LARGE'
    r.json.assert_not_called()
    r.content=b'{}';r.json.return_value={'price':float('nan')}
    assert p.discover('SPY').state=='SDK_REQUEST_UNAVAILABLE'


def test_offline_check_never_loads_credentials_or_creates_sdk(monkeypatch,capsys):
    import sys
    from app.asset_modules import options_data_check as module
    monkeypatch.setattr(sys,'argv',['check'])
    monkeypatch.setattr(module,'OptionsDataProbe',lambda *_args,**_kwargs:pytest.fail('Offline client creation'))
    assert module.main()==0
    report=json.loads(capsys.readouterr().out)
    assert not report['online'] and not report['market_data_requests']
    assert not report['options_trading_enabled']


def test_discovery_does_not_promote_a_different_underlying_symbol():
    p,_,_,_=sdk()
    assert p.discover('SPY').symbols == ()
    assert p.discover('QQQ').symbols == (SYMBOL,)


@pytest.mark.parametrize('explicit', [False, True])
def test_online_command_uses_market_data_scope_and_bounded_reads(tmp_path,monkeypatch,capsys,explicit):
    import sys
    import dotenv
    import app.configuration.loader as loader
    import app.webull.sdk_market_data as data
    from app.asset_modules import options_data_check as module
    c=loader.load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER',
        'MARKET_DATA_ENVIRONMENT':'PRODUCTION',
        'WEBULL_MARKET_DATA_APP_KEY':'test-key','WEBULL_MARKET_DATA_APP_SECRET':'test-secret',
        'WEBULL_MARKET_DATA_API_BASE_URL':'https://api.webull.com',
        'WEBULL_MARKET_DATA_STREAM_URL':'wss://quotes-stream.webullsolutions.com',
        'EXECUTION_DATABASE_PATH':str(tmp_path/'execution.db')})
    p,client,_,_=sdk()
    factory=Mock(return_value=client)
    monkeypatch.setattr(data,'create_official_data_client',factory)
    monkeypatch.setattr(loader,'load_configuration',lambda:c)
    monkeypatch.setattr(dotenv,'load_dotenv',lambda **_:None)
    monkeypatch.setattr(sys,'argv',['check','--online'] + (['--symbols', SYMBOL+',SPY261016P00640000'] if explicit else []))
    assert module.main()==0
    text=capsys.readouterr().out
    report=json.loads(text)
    assert report['market_data_requests']==(2 if explicit else 3)
    assert len(report['results'])==(2 if explicit else 3)
    assert client.instrument.get_option_contracts.call_count==(0 if explicit else 2)
    if explicit:
        assert [r['requested_symbols'] for r in report['results']]==[[SYMBOL],['SPY261016P00640000']]
    assert factory.call_count==1
    assert factory.call_args.kwargs['app_key']=='test-key'
    assert 'test-key' not in text and 'test-secret' not in text
    assert not report['options_trading_enabled']


@pytest.mark.parametrize('kind',['server','client','attribute','unknown','unsafe_code'])
def test_safe_exception_diagnostics_do_not_expose_messages_ids_or_unknown_codes(kind):
    from webull.core.exception.exceptions import ServerException,ClientException
    p,c,_,_=sdk()
    if kind=='server': exc=ServerException('QUOTE_PERMISSION_DENIED','private signed request',403,'private-id')
    elif kind=='client': exc=ClientException('SDK.InvalidParameter','private signed request')
    elif kind=='attribute': exc=AttributeError('private signed request')
    elif kind=='unsafe_code': exc=ServerException('ERROR_PRIVATE_KEY_ABCD','private signed request',417,'private-id')
    else: exc=RuntimeError('private signed request')
    c.option_market_data.get_option_snapshot.side_effect=exc
    r=p.snapshots((SYMBOL,))
    text=json.dumps(r.report())
    assert 'private' not in text and 'ABCD' not in text and 'signed' not in text
    assert r.state=='SDK_REQUEST_UNAVAILABLE' and r.requested_symbols==(SYMBOL,)
    if kind=='server':
        assert r.diagnostics=={'exception_class':'ServerException','error_code':'QUOTE_PERMISSION_DENIED','http_status':403}
    elif kind=='client': assert r.diagnostics['error_code']=='SDK.InvalidParameter'
    elif kind=='unsafe_code': assert r.diagnostics['error_code']=='UNRECOGNIZED_ERROR_CODE'
    elif kind=='attribute': assert r.diagnostics['exception_class']=='AttributeError'
    else: assert r.diagnostics['exception_class']=='OTHER_EXCEPTION'


@pytest.mark.parametrize('args',[['--symbols',SYMBOL],['--online','--symbols','QQQ'],
    ['--online','--symbols',SYMBOL+','+SYMBOL],['--online','--symbols',','.join([SYMBOL]*3)]])
def test_invalid_explicit_contracts_stop_before_network(monkeypatch,args):
    import sys
    from app.asset_modules import options_data_check as module
    monkeypatch.setattr(sys,'argv',['check']+args)
    monkeypatch.setattr(module,'OptionsDataProbe',lambda *_args,**_kwargs:pytest.fail('Invalid network initialization'))
    with pytest.raises(SystemExit) as caught: module.main()
    assert caught.value.code==2
