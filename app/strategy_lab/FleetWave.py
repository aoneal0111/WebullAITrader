"""SOL/USD long WaveRider experiment; frozen source plan, first-target exit.

Uses public source trend/volume/ATR rules on completed 4h prefixes. This is a
spot adaptation, not reproduction of the private WFO or its trade win rate.
"""
import math
import numpy as np
from freqtrade.strategy import IStrategy, stoploss_from_absolute
from FleetWaveReference import StrategyEngine
from FleetWaveConfig import REGIME_GRID


def decode_plan(tag):
    try:
        name, stop, target = tag.split(':')
        stop, target = float.fromhex(stop), float.fromhex(target)
        if name != 'WAVE' or not all(math.isfinite(v) for v in (stop, target)) or not 0 < stop < target:
            return None
        return stop, target
    except (AttributeError, ValueError):
        return None


class FleetWave(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '4h'
    startup_candle_count = 640
    can_short = False
    stoploss = -.99
    minimal_roi = {'0': 100.0}
    use_custom_stoploss = True
    process_only_new_candles = True

    def informative_pairs(self):
        return [('BTC/USD', '4h')]

    def populate_indicators(self, dataframe, metadata):
        engine = StrategyEngine()
        frame = engine._compute_indicators(dataframe)
        frame['fleet_signal'] = 0
        frame['fleet_tag'] = ''
        btc = self.dp.get_pair_dataframe(pair='BTC/USD', timeframe='4h').copy()
        btc['ema50'] = btc['close'].ewm(span=50, adjust=False).mean()
        btc['ema200'] = btc['close'].ewm(span=200, adjust=False).mean()
        cell = REGIME_GRID['SOL/USDT_long_trending_up']
        if metadata['pair'] != 'SOL/USD':
            return frame
        for i in range(self.startup_candle_count, len(frame)):
            prefix = frame.iloc[:i+1]
            market = btc[btc['date'] <= frame.iloc[i]['date']]
            if len(market) < 200:
                continue
            b = market.iloc[-1]
            btc_trend = ('up' if b['close'] > b['ema50'] > b['ema200'] else
                         'down' if b['close'] < b['ema50'] < b['ema200'] else 'neutral')
            if engine.regime_detector.detect(prefix).regime.value != 'trending_up':
                continue
            signal = engine._check_waverider_with_params(prefix, 'SOL/USDT', 'long', cell, btc_trend)
            if signal is not None:
                # Persist the source's absolute geometry with the native trade;
                # later candles/restarts cannot replace it with a different plan.
                frame.loc[frame.index[i], 'fleet_signal'] = 1
                frame.loc[frame.index[i], 'fleet_tag'] = (
                    'WAVE:' + float(signal.sl_price).hex() + ':' + float(signal.tp1_price).hex())
        return frame

    def populate_entry_trend(self, dataframe, metadata):
        dataframe['enter_long'] = dataframe['fleet_signal']
        dataframe['enter_tag'] = dataframe['fleet_tag']
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = 0
        return dataframe

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, entry_tag, **kwargs):
        plan = decode_plan(entry_tag)
        return bool(plan and plan[0] < rate < plan[1])

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        plan = decode_plan(trade.enter_tag)
        return (stoploss_from_absolute(plan[0], current_rate, is_short=False, leverage=1.)
                if plan else None)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        plan = decode_plan(trade.enter_tag)
        if plan and current_rate <= plan[0]: return 'WAVE_SOURCE_STOP'
        if plan and current_rate >= plan[1]: return 'WAVE_FIRST_TARGET'
