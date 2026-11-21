"""Synthetic interrupted recordings; no private workstation data."""
import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.strategy_research.paper_day_review import collect, require_stopped_desktop
from tests.strategy_research.test_audit_stdlib import make_ledger
from tests.strategy_research.test_paper_day_review_stdlib import assign_trial, TRIAL


def fixture(root):
    trial=root/'trial';trial.mkdir();session=root/'session';session.mkdir()
    spec={'trial_id':TRIAL}
    (trial/'experiment.json').write_text(json.dumps(spec))
    (trial/'recording-session.json').write_text(json.dumps({'trial_id':TRIAL,'session':str(session)}))
    baseline='c'*64
    (session/'research-manifest.json').write_text(json.dumps({'paper_experiment':spec,'baseline_sha256':baseline}))
    journal=session/'journal';journal.mkdir()
    row=dict(schema='ATLAS_DAILY_EVIDENCE_V1',sequence=1,kind='PAPER_EVENT',
        previous_sha256='0'*64,baseline_sha256=baseline,payload={'synthetic':True})
    row['sha256']=sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    raw=json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n'
    (journal/'events-00001.jsonl').write_bytes(raw+b'{unverified partial tail')
    anchor=dict(baseline_sha256=baseline,counts={'written':1,'queued':3},bytes_written=len(raw),
        chain_head=row['sha256'],updated_at='2026-01-01T00:00:00+00:00',
        closed=False,writer_alive=True,writer_failed=False,evidence_complete=True)
    checkpoint=journal/'journal-status.json';checkpoint.write_text(json.dumps(anchor))
    ledger=root/'ledger.sqlite3';make_ledger(ledger);assign_trial(ledger)
    return trial,session,ledger,checkpoint


class InterruptedReviewTests(unittest.TestCase):
    def test_prefix_tail_exclusion_and_reconciliation_preserve_all_sources(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
                'app.strategy_research.paper_day_review.require_stopped_desktop') as guard:
            root=Path(tmp);trial,session,ledger,_=fixture(root)
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            report,archive=collect(trial,ledger,root/'export',interrupted=True)
            self.assertEqual(guard.call_count,2)
            self.assertEqual(report['scope'],'PRIVATE_INTERRUPTED_PAPER_SESSION_REVIEW_V1')
            self.assertFalse(report['capture']['closed'])
            self.assertIsNone(report['entry_diagnostics'])
            self.assertEqual(report['verified_prefix']['records'],1)
            self.assertFalse(report['verified_prefix']['full_run_complete'])
            self.assertEqual(report['native_trial']['closed_lifecycles'],1)
            self.assertEqual(report['native_trial']['overall']['net_pnl'],'0.50')
            self.assertFalse((session/'research-summary.json').exists())
            for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)
            with zipfile.ZipFile(archive) as z:
                self.assertFalse(any('events-' in name or 'research-summary' in name for name in z.namelist()))
                self.assertIn('capture/journal-status.json',z.namelist())

    def test_corruption_or_missing_prefix_is_refused_before_output(self):
        for missing in (False,True):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as tmp,patch(
                    'app.strategy_research.paper_day_review.require_stopped_desktop'):
                root=Path(tmp);trial,session,ledger,_=fixture(root)
                path=session/'journal/events-00001.jsonl'
                if missing:path.unlink()
                else:path.write_bytes(path.read_bytes().replace(b'PAPER_EVENT',b'WRONG_EVENT'))
                with self.assertRaisesRegex(ValueError,'CHAIN_MISMATCH|PREFIX_MISMATCH'):
                    collect(trial,ledger,root/'export',interrupted=True)
                self.assertFalse((root/'export').exists())

    def test_changed_checkpoint_and_active_producer_do_not_produce_zip(self):
        for active in (False,True):
            with self.subTest(active=active),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);trial,session,ledger,checkpoint=fixture(root)
                def guard():
                    if active:raise ValueError('ACTIVE_ATLAS')
                    if (root/'export').exists():checkpoint.write_text(checkpoint.read_text()+' ')
                with patch('app.strategy_research.paper_day_review.require_stopped_desktop',side_effect=guard):
                    with self.assertRaisesRegex(ValueError,'ACTIVE_ATLAS|RECORDING_CHANGED'):
                        collect(trial,ledger,root/'export',interrupted=True)
                self.assertFalse(list(root.rglob('*.zip')))

    def test_normal_mode_still_requires_final_and_interrupted_rejects_existing_summary(self):
        with tempfile.TemporaryDirectory() as tmp,patch(
                'app.strategy_research.paper_day_review.require_stopped_desktop'):
            root=Path(tmp);trial,session,ledger,_=fixture(root)
            with self.assertRaises(ValueError):collect(trial,ledger,root/'export')
            (session/'research-summary.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'SUMMARY_PRESENT'):
                collect(trial,ledger,root/'export',interrupted=True)

    def test_windows_process_check_fails_closed_and_does_not_print_command_lines(self):
        from types import SimpleNamespace
        with patch('sys.platform','win32'),patch(
                'app.strategy_research.paper_day_review.subprocess.run',return_value=SimpleNamespace(stdout='1\n')):
            with self.assertRaisesRegex(ValueError,'ACTIVE_ATLAS'):require_stopped_desktop()


if __name__=='__main__':unittest.main()
