import sys,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,UTC,timedelta
from types import SimpleNamespace
from unittest.mock import patch
from app.strategy_lab.bollinger_paper_pilot import prepare


class BollingerPilotTests(unittest.TestCase):
    def test_native_candidate_prefix_cost_geometry_and_durable_atr(self):
        import pandas as pd,numpy as np
        folder=str(Path(__file__).resolve().parents[2]/'app/strategy_lab');sys.path.insert(0,folder)
        try:
            from AtlasBollingerResearch import AtlasBollingerResearch
            s=AtlasBollingerResearch(dict(dry_run=True));x=np.arange(240);c=100+np.sin(x/5);c[225]=90
            f=pd.DataFrame(dict(date=pd.date_range('2026-01-01',periods=240,freq='4h',tz='UTC'),open=c,high=c+.5,low=c-.5,close=c,volume=100))
            def calc(f):return s.populate_entry_trend(s.populate_indicators(f.copy(),{}),{})
            full=calc(f);prefix=calc(f.iloc[:230]);self.assertEqual(list(full.enter_long[:230]),list(prefix.enter_long));self.assertEqual(full.enter_long.iloc[225],1)
            t=SimpleNamespace(open_rate=100,fee_open=.004,fee_close=.004,get_custom_data=lambda *a,**k:1)
            self.assertAlmostEqual(s.custom_roi('BTC/USD',t,None,0,None,'long'),102*.996/(100*1.004)-1)
            at=datetime(2026,1,1,tzinfo=UTC);t.open_date_utc=at
            self.assertIsNone(s.custom_exit('',t,at+timedelta(hours=95),100,0))
            self.assertEqual(s.custom_exit('',t,at+timedelta(hours=96),100,0),'BOLL_96H_TIMEOUT')
        finally:sys.path.remove(folder)

    def test_prepare_and_native_resolve_keep_old_wallet_and_disable_real_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fleet=root/'fleet';(fleet/'e0').mkdir(parents=True);(fleet/'shared').mkdir()
            pairs=['BTC/USD','ETH/USD','SOL/USD']+[f'X{i}/USD' for i in range(24)]+['AXS/USD']
            p=fleet/'e0/config.json';p.write_text(json.dumps(dict(dry_run=True,exchange={'pair_whitelist':pairs})));before=p.read_bytes()
            source=Path(__file__).resolve().parents[2]
            pilot=root/'pilot';prepare(pilot,fleet,source)
            from app.strategy_lab.crypto_fleet_runtime import check,verify
            result=check(pilot/'paper');self.assertEqual(result['status'],'RUNTIME_READY')
            cfg,profile=verify(pilot/'paper');self.assertTrue(cfg['dry_run']);self.assertFalse(cfg['force_entry_enable']);self.assertEqual(cfg['fee'],.004)
            self.assertNotIn('AXS/USD',profile['pairs']);self.assertEqual(p.read_bytes(),before)
            self.assertFalse((pilot/'paper/paper.sqlite3').exists())


if __name__=='__main__':unittest.main()
