"""Offline check inside the pinned Freqtrade runtime. Does not start a bot."""
import argparse
from datetime import UTC,datetime
import json
from pathlib import Path
import socket


def forbidden(*args,**kwargs):raise RuntimeError('NETWORK_DISABLED_DURING_LAB_RUNTIME_CHECK')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--user-data',type=Path,default=Path('/freqtrade/user_data'));a=p.parse_args()
    socket.socket.connect=forbidden;socket.socket.connect_ex=forbidden
    socket.socket.sendto=forbidden;socket.getaddrinfo=forbidden
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.configuration.config_validation import validate_config_consistency
    from freqtrade import __version__
    if __version__!='2026.9':raise ValueError('PINNED_FREQTRADE_VERSION_REQUIRED')
    c=json.loads((a.user_data/'config.json').read_text(encoding='utf-8'))
    c['user_data_dir']=a.user_data;c['strategy_path']=str(a.user_data/'strategies')
    c['runmode']=RunMode.DRY_RUN
    strategy=StrategyResolver.load_strategy(c)
    validate_config_consistency(c)
    assert strategy.version()=='ATLAS_NFI_CAPPED_DRY_RUN_V1'
    assert strategy.stoploss==-.02 and not strategy.position_adjustment_enable
    assert not strategy.use_custom_stoploss and not strategy.can_short
    at=datetime.now(UTC)
    assert strategy.custom_stake_amount('BTC/USD',at,100,500,10,500,1,'1','long')==50
    assert strategy.adjust_trade_position() is None
    assert strategy.confirm_trade_exit(None,None,None,None,None,None,'stop_loss',at)
    print('FREQTRADE_RUNTIME_CHECK=PASSED; NETWORK_REQUESTS=0; ORDERS=0')
    print('STRATEGY='+strategy.version()+'; STOPLOSS=-0.02; MAX_STAKE=50; ADDITIONS_DISABLED')


if __name__=='__main__':main()
