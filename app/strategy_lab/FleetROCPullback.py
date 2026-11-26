"""Independent hourly research adaptation, native Freqtrade spot PAPER only."""
from datetime import timedelta
import math
from freqtrade.strategy import IStrategy, stoploss_from_absolute
from app.strategy_lab.broad_strategy_lab import indicators, rules, Case


def entry_atr(tag):
    try:
        if not tag.startswith('ROC3_08:'):return None
        value=float.fromhex(tag.split(':',1)[1])
        return value if math.isfinite(value) and value>0 else None
    except (AttributeError,ValueError):return None


class FleetROCPullback(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h'
    startup_candle_count=200
    can_short=False
    stoploss=-.99
    minimal_roi={'0':100.}
    use_custom_stoploss=True
    use_exit_signal=True
    process_only_new_candles=True
    position_adjustment_enable=False
    order_types={'entry':'limit','exit':'limit','stoploss':'market','stoploss_on_exchange':False}

    def populate_indicators(self,dataframe,metadata):
        return indicators(dataframe)

    def populate_entry_trend(self,dataframe,metadata):
        frame=dataframe.copy();frame['eligible']=frame.index>=self.startup_candle_count
        frame['enter_long']=rules(frame,Case('ROC3_PULLBACK',.08))['buy'].astype(int)
        frame['enter_tag']=frame['atr'].map(lambda v:'ROC3_08:'+float(v).hex())
        return frame

    def populate_exit_trend(self,dataframe,metadata):
        dataframe['exit_long']=rules(dataframe,Case('ROC3_PULLBACK',.08))['sell'].astype(int)
        return dataframe

    def confirm_trade_entry(self,pair,order_type,amount,rate,time_in_force,entry_tag,**kwargs):
        return self.config.get('dry_run') is True and entry_atr(entry_tag) is not None and rate>0

    def custom_stoploss(self,pair,trade,current_time,current_rate,current_profit,**kwargs):
        atr=entry_atr(trade.enter_tag)
        if atr is None:return None
        stop=max(.01*trade.open_rate,trade.open_rate-3*atr)
        return stoploss_from_absolute(stop,current_rate,is_short=False,leverage=1.)

    def custom_exit(self,pair,trade,current_time,current_rate,current_profit,**kwargs):
        atr=entry_atr(trade.enter_tag)
        if atr and current_rate<=max(.01*trade.open_rate,trade.open_rate-3*atr):return 'ROC3_ENTRY_ATR_STOP'
        if current_time-trade.open_date_utc>=timedelta(hours=10):return 'ROC3_10_BAR_TIME_EXIT'
