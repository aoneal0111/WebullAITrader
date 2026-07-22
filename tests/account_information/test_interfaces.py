from inspect import signature
from app.account_information import AccountInformationRuntime, BrokerAccountGateway, DeterministicAccountInformationRuntime


def test_public_runtime_has_exact_operation():
    public={n for n in dir(DeterministicAccountInformationRuntime) if not n.startswith("_")}
    assert public=={"get_account_information"}
    assert list(signature(AccountInformationRuntime.get_account_information).parameters)==["self","request"]
    assert list(signature(BrokerAccountGateway.get_account_information).parameters)==["self","request"]
