from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import json
from hashlib import sha256
from pathlib import Path

import pytest
from app.equity_overhaul.cent_scalper import Quote
from app.equity_overhaul.cent_portfolio import Portfolio,Protocol
from app.equity_overhaul.cent_journal_replay import run,PROOF_SOURCE
from app.asset_modules.daily_research_journal import DailyJournal

T=datetime(2026,10,28,15,tzinfo=UTC)


def q(sec,bid='10',symbol='AAA',**changes):
    at=T+timedelta(seconds=sec)
    return replace(Quote(symbol,at,at,D(bid),D(bid)+D('.01'),D(100),D(100),True),**changes)


def feed(engine,sec,bid='10',symbol='AAA',**kwargs):
    engine.feed(replace(q(sec,bid,symbol),**kwargs.pop('quote_changes',{})),D(str(sec)),int(sec*100)+1,
        eligible=kwargs.pop('eligible',True),session=kwargs.pop('session','CORE'),**kwargs)


def enter(engine,symbol='AAA'):
    for sec,bid in enumerate(('10','10.01','10.02','10.03')):feed(engine,sec,bid,symbol)


def test_net_target_and_cash_use_market_ask_then_bid_with_no_extra_spread_fee():
    engine=Portfolio(Protocol());enter(engine)
    assert len(engine.positions)==1 and engine.cash==D('899.515')
    feed(engine,4,'10.11')
    r=engine.result()
    assert r['closed_net']==D('.530') and r['ending_cash']==D('1000.530')
    assert r['wins']==1 and not r['open_positions'] and not r['qualified']


def test_five_and_ten_cent_cases_remain_distinct():
    five=Portfolio(Protocol());ten=Portfolio(Protocol(target=D('.10')))
    for engine in (five,ten):enter(engine);feed(engine,4,'10.11')
    assert len(five.trades)==1 and len(ten.positions)==1
    feed(ten,5,'10.16')
    assert ten.result()['closed_net']==D('1.030')


def test_shared_capital_and_two_position_limit_apply_across_symbols():
    engine=Portfolio(Protocol())
    for sec,bid in enumerate(('10','10.01','10.02','10.03')):
        for symbol in ('AAA','BBB','CCC'):feed(engine,sec,bid,symbol)
    assert len(engine.positions)==2 and engine.cash>=0
    assert engine.counts['SHARED_POSITION_LIMIT']==1


def test_no_entry_from_unknown_state_wide_spread_or_spread_compression():
    for mode in ('unknown','wide','compression'):
        engine=Portfolio(Protocol())
        for sec,bid in enumerate(('10','10.01','10.02','10.03')):
            changes={'ask':D(bid)+D('.02')} if mode=='wide' else {}
            if mode=='compression':changes={'bid':D('10'),'ask':D('10.04')-D('.01')*sec}
            feed(engine,sec,bid,eligible=mode!='unknown',quote_changes=changes)
        assert engine.counts['simulated_entry_fills']==0


def test_expired_entry_state_does_not_block_protective_bid_exit():
    engine=Portfolio(Protocol());enter(engine)
    feed(engine,4,'10.11',eligible=False)
    assert engine.result()['closed_trades']==1


def test_gap_stop_and_partial_exit_use_observed_size_and_prices():
    engine=Portfolio(Protocol());enter(engine)
    feed(engine,10,'9.50',quote_changes={'bid_size':D(4)})
    assert engine.positions['AAA']['remaining']==6
    assert engine.result()['current_liquidation_mark'] is None
    feed(engine,11,'9.40')
    assert engine.trades[0]['reason']=='STOP' and engine.trades[0]['net']==D('-6.170')
    assert engine.cash==D('993.830')


def test_stale_halted_or_duplicate_quotes_do_not_invent_exit_fills():
    engine=Portfolio(Protocol());enter(engine)
    feed(engine,4,'11',halted=True)
    assert engine.result()['current_liquidation_mark'] is None and len(engine.positions)==1
    feed(engine,5,'11',quote_changes={'quoted_at':T+timedelta(seconds=1)})
    assert len(engine.positions)==1
    feed(engine,6,'10.11')
    assert len(engine.positions)==0


def test_session_change_and_time_exit_close_at_observed_bid():
    for when,session,reason in ((64,'CORE','TIME_EXIT'),(4,'AFTER_HOURS','SESSION_EXIT')):
        engine=Portfolio(Protocol());enter(engine);feed(engine,when,'10.04',session=session)
        assert engine.trades[0]['reason']==reason
    engine=Portfolio(Protocol())
    for sec,bid in enumerate(('10','10.01','10.02','10.03')):feed(engine,sec,bid,session='AFTER_HOURS')
    assert len(engine.positions)==1


def test_pending_signal_requires_next_quote_and_cancels_if_move_already_consumed():
    engine=Portfolio(Protocol())
    for sec,bid in enumerate(('10','10.01','10.02')):feed(engine,sec,bid)
    assert not engine.positions and engine.pending
    feed(engine,3,'10.20')
    assert not engine.positions and engine.counts['pending_cancelled']==1


def test_frozen_protocol_refuses_tuning_and_unknown_marks_remain_explicit():
    with pytest.raises(ValueError):Protocol(max_positions=9)
    with pytest.raises(ValueError):Protocol(target=D('.07'))
    engine=Portfolio(Protocol());enter(engine);engine.last_tick=D(100)
    assert engine.result()['current_liquidation_mark'] is None
    assert len(engine.result()['open_positions'])==1


def session_fixture(tmp_path,proof=True):
    root=tmp_path/'session';root.mkdir();base='a'*64
    source=Path(__file__).resolve().parents[2]
    (root/'research-manifest.json').write_text(json.dumps(dict(scope='NORMAL_DESKTOP_DAILY_RESEARCH_V1',
        baseline_sha256=base,source_files={name:sha256((source/name).read_bytes()).hexdigest() for name in (PROOF_SOURCE,
            'app/strategies/warrior_momentum/desktop_sidecar.py','app/asset_modules/grok_observer_runtime_tap.py')})))
    (root/'research-summary.json').write_text(json.dumps(dict(policy_unchanged=True)))
    tick=[0]
    journal=DailyJournal(root/'journal',base,clock=lambda:T+timedelta(seconds=tick[0]),timer=lambda:tick[0])
    for sec,bid in enumerate(('10','10.01','10.02','10.03','10.11','10.16')):
        tick[0]=sec
        if proof=='heartbeat':
            journal.submit('MARKET_ELIGIBILITY',dict(symbol='AAA',tradable=True,halted=False,
                known_at=T+timedelta(seconds=sec),source='NATIVE_SCANNER_OBSERVATION'))
        elif proof:
            from tests.warrior_momentum.test_quick_scalper import snapshot
            journal.submit('STREAM_ASSESSMENT',dict(snapshot=replace(snapshot(),symbol='AAA',
                setup_type='QUICK_SCALP_STREAM',decision_at=T+timedelta(seconds=sec))))
        quote=q(sec,bid)
        journal.submit('MARKET_EVENT',dict(symbol='AAA',event_type='QUOTE',timestamp=quote.quoted_at,
            payload=dict(bid=quote.bid,ask=quote.ask,bid_size=quote.bid_size,ask_size=quote.ask_size)))
    journal.close();return root


def test_journal_adapter_runs_all_frozen_cases_and_preserves_recording(tmp_path):
    root=session_fixture(tmp_path);before={p:sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    report=run(root,tmp_path/'result')
    assert len(report['cases'])==4 and report['cases'][0]['result']['closed_net']==D('.530')
    assert report['promotion']=='NONE' and report['orders']==report['grok_requests']==report['network_requests']==0
    assert report['input_counts']['book_quotes']==6
    assert before=={p:sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_no_native_proof_blocks_entries_instead_of_inventing_tradability(tmp_path):
    report=run(session_fixture(tmp_path,False),tmp_path/'result')
    assert all(case['result']['closed_trades']==0 for case in report['cases'])
    assert report['input_counts']['book_without_current_eligibility']==6


def test_future_provider_time_does_not_poison_subsequent_valid_book():
    engine=Portfolio(Protocol())
    feed(engine,0,quote_changes={'quoted_at':T+timedelta(seconds=90)})
    enter(engine)
    assert engine.counts['future_quote']==1 and len(engine.positions)==1


def test_halted_quote_object_cannot_enter_even_if_eligibility_argument_is_true():
    engine=Portfolio(Protocol())
    for sec,bid in enumerate(('10','10.01','10.02','10.03')):
        feed(engine,sec,bid,quote_changes={'halted':True})
    assert not engine.positions


def test_market_eligibility_heartbeat_coalesces_unchanged_state_but_records_changes(tmp_path):
    from app.asset_modules.grok_observer_capture import ContextCapture
    from app.asset_modules.grok_observer_runtime_tap import RuntimeContextTap
    base='a'*64;journal=DailyJournal(tmp_path/'journal',base,clock=lambda:T)
    capture=ContextCapture(tmp_path/'capture',base,clock=lambda:T)
    tap=RuntimeContextTap(capture,base,journal=journal)
    try:
        assert tap.record_market_eligibility('AAA',tradable=True,halted=False,at=T)
        assert not tap.record_market_eligibility('AAA',tradable=True,halted=False,at=T+timedelta(seconds=.5))
        assert tap.record_market_eligibility('AAA',tradable=True,halted=True,at=T+timedelta(seconds=.6))
        assert tap.record_market_eligibility('AAA',tradable=False,halted=False,at=T+timedelta(seconds=.7))
    finally:tap.close();result=journal.close()
    assert result['counts']['MARKET_ELIGIBILITY']==3


def test_session_loss_limit_stops_new_entries_after_gap_loss():
    engine=Portfolio(Protocol());enter(engine);feed(engine,10,'1')
    assert engine.realized<-D(20)
    for sec,bid in zip(range(20,24),('10','10.01','10.02','10.03')):feed(engine,sec,bid,symbol='BBB')
    assert not engine.positions and engine.counts['SESSION_LOSS_LIMIT']>0


def test_market_state_heartbeat_allows_replay_without_native_scalper_signal(tmp_path):
    report=run(session_fixture(tmp_path,'heartbeat'),tmp_path/'result')
    assert report['market_state_sources_verified']
    assert report['input_counts'].get('native_eligibility_proofs',0)==0
    assert report['input_counts']['market_state_heartbeats']==6
    assert report['cases'][0]['result']['closed_trades']==1


def test_real_sidecar_records_state_before_any_rising_quote_setup(tmp_path):
    from app.asset_modules.grok_observer_capture import ContextCapture
    from app.asset_modules.grok_observer_runtime_tap import RuntimeContextTap
    from app.strategies.warrior_momentum.desktop_sidecar import WarriorDesktopSidecar
    from tests.warrior_momentum.test_desktop_sidecar import adapter,quote,trade,T0
    base='a'*64;journal=DailyJournal(tmp_path/'journal',base,clock=lambda:T0)
    capture=ContextCapture(tmp_path/'capture',base,clock=lambda:T0)
    tap=RuntimeContextTap(capture,base,journal=journal)
    sidecar=WarriorDesktopSidecar(enabled=True,storage_path=tmp_path/'forward.sqlite3',
        observer_context_tap=tap,clock=lambda:T0)
    scanner=adapter();sidecar.bind_scanner_adapter(scanner);sidecar.start('PAPER')
    try:
        for event in (quote(T0),trade(2,T0+timedelta(seconds=1),'10.20'),quote(T0+timedelta(seconds=2))):
            scanner.consume(event);sidecar(event)
    finally:sidecar.stop();tap.close();report=journal.close()
    assert report['counts']['MARKET_ELIGIBILITY']>=1
    rows=[json.loads(line) for path in (tmp_path/'journal').glob('events-*.jsonl') for line in path.read_text().splitlines()]
    state=next(row['payload'] for row in rows if row['kind']=='MARKET_ELIGIBILITY')
    assert state['tradable'] is True and state['halted'] is False
