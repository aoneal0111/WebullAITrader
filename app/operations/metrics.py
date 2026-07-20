from collections import defaultdict
from threading import RLock
class MetricsRegistry:
 def __init__(self):self._values=defaultdict(float);self._lock=RLock()
 def increment(self,name,value=1):
  with self._lock:self._values[name]+=value
 def set(self,name,value):
  with self._lock:self._values[name]=float(value)
 def render(self):
  with self._lock:return "".join(f"{k} {self._values[k]}\n" for k in sorted(self._values))
