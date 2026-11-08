from datetime import UTC, datetime
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from app.strategy_lab.crypto_fleet_runtime import repair_bb_tag_names
from app.strategy_lab.crypto_parameter_checks import CallbackAudit, isolate_native_parameters


class ParameterChecks(unittest.TestCase):
    def test_tag_migration_preserves_plural_and_is_idempotent(self):
        source = '''def f(trade):
    enter_tag = 'empty'
    if hasattr(trade, 'enter_tag') and trade.buy_tag is not None:
        enter_tag = trade.buy_tag
    buy_tags = buy_tag.split()
    return buy_tags
'''
        fixed = repair_bb_tag_names(source)
        namespace = {}; exec(fixed, namespace)
        self.assertEqual(namespace['f'](SimpleNamespace(enter_tag='bb ewo')), ['bb', 'ewo'])
        self.assertIn('buy_tags', fixed)
        self.assertEqual(repair_bb_tag_names(fixed), fixed)

    def test_native_subclasses_have_independent_parameters(self):
        from freqtrade.strategy import IStrategy, DecimalParameter
        class Base(IStrategy):
            buy_offset = DecimalParameter(.9, .99, default=.942, space='buy')
            def populate_indicators(self, dataframe, metadata): return dataframe
        class Variant(Base):
            buy_params = {'buy_offset': .99}
        baseline, variant = Base({}), Variant({})
        isolate_native_parameters(baseline); isolate_native_parameters(variant)
        self.assertEqual(baseline.buy_offset.value, .942)
        self.assertEqual(variant.buy_offset.value, .99)
        variant.buy_offset.value = .98
        self.assertEqual(baseline.buy_offset.value, .942)
        self.assertEqual(Base.buy_offset.value, .942)

    def test_suppressed_callback_errors_invalidate_result_and_are_bounded(self):
        audit = CallbackAudit()
        record = logging.LogRecord('freqtrade.strategy.strategy_wrapper', logging.ERROR,
                                   '', 0, 'Unexpected error NameError', (), None)
        for _ in range(200): audit.emit(record)
        self.assertEqual(audit.count, 200)
        self.assertEqual(len(audit.examples), 10)
        self.assertFalse(audit.report()['result_valid'])


class BBRealSourceContract(unittest.TestCase):
    def test_real_custom_exit_accepts_v3_trade_tags(self):
        import os, tempfile
        import pandas as pd
        from app.strategy_lab.crypto_fleet import prepare, verify_sources
        from app.strategy_lab.crypto_fleet_runtime import check, resolve
        source = os.environ.get('ATLAS_FLEET_TEST_SOURCES')
        if not source: self.skipTest('Verified pinned source cache required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'fleet'; prepare(root, source)
            check(root/'bb')
            _, _, strategy, _ = resolve(root/'bb')
            strategy.dp = SimpleNamespace(get_analyzed_dataframe=lambda *a: (pd.DataFrame({'rsi':[45.,45.,45.]}), None))
            trade = SimpleNamespace(enter_tag='bb', open_rate=100., max_rate=110., min_rate=99.)
            result = strategy.custom_exit('BTC/USD', trade, datetime.now(UTC), 100.5, .005)
            self.assertIn('sell_profit_t_0_1', result)
            self.assertIn('bb', result)
            verify_sources(source)

    def test_e0_looser_discount_really_changes_native_entry_signal(self):
        import os, tempfile
        from copy import deepcopy
        import pandas as pd
        from app.strategy_lab.crypto_fleet import prepare
        from app.strategy_lab.crypto_fleet_runtime import check, resolve
        source=os.environ.get('ATLAS_FLEET_TEST_SOURCES')
        if not source:self.skipTest('Verified source cache required')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'fleet';prepare(root,source);check(root/'e0')
            _,_,original,_=resolve(root/'e0')
            isolate_native_parameters(original)
            changed=deepcopy(original)
            changed.buy_ema_low.value=.98
            changed.buy_sma15_32.value=.98
            frame=pd.DataFrame(dict(close=[97.5,97.5],ema_8=[100.,100.],ema_16=[100.,100.],
                sma_15=[100.,100.],rsi_fast=[20.,20.],rsi=[25.,25.],rsi_slow=[40.,39.],
                cti=[-.9,-.9],EWO=[0.,0.]))
            before=original.populate_entry_trend(frame.copy(),{})
            after=changed.populate_entry_trend(frame.copy(),{})
            self.assertEqual(before.get('enter_long',pd.Series([0,0])).fillna(0).sum(),0)
            self.assertEqual(after['enter_long'].iloc[-1],1)
            self.assertEqual(original.buy_ema_low.value,.942)


class NativeScoreContract(unittest.TestCase):
    def test_forced_inventory_is_not_a_closed_win_and_terminal_losses_remain(self):
        from app.strategy_lab.crypto_parameter_lab import score
        stats=dict(trades=[dict(is_open=False,exit_reason='roi',profit_abs=10),
                           dict(is_open=False,exit_reason='force_exit',profit_abs=-50)],
                   profit_total_abs=-40,max_drawdown_account=.01)
        result=score(stats)
        self.assertEqual(result['closed_trades'],1)
        self.assertEqual(result['closed_win_rate'],1)
        self.assertEqual(result['terminal_net_usdt'],-40)
        self.assertEqual(result['force_exit_count'],1)

    def test_grid_retains_each_original_and_has_unique_parameter_variants(self):
        from app.strategy_lab.crypto_parameter_lab import grid
        rows=grid()
        self.assertEqual(len(rows),30)
        self.assertEqual(len({r['name'] for r in rows}),30)
        self.assertEqual(sum(r.get('control')=='original' for r in rows),3)
        self.assertEqual(sum(r['overlay'] for r in rows),24)
