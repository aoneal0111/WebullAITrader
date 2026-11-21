import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from app.strategy_research.paper_call_summary import summarize
from app.strategy_research.paper_day_review import collect
from tests.strategy_research.test_audit_stdlib import make_ledger
from tests.strategy_research.test_paper_day_review_stdlib import assign_trial,TRIAL


class PersistentCallReviewTests(unittest.TestCase):
    def make_budget(self,path,count=1346):
        with contextlib.closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE calls(engine TEXT,stage TEXT,started TEXT,context TEXT,result TEXT)')
            for index in range(count):
                result=dict(status='RESPONSE_RECEIVED',answer=dict(action='PASS',reason='Fixture'),
                    usage_scope='client-one' if index<1000 else 'client-two',
                    usage_cumulative_not_per_request=dict(input_tokens=(index+1)*10,output_tokens=index+1))
                db.execute('INSERT INTO calls VALUES(?,?,?,?,?)',('WARRIOR_MOMENTUM_V1','DIRECTOR',
                    str(index),json.dumps({'market':{'private_fixture':'not copied'}}),json.dumps(result)))

    def test_more_than_sixty_calls_are_counted_without_cumulative_usage_double_count(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'budget.sqlite3';self.make_budget(path)
            original=path.read_bytes();r=summarize(path)
            self.assertEqual(r['total_calls'],1346)
            self.assertEqual(r['roles']['WARRIOR_MOMENTUM_V1|DIRECTOR'],1346)
            self.assertEqual(r['recorded_tokens']['input_tokens'],10000+13460)
            self.assertEqual(len(r['recent_reviews']),500)
            self.assertNotIn('private_fixture',json.dumps(r))
            self.assertEqual(path.read_bytes(),original)

    def test_compact_closed_review_preserves_native_results_and_omits_raw_model_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);trial=root/'paper';trial.mkdir();session=root/'daily';session.mkdir()
            spec=dict(trial_id=TRIAL,scope='NATIVE_PAPER_OPERATION_V1')
            (trial/'experiment.json').write_text(json.dumps(spec))
            (trial/'recording-session.json').write_text(json.dumps(dict(trial_id=TRIAL,session=str(session))))
            (session/'research-manifest.json').write_text(json.dumps(dict(paper_experiment=spec)))
            (session/'research-summary.json').write_text(json.dumps(dict(reason='ATLAS_CLOSED',
                journal=dict(closed=True,writer_stopped=True,evidence_complete=True))))
            ledger=root/'native.sqlite3';make_ledger(ledger);assign_trial(ledger)
            budget=trial/'grok-team-budget.sqlite3';self.make_budget(budget)
            original=ledger.read_bytes();crypto=root/'empty-crypto';crypto.mkdir()
            r,archive=collect(trial,ledger,root/'output',crypto_base=crypto)
            self.assertEqual(r['native_trial']['closed_lifecycles'],1)
            self.assertEqual(r['native_trial']['overall']['net_pnl'],'0.50')
            self.assertEqual(r['grok_call_summary']['total_calls'],1346)
            self.assertEqual(ledger.read_bytes(),original)
            with zipfile.ZipFile(archive) as z:
                self.assertNotIn('grok/grok-team-budget.sqlite3',z.namelist())
                self.assertIn('native/paper-ledger.sqlite3',z.namelist())
                crypto=json.loads(z.read('crypto-paper-status.json'))
                self.assertEqual(crypto['engines'],[])
                self.assertEqual(crypto['orders_sent'],0)
            self.assertLess(archive.stat().st_size,1024*1024)

    def test_pending_and_invalid_results_are_not_successful_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'budget.sqlite3';self.make_budget(path,0)
            with contextlib.closing(sqlite3.connect(path)) as db,db:
                db.executemany('INSERT INTO calls VALUES(?,?,?,?,?)',[
                    ('WARRIOR_MOMENTUM_V1','DIRECTOR','one','{}',None),
                    ('WARRIOR_MOMENTUM_V1','DIRECTOR','two','{}','invalid')])
            r=summarize(path)
            self.assertEqual(r['unfinalized_calls'],1);self.assertEqual(r['invalid_results'],1)
            self.assertNotIn('PASS',r['actions'])
