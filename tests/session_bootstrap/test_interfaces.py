from app.session_bootstrap import DeterministicSessionBootstrapRuntime,SessionBootstrapRuntime
def test_exact_public_interface():
 assert {n for n in SessionBootstrapRuntime.__dict__ if not n.startswith("_")}=={"bootstrap"};assert {n for n in DeterministicSessionBootstrapRuntime.__dict__ if not n.startswith("_")}=={"bootstrap"}
