"""Shared, short-lived public OHLC responses. No credentials or trading authority.

Exact request keys, original provider timestamps, and native entry freshness
checks are retained. Cache entries never survive a candle boundary.
"""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit,parse_qsl,urlencode
from uuid import uuid4

MAX_BYTES=4*1024*1024
TTL=15.


def request_key(url,method='GET',headers=None,body=None):
    u=urlsplit(url)
    if (method!='GET' or headers or body or u.scheme!='https' or
        u.netloc!='api.kraken.com' or u.path!='/0/public/OHLC' or u.fragment):return None
    params=parse_qsl(u.query,keep_blank_values=True)
    if len(dict(params))!=len(params) or set(dict(params))-{'pair','interval','since'}:return None
    values=dict(params)
    if not values.get('pair') or not values.get('interval','').isdigit():return None
    interval=int(values['interval'])
    if interval not in (1,5,15,30,60,240,1440,10080,21600):return None
    # Since is part of the key: different history windows cannot share responses.
    key=sha256(urlencode(sorted(params)).encode()).hexdigest()
    return key,interval*60


def atomic(path,value):
    raw=json.dumps(value,allow_nan=False,separators=(',',':'))
    temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:temp.write_text(raw,encoding='utf-8');temp.replace(path)
    finally:temp.unlink(missing_ok=True)


class PublicTransport:
    def __init__(self,root,gate):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.gate=gate;self.stats={};self.lock=threading.Lock();self.semaphores={}

    def mark(self,name,value=1):
        with self.lock:self.stats[name]=self.stats.get(name,0)+value

    def health(self):
        with self.lock:return dict(self.stats)

    def circuit(self,success=None):
        """Short OHLC-only cooldown after three network failures in a minute."""
        import fcntl
        now=time.time();path=self.root/'circuit-state.json'
        with (self.root/'circuit.lock').open('a') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:
                try:state=json.loads(path.read_text())
                except (OSError,ValueError):state={}
                failures=[at for at in state.get('failures',[]) if 0<=now-at<=60]
                until=state.get('until',0)
                if success is None:return now<until and until-now<=15
                if success:state=dict(failures=[],until=0)
                else:
                    failures.append(now);state=dict(failures=failures[-3:],until=now+15 if len(failures)>=3 else 0)
                atomic(path,state)
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

    def allow_candles(self):
        if self.circuit():
            self.mark('cooldown_skips')
            import ccxt
            raise ccxt.RequestTimeout('PUBLIC_OHLC_NETWORK_COOLDOWN; no HTTP request sent')

    def network_failure(self,exc,candles):
        if candles:
            import ccxt
            if isinstance(exc,(ccxt.NetworkError,TimeoutError,ConnectionError)):self.circuit(False)

    def cached(self,key,seconds):
        path=self.root/(key+'.json')
        try:
            if path.stat().st_size>MAX_BYTES:return None
            data=json.loads(path.read_text());now=time.time();age=now-data['fetched_at']
            if not 0<=age<TTL or int(now//seconds)!=data['boundary']:return None
            payload=data['payload']
            if not isinstance(payload,dict) or payload.get('error')!=[] or not isinstance(payload.get('result'),dict):return None
            return payload
        except (OSError,ValueError,KeyError,TypeError):return None

    def save(self,key,seconds,payload,started):
        now=time.time()
        # A request crossing a boundary may carry the preceding incomplete bar.
        if int(started//seconds)!=int(now//seconds):return
        if not isinstance(payload,dict) or payload.get('error')!=[] or not isinstance(payload.get('result'),dict):return
        if len(json.dumps(payload))>MAX_BYTES-1024:return
        atomic(self.root/(key+'.json'),dict(fetched_at=now,boundary=int(now//seconds),payload=payload))
        self.mark('cache_writes')
        if self.health()['cache_writes']%64==0:
            for path in self.root.glob('*.json'):
                try:
                    if now-path.stat().st_mtime>4*TTL:path.unlink(missing_ok=True)
                except OSError:pass

    @contextmanager
    def key_lock(self,key):
        import fcntl
        with (self.root/(key[:2]+'.lock')).open('a') as f:
            fcntl.flock(f.fileno(),fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(f.fileno(),fcntl.LOCK_UN)

    def restored(self,api,payload):
        # Evidence wrappers read CCXT's response fields after fetch_ohlcv.
        # Updating them prevents attribution to an unrelated prior HTTP response.
        api.last_json_response=deepcopy(payload)
        api.last_http_response=json.dumps(payload)
        api.last_response_headers={}
        self.mark('cache_hits')
        return deepcopy(payload)

    def network(self,call,*,candles=False):
        if candles:self.allow_candles()
        self.gate()
        if candles:self.allow_candles()
        started=time.monotonic();self.mark('network_attempts')
        try:
            result=call();self.mark('network_successes')
            if candles:self.circuit(True)
            with self.lock:self.stats['last_network_success_at']=time.time()
            return result
        except Exception as exc:
            self.network_failure(exc,candles)
            self.mark('network_failures');self.mark('failure_'+type(exc).__name__)
            with self.lock:self.stats['last_network_failure_at']=time.time()
            raise
        finally:self.mark('network_ms',round((time.monotonic()-started)*1000))

    def fetch(self,api,original,url,method='GET',headers=None,body=None):
        wanted=request_key(url,method,headers,body)
        if not wanted:return self.network(lambda:original(api,url,method,headers,body))
        key,seconds=wanted;start=time.monotonic()
        with self.key_lock(key):
            self.mark('queue_wait_ms',round((time.monotonic()-start)*1000))
            cached=self.cached(key,seconds)
            if cached is not None:return self.restored(api,cached)
            began=time.time();result=self.network(lambda:original(api,url,method,headers,body),candles=True)
            self.save(key,seconds,result,began);return result

    async def fetch_async(self,api,original,url,method='GET',headers=None,body=None):
        import fcntl
        loop=asyncio.get_running_loop()
        semaphore=self.semaphores.setdefault(loop,asyncio.Semaphore(2))
        queued=time.monotonic()
        async with semaphore:
            wanted=request_key(url,method,headers,body);file=None
            try:
                if wanted:
                    key,seconds=wanted;file=(self.root/(key[:2]+'.lock')).open('a')
                    while True:
                        try:fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);break
                        except BlockingIOError:await asyncio.sleep(.05)
                    cached=self.cached(key,seconds)
                    if cached is not None:return self.restored(api,cached)
                self.mark('queue_wait_ms',round((time.monotonic()-queued)*1000))
                if wanted:self.allow_candles()
                await asyncio.to_thread(self.gate)
                if wanted:self.allow_candles()
                began=time.time();started=time.monotonic();self.mark('network_attempts')
                try:
                    result=await original(api,url,method,headers,body);self.mark('network_successes')
                    if wanted:self.circuit(True)
                    with self.lock:self.stats['last_network_success_at']=time.time()
                except Exception as exc:
                    self.network_failure(exc,bool(wanted))
                    self.mark('network_failures');self.mark('failure_'+type(exc).__name__)
                    with self.lock:self.stats['last_network_failure_at']=time.time()
                    raise
                finally:self.mark('network_ms',round((time.monotonic()-started)*1000))
                if wanted:self.save(key,seconds,result,began)
                return result
            finally:
                if file is not None:
                    fcntl.flock(file.fileno(),fcntl.LOCK_UN);file.close()


def install(clock,gate):
    import ccxt,ccxt.async_support as async_ccxt
    from functools import wraps
    if getattr(ccxt.kraken.fetch,'_atlas_shared_transport',False):return ccxt.kraken.fetch._atlas_transport
    transport=PublicTransport(Path(clock).parent/'ohlc-cache',lambda:gate(clock))
    sync=ccxt.kraken.fetch;asynchronous=async_ccxt.kraken.fetch
    @wraps(sync)
    def fetch(api,url,method='GET',headers=None,body=None):
        return transport.fetch(api,sync,url,method,headers,body)
    @wraps(asynchronous)
    async def fetch_async(api,url,method='GET',headers=None,body=None):
        return await transport.fetch_async(api,asynchronous,url,method,headers,body)
    fetch._atlas_shared_transport=True;fetch._atlas_transport=transport
    ccxt.kraken.fetch=fetch;async_ccxt.kraken.fetch=fetch_async
    # Only candle retries are shortened. Order/exit retries are untouched.
    import freqtrade
    if freqtrade.__version__!='2026.9':raise ValueError('PINNED_FREQTRADE_REQUIRED')
    from freqtrade.exchange.exchange import Exchange
    original=Exchange._async_get_candle_history
    @wraps(original)
    async def candles(*args,**kwargs):
        kwargs['count']=min(kwargs.get('count',1),1)
        return await original(*args,**kwargs)
    Exchange._async_get_candle_history=candles
    return transport


def monitor(transport,work):
    """Separate process heartbeat; never advances candle or engine timestamps."""
    import os
    stop=threading.Event();work=Path(work)
    def publish():
        stats=transport.health();now=time.time();last=stats.get('last_network_success_at')
        failure=stats.get('last_network_failure_at',0)
        value=dict(scope='KRAKEN_PUBLIC_TRANSPORT_HEALTH_V1',updated_at=now,pid=os.getpid(),
            process_alive=True,stats=stats,last_network_success_age_seconds=now-last if last else None,
            state='REQUESTS_FAILING' if failure>(last or 0) else 'RESPONSES_RECEIVED' if last else 'WAITING',
            candle_freshness_proven=False)
        atomic(work/'transport-health.json',value)
    def run():
        while not stop.is_set():
            try:publish()
            except OSError:pass
            stop.wait(5)
    thread=threading.Thread(target=run,name='public-transport-health',daemon=True);thread.start()
    return stop
