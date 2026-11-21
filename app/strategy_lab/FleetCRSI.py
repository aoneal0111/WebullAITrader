"""Explicit long-only native PAPER adaptation of pinned CRSI baseline."""
from datetime import timedelta
from freqtrade.strategy import IStrategy
from FleetCRSIReference import calculate_indicators


class FleetCRSI(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '4h'
    startup_candle_count = 640
    can_short = False
    stoploss = -.99
    minimal_roi = {'0': 100.0}
    process_only_new_candles = True

    def populate_indicators(self, dataframe, metadata):
        return calculate_indicators(dataframe)

    def populate_entry_trend(self, dataframe, metadata):
        dataframe['enter_long'] = ((dataframe['crsi'] < 30)
            & (dataframe['close'] > dataframe['sma_200'])
            & (dataframe['volume'] > 0)).astype(int)
        dataframe['enter_tag'] = 'CRSI_BASELINE_SPOT_ADAPTATION'
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = (dataframe['crsi'] > 50).astype(int)
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(hours=60):
            return 'CRSI_15_BAR_TIME_EXIT'
