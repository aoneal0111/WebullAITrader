from app.webull_authentication_config import DeterministicWebullAuthenticationProfileLoader,WebullAuthenticationProfileLoader
def test_exact_loader_interface():
 assert {n for n in WebullAuthenticationProfileLoader.__dict__ if not n.startswith("_")}=={"load"};assert {n for n in DeterministicWebullAuthenticationProfileLoader.__dict__ if not n.startswith("_")}=={"load"}
