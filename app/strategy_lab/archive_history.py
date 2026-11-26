"""Read selected members from official Kraken ZIP archives without bulk extraction."""
import argparse
from collections import OrderedDict
import csv
from contextlib import ExitStack
from datetime import UTC,datetime
from hashlib import sha256
import io
import json
import math
from pathlib import Path,PurePosixPath
import re
import time
import urllib.request
import urllib.error
import zipfile

BASE='https://assets.kraken.com/marketing/institutions/'
FULL=[BASE+f'Kraken_OHLCVT_Full.zip.part{i:02d}' for i in range(5)]
Q3=[BASE+'Kraken_OHLCVT_2026Q3.zip']
PAIRS=('BTC/USD','ETH/USD','SOL/USD','LINK/USD','LTC/USD','AVAX/USD','XRP/USD','ADA/USD')
INTERVALS=(1,5,15,60,240,1440)
START=int(datetime(2026,7,1,tzinfo=UTC).timestamp())
END=int(datetime(2026,10,1,tzinfo=UTC).timestamp())
# At least 710 daily bars before the fixed evaluated period.
WARMUP=int(datetime(2024,6,1,tzinfo=UTC).timestamp())
ALIASES={'BTC/USD':('XBTUSD','BTCUSD','XXBTZUSD'),'ETH/USD':('ETHUSD','XETHZUSD')}
MAX_READS=700
MAX_BYTES=512*1024*1024
MAX_MEMBER=192*1024*1024


def write(path,data):Path(path).write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def digest(path):return sha256(Path(path).read_bytes()).hexdigest()


class Budget:
    def __init__(self):self.reads=0;self.bytes=0
    def reserve(self,size):
        if self.reads>=MAX_READS or size<0 or self.bytes+size>MAX_BYTES:raise ValueError('ARCHIVE_DOWNLOAD_BOUND')
        self.reads+=1;self.bytes+=size


class RangeZip(io.RawIOBase):
    """Seekable virtual concatenation of immutable HTTPS byte ranges."""
    block_size=4*1024*1024
    def __init__(self,urls,budget):
        super().__init__();self.budget=budget;self.parts=[];self.size=0;self.pos=0;self.cache=OrderedDict()
        for url in urls:
            if url not in FULL+Q3:raise ValueError('OFFICIAL_ARCHIVE_URL_REQUIRED')
            budget.reserve(0)
            req=urllib.request.Request(url,method='HEAD',headers={'Accept-Encoding':'identity','User-Agent':'AtlasResearch/1.0','User-Agent':'AtlasResearch/1.0'})
            with urllib.request.urlopen(req,timeout=30) as response:
                size=int(response.headers['Content-Length']);etag=response.headers.get('ETag')
                if response.status!=200 or response.geturl()!=url or size<=0 or not etag or etag.startswith('W/'):
                    raise ValueError('IMMUTABLE_ARCHIVE_METADATA_REQUIRED')
            self.parts.append({'url':url,'start':self.size,'size':size,'etag':etag});self.size+=size
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.pos
    def seek(self,offset,whence=0):
        new=offset if whence==0 else self.pos+offset if whence==1 else self.size+offset if whence==2 else -1
        if new<0:raise ValueError('NEGATIVE_SEEK')
        self.pos=new;return new
    def _range(self,start,end):
        result=[]
        for part in self.parts:
            lo=max(start,part['start']);hi=min(end,part['start']+part['size'])
            if lo>=hi:continue
            first,last=lo-part['start'],hi-part['start']-1;size=last-first+1
            self.budget.reserve(size)
            req=urllib.request.Request(part['url'],headers={'Accept-Encoding':'identity','User-Agent':'AtlasResearch/1.0',
                'Range':f'bytes={first}-{last}','If-Match':part['etag']})
            with urllib.request.urlopen(req,timeout=30) as response:
                if (response.status!=206 or response.geturl()!=part['url']
                    or response.headers.get('ETag')!=part['etag']
                    or response.headers.get('Content-Range')!=f'bytes {first}-{last}/{part["size"]}'):
                    raise ValueError('EXACT_ARCHIVE_RANGE_REQUIRED')
                data=response.read(size+1)
                if len(data)!=size:raise ValueError('ARCHIVE_RANGE_LENGTH')
                result.append(data)
        return b''.join(result)
    def read(self,size=-1):
        if size<0:size=self.size-self.pos
        size=min(size,max(0,self.size-self.pos))
        if size>16*1024*1024:raise ValueError('SINGLE_READ_BOUND')
        result=[];remaining=size
        while remaining:
            block=self.pos//self.block_size
            if block not in self.cache:
                start=block*self.block_size
                self.cache[block]=self._range(start,min(start+self.block_size,self.size))
                while len(self.cache)>4:self.cache.popitem(last=False)
            self.cache.move_to_end(block)
            offset=self.pos%self.block_size;chunk=self.cache[block][offset:offset+remaining]
            if not chunk:raise ValueError('ARCHIVE_TRUNCATED')
            result.append(chunk);self.pos+=len(chunk);remaining-=len(chunk)
        return b''.join(result)


def select_member(archive,pair,interval):
    names=ALIASES.get(pair,(pair.replace('/',''),))
    wanted={f'{name}_{interval}.csv' for name in names}
    entries=[i for i in archive.infolist() if PurePosixPath(i.filename).name in wanted]
    if len(entries)!=1:raise ValueError('ARCHIVE_MEMBER_MISSING_OR_AMBIGUOUS:'+pair+':'+str(interval))
    item=entries[0]
    if (item.file_size>MAX_MEMBER or item.compress_size>MAX_MEMBER or item.flag_bits&1
            or item.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)):
        raise ValueError('ARCHIVE_MEMBER_BOUND')
    return item


def parse_member(stream,interval,start,end):
    rows=[];previous=None
    for row in csv.reader(io.TextIOWrapper(stream,encoding='utf-8-sig',newline='')):
        if len(row)!=7:raise ValueError('ARCHIVE_CSV_SCHEMA')
        stamp=int(row[0]);values=[float(v) for v in row[1:6]];trades=int(row[6])
        if stamp%(interval*60) or (previous is not None and stamp<=previous):raise ValueError('ARCHIVE_CANDLE_ORDER')
        previous=stamp
        if not all(math.isfinite(x) for x in values):raise ValueError('ARCHIVE_NONFINITE')
        o,h,l,c,v=values
        if min(o,h,l,c)<=0 or v<0 or trades<0 or h<max(o,l,c) or l>min(o,h,c):raise ValueError('ARCHIVE_OHLC')
        if start<=stamp and stamp+interval*60<=end:rows.append([stamp*1000,o,h,l,c,v])
    return rows


def import_history(output,opener=RangeZip,*,resume=False):
    output=Path(output)
    prior=None
    if resume:
        if (output/'history-manifest.json').stat().st_size>1024*1024:raise ValueError('HISTORY_MANIFEST_BOUND')
        prior=json.loads((output/'history-manifest.json').read_text(encoding='utf-8'))
        if (prior.get('scope')!='FROZEN_KRAKEN_Q3_2026_HISTORY_V1' or prior.get('status')!='PARTIAL'
            or prior.get('pairs')!=list(PAIRS) or prior.get('warmup_start')!='2024-06-01T00:00:00+00:00'
            or prior.get('evaluation_start')!='2026-07-01T00:00:00+00:00'
            or prior.get('evaluation_end_exclusive')!='2026-10-01T00:00:00+00:00'):
            raise ValueError('MATCHING_PARTIAL_HISTORY_REQUIRED')
        if (type(prior.get('requests')) is not int or not 0<=prior['requests']<=MAX_READS
            or type(prior.get('downloaded_bytes')) is not int or not 0<=prior['downloaded_bytes']<=MAX_BYTES):
            raise ValueError('RESUME_BUDGET_INVALID')
        for name,item in prior['files'].items():
            path=output/'candles'/name
            if (name!=item['pair'].replace('/','_')+'-'+item['timeframe']+'.json'
                or not path.resolve().is_relative_to(output.resolve()) or path.is_symlink()
                or digest(path)!=item['sha256']):raise ValueError('RESUME_CANDLE_CHANGED')
        backup=output/('history-manifest-attempt-'+str(prior.get('resume_attempts',0))+'.json')
        with backup.open('x',encoding='utf-8') as stream:json.dump(prior,stream,indent=2)
    else:output.mkdir(parents=True,exist_ok=False)
    data=output/'candles'
    if not resume:data.mkdir()
    budget=Budget();manifest={'scope':'FROZEN_KRAKEN_Q3_2026_HISTORY_V1','status':'PARTIAL',
        'evaluation_start':'2026-07-01T00:00:00+00:00','evaluation_end_exclusive':'2026-10-01T00:00:00+00:00',
        'warmup_start':'2024-06-01T00:00:00+00:00','pairs':list(PAIRS),'files':{},'members':[],
        'archives':[],'orders':0,'grok_requests':0,'atlas_baseline_modified':False,
        'integrity':'HTTPS+ETag consistency+ZIP member CRC+selected output SHA256; full archive checksum not downloaded',
        'missing_intervals':'Source omits no-trade intervals; importer does not zero-fill or invent rows'}
    if prior:
        manifest=prior.copy();manifest['archives']=[]
        manifest.pop('error_type',None);manifest.pop('error_code',None)
        manifest['resume_attempts']=prior.get('resume_attempts',0)+1
        budget.reads=prior['requests'];budget.bytes=prior['downloaded_bytes']
    try:
        with ExitStack() as stack:
            sources=[]
            for title,urls in [('FULL',FULL),('Q3',Q3)]:
                remote=stack.enter_context(opener(urls,budget))
                manifest['archives'].extend(remote.parts)
                sources.append((title,stack.enter_context(zipfile.ZipFile(remote))))
            if prior and prior['archives'] and manifest['archives']!=prior['archives']:raise ValueError('ARCHIVE_IDENTIFIERS_CHANGED')
            for pair in PAIRS:
                for interval in INTERVALS:
                    tf={1:'1m',5:'5m',15:'15m',60:'1h',240:'4h',1440:'1d'}[interval]
                    name=pair.replace('/','_')+'-'+tf+'.json'
                    if name in manifest['files']:
                        print('ARCHIVE_FRAME_REUSED='+name,flush=True);continue
                    mapping={}
                    for title,archive in sources:
                        if title=='FULL' and interval==1:continue
                        item=select_member(archive,pair,interval)
                        with archive.open(item) as stream:
                            rows=parse_member(stream,interval,START if interval==1 else WARMUP,END)
                        manifest['members'].append({'archive':title,'pair':pair,'interval':interval,
                            'member':item.filename,'crc32':item.CRC,'compressed_bytes':item.compress_size,
                            'uncompressed_bytes':item.file_size,'selected_rows':len(rows)})
                        for row in rows:
                            if row[0] in mapping and mapping[row[0]]!=row:raise ValueError('ARCHIVE_OVERLAP_CONFLICT')
                            mapping[row[0]]=row
                        print(f'ARCHIVE_MEMBER_READY={title}:{pair}:{interval}',flush=True)
                    rows=[mapping[k] for k in sorted(mapping)]
                    if not rows:raise ValueError('EMPTY_ARCHIVE_FRAME')
                    step=interval*60000
                    gaps=sum((b[0]-a[0])//step-1 for a,b in zip(rows,rows[1:]))
                    before=sum(r[0]<START*1000 for r in rows)
                    evaluated=sum(START*1000<=r[0]<END*1000 for r in rows)
                    if (evaluated==0 or (interval!=1 and before<710)
                        or rows[-1][0]<(END-interval*60)*1000):raise ValueError('ARCHIVE_COVERAGE_INCOMPLETE')
                    tf={1:'1m',5:'5m',15:'15m',60:'1h',240:'4h',1440:'1d'}[interval]
                    name=pair.replace('/','_')+'-'+tf+'.json'
                    (data/name).write_text(json.dumps(rows,separators=(',',':'),allow_nan=False)+'\n',encoding='utf-8')
                    manifest['files'][name]={'pair':pair,'timeframe':tf,'sha256':digest(data/name),
                        'rows':len(rows),'startup_rows':before,'evaluation_rows':evaluated,
                        'missing_interval_count':gaps,'first_open_ms':rows[0][0],'last_open_ms':rows[-1][0]}
                    manifest['requests']=budget.reads;manifest['downloaded_bytes']=budget.bytes
                    write(output/'history-manifest.json',manifest)
        metadata={}
        for key,operation in [('assets','Assets'),('pairs','AssetPairs')]:
            budget.reserve(2*1024*1024)
            request=urllib.request.Request('https://api.kraken.com/0/public/'+operation+'?nonce='+str(time.time_ns()),headers={'User-Agent':'AtlasResearch/1.0'})
            with urllib.request.urlopen(request,timeout=30) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('MARKET_METADATA_BOUND')
            payload=json.loads(raw)
            if payload.get('error'):raise ValueError('MARKET_METADATA_ERROR')
            metadata[key]=payload
        write(output/'public-market-metadata.json',metadata)
        manifest['market_metadata_sha256']=digest(output/'public-market-metadata.json')
        manifest['status']='COMPLETE'
    except Exception as exc:
        manifest['error_type']=type(exc).__name__
        # Only our fixed diagnostics contain a bounded pair/timeframe; never raw responses.
        manifest['error_code']=str(exc)[:120] if isinstance(exc,ValueError) else 'ARCHIVE_READ_FAILED'
        raise
    finally:
        manifest['requests']=budget.reads;manifest['downloaded_bytes']=budget.bytes
        write(output/'history-manifest.json',manifest)
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--resume',action='store_true')
    a=p.parse_args();m=import_history(a.output,resume=a.resume)
    print('HISTORY_STATUS='+m['status']);print('HISTORY_DIRECTORY='+str(a.output.resolve()))


if __name__=='__main__':main()
