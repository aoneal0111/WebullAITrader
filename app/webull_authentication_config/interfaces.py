from typing import Protocol
from app.webull_authentication_config.models import WebullAuthenticationProfileConfigurationResult
class WebullAuthenticationProfileLoader(Protocol):
 def load(self,configuration)->WebullAuthenticationProfileConfigurationResult:...
