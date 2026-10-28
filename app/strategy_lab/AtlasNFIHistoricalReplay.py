# SPDX-License-Identifier: GPL-3.0-only
"""Replay-only lease adapter. Refuses live and dry-run trading modes."""
from datetime import timezone, datetime
import json
from pathlib import Path
from AtlasNFILab import AtlasNFILab


class AtlasNFIHistoricalReplay(AtlasNFILab):
    def __init__(self,config):
        if getattr(config.get('runmode'),'value',None)!='backtest':
            raise ValueError('OFFLINE_BACKTEST_MODE_REQUIRED')
        if not (Path(config['user_data_dir'])/'OFFLINE_REPLAY_ONLY').is_file():
            raise ValueError('OFFLINE_REPLAY_MARKER_REQUIRED')
        super().__init__(config)
        self.replay_entry_checks={'attempts':0,'parent_accepted':0,'parent_rejected':0}
        self.replay_signal_counts={}

    def populate_entry_trend(self,dataframe,metadata):
        result=super().populate_entry_trend(dataframe,metadata)
        selected=result.loc[(result['date']>=datetime(2026,7,1,tzinfo=timezone.utc))
                            & (result['date']<datetime(2026,10,1,tzinfo=timezone.utc))]
        self.replay_signal_counts[metadata['pair']]={'evaluated_rows':len(selected),
            'long_signal_rows':int((selected['enter_long']==1).sum()),
            'short_signal_rows':int((selected['enter_short']==1).sum())}
        return result

    def confirm_trade_entry(self,pair,order_type,amount,rate,time_in_force,current_time,
                            entry_tag,side,**kwargs):
        if getattr(self.config.get('runmode'),'value',None)!='backtest':return False
        if current_time.utcoffset() is None:return False
        self.replay_entry_checks['attempts']+=1
        # Simulated-time permission only. All native config, stake, NFI confirmation,
        # exit and stop rules still run through the original adapted wrapper.
        now=current_time.astimezone(timezone.utc).timestamp()
        payload={'lab_id':self.config['atlas_lab_id'],'action':'ALLOW','issued_at':now,'expires_at':now+1}
        (Path(self.config['user_data_dir'])/'grok-control.json').write_text(json.dumps(payload),encoding='utf-8')
        accepted=super().confirm_trade_entry(pair,order_type,amount,rate,time_in_force,
                                           current_time,entry_tag,side,**kwargs)
        self.replay_entry_checks['parent_accepted' if accepted else 'parent_rejected']+=1
        return accepted
