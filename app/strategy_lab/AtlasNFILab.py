# SPDX-License-Identifier: GPL-3.0-only
"""Experimental NFI subclass for a separate Freqtrade dry-run container only.

This adaptation changes NFI stops and position adjustment. Published NFI
performance does not describe this strategy. No Atlas desktop import exists.
"""
from datetime import timezone
import json
from pathlib import Path

from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


def entry_lease(path, lab_id, now):
    try:
        path=Path(path)
        if (path.parent/'PAUSE_REQUESTED').exists():return False
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

    def confirm_trade_entry(self,pair,order_type,amount,rate,time_in_force,current_time,
                            entry_tag,side,**kwargs):
        if (not dry_config(self.config) or side!='long' or entry_tag=='force_entry'
                or pair not in self.config['exchange']['pair_whitelist']):return False
        if current_time.utcoffset() is None:return False
        control=Path(self.config['user_data_dir'])/'grok-control.json'
        if not entry_lease(control,self.config['atlas_lab_id'],current_time.astimezone(timezone.utc).timestamp()):
            return False
        return super().confirm_trade_entry(pair,order_type,amount,rate,time_in_force,
            current_time,entry_tag,side,**kwargs)

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
