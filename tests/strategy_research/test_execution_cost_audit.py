"""Stdlib-only audit checks, runnable without pytest or the GUI runtime."""
import contextlib
from copy import deepcopy
from decimal import Decimal as D
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from app.strategy_research.execution_cost_audit import audit_costs,main


def order():
    return dict(order_id='private-order',paper_campaign_id='private-campaign',
        created_at='2026-10-20T14:00:00.100+00:00',filled_quantity='10',
        request=dict(symbol='SPY',side='BUY',asset_class='STOCK',metadata=dict(
            strategy_owner='QUICK_SCALPER',scalp_execution_bid='99.90',scalp_entry_trigger='100.10',
            scalp_execution_quote_timestamp='2026-10-20T14:00:00+00:00',
            scalp_provider_bid_timestamp='2026-10-20T13:59:59.900+00:00',
            scalp_provider_ask_timestamp='2026-10-20T13:59:59.900+00:00',secret='not-exported')),
        fills=[dict(fill_id='private-fill',order_id='private-order',quantity='10',price='100.20',
            commission='0.50',timestamp='2026-10-20T14:00:00.200+00:00')])


def ledger(path,orders=None):
    with contextlib.closing(sqlite3.connect(path)) as db,db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        db.executemany('INSERT INTO metadata VALUES(?,?)',[('environment','PAPER'),('active_campaign_id','private-campaign')])
        db.execute('CREATE TABLE orders(order_id TEXT PRIMARY KEY,payload TEXT)')
        for n,value in enumerate(orders if orders is not None else [order()]):
            db.execute('INSERT INTO orders VALUES(?,?)',(str(n),json.dumps(value)))


class ExecutionCostAuditTests(unittest.TestCase):
    def run_audit(self,orders=None,**kwargs):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'ledger.sqlite3';ledger(path,orders)
            before=path.read_bytes();result=audit_costs(path,**kwargs)
            self.assertEqual(before,path.read_bytes())
            path.unlink()  # Verifies the connection closed, including on Windows.
            return result

    def test_reference_arithmetic_and_privacy_do_not_authorize(self):
        result=self.run_audit()
        self.assertEqual(result['decision_reference_matches'],1)
        self.assertEqual(result['matched_signed_midpoint_difference_bps'],D(20))
        self.assertEqual(result['matched_commission_bps'],D(5))
        self.assertEqual(result['records'][0]['decision_reference']['half_spread_bps'],D(10))
        self.assertEqual(result['records'][0]['decision_reference']['signed_ask_difference_per_share'],D('.1'))
        self.assertFalse(result['costs_verified']);self.assertFalse(result['execution_authorized'])
        text=json.dumps(result,default=str)
        for private in ('private-order','private-fill','private-campaign','secret','not-exported'):
            self.assertNotIn(private,text)

    def test_partial_fills_reconcile_and_weight_by_reference_notional(self):
        value=order();first=value['fills'][0];first['quantity']='4'
        second=dict(first,fill_id='partial-2',quantity='6',price='100',commission='0.25')
        value['fills'].append(second)
        result=self.run_audit([value])
        self.assertEqual(result['fills'],2);self.assertEqual(result['matched_quantity'],D(10))
        self.assertEqual(result['matched_signed_midpoint_difference_bps'],D(8))
        self.assertEqual(result['total_recorded_commission'],D('.75'))

    def test_missing_quotes_and_sell_quotes_are_explicitly_excluded(self):
        for side,meta,reason in [('SELL',order()['request']['metadata'],'NO_SUPPORTED_DECISION_QUOTE'),
                                 ('BUY',{},'NO_SUPPORTED_DECISION_QUOTE'),
                                 ('BUY',{'strategy_owner':'QUICK_SCALPER'},'MISSING_OR_INVALID_DECISION_QUOTE')]:
            value=order();value['request'].update(side=side,metadata=meta)
            with self.subTest(side=side,meta=meta):
                result=self.run_audit([value]);self.assertEqual(result['exclusions'],{reason:1})
                self.assertIsNone(result['matched_signed_midpoint_difference_bps'])
                self.assertEqual(result['total_recorded_commission'],D('.5'))

    def test_future_stale_crossed_and_delayed_references_never_match(self):
        cases=[('scalp_provider_bid_timestamp','2026-10-20T14:00:01+00:00','INVALID_TIME_ORDER'),
               ('scalp_provider_bid_timestamp','2026-10-20T13:59:50+00:00','STALE_AT_DECISION'),
               ('scalp_execution_bid','101','CROSSED_DECISION_QUOTE')]
        for field,value,reason in cases:
            o=order();o['request']['metadata'][field]=value
            with self.subTest(field=field):self.assertEqual(self.run_audit([o])['exclusions'],{reason:1})
        o=order();o['fills'][0]['timestamp']='2026-10-20T14:00:06+00:00'
        self.assertEqual(self.run_audit([o])['exclusions'],{'DECISION_FILL_DELAY_EXCEEDED':1})

    def test_negative_signed_differences_are_not_clipped(self):
        o=order();o['fills'][0]['price']='99.80'
        self.assertEqual(self.run_audit([o])['matched_signed_midpoint_difference_bps'],D(-20))

    def test_inactive_campaign_does_not_leak(self):
        other=order();other['paper_campaign_id']='other';other['fills'][0]['fill_id']='other'
        self.assertEqual(self.run_audit([order(),other])['fills'],1)

    def test_corrupt_or_duplicate_fills_fail_instead_of_omitting(self):
        for field,value in [('price','NaN'),('commission','-1'),('quantity','0'),
                            ('timestamp','2026-10-20T14:00:00'),('order_id','wrong-order')]:
            o=order();o['fills'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.run_audit([o])
        o=order();o['filled_quantity']='11'
        with self.assertRaises(ValueError):self.run_audit([o])
        o=order();o['fills'].append(deepcopy(o['fills'][0]));o['filled_quantity']='20'
        with self.assertRaises(ValueError):self.run_audit([o])

    def test_missing_database_is_not_created_and_cli_errors_are_sanitized(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private-missing.sqlite3';out=io.StringIO();err=io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err),self.assertRaises(SystemExit):
                main(['--ledger',str(path)])
            self.assertFalse(path.exists());self.assertEqual(out.getvalue(),'')
            self.assertNotIn(str(path),err.getvalue())

    def test_bounds_and_wrong_environment_fail(self):
        for limit in (0,True,60001):
            with self.assertRaises(ValueError):self.run_audit(max_quote_age_ms=limit)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'ledger.sqlite3';ledger(path)
            with contextlib.closing(sqlite3.connect(path)) as db,db:
                db.execute("UPDATE metadata SET value='LIVE' WHERE key='environment'")
            with self.assertRaisesRegex(ValueError,'PAPER_ENVIRONMENT_REQUIRED'):audit_costs(path)

    def test_result_bounds_and_empty_source_are_explicit(self):
        result=self.run_audit([])
        self.assertEqual(result['fills'],0)
        self.assertEqual(result['status'],'INSUFFICIENT_LIVE_EXECUTION_EVIDENCE')
        self.assertIsNone(result['matched_commission_bps'])
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'ledger.sqlite3';ledger(path,[])
            with contextlib.closing(sqlite3.connect(path)) as db,db:
                db.executemany('INSERT INTO orders VALUES(?,?)',((str(n),json.dumps(order())) for n in range(5001)))
            with self.assertRaisesRegex(ValueError,'DATABASE_RESULT_LIMIT'):audit_costs(path)

    def test_cli_needs_no_site_packages(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'ledger.sqlite3';ledger(path)
            result=subprocess.run([sys.executable,'-S','-m','app.strategy_research.execution_cost_audit',
                '--ledger',str(path)],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['decision_reference_matches'],1)


if __name__=='__main__':unittest.main()
