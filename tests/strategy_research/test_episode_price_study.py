from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.strategy_research.episode_price_study import (
    PriceBar,DayWallet,BarCache,POLICIES,FAMILIES,build_index,aggregate,prefix_ok,prefix_evidence,normalized_prefix_matches,run,plan,VERSION)
from app.strategy_research.episode_exit_mechanics import ExitPolicy,replay_long,next_minute_entry
from app.strategy_research.equity_corpus_review import REPORT
from tests.strategy_research.test_equity_corpus_review import fixture

T=datetime(2026,6,1,14,tzinfo=UTC)
DAY='2026-06-01'


def bars(count=65):
    return tuple(PriceBar('ABC',T+timedelta(minutes=i),D(10),D('10.1'),D('9.9'),D(10),D(10000)) for i in range(count))


def event(identity='a',symbol='ABC',at=T):
    return dict(episode_id=identity,symbol=symbol,day=DAY,detected_at=(at-timedelta(minutes=1)).isoformat(),
        planned_price='10',stop='9',families=[FAMILIES[0]],prefix_ok=True)


def source_row(identity='a'):
    e=event(identity);return dict(episode_id=identity,symbol='ABC',trading_date=DAY,
        detected_timestamp=e['detected_at'],trigger_timestamp=e['detected_at'],trigger_price='10',structural_stop='9',
        strategy_memberships=[FAMILIES[0]],provenance=dict(decision_cutoff=e['detected_at']),
        bar_window=[dict(symbol='ABC',timestamp=e['detected_at'],open='10',high='10.1',low='9.9',close='10',volume='10000')])


def file_bars(path,values):
    path.write_text(''.join(json.dumps(dict(symbol=b.symbol,timestamp=b.timestamp.isoformat(),open=str(b.open),
        high=str(b.high),low=str(b.low),close=str(b.close),volume=str(b.volume)))+'\n' for b in values))


class EpisodePriceTests(unittest.TestCase):
    def test_exact_next_minute_open_and_missing_minute_is_not_replaced(self):
        r=next_minute_entry(bars(),symbol='ABC',detected_time=T-timedelta(minutes=1),planned_price=D(8),initial_stop=D(9))
        self.assertEqual(r['entry_price'],D(10));self.assertEqual(r['entry_time'],T)
        missing=next_minute_entry(bars()[1:],symbol='ABC',detected_time=T-timedelta(minutes=1),planned_price=D(10),initial_stop=D(9))
        self.assertEqual(missing['status'],'NO_ENTRY_MISSING_NEXT_MINUTE')

    def test_full_target_and_partial_break_even_are_distinct(self):
        values=list(bars());values[0]=replace(values[0],high=D('10.6'))
        values[1]=replace(values[1],open=D('10.1'),low=D('9.9'),high=D('10.2'))
        full=replay_long(values,symbol='ABC',entry_time=T,entry_price=D(10),initial_stop=D(9),quantity=D(10),policy=POLICIES[0],cost_per_share_per_side=D('.01'))
        half=replay_long(values,symbol='ABC',entry_time=T,entry_price=D(10),initial_stop=D(9),quantity=D(10),policy=POLICIES[2],cost_per_share_per_side=D('.01'))
        self.assertEqual(full.net_pnl,D('4.80'));self.assertEqual(half.net_pnl,D('2.30'))
        self.assertEqual(half.fills[-1].price,D(10))

    def test_entry_bar_stop_target_conflict_remains_unresolved(self):
        values=[replace(bars()[0],high=D(12),low=D(8))]
        r=replay_long(values,symbol='ABC',entry_time=T,entry_price=D(10),initial_stop=D(9),quantity=D(10),policy=POLICIES[1])
        self.assertEqual(r.status,'UNRESOLVED_INTRABAR_ORDER');self.assertIsNone(r.net_pnl)

    def test_shared_position_slots_and_same_symbol_overlap(self):
        lane=DayWallet(FAMILIES[0],POLICIES[1],D('.01'),lambda r:None)
        for symbol in ('ABC','ABC','BBB','CCC'):
            values=tuple(replace(b,symbol=symbol) for b in (replace(bars()[0],timestamp=T-timedelta(minutes=1)),)+bars())
            lane.consider(event(symbol=symbol),values,T)
        self.assertEqual(len(lane.active),2)
        self.assertEqual(lane.counts['SYMBOL_ALREADY_OPEN'],1)
        self.assertEqual(lane.counts['POSITION_LIMIT'],1)
        self.assertGreaterEqual(lane.cash,0)

    def test_prior_completed_volume_caps_size_without_entry_bar_volume_lookahead(self):
        values=(replace(bars()[0],timestamp=T-timedelta(minutes=1),volume=D(100)),)+tuple(replace(b,volume=D(10000000)) for b in bars())
        lane=DayWallet(FAMILIES[0],POLICIES[1],D('.01'),lambda r:None)
        lane.consider(event(),values,T)
        self.assertEqual(lane.active['ABC']['audit']['quantity'],D(1))

    def test_exit_money_is_unavailable_until_intrabar_fill_is_known(self):
        values=(replace(bars()[0],timestamp=T-timedelta(minutes=1)),replace(bars()[0],high=D('10.6')))
        lane=DayWallet(FAMILIES[0],POLICIES[0],D('.01'),lambda r:None)
        lane.consider(event(),values,T)
        lane.settle(T+timedelta(seconds=30));self.assertIn('ABC',lane.active)
        lane.settle(T+timedelta(minutes=1));self.assertNotIn('ABC',lane.active)
        self.assertGreater(lane.cash,D(1000))

    def test_unresolved_position_reserves_cash_and_excludes_day_from_headline(self):
        values=(replace(bars()[0],timestamp=T-timedelta(minutes=1)),replace(bars()[0],high=D(12),low=D(8)))
        lane=DayWallet(FAMILIES[0],POLICIES[1],D('.01'),lambda r:None)
        lane.consider(event(),values,T);day=lane.finish(DAY)
        self.assertEqual(day['unresolved_positions'],1);self.assertGreater(day['reserved_cash'],0)
        self.assertEqual(aggregate([day])['excluded_unresolved_days'],1)
        self.assertIsNone(aggregate([day])['win_rate_resolved_days'])

    def test_future_or_missing_signal_prefix_blocks_entry(self):
        self.assertFalse(prefix_ok({},T))
        self.assertFalse(prefix_ok({'bar_window':[{'timestamp':T.isoformat()}]},T))
        self.assertTrue(prefix_ok(source_row(),T-timedelta(minutes=1)))
        lane=DayWallet(FAMILIES[0],POLICIES[1],D('.01'),lambda r:None)
        lane.consider(dict(event(),prefix_ok=False),bars(),T)
        self.assertEqual(lane.counts['UNVERIFIED_SIGNAL_PREFIX'],1)

    def test_bar_cache_rejects_unordered_invalid_and_changed_prices(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/f'ABC_{DAY}.jsonl';file_bars(p,bars())
            cache=BarCache(root);self.assertEqual(len(cache.get('ABC',DAY)),65)
            file_bars(p,bars()[:2])
            with self.assertRaisesRegex(ValueError,'CHANGED'):cache.verify_stable()
            file_bars(p,tuple(reversed(bars())))
            with self.assertRaises(ValueError):BarCache(root).get('ABC',DAY)

    def test_index_hash_counts_and_deterministic_sampling_do_not_read_outcome_labels(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'episodes.jsonl'
            r=source_row();r['outcomes']={'DO_NOT_USE':'future profit'}
            source.write_text(json.dumps(r)+'\n');digest=sha256(source.read_bytes()).hexdigest()
            db=sqlite3.connect(':memory:')
            try:
                result=build_index(source,db,{FAMILIES[0]:1},1,expected_sha=digest,progress=lambda *a,**k:None)
                self.assertEqual(result['counts']['selected_symbol_days'],1)
                self.assertNotIn('future profit',db.execute('SELECT body FROM events').fetchone()[0])
            finally:db.close()
            db=sqlite3.connect(':memory:')
            try:
                with self.assertRaisesRegex(ValueError,'SHA_MISMATCH'):build_index(source,db,{FAMILIES[0]:1},1,expected_sha='0'*64)
            finally:db.close()

    def test_end_to_end_replays_prices_with_frozen_protocol_and_no_source_changes(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d);report=repo/REPORT;report.parent.mkdir(parents=True)
            p=fixture();p['metadata']['records_ingested']=1
            for c in p['strategy_scorecards'].values():c['sample_count']=0
            p['strategy_scorecards'][FAMILIES[0]]['sample_count']=1
            report.write_text(json.dumps(p));root=report.parent.parent
            corpus=root/'corpus_repaired';corpus.mkdir();source=corpus/'episodes.jsonl'
            source.write_text(json.dumps(source_row())+'\n');digest=sha256(source.read_bytes()).hexdigest()
            normalized=root/'normalized';normalized.mkdir()
            values=(replace(bars()[0],timestamp=T-timedelta(minutes=2)),replace(bars()[0],timestamp=T-timedelta(minutes=1)))+bars()
            file_bars(normalized/f'ABC_{DAY}.jsonl',values)
            original=build_index
            def index(*args,**kwargs):return original(*args,**kwargs,expected_sha=digest)
            with patch('app.strategy_research.episode_price_study.build_index',side_effect=index):
                result=run(repo,repo/'output',progress=lambda *a,**k:None)
            self.assertEqual(len(result['cases']),40);self.assertEqual(result['orders'],0)
            self.assertEqual(sha256(source.read_bytes()).hexdigest(),digest)
            self.assertTrue((repo/'output/frozen-plan.json').is_file())
            self.assertEqual(result['promotion'],'NONE')
            self.assertEqual(len(result['common_resolved_dates'][FAMILIES[0]]),1)
            self.assertEqual(result['evidence_status'],'PRICE_REPLAY_WITH_ENTRIES')
            self.assertGreater(sum(c['counts'].get('entries',0) for c in result['cases']),0)
            self.assertEqual(plan()['version'],VERSION)


if __name__=='__main__':unittest.main()


class StudyCoverageTests(unittest.TestCase):
    def test_positive_closed_results_on_incomplete_days_do_not_enter_headline(self):
        day=dict(fully_resolved=True,input_complete=False,closed_trades=1,wins=1,losses=0,
            closed_net=D(100),positive_net=D(100),negative_net=D(0),unresolved_positions=0,
            counts={'INVALID_OR_MISSING_PRICE_PARTITION':1},unresolved_statuses={})
        r=aggregate([day])
        self.assertEqual(r['net_resolved_days'],0)
        self.assertEqual(r['all_days_closed_net_conditional'],100)
        self.assertEqual(r['excluded_price_error_days'],1)
        self.assertIsNone(r['win_rate_resolved_days'])

    def test_index_canonicalizes_timezones_before_chronological_sort(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'episodes.jsonl'
            a=source_row('a');b=source_row('b')
            b['detected_timestamp']=(T-timedelta(minutes=1)).astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).isoformat()
            b['trigger_timestamp']=b['detected_timestamp']
            source.write_text(json.dumps(a)+'\n'+json.dumps(b)+'\n')
            digest=sha256(source.read_bytes()).hexdigest();db=sqlite3.connect(':memory:')
            try:
                build_index(source,db,{FAMILIES[0]:2},2,expected_sha=digest,progress=lambda *a,**k:None)
                values=list(db.execute('SELECT entry_time FROM events ORDER BY entry_time,id'))
                self.assertEqual(values[0],values[1]);self.assertTrue(values[0][0].endswith('+00:00'))
            finally:db.close()


class CompletedPrefixRegressionTests(unittest.TestCase):
    def test_completion_at_decision_requires_exact_ohlcv_match_and_keeps_future_block(self):
        row=source_row();at=datetime.fromisoformat(row['detected_timestamp'])
        proof=prefix_evidence(row,at)
        self.assertTrue(proof['ok'])
        values=(replace(bars()[0],timestamp=at-timedelta(minutes=1)),)+bars()
        self.assertTrue(normalized_prefix_matches(proof,values))
        self.assertFalse(normalized_prefix_matches(proof,values[1:]))
        changed=(replace(values[0],close=D('10.01')),)+values[1:]
        self.assertFalse(normalized_prefix_matches(proof,changed))
        row['bar_window'][-1]['timestamp']=(at+timedelta(minutes=1)).isoformat()
        self.assertFalse(prefix_ok(row,at))

    def test_failed_prefix_is_not_a_complete_evidence_day(self):
        lane=DayWallet(FAMILIES[0],POLICIES[0],D('.01'),lambda r:None)
        lane.consider(dict(event(),prefix_ok=False),bars(),T)
        day=lane.finish(DAY);r=aggregate([day])
        self.assertFalse(day['signal_evidence_complete'])
        self.assertEqual(r['excluded_signal_evidence_days'],1)
        self.assertEqual(r['headline_complete_days'],0)
        self.assertIsNone(r['win_rate_resolved_days'])
