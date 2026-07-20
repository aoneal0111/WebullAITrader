from typing import Protocol
class CredentialProvider(Protocol):
 def get_api_key(self)->str:...
 def get_api_secret(self)->str:...
 def get_account_id(self)->str:...
class EnvironmentCredentialProvider:
 def __init__(self,environment):self.__environment=environment
 def _get(self,key):
  value=self.__environment.get(key)
  if not isinstance(value,str) or not value.strip():raise ValueError("required credential is unavailable")
  return value
 def get_api_key(self):return self._get("WEBULL_API_KEY")
 def get_api_secret(self):return self._get("WEBULL_API_SECRET")
 def get_account_id(self):return self._get("WEBULL_ACCOUNT_ID")
