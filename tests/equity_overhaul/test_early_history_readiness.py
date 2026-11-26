import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime,timedelta
import zipfile

MODULE=Path(__file__).resolve().parents[2]/'app/equity_overhaul/early_history_readiness.py'
spec=importlib.util.spec_from_file_location('readiness_under_test',MODULE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def rows():
    start=datetime.fromisoformat('2026-06-01T13:30:00+00:00')
    return [dict(symbol='TEST',timestamp=(start+timedelta(minutes=i)).isoformat(),
        open='5',high='5.6',low='4.9',close='5.1',volume='100',previous_close='5',
        feed='IEX',session='REGULAR') for i in range(390)]


class ReadinessTests(unittest.TestCase):
    def audit(self,data):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bars.zip'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('TEST_2026-06-01.jsonl','\n'.join(json.dumps(r) for r in data))
            original=path.read_bytes();report=m.audit_archive(path)
            self.assertEqual(original,path.read_bytes())
            self.assertFalse(report['execution_authorized'])
            return report

    def test_complete_grid_ready_but_not_strategy_approval(self):
        r=self.audit(rows());self.assertEqual(r['ready_watch_days'],1)
        self.assertEqual(r['promotion'],'NONE')

    def test_single_missing_minute_is_not_filled(self):
        data=rows();del data[1];r=self.audit(data)
        self.assertEqual(r['data_ready_symbol_days'],0)
        self.assertEqual(r['cells'][0]['missing_minutes'],1)

    def test_duplicate_does_not_hide_a_gap(self):
        data=rows();data[1]=data[0].copy();c=self.audit(data)['cells'][0]
        self.assertEqual((c['duplicate_minutes'],c['missing_minutes']),(1,1))

    def test_prior_close_changes_refuse_identity(self):
        data=rows();data[-1]['previous_close']='4'
        self.assertFalse(self.audit(data)['cells'][0]['previous_close_consistent'])

    def test_naive_future_grid_and_nonfinite_rows_refused(self):
        for key,value in [('timestamp','2026-06-01T13:30:00'),('high','NaN'),('low','7')]:
            with self.subTest(key=key,value=value):
                data=rows();data[0][key]=value;r=self.audit(data)
                self.assertEqual(r['cells'][0]['invalid_rows'],1)
                self.assertEqual(r['data_ready_symbol_days'],0)

    def test_unsorted_full_grid_refused(self):
        data=rows();data[1],data[2]=data[2],data[1]
        self.assertEqual(self.audit(data)['data_ready_symbol_days'],0)

    def test_half_day_is_conservatively_excluded(self):
        c=self.audit(rows()[:210])['cells'][0]
        self.assertFalse(c['data_ready_for_full_day_comparison'])
        self.assertEqual(c['missing_minutes'],180)

    def test_missing_prior_close_preserves_bar_coverage(self):
        data=rows()
        for row in data:row['previous_close']=None
        c=self.audit(data)['cells'][0]
        self.assertEqual(c['unique_regular_minutes'],390)
        self.assertTrue(c['full_390_minute_grid'])
        self.assertFalse(c['data_ready_for_full_day_comparison'])
        self.assertIsNone(c['observed_ten_percent_watch'])

    def test_extended_hours_are_excluded_not_called_corrupt(self):
        data=rows();extra=data[0].copy()
        extra.update(timestamp='2026-06-01T20:05:00+00:00',session='AFTER_HOURS')
        data.append(extra);c=self.audit(data)['cells'][0]
        self.assertTrue(c['data_ready_for_full_day_comparison'])
        self.assertEqual(c['excluded_extended_hours_rows'],1)
        self.assertEqual(c['invalid_rows'],0)

    def test_row_budget_refused(self):
        data=rows()[:1];data[0]['extra']='x'*m.MAX_ROW_BYTES
        with self.assertRaisesRegex(ValueError,'ROW_SIZE_LIMIT'):self.audit(data)


if __name__=='__main__':unittest.main()
