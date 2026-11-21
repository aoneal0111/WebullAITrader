"""Native Bollinger research candidates for backtests and isolated dry-runs."""
from AtlasIncomeEMA import AtlasIncomeEMACausal
from app.strategy_lab.broad_strategy_lab import indicators


class AtlasBollingerResearch(AtlasIncomeEMACausal):
    STOP_ATR=5.
    TARGET_ATR=2.
    HOLD_HOURS=96
    WIDTH=2.5
    SELL_SIGNAL=True

    def populate_indicators(self,dataframe,metadata):
        f=super().populate_indicators(dataframe,metadata)
        return indicators(f)

    def populate_entry_trend(self,dataframe,metadata):
        f=dataframe
        f['enter_long']=((f.close<f.sma20-self.WIDTH*f.std20)&f.eligible&(f.volume>0)).astype(int)
        f['enter_tag']='BOLLINGER_RESEARCH_ONLY'
        return f

    def populate_exit_trend(self,dataframe,metadata):
        dataframe['exit_long']=((dataframe.close>=dataframe.sma20)&self.SELL_SIGNAL).astype(int)
        return dataframe

    def custom_exit(self,pair,trade,current_time,current_rate,current_profit,**kwargs):
        if (current_time-trade.open_date_utc).total_seconds()>=96*3600:return 'BOLL_96H_TIMEOUT'
        return None


class AtlasBollingerActiveResearch(AtlasBollingerResearch):
    WIDTH=1.5
    SELL_SIGNAL=False
