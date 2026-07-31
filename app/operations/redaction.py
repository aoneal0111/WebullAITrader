from urllib.parse import urlsplit,urlunsplit
SENSITIVE=frozenset(("authorization","api_key","app_key","api_secret","app_secret","access_token","refresh_token","password","cookie","set-cookie","signature","private_key","account_id","account_number","x-app-key","x-signature","x-signature-nonce","x-access-token","signed_headers"))
def redact(value,key=""):
 normalized_key=key.lower().replace("-","_")
 if key.lower() in SENSITIVE or normalized_key in SENSITIVE or any(token in normalized_key for token in ("secret","signature","authorization","password","token")):return "[REDACTED]"
 if isinstance(value,dict):return {k:redact(v,str(k)) for k,v in value.items()}
 if isinstance(value,list):return [redact(v) for v in value]
 if isinstance(value,tuple):return tuple(redact(v) for v in value)
 if isinstance(value,BaseException):return {"error_type":type(value).__name__,"message":"operation failed"}
 if isinstance(value,str) and "://" in value:
  p=urlsplit(value);return urlunsplit((p.scheme,p.hostname or "",p.path,"",""))
 return value
