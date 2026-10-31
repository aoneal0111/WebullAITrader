# SPDX-License-Identifier: GPL-3.0-only
"""Experimental NFI subclass for a separate Freqtrade dry-run container only.

This adaptation changes NFI stops and position adjustment. Published NFI
performance does not describe this strategy. No Atlas desktop import exists.
"""
from datetime import timezone
import json
import os
import time
from datetime import datetime, UTC
from pathlib import Path

from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


def entry_lease(path, lab_id, now):
    try:
        path=Path(path)
        if ((path.parent/'PAUSE_REQUESTED').exists()
                or (path.parent/'DIAGNOSTICS_ENABLED').exists()):return False
        if path.is_symlink() or path.stat().st_size>4096:return False
        data=json.loads(path.read_text(encoding='utf-8'))
        if set(data)!={'lab_id','action','issued_at','expires_at'}:return False
        if data['lab_id']!=lab_id or data['action']!='ALLOW':return False
        issued,expires=data['issued_at'],data['expires_at']
        if any(type(x) not in (int,float) for x in (issued,expires)):return False
        return 0<=now-issued<=600 and issued<=now<expires<=issued+600
    except (OSError,ValueError,TypeError):return False


def dry_config(config):
    exchange=config.get('exchange',{})
    return (config.get('dry_run') is True and config.get('trading_mode')=='spot'
        and config.get('max_open_trades')==2 and config.get('stake_amount')==50
        and config.get('stoploss')==-.02
        and config.get('position_adjustment_enable') is False
        and config.get('force_entry_enable') is False
        and config.get('api_server',{}).get('enabled') is False
        and config.get('telegram',{}).get('enabled') is False
        and config.get('exchange',{}).get('name')=='kraken'
        and config.get('stake_currency')=='USD'
        and not any(exchange.get(k) for k in ('key','secret','password','uid','privateKey','walletAddress'))
        and config.get('atlas_lab_id','').isalnum()
        and len(config.get('atlas_lab_id',''))==32)


class AtlasNFILab(NostalgiaForInfinityX8):
    stoploss=-.02
    position_adjustment_enable=False
    use_custom_stoploss=False
    hold_support_enabled=False
    can_short=False

    def version(self):return 'ATLAS_NFI_CAPPED_DRY_RUN_V1'

    def __init__(self,config):
        if not dry_config(config):raise ValueError('ISOLATED_DRY_RUN_CONFIG_REQUIRED')
        super().__init__(config)

    def _diagnostic(self, stage, pair=None, dataframe=None, outcome=None):
        """Optional, bounded telemetry; never changes a native decision or raises."""
        try:
            root=Path(self.config['user_data_dir'])
            if not (root/'DIAGNOSTICS_ENABLED').is_file():return
            state=getattr(self,'_diagnostic_state',None)
            if state is None:
                state={'scope':'NATIVE_NFI_PIPELINE_DIAGNOSTICS_V1','pid':os.getpid(),
                       'started_at':datetime.now(UTC).isoformat(),'stages':{},'pairs':{},
                       'execution_authorized':False,
                       'limitations':['Counts describe this diagnostic process only',
                                      'Signal counts are observations, not unique trades or profitability']}
                self._diagnostic_state=state
            # Only fixed stage names, booleans, counts and timestamps are recorded.
            state['stages'][stage]=min(state['stages'].get(stage,0)+1,1000000000)
            state['updated_at']=datetime.now(UTC).isoformat()
            state['resolved_dry_config_accepted']=bool(dry_config(self.config))
            state['startup_candle_count']=int(getattr(self,'startup_candle_count',0))
            state['entry_lease_accepted_now']=bool(entry_lease(root/'grok-control.json',
                self.config['atlas_lab_id'],datetime.now(UTC).timestamp()))
            if pair in self.config['exchange']['pair_whitelist']:
                item=state['pairs'].setdefault(pair,{'stages':{}})
                item['stages'][stage]=min(item['stages'].get(stage,0)+1,1000000000)
                if outcome is not None:item['last_'+stage+'_outcome']=outcome
                if dataframe is not None:
                    item['rows']=len(dataframe)
                    item['has_startup_row_count']=len(dataframe)>=state['startup_candle_count']
                    if len(dataframe):
                        latest=dataframe.iloc[-1]
                        stamp=latest.get('date')
                        if hasattr(stamp,'to_pydatetime'):stamp=stamp.to_pydatetime()
                        if isinstance(stamp,datetime) and stamp.utcoffset() is not None:
                            item['last_candle_open_utc']=stamp.astimezone(UTC).isoformat()
                            item['last_candle_open_age_seconds']=round((datetime.now(UTC)-stamp).total_seconds(),3)
                        if 'enter_long' in dataframe:
                            item['last_row_enter_long']=bool(latest.get('enter_long')==1)
                            item['long_signal_rows_last_12']=int((dataframe['enter_long'].tail(12)==1).sum())
            tick=time.monotonic()
            if tick-getattr(self,'_diagnostic_written_at',float('-inf'))<5:return
            target=root/'native-diagnostics.json';temporary=root/'native-diagnostics.tmp'
            if target.is_symlink() or temporary.is_symlink():return
            temporary.write_text(json.dumps(state,allow_nan=False,indent=2)+'\n',encoding='utf-8')
            os.replace(temporary,target)
            self._diagnostic_written_at=tick
        except Exception:
            # Telemetry must never affect signals, entries or protective exits.
            pass

    def bot_loop_start(self,*args,**kwargs):
        self._diagnostic('loop_started')
        parent=getattr(super(),'bot_loop_start',None)
        return parent(*args,**kwargs) if parent is not None else None

    def populate_indicators(self,dataframe,metadata):
        pair=metadata.get('pair')
        self._diagnostic('indicators_started',pair,dataframe)
        try:result=super().populate_indicators(dataframe,metadata)
        except Exception:
            self._diagnostic('indicators_failed',pair);raise
        self._diagnostic('indicators_completed',pair,result)
        return result

    def populate_entry_trend(self,dataframe,metadata):
        pair=metadata.get('pair')
        self._diagnostic('signals_started',pair,dataframe)
        try:result=super().populate_entry_trend(dataframe,metadata)
        except Exception:
            self._diagnostic('signals_failed',pair);raise
        self._diagnostic('signals_completed',pair,result)
        return result

    def get_entry_signal(self,pair,timeframe,dataframe):
        result=super().get_entry_signal(pair,timeframe,dataframe)
        self._diagnostic('entry_signal_checked',pair,dataframe,
                         outcome=bool(result[0] is not None))
        return result

    def confirm_trade_entry(self,pair,order_type,amount,rate,time_in_force,current_time,
                            entry_tag,side,**kwargs):
        if (not dry_config(self.config) or side!='long' or entry_tag=='force_entry'
                or pair not in self.config['exchange']['pair_whitelist']):
            self._diagnostic('entry_config_rejected',pair);return False
        if current_time.utcoffset() is None:
            self._diagnostic('entry_time_rejected',pair);return False
        control=Path(self.config['user_data_dir'])/'grok-control.json'
        if not entry_lease(control,self.config['atlas_lab_id'],current_time.astimezone(timezone.utc).timestamp()):
            self._diagnostic('entry_lease_rejected',pair);return False
        self._diagnostic('native_entry_confirmation_started',pair)
        result=super().confirm_trade_entry(pair,order_type,amount,rate,time_in_force,
            current_time,entry_tag,side,**kwargs)
        self._diagnostic('native_entry_confirmation_completed',pair,outcome=bool(result))
        return result

    def confirm_trade_exit(self,pair,trade,order_type,amount,rate,time_in_force,
                           exit_reason,current_time,**kwargs):
        # Grok and NFI hold logic cannot veto protective or manual exits.
        return True

    def custom_stake_amount(self,pair,current_time,current_rate,proposed_stake,
                            min_stake,max_stake,leverage,entry_tag,side,**kwargs):
        import math
        if side!='long' or leverage!=1:return 0.0
        if any(not math.isfinite(float(v)) for v in (proposed_stake,max_stake)):return 0.0
        stake=min(50.0,float(proposed_stake),float(max_stake))
        return stake if stake>0 and (min_stake is None or float(min_stake)<=stake) else 0.0

    def adjust_trade_position(self,*args,**kwargs):return None
