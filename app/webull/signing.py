from __future__ import annotations
import base64,hashlib,hmac
from datetime import datetime,timezone
from urllib.parse import quote,urlparse
from typing import Protocol
class Clock(Protocol):
 def __call__(self)->datetime:...
class NonceProvider(Protocol):
 def __call__(self)->str:...
class WebullRequestSigner:
 def __init__(self,credentials,host,clock,nonce_provider,*,maximum_clock_skew_seconds=30,reference_clock=None,access_token_provider=None):
  self.credentials,self.host,self.clock,self.nonce=credentials,urlparse(host).netloc or host,clock,nonce_provider
  self.maximum_skew=maximum_clock_skew_seconds;self.reference_clock=reference_clock;self.access_token_provider=access_token_provider
 def headers(self,method,path,query,body):
  del method
  now=self.clock()
  if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError("signing clock must be timezone-aware")
  if self.reference_clock and abs((now-self.reference_clock()).total_seconds())>self.maximum_skew:raise ValueError("signing clock skew exceeds limit")
  key=self.credentials.get_api_key();secret=self.credentials.get_api_secret();nonce=self.nonce()
  if not key.strip() or not secret.strip() or not isinstance(nonce,str) or not nonce.strip():raise ValueError("signing credentials are unavailable")
  timestamp=now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
  signing={"host":self.host,"x-app-key":key,"x-signature-algorithm":"HMAC-SHA1","x-signature-nonce":nonce,"x-signature-version":"1.0","x-timestamp":timestamp}
  params={str(k):str(v) for k,v in query};params.update(signing)
  joined="&".join(f"{k}={params[k]}" for k in sorted(params));base=f"{path}&{joined}"
  if body:base+="&"+hashlib.md5(body).hexdigest().upper()
  encoded=quote(base,safe="");signature=base64.b64encode(hmac.new((secret+"&").encode(),encoded.encode(),hashlib.sha1).digest()).decode()
  headers={k:v for k,v in signing.items() if k!="host"};headers.update({"x-signature":signature,"x-version":"v2"})
  if self.access_token_provider:
   token=self.access_token_provider()
   if token:headers["x-access-token"]=token
  return headers
