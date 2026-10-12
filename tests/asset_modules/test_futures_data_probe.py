from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
import json
import sys

import pytest

from app.webull.futures_data_probe import FuturesDataProbe, schema_sample, supported_contract

NOW=datetime(2026,10,12,15,tzinfo=UTC)
SYMBOL='MESZ6'  # Test identity only; the probe does not choose a tradable expiry.


def fixture_sdk(status=200):
    response=SimpleNamespace(status_code=status,content=b'{}',json=Mock(return_value={
        'data':[{'symbol':SYMBOL,'tick_size':'0.25','expire_date':'2026-12-18'}]}))
    client=SimpleNamespace(instrument=SimpleNamespace(
        get_futures_instrument_by_code=Mock(return_value=response),get_futures_instrument=Mock(return_value=response)),
        futures_market_data=SimpleNamespace(get_futures_depth=Mock(return_value=response),
                                            get_futures_history_bars=Mock(return_value=response)))
    factory=Mock(return_value=client)
    return FuturesDataProbe(factory,clock=lambda:NOW),client,factory,response


def test_lazy_monthly_discovery_never_picks_a_contract_or_reads_quotes():
    p,c,f,_=fixture_sdk()
    assert not f.called
    result=p.discover('MES')
    assert result.state=='RESPONSE_CAPTURED' and result.symbols==(SYMBOL,)
    c.instrument.get_futures_instrument_by_code.assert_called_once_with(code='MES',category='US_FUTURES',contract_type='MONTHLY')
    assert not c.futures_market_data.get_futures_depth.called
    assert result.report()['quote_readiness']=='UNVALIDATED_RESPONSE_SCHEMA'


def test_profile_depth_and_minute_bars_match_installed_sdk_and_cap_requests():
    p,c,f,_=fixture_sdk()
    for method in (p.profile,p.depth,p.bars): assert method(SYMBOL).state=='RESPONSE_CAPTURED'
    c.instrument.get_futures_instrument.assert_called_once_with(symbols=SYMBOL,category='US_FUTURES')
    c.futures_market_data.get_futures_depth.assert_called_once_with(symbol=SYMBOL,category='US_FUTURES',depth=1)
    c.futures_market_data.get_futures_history_bars.assert_called_once_with(symbols=SYMBOL,category='US_FUTURES',timespan='M1',count='48',real_time_required=True)
    assert p.depth(SYMBOL).state=='PROBE_REQUEST_LIMIT' and f.call_count==1
    assert p.calls==3


@pytest.mark.parametrize('symbol',['MESmain','MNQmain','ESZ6','NQZ6','MES','MESZ2026','MESY6','MESZ6,MNQZ6','mesz6',None])
def test_continuous_out_of_scope_or_unbounded_symbols_never_initialize_sdk(symbol):
    p,_,f,_=fixture_sdk()
    assert not supported_contract(symbol)
    for method in (p.profile,p.depth,p.bars):
        with pytest.raises(ValueError): method(symbol)
    assert not f.called and p.calls==0


def test_wrong_product_and_inherited_option_snapshot_are_refused():
    p,_,f,_=fixture_sdk()
    with pytest.raises(ValueError): p.discover('ES')
    with pytest.raises(ValueError): p.snapshots(('SPY261218C00640000',))
    assert not f.called


@pytest.mark.parametrize('status',[401,403,417,429,500])
def test_error_bodies_are_never_parsed(status):
    p,_,_,response=fixture_sdk(status)
    response.json.return_value={'api_key':'private-value'}
    r=p.profile(SYMBOL)
    assert r.state==f'HTTP_{status}' and r.schema is None
    response.json.assert_not_called()
    assert 'private-value' not in json.dumps(r.report())


def test_bounded_schema_retains_only_selected_futures_market_values():
    sample,symbols=schema_sample({'data':[{'symbol':SYMBOL,'bid_price':'5000.25',
        'ask_price':'5000.50','timestamp':'1791817200000','multiplier':'5',
        'initial_margin':'1000','api_key':'private-value','account_id':'hidden',
        'message':'secret-body','price':'NaN','code':'MES'}]})
    result=json.dumps(sample)
    assert symbols==(SYMBOL,) and '5000.25' in result and '1791817200000' in result
    assert 'private-value' not in result and 'hidden' not in result and 'secret-body' not in result
    assert 'NaN' not in result
    assert schema_sample({'symbol':'MESmain'})[1]==()


def test_schema_depth_budget_size_and_failures_are_bounded():
    p,_,_,response=fixture_sdk()
    response.content=b'x'*131073
    assert p.depth(SYMBOL).state=='RESPONSE_TOO_LARGE'
    response.json.assert_not_called()
    response.content=b'{}'; response.json.return_value={'price':float('nan')}
    assert p.depth(SYMBOL).state=='SDK_REQUEST_UNAVAILABLE'
    response.json.side_effect=RuntimeError('private-value')
    assert p.depth(SYMBOL).state=='SDK_REQUEST_UNAVAILABLE'
    assert p.depth(SYMBOL).state=='PROBE_REQUEST_LIMIT'
    nested={'bid':'1'}
    for _ in range(20): nested={'data':nested}
    assert 'truncated' in json.dumps(schema_sample(nested)[0])


def test_discovery_does_not_return_other_product_contracts():
    p,_,_,response=fixture_sdk()
    response.json.return_value={'data':[{'symbol':'MNQZ6'},{'symbol':SYMBOL}]}
    assert p.discover('MES').symbols==(SYMBOL,)


def test_cli_offline_has_zero_factory_calls_and_no_files(monkeypatch,capsys,tmp_path):
    from app.asset_modules.futures_data_check import main
    import app.webull.sdk_market_data as sdk
    factory=Mock(side_effect=AssertionError('No network'))
    monkeypatch.setattr(sdk,'create_official_data_client',factory)
    monkeypatch.setattr(sys,'argv',['futures_data_check'])
    monkeypatch.chdir(tmp_path)
    assert main()==0
    report=json.loads(capsys.readouterr().out)
    assert report['market_data_requests']==0 and report['futures_trading_enabled'] is False
    assert not factory.called and list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('args',[['--symbol',SYMBOL],['--online','--symbol','MESmain']])
def test_cli_invalid_arguments_fail_before_credentials_or_sdk(monkeypatch,args):
    from app.asset_modules.futures_data_check import main
    monkeypatch.setattr(sys,'argv',['futures_data_check',*args])
    with pytest.raises(SystemExit) as exc: main()
    assert exc.value.code==2
