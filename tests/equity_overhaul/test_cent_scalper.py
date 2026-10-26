from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import pytest
from app.equity_overhaul.cent_scalper import Quote, Rules, replay

T = datetime(2026,10,26,15,tzinfo=UTC)


def q(sec, bid, **kw):
    at = T+timedelta(seconds=sec)
    return replace(Quote('AAPL',at,at,D(bid),D(bid)+D('.01'),D(100),D(100),True),**kw)


def entry():
    return [q(0,'10'),q(1,'10.01'),q(2,'10.02'),q(3,'10.03')]


def test_next_quote_ask_entry_net_bid_exit_and_separate_five_ten_cent_targets():
    rows=entry()+[q(4,'10.11'),q(5,'10.16')]
    five=replay(rows);ten=replay(rows,Rules(target=D('.10')))
    assert five['trades'][0]['exit_at']==rows[4].received_at.isoformat()
    assert ten['trades'][0]['exit_at']==rows[5].received_at.isoformat()
    assert five['net']==D('.53') and ten['net']==D('1.03')
    assert five['orders']==0 and not five['qualified'] and not five['execution_authorized']


def test_multiple_independent_cycles_with_cooldown_no_hod_or_bar_dependency():
    rows=entry()+[q(4,'10.11'),q(14,'10.20'),q(15,'10.21'),q(16,'10.22'),q(17,'10.23'),q(18,'10.31')]
    r=replay(rows)
    assert r['closed_trades']==2 and r['wins']==2


def test_spread_compression_alone_and_costs_larger_than_target_do_not_signal():
    assert replay([q(i,'10',ask=D(10)+D('.03')-D('.01')*i) for i in range(3)])['signals']==0
    assert replay(entry(),Rules(minimum_fee=D('1')))['signals']==0


def test_signal_quote_cannot_fill_itself_and_end_inventory_is_reported():
    r=replay(entry()[:3]);assert r['pending_entries']==1 and r['closed_trades']==0
    r=replay(entry());assert r['open_positions']==1 and r['closed_trades']==0
    assert r['open_net_mark']<0


def test_gap_stop_fills_at_worse_bid_not_ideal_stop():
    r=replay(entry()+[q(10,'9.50')])
    assert r['trades'][0]['reason']=='STOP' and r['trades'][0]['exit_after_gap']
    assert r['net']==D('-5.57')


@pytest.mark.parametrize('change',[dict(halted=True),dict(tradable=False),dict(quoted_at=T),dict(quoted_at=T+timedelta(seconds=5))])
def test_invalid_entry_quote_does_not_fill(change):
    rows=entry();rows[-1]=replace(rows[-1],**change)
    if change.get('quoted_at')==T:
        with pytest.raises(ValueError):replay(rows)
    else:
        assert replay(rows)['open_positions']==0


def test_no_perfect_fill_when_next_quote_has_already_run_target_distance():
    r=replay(entry()[:3]+[q(3,'10.20')])
    assert r['cancelled_entries']==1 and r['open_positions']==0


def test_time_and_session_exits_and_insufficient_size():
    assert replay(entry()+[q(64,'10.04')])['trades'][0]['reason']=='TIME_EXIT'
    assert replay(entry()+[q(4,'10.04',regular_session=False)])['trades'][0]['reason']=='SESSION_EXIT'
    assert replay(entry()[:3]+[q(3,'10.03',ask_size=D(1))])['open_positions']==0


def test_bad_or_reordered_inputs_rejected_and_stale_open_mark_not_invented():
    with pytest.raises(ValueError):q(0,'NaN')
    with pytest.raises(ValueError):replay([q(1,'10'),q(0,'10')])
    with pytest.raises(ValueError):replay([q(0,'10'),q(1,'10',symbol='MSFT')])
    r=replay(entry()+[q(4,'11',halted=True)])
    assert r['open_positions']==1 and r['open_net_mark'] is None
    assert replay([])['closed_trades']==0


def test_offline_cli_hashes_input_and_keeps_existing_reports(tmp_path,monkeypatch):
    from dataclasses import asdict
    import hashlib, importlib.util, json
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'tools/compare-cent-scalper.py'
    spec=importlib.util.spec_from_file_location('cent_compare',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=tmp_path/'quotes.jsonl';out=tmp_path/'report.json'
    source.write_text('\n'.join(json.dumps(asdict(x),default=str) for x in entry()+[q(4,'10.16')]))
    monkeypatch.setattr('sys.argv',['compare','--quotes',str(source),'--output',str(out)])
    module.main()
    result=json.loads(out.read_text())
    assert result['quote_file_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(result['cases'])==2 and result['promotion']=='NONE'
    assert result['network_requests']==result['orders']==0
    before=out.read_bytes()
    with pytest.raises(SystemExit):module.main()
    assert out.read_bytes()==before
