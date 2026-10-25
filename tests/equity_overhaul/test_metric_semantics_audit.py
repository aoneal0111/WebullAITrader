import importlib.util
from itertools import product
from pathlib import Path
import unittest
from decimal import Decimal as D
from copy import deepcopy

path=Path(__file__).resolve().parents[2]/'app/equity_overhaul/metric_semantics_audit.py'
spec=importlib.util.spec_from_file_location('semantics_under_test',path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture():
    row=dict(sample_count=10,hit_rate='0.8',stop_first_rate='0.6')
    return dict(metadata=dict(research_only=True),strategy_scorecards={'S':{}},
        r_multiple_research={'S':{t:dict(row) for t in ('0.5','1','2')}},
        temporal=dict(policy_stability={'S':{split:{'2R':dict(row)} for split in ('TRAIN','VALIDATION','TEST')}}))


class MetricSemanticsTests(unittest.TestCase):
    def test_bounds_against_all_four_event_joint_distributions(self):
        # Independent integer contingency tables, not a duplicate implementation.
        for both,hit_only,stop_only,neither in product(range(4),repeat=4):
            total=both+hit_only+stop_only+neither
            if not total:continue
            tolerance=D('1e-25')  # Decimal rounding of nonterminating ratios.
            h=D(both+hit_only)/total;s=D(both+stop_only)/total
            result=m.marginal_bounds(h,s)
            truth=D(hit_only)/total
            self.assertLessEqual(D(result['target_hit_without_stop_first_lower_bound']),truth+tolerance)
            self.assertGreaterEqual(D(result['target_hit_without_stop_first_upper_bound']),truth-tolerance)
            self.assertLessEqual(D(result['forced_overlap_lower_bound']),D(both)/total+tolerance)

    def test_overlapping_high_hit_rate_is_never_called_a_win_rate(self):
        r=m.audit_report(fixture());c=r['results'][0]['cohorts'][0]
        self.assertEqual(c['target_hit_without_stop_first_upper_bound'],'0.4')
        self.assertEqual(c['forced_overlap_lower_bound'],'0.4')
        self.assertIsNone(c['realized_win_rate'])
        self.assertFalse(c['metrics_are_trade_win_rates'])
        self.assertEqual(r['rankings'],[])
        self.assertFalse(r['execution_authorized'])

    def test_same_marginals_can_hide_different_stop_aware_results(self):
        # h=.6, s=.5 permits both .1 and .5 hit-only rates.
        r=m.marginal_bounds('.6','.5')
        self.assertEqual(D(r['target_hit_without_stop_first_lower_bound']),D('.1'))
        self.assertEqual(D(r['target_hit_without_stop_first_upper_bound']),D('.5'))

    def test_invalid_probabilities_refused(self):
        for value in (True,'NaN','Infinity','-0.01','1.01',None):
            with self.subTest(value=value),self.assertRaises(ValueError):m.marginal_bounds(value,'.3')

    def test_empty_and_missing_rates_stay_unavailable(self):
        for row in (dict(sample_count=0),dict(sample_count=10,hit_rate=None,stop_first_rate=.5)):
            result=m.cohort('TEST',row)
            self.assertEqual(result['status'],'INSUFFICIENT_AGGREGATE_EVIDENCE')
            self.assertIsNone(result['realized_win_rate'])

    def test_input_immutable_all_split_labels_retained(self):
        source=fixture();before=deepcopy(source);r=m.audit_report(source)
        self.assertEqual(before,source)
        self.assertEqual(len(r['results'][0]['cohorts']),6)

    def test_population_mismatch_refused(self):
        r=fixture();r['temporal']['policy_stability']['OTHER']={}
        with self.assertRaisesRegex(ValueError,'STRATEGY_MEMBERSHIP_MISMATCH'):m.audit_report(r)


if __name__=='__main__':unittest.main()
