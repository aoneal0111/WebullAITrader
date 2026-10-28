"""Standalone checks; no scientific dependencies or trading runtime imports."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from app.strategy_research.equity_corpus_review import registry,review_report,read_json,inventory,run,REPORT


def fixture():
    names=[m['strategy'] for m in registry()['strategy_mapping']]
    cards={n:dict(sample_count=100) for n in names}
    split={s:{n:dict(sample_count=c) for n in names} for s,c in (('TRAIN',60),('VALIDATION',20),('TEST',20))}
    return dict(metadata=dict(research_only=True),strategy_scorecards=cards,
        r_multiple_research={n:{t:dict(sample_count=100,hit_rate=.8,stop_first_rate=.6)
                            for t in ('0.5','1','2')} for n in names},
        temporal=dict(splits=split,policy_stability={n:{s:{'2R':dict(sample_count=c,hit_rate=.8,stop_first_rate=.6)}
            for s,c in (('TRAIN',60),('VALIDATION',20),('TEST',20))} for n in names}))


class CorpusReviewTests(unittest.TestCase):
    def test_pinned_sources_cover_all_23_without_claiming_parity_or_profit(self):
        lock=registry()
        self.assertEqual(len(lock['repositories']),6)
        self.assertEqual(len(lock['strategy_mapping']),23)
        self.assertTrue(all(r['verification']=='SOURCE_INSPECTED_PERFORMANCE_NOT_REPRODUCED' for r in lock['repositories']))
        self.assertTrue(all(not r['exact_parity_verified'] for r in lock['strategy_mapping']))
        self.assertTrue(all(r['source_files'] for r in lock['repositories']))

    def test_aggregate_touch_rates_never_become_net_win_rates(self):
        r=review_report(fixture());s=r['strategies'][0]
        self.assertEqual(r['rankings'],[])
        self.assertIsNone(s['realized_net_win_rate']);self.assertIsNone(s['net_expectancy'])
        self.assertEqual(s['descriptive_cohorts'][0]['target_hit_without_stop_first_upper_bound'],'0.4')
        self.assertEqual(s['split_count_status'],'MATCH')
        self.assertEqual(r['total_strategy_memberships'],2300)
        self.assertIsNone(r['unique_trade_count'])
        self.assertFalse(r['execution_authorized'])

    def test_exact_23_population_required(self):
        for change in ('missing','extra'):
            p=fixture()
            if change=='missing':p['strategy_scorecards'].pop(next(iter(p['strategy_scorecards'])))
            else:p['strategy_scorecards']['INVENTED']={}
            with self.assertRaisesRegex(ValueError,'EXACT_23'):review_report(p)

    def test_test_split_is_not_backfilled_from_execution_or_whole_sample(self):
        p=fixture();p['temporal']['splits'].pop('TEST')
        p['temporal']['execution_analysis']={'TEST':p['strategy_scorecards']}
        row=review_report(p)['strategies'][0]
        self.assertIsNone(row['split_sample_memberships']['TEST'])
        self.assertEqual(row['split_count_status'],'MISSING_SPLIT_COUNTS')

    def test_zero_evidence_and_inconsistent_denominators_stay_visible(self):
        p=fixture();names=list(p['strategy_scorecards'])
        p['strategy_scorecards'][names[0]]['sample_count']=0
        p['temporal']['splits']['TEST'][names[1]]['sample_count']=21
        r=review_report(p)
        self.assertIn(names[0],r['strategies_with_no_observations'])
        self.assertIn(names[1],r['split_count_mismatches'])
        self.assertTrue(all(not s['performance_reproduced'] for s in r['strategies']))

    def test_split_case_aliases_and_missing_cohorts(self):
        p=fixture();p['temporal']['splits']={s.lower():v for s,v in p['temporal']['splits'].items()}
        p.pop('r_multiple_research')
        self.assertEqual(review_report(p)['strategies'][0]['split_count_status'],'MATCH')
        self.assertEqual(review_report(p)['strategies'][0]['descriptive_cohorts'][0]['status'],'MISSING')
        p['temporal']['splits']['TEST']={}
        with self.assertRaisesRegex(ValueError,'AMBIGUOUS'):review_report(p)

    def test_untrusted_json_and_invalid_counts_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'bad.json'
            for raw in ('{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}'):
                path.write_text(raw)
                with self.assertRaises(ValueError):read_json(path,100)
            path.write_text(' '*101)
            with self.assertRaisesRegex(ValueError,'INPUT_SIZE_LIMIT'):read_json(path,100)
        for value in (True,-1,'100'):
            p=fixture();next(iter(p['strategy_scorecards'].values()))['sample_count']=value
            with self.assertRaises(ValueError):review_report(p)

    def test_inventory_is_bounded_and_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for i in range(10):(root/f'{i}.jsonl').write_text('private prices')
            r=inventory(root,max_entries=3)
            self.assertTrue(r['enumeration_capped']);self.assertEqual(r['entries_examined'],3)
            self.assertFalse(r['prices_read']);self.assertEqual(r['databases_opened'],0)

    def test_end_to_end_preserves_source_and_does_not_copy_private_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d);source=repo/REPORT;source.parent.mkdir(parents=True)
            p=fixture();p['metadata']['DO_NOT_EXPORT']='private secret'
            source.write_text(json.dumps(p));before=sha256(source.read_bytes()).hexdigest()
            r=run(repo,repo/'output')
            self.assertEqual(before,sha256(source.read_bytes()).hexdigest())
            self.assertNotIn('private secret',(repo/'output/equity-23-repository-review.json').read_text())
            self.assertEqual(r['source_report_sha256'],before)
            for key in ('orders','network_requests','grok_requests'):self.assertEqual(r[key],0)
            self.assertEqual(r['promotion'],'NONE')
            with self.assertRaises(FileExistsError):run(repo,repo/'output')

    def test_review_does_not_mutate_source_report(self):
        p=fixture();before=deepcopy(p);review_report(p);self.assertEqual(p,before)


if __name__=='__main__':unittest.main()
