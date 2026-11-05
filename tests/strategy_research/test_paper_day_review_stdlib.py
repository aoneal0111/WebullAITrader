import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from app.strategy_research.paper_day_review import trial_outcomes, snapshot, collect, call_reviews
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
            budget=trial/'grok-team-budget.sqlite3'
            with contextlib.closing(sqlite3.connect(budget)) as db,db:
                db.execute('CREATE TABLE calls(engine TEXT,stage TEXT,started TEXT,context TEXT,result TEXT)')
                context={'candidates':[{'engine':'WARRIOR_MOMENTUM_V1','candidate_id':'original-id',
                    'valid_until':'2026-11-05T14:30:10+00:00'}]}
                answer={'selected_ids':['original-id'],'reason':'Synthetic model choice, not an order'}
                db.execute('INSERT INTO calls VALUES(?,?,?,?,?)',('WARRIOR_MOMENTUM_V1','NATIVE_REVIEW',
                    '2026-11-05T14:30:00+00:00',json.dumps(context),json.dumps({'status':'RESPONSE_RECEIVED',
                    'latency_ms':11000,'answer':answer})))
            before_budget=budget.read_bytes()
            before=ledger.read_bytes();r,archive=collect(trial,ledger,root/'output')
            self.assertEqual(r['native_trial']['closed_lifecycles'],1)
            self.assertEqual(r['orders'],0);self.assertEqual(r['network_requests'],0)
            self.assertEqual(ledger.read_bytes(),before)
            self.assertEqual(budget.read_bytes(),before_budget)
            review=r['grok_reviews'][0]
            self.assertEqual(review['selected_ids'],['original-id'])
            self.assertEqual(review['candidates'][0]['valid_until'],'2026-11-05T14:30:10+00:00')
            self.assertTrue(review['model_selection_is_not_execution'])
            with zipfile.ZipFile(archive) as z:
                self.assertIn('paper-day-review.json',z.namelist())
                self.assertIn('native/paper-ledger.sqlite3',z.namelist())
                self.assertFalse(any('.env' in name or 'events-' in name for name in z.namelist()))
            with self.assertRaises(ValueError):collect(trial,ledger,root/'output')

    def test_call_review_preserves_abstention_unfinalized_and_invalid_rows(self):
        rows=[('WARRIOR_MOMENTUM_V1','NATIVE_REVIEW','original-time','{}',json.dumps({
            'status':'RESPONSE_RECEIVED','answer':{'selected_ids':[],'reason':'Synthetic abstention'}})),
            ('WARRIOR_MOMENTUM_V1','DIRECTOR','original-time','{}',None),
            ('WARRIOR_MOMENTUM_V1','DIRECTOR','original-time','invalid','[1]')]
        r=call_reviews(rows)
        self.assertEqual(r[0]['selected_ids'],[])
        self.assertEqual(r[0]['reason'],'Synthetic abstention')
        self.assertEqual(r[1]['status'],'UNFINALIZED')
        self.assertIsNone(r[1]['selected_ids'])
        self.assertEqual(r[2]['context_decode_status'],'INVALID_JSON_OBJECT')
        self.assertEqual(r[2]['result_decode_status'],'INVALID_JSON_OBJECT')

    def test_powershell_lookup_matches_explicit_controller_profile_and_keeps_digest_check(self):
        import io,os,sys,types
        from unittest.mock import patch
        from hashlib import sha256
        from app.asset_modules.paper_day_setup import FLAGS
        script=Path(__file__).resolve().parents[2]/'tools/capture-paper-day-review.ps1'
        lookup=script.read_text().split("$Lookup = @'\n",1)[1].split("\n'@",1)[0]
        def digest(config):return sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
        dotenv=types.ModuleType('dotenv');dotenv.dotenv_values=lambda _:{}
        loader=types.ModuleType('app.configuration.loader');loader.load_configuration=lambda values:types.SimpleNamespace(values=values,execution_database_path='execution.sqlite3')
        observer=types.ModuleType('app.asset_modules.grok_observer_session');observer.configuration_digest=lambda config:digest(config.values)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);trial=root/'trial';trial.mkdir();session=root/'session';session.mkdir()
            before_cwd=os.getcwd()
            try:
                for profile in ('LEGACY_V1','GROK_WARRIOR_V5'):
                    spec={'trial_id':TRIAL,'profile':profile}
                    (trial/'experiment.json').write_text(json.dumps(spec))
                    (trial/'recording-session.json').write_text(json.dumps({'trial_id':TRIAL,'session':str(session)}))
                    expected=dict(FLAGS,ATLAS_EQUITY_PAPER_EXPERIMENT=str(trial/'experiment.json'),
                        ATLAS_LEARNING_OUTCOME_JOURNAL_PATH=str(trial/'learning-outcomes.json'),
                        ATLAS_GROK_EXECUTION_TRIAL='',ATLAS_CRYPTO_OBSERVATION_CAPTURE='',ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED='false')
                    if profile=='GROK_WARRIOR_V5':expected['ATLAS_PAPER_CONTROLLER_ENABLED']='true'
                    manifest={'paper_experiment':spec,'configuration_sha256':digest(expected)}
                    target=session/'research-manifest.json';target.write_text(json.dumps(manifest))
                    with patch.dict(sys.modules,{'dotenv':dotenv,'app.configuration.loader':loader,
                            'app.asset_modules.grok_observer_session':observer}),patch.dict(os.environ,{},clear=True),\
                            patch.object(sys,'argv',['lookup',str(root),str(trial)]),contextlib.redirect_stdout(io.StringIO()) as out:
                        exec(compile(lookup,'lookup','exec'),{})
                        self.assertEqual(out.getvalue().strip(),'paper-execution.sqlite3')
                        manifest['configuration_sha256']='f'*64;target.write_text(json.dumps(manifest))
                        with self.assertRaisesRegex(ValueError,'NATIVE_CONFIGURATION_CHANGED'):
                            exec(compile(lookup,'lookup','exec'),{})
            finally:os.chdir(before_cwd)

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
