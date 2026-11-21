"""Disconnected native order-path contract with explicitly synthetic signals.

Run in the pinned Freqtrade environment. This tests software plumbing, not NFI
entry quality, fills or profitability. No source strategy/wallet is modified.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

freqtrade = pytest.importorskip('freqtrade')
if freqtrade.__version__ != '2026.9':
    pytest.skip('Pinned Freqtrade 2026.9 required', allow_module_level=True)

import pandas as pd
from freqtrade.enums import TradingMode
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade, init_db
from freqtrade.strategy import IStrategy

from app.strategy_lab.nfi_forward_engine import Evidence
from app.strategy_lab.nfi_forward_paper import TIMEFRAMES
from app.strategy_lab.nfi_runtime_preflight import disconnected


class SyntheticSignal(IStrategy):
    timeframe = '5m'
    startup_candle_count = 710
    stoploss = -.1
    minimal_roi = {'0': .02}

    def populate_indicators(self, dataframe, metadata): return dataframe
    def populate_entry_trend(self, dataframe, metadata): return dataframe
    def populate_exit_trend(self, dataframe, metadata): return dataframe


@pytest.mark.parametrize('case,expected', [
    ('signal', 1), ('no_signal', 0), ('paused', 0),
    ('stale', 0), ('capture_unready', 0),
])
def test_native_signal_to_persisted_order_is_disconnected(tmp_path, case, expected):
    with disconnected():
        init_db('sqlite:///' + str(tmp_path / 'test-only.sqlite3'))
        now = datetime.now(UTC)
        config = dict(dry_run=True, max_open_trades=6, timeframe='5m',
                      stake_currency='USD', trading_mode=TradingMode.SPOT,
                      exchange={'name':'kraken'}, entry_pricing={})
        bot = FreqtradeBot.__new__(FreqtradeBot)
        bot.config, bot.trading_mode = config, TradingMode.SPOT
        bot.strategy = SyntheticSignal(config)
        bot.strategy.order_types = {'entry':'limit', 'exit':'limit', 'stoploss':'market'}
        bot.strategy.order_time_in_force = {'entry':'GTC', 'exit':'GTC'}
        frames = {}
        for tf, seconds in TIMEFRAMES.items():
            end = int(now.timestamp()) // seconds * seconds - seconds
            dates = pd.to_datetime([end-(709-i)*seconds for i in range(710)], unit='s', utc=True)
            frames[tf] = pd.DataFrame(dict(date=dates, open=100., high=101., low=99., close=100., volume=10.))
        frames['5m']['enter_long'] = int(case != 'no_signal')
        frames['5m']['exit_long'] = 0
        frames['5m']['enter_tag'] = 'SYNTHETIC_PLUMBING_TEST'
        bot.dataprovider = SimpleNamespace(
            ohlcv=lambda pair, tf, copy=False: frames[tf],
            get_analyzed_dataframe=lambda pair, tf: (frames[tf], now))
        bot.active_pair_whitelist = ['BTC/USD']
        bot.wallets = Mock()
        bot.wallets.get_trade_stake_amount.return_value = 100.
        bot.wallets.get_available_stake_amount.return_value = 10000.
        bot.wallets.validate_stake_amount.side_effect = lambda **kw: kw['stake_amount']
        exchange = bot.exchange = Mock()
        exchange._api = SimpleNamespace(create_order=Mock(), cancel_order=Mock())
        exchange._api_async = SimpleNamespace(create_order=Mock(), cancel_order=Mock())
        exchange.id = 'kraken'
        exchange.precisionMode = exchange.precision_mode_price = 2
        exchange.get_rate.return_value = 100.
        exchange.get_min_pair_stake_amount.return_value = 1.
        exchange.get_max_pair_stake_amount.return_value = 10000.
        exchange.get_fee.return_value = .001
        exchange.get_pair_base_currency.return_value = 'BTC'
        exchange.get_precision_amount.return_value = 8
        exchange.get_precision_price.return_value = 2
        exchange.get_contract_size.return_value = 1.
        # Test double at the public dry-run exchange boundary: pending order,
        # no invented fill and no real exchange client calls.
        exchange.create_order.return_value = dict(id='synthetic-paper-order',
            symbol='BTC/USD', type='limit', side='buy', status='open',
            price=100., amount=1., filled=0., remaining=1., cost=0.,
            timestamp=int(now.timestamp()*1000))
        bot._notify_enter = Mock()  # No RPC/Telegram dependency.
        evidence = Evidence(tmp_path, 'a'*64)
        try:
            evidence.attach(bot, tmp_path)
            if case != 'capture_unready': evidence.capture(bot, now)
            if case == 'paused': (tmp_path/'PAUSE_NEW_ENTRIES').touch()
            if case == 'stale':
                frames['5m']['date'] -= timedelta(hours=2)
            assert bot.create_trade('BTC/USD') is bool(expected)
            assert exchange.create_order.call_count == expected
            trades = Trade.get_trades().all()
            assert len(trades) == expected
            if expected:
                assert trades[0].enter_tag == 'SYNTHETIC_PLUMBING_TEST'
                assert len(trades[0].orders) == 1
                assert trades[0].orders[0].filled == 0
            with pytest.raises(RuntimeError, match='REAL_EXCHANGE_WRITES_DISABLED'):
                exchange._api.create_order()
        finally:
            evidence.journal.close()
            Trade.session.remove()
