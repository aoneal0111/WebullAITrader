from urllib.parse import urlsplit,urlunsplit
SENSITIVE=frozenset(("authorization","api_key","api_secret","access_token","refresh_token","password","cookie","set-cookie","signature","private_key","account_id","account_number","x-app-key","x-signature","x-access-token"))
def redact(value,key=""):
 if key.lower() in SENSITIVE:return "[REDACTED]"
 if isinstance(value,dict):return {k:redact(v,str(k)) for k,v in value.items()}
 if isinstance(value,list):return [redact(v) for v in value]
 if isinstance(value,tuple):return tuple(redact(v) for v in value)
 if isinstance(value,BaseException):return {"error_type":type(value).__name__,"message":"operation failed"}
 if isinstance(value,str) and "://" in value:
  p=urlsplit(value);return urlunsplit((p.scheme,p.hostname or "",p.path,"",""))
 return value
