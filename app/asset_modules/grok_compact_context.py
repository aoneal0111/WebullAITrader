"""Lossless bounded candle packing; never a cache of trading decisions."""
from collections import OrderedDict
from hashlib import sha256
import json
from threading import Lock

from .daily_research_journal import canonical, safe

FORMAT = 'ATLAS_COLUMNAR_ROWS_V1'


class ContextPacker:
    """Cache immutable encoded bar windows only. Quotes/account/IDs stay current."""
    def __init__(self):
        self._cache=OrderedDict();self._lock=Lock()
        self.hits=0;self.misses=0

    def _rows(self, rows):
        if (not isinstance(rows,list) or not rows or len(rows)>64
                or any(not isinstance(row,dict) for row in rows)):
            return rows
        keys=set(rows[0])
        if any(set(row)!=keys for row in rows):return rows
        raw=canonical(rows)
        if len(raw)>65536:return rows
        identity=sha256(raw).hexdigest()
        with self._lock:
            old=self._cache.pop(identity,None)
            if old is not None:
                self.hits+=1;self._cache[identity]=old
                return json.loads(old)
        common={key:rows[0][key] for key in sorted(keys)
                if all(canonical(row[key])==canonical(rows[0][key]) for row in rows)}
        columns=sorted(keys-set(common))
        packed=dict(format=FORMAT,common=common,columns=columns,
                    rows=[[row[key] for key in columns] for row in rows])
        encoded=canonical(packed)
        # Small/sparse windows need not become larger just to be columnar.
        if len(encoded)>=len(raw):return rows
        with self._lock:
            self.misses+=1;self._cache[identity]=encoded
            while len(self._cache)>32:self._cache.popitem(last=False)
        return json.loads(encoded)

    def pack(self, context):
        source=safe(context)
        def visit(value,key=None):
            if isinstance(value,dict):
                if key=='completed_provider_bars':
                    return {symbol:self._rows(rows) for symbol,rows in value.items()}
                return {name:visit(item,name) for name,item in value.items()}
            if key=='completed_native_bars':return self._rows(value)
            if isinstance(value,list):return [visit(item) for item in value]
            return value
        return visit(source)
