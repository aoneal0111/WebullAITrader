from typing import Protocol

from app.account_information.models import AccountInformationRequest, AccountInformationResult, BrokerNeutralAccountInformation


class BrokerAccountGateway(Protocol):
    def get_account_information(self, request: AccountInformationRequest) -> BrokerNeutralAccountInformation: ...


class AccountInformationRuntime(Protocol):
    def get_account_information(self, request: AccountInformationRequest) -> AccountInformationResult: ...
