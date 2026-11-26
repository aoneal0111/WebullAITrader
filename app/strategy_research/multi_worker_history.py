"""Explicit public candle download for the offline multi-worker experiment."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1, sha256
import json
from pathlib import Path
from urllib.request import urlopen


def capture(folder, lock):
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=False)
    selected=sorted((x for x in lock['files'] if x['path'].endswith('-1d.feather')),
                    key=lambda x:x['path'])[:50]
    records=[]
    def fetch(item):
        url=('https://raw.githubusercontent.com/DigiTuccar/HistoricalDataForTradeBacktest/'
             +lock['commit']+'/'+item['path'])
        with urlopen(url,timeout=45) as response:
            raw=response.read(item['bytes']+1)
        if (len(raw)!=item['bytes'] or sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
                !=item['git_blob_sha']):
            raise ValueError('PINNED_CANDLE_HASH_MISMATCH')
        path=folder/Path(item['path']).name
        with path.open('xb') as stream:stream.write(raw)
        return dict(**item,sha256=sha256(raw).hexdigest())
    status='PARTIAL'
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for record in pool.map(fetch,selected):
                records.append(record)
        status='VERIFIED_PINNED_HISTORY'
    finally:
        # Completed individual files survive an interrupted/failed capture.
        report=dict(status=status,source_commit=lock['commit'],selected_files=len(selected),
                    files=records,orders=0,grok_requests=0)
        (folder/'capture.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    lock=json.loads((Path(__file__).resolve().parents[1]/'strategy_lab'/'nfi-history-source-lock.json').read_text())
    result=capture(a.output,lock)
    print('VERIFIED_DAILY_HISTORY_FILES='+str(len(result['files'])))


if __name__=='__main__':main()
