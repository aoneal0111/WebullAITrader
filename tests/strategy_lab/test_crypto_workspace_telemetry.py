from datetime import UTC,datetime
from pathlib import Path
from types import SimpleNamespace as NS,ModuleType
import json
import sys
from app.strategy_lab.crypto_workspace_telemetry import publish,observe
from app.gui.projections.asset_workspace import crypto_view

NOW=datetime(2026,11,14,15,tzinfo=UTC)


def fixture(monkeypatch,tmp_path):
    trade=NS(id=1,pair='BTC/USD',amount=2,calc_profit=lambda mark:2*(mark-100)-2,
        calc_profit_ratio=lambda mark:(mark-101)/100)
    persistence=ModuleType('freqtrade.persistence');persistence.Trade=NS(get_open_trades=lambda:[trade])
    monkeypatch.setitem(sys.modules,'freqtrade.persistence',persistence)
    row={'date':NOW,'close':110,'volume':10}
    class Frame:
        empty=False
        @property
        def iloc(self):return self
        def __getitem__(self,index):return row
    bot=NS(config=dict(dry_run=True,stake_currency='USD'),active_pair_whitelist=['BTC/USD'],
        dataprovider=NS(get_analyzed_dataframe=lambda *a:(Frame(),None)),
        wallets=NS(get_free=lambda s:700,get_used=lambda s:100,get_total=lambda s:800,get_available_stake_amount=lambda:690))
    evidence=NS(root=Path('recording-a'),primary='4h',timeframes={'4h':14400},source_states={'BTC/USD':dict(state='SOURCE_BUY_SIGNAL',buy=1)},ready=lambda *a:True)
    return bot,evidence


def test_publishes_native_balances_candle_estimates_and_fee_inclusive_profit_without_network(monkeypatch,tmp_path):
    bot,evidence=fixture(monkeypatch,tmp_path)
    publish(tmp_path,bot,evidence,NOW)
    telemetry=json.loads((tmp_path/'workspace-snapshot.json').read_text())
    assert telemetry['positions'][0]['unrealized']==18
    assert telemetry['positions'][0]['mark_basis']=='CANDLE CLOSE ESTIMATE'
    assert telemetry['account']['free']==700 and telemetry['account']['available_stake']==690
    bot.config['dry_run']=False;publish(tmp_path,bot,evidence,NOW)
    assert 'error' in json.loads((tmp_path/'workspace-snapshot.json').read_text())


def test_projects_native_wallet_scanner_and_only_matching_position_values(monkeypatch,tmp_path):
    bot,evidence=fixture(monkeypatch,tmp_path);publish(tmp_path,bot,evidence,NOW)
    telemetry=json.loads((tmp_path/'workspace-snapshot.json').read_text())
    wallet=dict(label='test',state='RUNNING',health='RECENT_CHECKPOINT',container_running=True,
        container_state='running',ledger_present=True,open_positions=1,working_orders=0,
        closed_lifecycle_net_usd=12,open_lifecycle_realized_usd=0,workspace_telemetry=telemetry,
        holdings=[dict(id=1,pair='BTC/USD',amount=2,open_rate=100)])
    view=crypto_view(wallet,NOW.isoformat());metrics=dict(view.portfolio.metrics)
    assert metrics['Equity']=='$1,020.00' and metrics['Cash']=='$800.00'
    assert metrics['Buying Power']=='$690.00' and metrics['Unrealized P/L']=='+$18.00'
    assert view.scanner.candidate_count==1 and view.scanner.rows[0].latest_price=='$110.00'
    assert 'CANDLE' in view.scanner.rows[0].freshness
    wallet['holdings'][0]['amount']=3
    mismatched=crypto_view(wallet,NOW.isoformat())
    assert dict(mismatched.portfolio.metrics)['Unrealized P/L']=='--'
    assert mismatched.positions.rows[0][4]=='--'
    wallet['container_running']=False
    stopped=crypto_view(wallet,NOW.isoformat())
    assert dict(stopped.portfolio.metrics)['Cash']=='--' and not stopped.scanner.rows


def test_reader_discards_stale_or_wrong_recording_telemetry_without_losing_ledger(tmp_path):
    from tests.strategy_lab.test_crypto_monitor import wallet,NOW as CLOCK
    from app.strategy_lab.crypto_monitor import CryptoMonitorReader
    work=wallet(tmp_path)
    status=json.loads((work/'engine-status.json').read_text());status['recording_id']='current'
    (work/'engine-status.json').write_text(json.dumps(status))
    reader=CryptoMonitorReader(tmp_path,clock=lambda:CLOCK,container_reader=lambda names:{})
    path=work/'workspace-snapshot.json'
    for value in [dict(scope='CRYPTO_WORKSPACE_TELEMETRY_V1',updated_at=CLOCK.isoformat(),
                       live_trading_enabled=False,recording_id='old'),
                  dict(scope='CRYPTO_WORKSPACE_TELEMETRY_V1',updated_at='2020-01-01T00:00:00+00:00',
                       live_trading_enabled=False,recording_id='current')]:
        path.write_text(json.dumps(value))
        row=reader.read()['engines'][0]
        assert row['workspace_telemetry'] is None and row['ledger_present']
    path.write_text('bad json')
    row=reader.read()['engines'][0]
    assert row['workspace_telemetry_state']=='INVALID_SNAPSHOT' and row['ledger_present']
