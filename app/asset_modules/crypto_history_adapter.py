"""Read-only provider-bar adapter for bounded crypto strategy-worker inputs."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.asset_modules.contract_logic import Bar
from app.asset_modules.contract_worker_codec import _decimal, _symbol
from app.asset_modules.crypto_signals import CryptoHistory, CryptoQuote
from app.crypto_research.evidence import CryptoBarInterval, CryptoCompletedBar
from app.crypto_research.models import CryptoObservation, CryptoResearchDecision
from app.crypto_research.runtime import CryptoResearchRuntime


@dataclass(frozen=True)
class CryptoHistoryFrame:
    status: str
    history: CryptoHistory | None
    quote: CryptoObservation | CryptoResearchDecision | None
    source_bar_count: int
    worker_bar_count: int
    provider: str | None
    available_at: datetime | None

    def report(self):
        return {'scope':'READ_ONLY_CRYPTO_HISTORY_COVERAGE_NO_ORDERS', 'status':self.status,
            'source_bar_count':self.source_bar_count,'worker_bar_count':self.worker_bar_count,
            'provider':self.provider,'available_at':None if self.available_at is None else self.available_at.isoformat(),
            'evidence':'RETAINED_PROVIDER_COMPLETED_BARS_NOT_SNAPSHOT_CANDLES'}


def adapt(symbol, bars, quote, *, now):
    symbol = _symbol(symbol)
    if not symbol.endswith('/USD') or symbol.count('/') != 1:
        raise ValueError('Canonical spot USD symbol required')
    if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Aware observation cutoff required')
    if not isinstance(bars,tuple) or len(bars)>64 or any(not isinstance(b,CryptoCompletedBar) for b in bars):
        raise ValueError('At most 64 typed retained provider bars required')
    def result(status,history=None,owned_quote=None,provider=None,available=None):
        return CryptoHistoryFrame(status,history,owned_quote,len(bars),0 if history is None else len(history.bars),provider,available)
    if not bars:return result('NO_COMPLETED_PROVIDER_BARS')
    if any(b.canonical_symbol!=symbol or b.interval is not CryptoBarInterval.M1 or b.is_complete is not True for b in bars):
        return result('BAR_OWNERSHIP_OR_INTERVAL_MISMATCH')
    if any(max(b.closed_at,b.observed_at,b.decision_cutoff)>now for b in bars):
        return result('BAR_NOT_YET_AVAILABLE')
    if any(b.observed_at < b.closed_at for b in bars):
        return result('BAR_AVAILABILITY_TIMESTAMPS_INVALID')
    if any(a.closed_at>=b.closed_at for a,b in zip(bars,bars[1:])):
        return result('BAR_ORDER_OR_DUPLICATE_ERROR')
    selected=bars[-48:]
    providers={(b.provider,b.provider_symbol) for b in selected}
    if len(providers)!=1:return result('MIXED_PROVIDER_HISTORY')
    provider=selected[0].provider
    available=max(max(b.observed_at,b.decision_cutoff,b.closed_at) for b in selected)
    if len(selected)<22:return result('INSUFFICIENT_COMPLETED_HISTORY',provider=provider,available=available)
    if (any(b.closed_at-a.closed_at!=timedelta(minutes=1) for a,b in zip(selected,selected[1:]))
            or now-selected[-1].closed_at>timedelta(minutes=1)):
        return result('HISTORY_NOT_CURRENT_OR_CONTIGUOUS',provider=provider,available=available)
    for b in selected:
        for value in (b.high,b.low,b.close):_decimal(str(value))
    if not isinstance(quote,(CryptoObservation,CryptoResearchDecision)):
        return result('QUOTE_UNAVAILABLE',provider=provider,available=available)
    if (quote.pair.canonical_symbol!=symbol or quote.pair.quote_asset!='USD'
            or quote.pair.provider_symbol != selected[0].provider_symbol
            or getattr(quote,'decision_cutoff',quote.timestamp)>now
            or quote.timestamp<selected[-1].closed_at
            or not timedelta(0)<=now-quote.timestamp<=timedelta(seconds=2)
            or quote.bid is None or quote.ask is None):
        return result('QUOTE_UNAVAILABLE',provider=provider,available=available)
    # Validate worker monetary limits without changing any quote timestamps.
    q=CryptoQuote(symbol,_decimal(str(quote.bid)),_decimal(str(quote.ask)),quote.timestamp)
    if not 0<q.bid<=q.ask:return result('QUOTE_UNAVAILABLE',provider=provider,available=available)
    history=CryptoHistory(symbol,tuple(Bar(b.closed_at,b.high,b.low,b.close) for b in selected))
    return result('READY',history,quote,provider,available)


def snapshot(runtime, symbol, *, now):
    if not isinstance(runtime,CryptoResearchRuntime):raise ValueError('Crypto research runtime required')
    bars=runtime.completed_bars(symbol,as_of=now,limit=64)
    quote=next((q for q in runtime.latest() if q.pair.canonical_symbol==symbol),None)
    return adapt(symbol,bars,quote,now=now)
