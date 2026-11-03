import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from app.strategy_research.paper_day_review import trial_outcomes, snapshot, collect
from tests.strategy_research.test_audit_stdlib import make_ledger

TRIAL='a'*32


def assign_trial(path,trial=TRIAL):
    with contextlib.closing(sqlite3.connect(path)) as db,db:
        for ident,raw in db.execute('SELECT order_id,payload FROM orders').fetchall():
            row=json.loads(raw)
            if row['request']['side']=='BUY':row['request']['metadata']['strategy_trial_id']=trial
            db.execute('UPDATE orders SET payload=? WHERE order_id=?',(json.dumps(row),ident))


class PaperDayReviewTests(unittest.TestCase):
    def test_partial_fills_and_old_campaign_history_are_not_mixed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ledger.sqlite3';make_ledger(path);assign_trial(path)
            with contextlib.closing(sqlite3.connect(path)) as db,db:
                originals=db.execute('SELECT payload FROM orders').fetchall()
                for raw, in originals:
                    row=json.loads(raw);row['order_id']+='old';row['request']['strategy_lifecycle_id']+='old'
                    row['request']['metadata']['strategy_trial_id']='b'*32
                    for fill in row['fills']:fill['fill_id']+='old'
                    db.execute('INSERT INTO orders(payload) VALUES(?)',(json.dumps(row),))
            before=path.read_bytes();r=trial_outcomes(path,TRIAL)
            self.assertEqual(r['closed_lifecycles'],1)
            self.assertEqual(r['overall']['net_pnl'],'0.50')
            self.assertEqual(r['overall']['wins'],1)
            self.assertEqual(path.read_bytes(),before)

    def test_mixed_trial_buy_inventory_is_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ledger.sqlite3';make_ledger(path);assign_trial(path)
            with contextlib.closing(sqlite3.connect(path)) as db,db:
                row=json.loads(db.execute('SELECT payload FROM orders WHERE order_id=1').fetchone()[0])
                row['order_id']='otherbuy';row['request']['metadata']['strategy_trial_id']='b'*32
                row['fills'][0]['fill_id']='otherbuy'
                db.execute('INSERT INTO orders(payload) VALUES(?)',(json.dumps(row),))
            r=trial_outcomes(path,TRIAL)
            self.assertEqual(len(r['mixed_trial_lifecycles']),1)
            self.assertEqual(r['closed_lifecycles'],0)
            self.assertIsNone(r['overall']['win_rate'])

    def test_unbalanced_inventory_is_not_reported_as_zero_pnl(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ledger.sqlite3';make_ledger(path);assign_trial(path)
            with contextlib.closing(sqlite3.connect(path)) as db,db:db.execute('DELETE FROM orders WHERE order_id=3')
            r=trial_outcomes(path,TRIAL)
            self.assertEqual(r['closed_lifecycles'],0)
            self.assertEqual(len(r['unreconciled_trial_lifecycles']),1)
            self.assertIsNone(r['overall']['win_rate'])

    def test_nonactive_trial_campaign_cannot_be_reported_as_empty_performance(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ledger.sqlite3';make_ledger(path);assign_trial(path)
            with contextlib.closing(sqlite3.connect(path)) as db,db:
                for ident,raw in db.execute('SELECT order_id,payload FROM orders').fetchall():
                    row=json.loads(raw);row['paper_campaign_id']='previous'
                    db.execute('UPDATE orders SET payload=? WHERE order_id=?',(json.dumps(row),ident))
            with self.assertRaisesRegex(ValueError,'NONACTIVE_CAMPAIGN'):trial_outcomes(path,TRIAL)

    def test_sqlite_snapshot_uses_new_destination_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.sqlite3';make_ledger(path);before=path.read_bytes()
            target=Path(temp)/'copy.sqlite3';r=snapshot(path,target)
            self.assertEqual(path.read_bytes(),before);self.assertGreater(r['bytes'],0)
            self.assertEqual(trial_outcomes(target,TRIAL)['closed_lifecycles'],0)
            with self.assertRaises(ValueError):snapshot(path,target)

    def test_closed_package_contains_trial_results_and_not_raw_history_or_env(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);trial=root/'trial';trial.mkdir();session=root/'daily';session.mkdir()
            spec={'trial_id':TRIAL};(trial/'experiment.json').write_text(json.dumps(spec))
            (trial/'recording-session.json').write_text(json.dumps({'trial_id':TRIAL,'session':str(session)}))
            (session/'research-manifest.json').write_text(json.dumps({'paper_experiment':spec}))
            (session/'research-summary.json').write_text(json.dumps({'reason':'ATLAS_CLOSED',
                'journal':{'closed':True,'writer_stopped':True,'evidence_complete':True}}))
            (session/'events-should-not-copy.jsonl').write_text('private huge source')
            (trial/'.env').write_text('NOT_FOR_EXPORT=secret-fixture')
            ledger=root/'native.sqlite3';make_ledger(ledger);assign_trial(ledger)
            before=ledger.read_bytes();r,archive=collect(trial,ledger,root/'output')
            self.assertEqual(r['native_trial']['closed_lifecycles'],1)
            self.assertEqual(r['orders'],0);self.assertEqual(r['network_requests'],0)
            self.assertEqual(ledger.read_bytes(),before)
            with zipfile.ZipFile(archive) as z:
                self.assertIn('paper-day-review.json',z.namelist())
                self.assertIn('native/paper-ledger.sqlite3',z.namelist())
                self.assertFalse(any('.env' in name or 'events-' in name for name in z.namelist()))
            with self.assertRaises(ValueError):collect(trial,ledger,root/'output')

    def test_missing_or_nonfinalized_recording_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);trial=root/'trial';trial.mkdir();session=root/'daily';session.mkdir()
            spec={'trial_id':TRIAL};(trial/'experiment.json').write_text(json.dumps(spec))
            (trial/'recording-session.json').write_text(json.dumps({'trial_id':TRIAL,'session':str(session)}))
            (session/'research-manifest.json').write_text(json.dumps({'paper_experiment':spec}))
            (session/'research-summary.json').write_text(json.dumps({'journal':{'closed':False}}))
            with self.assertRaisesRegex(ValueError,'FINALIZED'):collect(trial,root/'missing',root/'output')
            self.assertFalse((root/'output').exists())


if __name__=='__main__':unittest.main()
