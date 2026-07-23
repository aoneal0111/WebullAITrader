from app.http_runtime import HTTPMethod
from app.webull_authentication import WebullAuthenticationProfile,WebullAuthenticationPolicy
def profile(**changes):
 values=dict(profile_id="synthetic-profile-v1",endpoint_url="https://mock.invalid/authenticate",http_method=HTTPMethod.POST,username_field="synthetic_user",password_field="synthetic_secret",device_id_field="synthetic_device",username_reference="username_ref",password_reference="password_ref",device_reference="device_ref",success_field_path=("result","state"),success_values=("accepted",),failure_message_field_path=("result","message"),verification_output_field_paths=(("decision_code",("result","code")),),required_response_headers=("x-synthetic-profile",),static_headers=(("accept","application/json"),("x-fixture","synthetic")),metadata={"synthetic":True})
 values.update(changes);return WebullAuthenticationProfile(**values)
def policy(**changes):
 values=dict(enabled=True);values.update(changes);return WebullAuthenticationPolicy(**values)
SUCCESS={"result":{"state":"accepted","code":"fixture-ok","ignored_token":"must-not-persist"}}
REJECTED={"result":{"state":"rejected","code":"fixture-no","message":"synthetic rejection"}}
MALFORMED={"result":[]}
