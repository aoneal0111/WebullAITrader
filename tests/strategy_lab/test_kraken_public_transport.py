import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app.strategy_lab.kraken_public_transport import PublicTransport,request_key,monitor

URL='https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=5'
PAYLOAD={'error':[],'result':{'XXBTZUSD':[[1794252000,'1','2','1','2','1','10',2]],'last':1794252000}}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.transport=PublicTransport(self.root/'cache',lambda:None)
        self.api=SimpleNamespace()

    def test_exact_keys_exclude_orders_quotes_credentials_and_distinct_since(self):
        self.assertEqual(request_key(URL),request_key('https://api.kraken.com/0/public/OHLC?interval=5&pair=XBTUSD'))
        self.assertNotEqual(request_key(URL),request_key(URL+'&since=1'))
        for url,method,headers in [(URL,'POST',None),(URL,'GET',{'API-Key':'secret'}),
            (URL.replace('/public/OHLC','/private/AddOrder'),'GET',None),
            (URL.replace('OHLC','Ticker'),'GET',None),(URL.replace('api.kraken.com','evil.example'),'GET',None)]:
            self.assertIsNone(request_key(url,method,headers))

    def test_shared_response_keeps_original_timestamps_and_evidence_fields(self):
        calls=[]
        def get(*args):calls.append(args);return deepcopy(PAYLOAD)
        with patch('time.time',return_value=1000):
            self.transport.fetch(self.api,get,URL)
            other=PublicTransport(self.root/'cache',lambda:None)
            response=other.fetch(self.api,get,URL)
        self.assertEqual(len(calls),1);self.assertEqual(response,PAYLOAD)
        self.assertEqual(self.api.last_json_response,PAYLOAD)
        response['result']['last']=1
        self.assertEqual(self.api.last_json_response['result']['last'],1794252000)

    def test_expiry_and_boundary_force_new_request(self):
        calls=[]
        def get(*args):calls.append(1);return deepcopy(PAYLOAD)
        for at in (1000,1005,1016,1199,1200):
            with patch('time.time',return_value=at):self.transport.fetch(self.api,get,URL)
        self.assertEqual(len(calls),4)

    def test_failed_request_and_provider_error_are_never_cached(self):
        def fail(*args):raise TimeoutError('provider timeout')
        with self.assertRaises(TimeoutError):self.transport.fetch(self.api,fail,URL)
        self.transport.fetch(self.api,lambda *a:{'error':['EAPI:Rate limit exceeded']},URL)
        self.assertFalse((self.root/'cache'/(request_key(URL)[0]+'.json')).exists())
        self.assertEqual(self.transport.health()['network_failures'],1)
        self.assertEqual(self.transport.fetch(self.api,lambda *a:deepcopy(PAYLOAD),URL),PAYLOAD)

    def test_corrupt_cache_falls_back_and_boundary_crossing_response_is_not_saved(self):
        key,seconds=request_key(URL);path=self.root/'cache'/(key+'.json');path.write_text('{bad')
        with patch('time.time',return_value=1000):
            self.transport.fetch(self.api,lambda *a:deepcopy(PAYLOAD),URL)
        self.assertEqual(json.loads(path.read_text())['payload'],PAYLOAD)
        path.unlink()
        with patch('time.time',return_value=1200):self.transport.save(key,seconds,PAYLOAD,1199)
        self.assertFalse(path.exists())

    def test_async_duplicate_requests_are_shared_across_transport_instances(self):
        calls=[]
        async def get(*args):calls.append(1);await asyncio.sleep(.01);return deepcopy(PAYLOAD)
        other=PublicTransport(self.root/'cache',lambda:None)
        async def run():
            return await asyncio.gather(self.transport.fetch_async(self.api,get,URL),
                other.fetch_async(SimpleNamespace(),get,URL))
        result=asyncio.run(run())
        self.assertEqual(len(calls),1);self.assertEqual(result,[PAYLOAD,PAYLOAD])

    def test_async_requests_are_bounded_and_recover_after_failure(self):
        active=maximum=0
        async def get(*args):
            nonlocal active,maximum
            active+=1;maximum=max(active,maximum)
            try:
                await asyncio.sleep(.01)
                if 'since=0' in args[1]:raise TimeoutError()
                return deepcopy(PAYLOAD)
            finally:active-=1
        async def run():return await asyncio.gather(*[
            self.transport.fetch_async(SimpleNamespace(),get,URL+'&since='+str(i)) for i in range(8)],return_exceptions=True)
        result=asyncio.run(run())
        self.assertIsInstance(result[0],TimeoutError);self.assertTrue(all(r==PAYLOAD for r in result[1:]))
        self.assertLessEqual(maximum,2);self.assertEqual(self.transport.health()['network_failures'],1)

    def test_cancelled_request_releases_key_lock(self):
        async def get(*args):await asyncio.sleep(10)
        async def run():
            task=asyncio.create_task(self.transport.fetch_async(self.api,get,URL))
            await asyncio.sleep(.02);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            async def success(*args):return deepcopy(PAYLOAD)
            return await asyncio.wait_for(self.transport.fetch_async(self.api,success,URL),1)
        self.assertEqual(asyncio.run(run()),PAYLOAD)

    def test_network_outage_cooldown_is_shared_bounded_and_does_not_block_quotes(self):
        calls=[]
        def fail(*args):calls.append(1);raise TimeoutError()
        with patch('time.time',return_value=1000):
            for _ in range(3):
                with self.assertRaises(TimeoutError):self.transport.fetch(self.api,fail,URL)
            other=PublicTransport(self.root/'cache',lambda:None)
            with self.assertRaisesRegex(Exception,'COOLDOWN'):
                other.fetch(self.api,fail,URL)
            self.assertEqual(len(calls),3)
            # Fresh execution quote calls still use the real HTTP path.
            result=other.fetch(self.api,lambda *a:{'quote':'response'},URL.replace('OHLC','Ticker'))
            self.assertEqual(result,{'quote':'response'})
        with patch('time.time',return_value=1016):
            self.assertEqual(other.fetch(self.api,lambda *a:deepcopy(PAYLOAD),URL),PAYLOAD)
            self.assertFalse(other.circuit())

    def test_monitor_does_not_rewrite_engine_or_candle_timestamps(self):
        work=self.root/'work';work.mkdir();path=work/'engine-status.json';path.write_text('{"updated_at":"old"}')
        stop=monitor(self.transport,work)
        try:
            import time
            for _ in range(20):
                if (work/'transport-health.json').exists():break
                time.sleep(.01)
            r=json.loads((work/'transport-health.json').read_text())
            self.assertFalse(r['candle_freshness_proven']);self.assertEqual(r['state'],'WAITING')
            self.assertEqual(path.read_text(),'{"updated_at":"old"}')
        finally:stop.set()


class NativeRetryTests(unittest.TestCase):
    def test_real_ccxt_ohlcv_parsing_uses_shared_response_without_extra_http(self):
        try:import ccxt.async_support as ccxt
        except ImportError:self.skipTest('Native research environment required')
        async def run(root):
            api=ccxt.kraken({'enableRateLimit':False});calls=[]
            api.set_markets([dict(id='XXBTZUSD',symbol='BTC/USD',base='BTC',quote='USD',
                spot=True,swap=False,future=False,option=False,type='spot')])
            transport=PublicTransport(root,lambda:None)
            async def original(*args):calls.append(1);return deepcopy(PAYLOAD)
            async def fetch(url,method='GET',headers=None,body=None):
                return await transport.fetch_async(api,original,url,method,headers,body)
            api.fetch=fetch
            try:
                first=await api.fetch_ohlcv('BTC/USD','5m')
                second=await api.fetch_ohlcv('BTC/USD','5m')
                self.assertEqual(first,second);self.assertEqual(first[0][0],1794252000000)
                self.assertEqual(len(calls),1);self.assertEqual(api.last_json_response,PAYLOAD)
            finally:await api.close()
        with TemporaryDirectory() as root:asyncio.run(run(root))

    def test_candle_retry_is_two_attempts_and_order_method_is_untouched(self):
        try:
            import ccxt,ccxt.async_support as ac
            from freqtrade.exchange.exchange import Exchange
            from freqtrade.exchange.common import retrier_async
            from freqtrade.exceptions import TemporaryError
        except ImportError:self.skipTest('Native research environment required')
        from app.strategy_lab.kraken_public_transport import install
        calls=[]
        @retrier_async
        async def candle(api,*args,**kwargs):calls.append(1);raise TemporaryError('test')
        def sync(*a,**k):return deepcopy(PAYLOAD)
        async def asynchronous(*a,**k):return deepcopy(PAYLOAD)
        order=Exchange.create_order
        with TemporaryDirectory() as root,patch.object(ccxt.kraken,'fetch',sync),patch.object(ac.kraken,'fetch',asynchronous),patch.object(Exchange,'_async_get_candle_history',candle):
            transport=install(Path(root)/'clock',lambda p:None)
            self.assertIs(transport,install(Path(root)/'clock',lambda p:None))
            with self.assertRaises(TemporaryError):asyncio.run(Exchange._async_get_candle_history(SimpleNamespace(name='Kraken')))
            self.assertEqual(len(calls),2);self.assertIs(Exchange.create_order,order)


if __name__=='__main__':unittest.main()
