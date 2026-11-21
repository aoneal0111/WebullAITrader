from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.strategy_research.equity_corpus_reconcile import scan,run
from app.strategy_research.equity_corpus_review import REPORT
from tests.strategy_research.test_equity_corpus_review import fixture


def row(identity='A',members=None,label='TARGET_FIRST'):
    return dict(episode_id=identity,strategy_memberships=['FIRST_PULLBACK'] if members is None else members,
        symbol='ABC',trading_date='2026-07-01',outcomes={'r_targets':{
            t:dict(first_plan_event=label) for t in ('0.5','1','2')}})


class ReconcileTests(unittest.TestCase):
    def check(self,rows,expected=None,episodes=None,**kwargs):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'episodes.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            before=path.read_bytes()
            result=scan(path,expected or {'FIRST_PULLBACK':len(rows)},len(rows) if episodes is None else episodes,
                root/'normalized',progress=lambda *a,**k:None,**kwargs)
            self.assertEqual(before,path.read_bytes());return result

    def test_counts_and_overlapping_memberships_reconcile_without_calling_them_trades(self):
        r=self.check([row(members=['FIRST_PULLBACK','MICRO_PULLBACK'])],
            expected={'FIRST_PULLBACK':1,'MICRO_PULLBACK':1})
        self.assertTrue(r['counts_reconcile_with_report'])
        self.assertEqual(r['unique_episode_ids'],1);self.assertEqual(r['strategy_memberships'],2)
        self.assertEqual(r['identity_status'],'COUNT_RECONCILED_NOT_CRYPTOGRAPHIC_PROVENANCE')
        self.assertIsNone(r['ordered_label_inventory'][0]['cohorts'][0]['net_trade_win_rate'])

    def test_duplicate_ids_refuse_match_even_if_membership_counts_match(self):
        r=self.check([row(),row()],episodes=1)
        self.assertFalse(r['counts_reconcile_with_report']);self.assertEqual(r['counts']['duplicate_episode_ids'],1)

    def test_ambiguous_and_unknown_labels_are_not_wins_or_resolved_denominator(self):
        rows=[row('A'),row('B',label='STOP_FIRST'),row('C',label='INTRABAR_ORDER_UNKNOWN'),row('D',label=None)]
        r=self.check(rows);c=r['ordered_label_inventory'][0]['cohorts'][0]
        self.assertEqual(c['target_first_share_of_all'],.25)
        self.assertEqual(c['target_first_share_of_resolved'],.5)
        self.assertEqual(c['counts']['INTRABAR_ORDER_UNKNOWN'],1)

    def test_row_cap_never_receives_full_digest_or_matching_status(self):
        r=self.check([row('A'),row('B')],max_rows=1,episodes=1,expected={'FIRST_PULLBACK':1})
        self.assertFalse(r['complete_scan']);self.assertIsNone(r['corpus_sha256'])
        self.assertFalse(r['counts_reconcile_with_report'])

    def test_invalid_or_duplicate_membership_is_not_silently_repaired(self):
        for members in ([],['FIRST_PULLBACK','FIRST_PULLBACK'],['INVENTED'],[False]):
            r=self.check([row(members=members)])
            self.assertEqual(r['counts']['invalid_identity_or_memberships'],1)
            self.assertFalse(r['counts_reconcile_with_report'])

    def test_partition_traversal_is_not_used(self):
        r=row();r['symbol']='../../private'
        out=self.check([r]);self.assertEqual(out['counts']['invalid_partition_identity'],1)
        self.assertEqual(out['normalized_partitions_observed'],0)

    def test_duplicate_tracking_survives_sqlite_commit_boundary(self):
        rows=[row(str(i)) for i in range(5000)]+[row('0')]
        r=self.check(rows,episodes=5000)
        self.assertEqual(r['unique_episode_ids'],5000)
        self.assertEqual(r['counts']['duplicate_episode_ids'],1)
        self.assertFalse(r['counts_reconcile_with_report'])

    def test_partial_row_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'episodes.jsonl';path.write_text(json.dumps(row()))
            with self.assertRaisesRegex(ValueError,'INCOMPLETE'):scan(path,{'FIRST_PULLBACK':1},1,Path(d))

    def test_whole_pipeline_flags_stale_manifest_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=fixture();p['metadata']['records_ingested']=1
            for c in p['strategy_scorecards'].values():c['sample_count']=0
            p['strategy_scorecards']['FIRST_PULLBACK']['sample_count']=1
            report=root/REPORT;report.parent.mkdir(parents=True);report.write_text(json.dumps(p))
            corpus=report.parent.parent/'corpus_repaired';corpus.mkdir()
            (corpus/'episodes.jsonl').write_text(json.dumps(row())+'\n')
            manifest=corpus/'manifest.json';manifest.write_text(json.dumps({'summary':{'accepted_unique':999,'strategy_memberships':999}}))
            before=manifest.read_bytes();r=run(root,root/'out',progress=lambda *a,**k:None)
            self.assertEqual(before,manifest.read_bytes())
            self.assertEqual(r['matched_corpora'],['corpus_repaired'])
            self.assertFalse(r['corpora'][1]['manifest_counts_match_file'])
            self.assertEqual(r['orders'],0);self.assertEqual(r['promotion'],'NONE')


if __name__=='__main__':unittest.main()
