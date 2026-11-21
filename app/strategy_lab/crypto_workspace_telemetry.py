"""Native PAPER worker telemetry. No market requests or order methods are called."""
from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
from uuid import uuid4

SCOPE = 'CRYPTO_WORKSPACE_TELEMETRY_V1'


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _observe(evidence, method, subject, response):
    """Remember only quotes already requested by native trading code."""
    if method not in ('fetch_ticker', 'get_tickers') or not isinstance(response, dict):
        return
    quotes = getattr(evidence, 'workspace_quotes', None)
    if quotes is None:
        quotes = evidence.workspace_quotes = {}
    values={k: finite(response.get(k)) for k in ('last', 'bid', 'ask', 'percentage', 'baseVolume', 'quoteVolume')}
    previous=quotes.get(subject,{})
    if all(previous.get(k)==v for k,v in values.items()):return
    quotes[subject] = dict(observed_at=datetime.now(UTC).isoformat(), **values)


def observe(evidence, method, subject, response):
    try:_observe(evidence,method,subject,response)
    except Exception:pass


def snapshot(bot, evidence, now):
    from freqtrade.persistence import Trade
    if bot.config.get('dry_run') is not True:
        raise ValueError('PAPER_TELEMETRY_REQUIRED')
    trades = Trade.get_open_trades()
    pairs = sorted(set(bot.active_pair_whitelist) | {t.pair for t in trades})[:160]
    markets = {}
    for pair in pairs:
        frame, _ = bot.dataprovider.get_analyzed_dataframe(pair, evidence.primary)
        source = evidence.source_states.get(pair, {})
        row = dict(pair=pair, timeframe=evidence.primary, source=source,
            ready=bool(evidence.ready(bot,pair,now)))
        if not frame.empty:
            last = frame.iloc[-1]
            row.update(candle_at=last['date'].isoformat(), candle_close=finite(last.get('close')),
                candle_volume=finite(last.get('volume')),
                candle_closed_at=(last['date']+timedelta(seconds=evidence.timeframes[evidence.primary])).isoformat())
        row['quote'] = getattr(evidence, 'workspace_quotes', {}).get(pair)
        markets[pair] = row
    positions = []
    for trade in trades:
        row = markets.get(trade.pair, {})
        quote = row.get('quote') or {}
        at = quote.get('observed_at')
        age = (now - datetime.fromisoformat(at)).total_seconds() if at else None
        mark = quote.get('bid') if age is not None and 0 <= age <= 60 else None
        basis = 'NATIVE BID' if mark is not None and mark > 0 else 'CANDLE CLOSE ESTIMATE'
        if basis != 'NATIVE BID':
            mark = row.get('candle_close') if row.get('ready') else None
            at = row.get('candle_closed_at')
        valid = mark is not None and mark > 0
        positions.append(dict(id=trade.id, pair=trade.pair, amount=trade.amount,
            mark=mark if valid else None, mark_at=at, mark_basis=basis,
            market_value=trade.amount * mark if valid else None,
            unrealized=finite(trade.calc_profit(mark)) if valid else None,
            profit_ratio=finite(trade.calc_profit_ratio(mark)) if valid else None))
    stake = bot.config['stake_currency']
    # Native balances only: do not refresh wallets or initiate any network work.
    account = dict(currency=stake, free=finite(bot.wallets.get_free(stake)),
        used=finite(bot.wallets.get_used(stake)), total=finite(bot.wallets.get_total(stake)),
        available_stake=finite(bot.wallets.get_available_stake_amount()))
    settings={name:getattr(bot.strategy,name,None) for name in ('timeframe','stoploss','minimal_roi',
        'trailing_stop','trailing_stop_positive','use_exit_signal','use_custom_stoploss','position_adjustment_enable')} if hasattr(bot,'strategy') else {}
    return dict(scope=SCOPE, updated_at=now.isoformat(), recording_id=evidence.root.name,
        live_trading_enabled=False, markets=markets, positions=positions, account=account,strategy_settings=settings)


def publish(work, bot, evidence, now):
    """Display failures must never terminate or inhibit a trading cycle."""
    path = Path(work) / 'workspace-snapshot.json'
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        value = snapshot(bot, evidence, now)
        temporary.write_text(json.dumps(value, allow_nan=False, default=str), encoding='utf-8')
        temporary.replace(path)
    except Exception as exc:
        # Overwrite old values with a fresh unavailable snapshot on error.
        try:
            temporary.write_text(json.dumps(dict(scope=SCOPE, updated_at=now.isoformat(),
                live_trading_enabled=False, error=type(exc).__name__)), encoding='utf-8')
            temporary.replace(path)
        except OSError:
            pass
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass
