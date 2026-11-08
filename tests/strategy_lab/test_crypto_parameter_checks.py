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
