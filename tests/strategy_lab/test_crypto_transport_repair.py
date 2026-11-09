from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import sqlite3
from app.strategy_lab.crypto_transport_repair import plan,apply,read,write,digest
from app.strategy_lab.nfi_source_reproduction import IMAGE


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.fleet=self.root/'fleet';self.addon=self.root/'addon';self.source=self.root/'source';self.output=self.root/'repair'
        self.before={}
        for folder,keys in [(self.fleet,('e0','nasos','bb','crsi','wave','nfi')),(self.addon,('crypto','equity'))]:
            folder.mkdir();services={}
            for key in keys:
                work=folder/key;work.mkdir();(work/'strategies').mkdir();(work/'strategies/A.py').write_text('# original source')
                cfg=work/('resolved-config.json' if key=='nfi' else 'config.json')
                write(cfg,dict(dry_run=True,trading_mode='spot',exchange={'name':'kraken'}))
                db=sqlite3.connect(work/('nfi-paper.sqlite3' if key=='nfi' else 'paper.sqlite3'))
                db.execute('CREATE TABLE wallet(cash REAL)');db.execute('INSERT INTO wallet VALUES(8765.43)');db.commit();db.close()
                services[key]=dict(image=IMAGE,container_name='original-'+key,command=['unchanged'],
                    volumes=[dict(type='bind',source='/old-source',target='/check',read_only=True),
                        dict(type='bind',source=str(work),target='/work'),
                        dict(type='bind',source=str(folder/'shared'),target='/shared')])
            write(folder/'compose.json',dict(services=services))
        self.before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_mount_only_update_retains_all_ledgers_configs_and_equity_service(self):
        r=plan(self.fleet,self.addon,self.source,self.output)
        oldequity=read(self.addon/'compose.json')['services']['equity']
        apply(self.output,is_stopped=lambda n:True)
        for path,raw in self.before.items():
            if not path.endswith('compose.json'):self.assertEqual(Path(path).read_bytes(),raw)
        self.assertEqual(read(self.addon/'compose.json')['services']['equity'],oldequity)
        for group in r['groups']:
            updated=read(group['base'])
            for s in group['services']:
                volumes={v['target']:v for v in updated['services'][s['key']]['volumes']}
                self.assertEqual(volumes['/check']['source'],str(self.source))
                self.assertEqual(volumes['/shared']['source'],str(self.fleet/'shared'))
        self.assertEqual(len(list((self.output/'backups').glob('*.sqlite3'))),7)
        for p in (self.output/'backups').glob('*.sqlite3'):
            with sqlite3.connect(p) as db:self.assertEqual(db.execute('SELECT cash FROM wallet').fetchone()[0],8765.43)

    def test_running_worker_refuses_before_any_compose_or_ledger_mutation(self):
        plan(self.fleet,self.addon,self.source,self.output)
        with self.assertRaisesRegex(ValueError,'MUST_BE_STOPPED'):apply(self.output,is_stopped=lambda n:False)
        for path,raw in self.before.items():self.assertEqual(Path(path).read_bytes(),raw)

    def test_changed_config_or_compose_refuses(self):
        plan(self.fleet,self.addon,self.source,self.output)
        cfg=self.fleet/'bb/config.json';cfg.write_text('{}')
        with self.assertRaisesRegex(ValueError,'SEALED'):apply(self.output,is_stopped=lambda n:True)
        self.assertEqual((self.fleet/'compose.json').read_bytes(),self.before[str(self.fleet/'compose.json')])

    def test_partial_replace_failure_rolls_back_both_compose_files(self):
        plan(self.fleet,self.addon,self.source,self.output)
        original=Path.replace
        def replace(path,target):
            if path.parent==self.addon:raise OSError('simulated write failure')
            return original(path,target)
        with patch.object(Path,'replace',replace),self.assertRaises(OSError):apply(self.output,is_stopped=lambda n:True)
        for folder in (self.fleet,self.addon):
            self.assertEqual((folder/'compose.json').read_bytes(),self.before[str(folder/'compose.json')])


if __name__=='__main__':unittest.main()
