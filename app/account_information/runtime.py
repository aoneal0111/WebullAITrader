from decimal import Decimal

from app.account_information.exceptions import AccountInformationDependencyError
from app.account_information.models import (
    AccountInformationCriteriaResult, AccountInformationDecision,
    AccountInformationResult, BrokerNeutralAccountInformation,
)
from app.account_information.validation import validate_dependencies, validate_request
from app.session.models import SessionSnapshot, SessionStatus


class DeterministicAccountInformationRuntime:
    def __init__(self, session_manager, broker_gateway, policy):
        validate_dependencies(session_manager, broker_gateway, policy)
        self._session_manager = session_manager
        self._broker_gateway = broker_gateway
        self._policy = policy

    def get_account_information(self, request):
        request = validate_request(request)
        if not self._policy.enabled:
            return self._result(request, AccountInformationDecision.DISABLED, None, (False, False, False))
        try:
            snapshot = self._session_manager.state()
        except Exception as exc:
            raise AccountInformationDependencyError("session manager failed to resolve session") from exc
        if not isinstance(snapshot, SessionSnapshot):
            raise AccountInformationDependencyError("session manager returned invalid snapshot")
        valid = (snapshot.status is SessionStatus.ACTIVE and snapshot.session is not None
                 and snapshot.session.identifier.value == request.session_id)
        if not valid:
            return self._result(request, AccountInformationDecision.SESSION_INVALID, None, (True, False, False))
        try:
            account = self._broker_gateway.get_account_information(request)
        except Exception:
            return self._result(request, AccountInformationDecision.GATEWAY_FAILURE, None, (True, True, False))
        if not isinstance(account, BrokerNeutralAccountInformation):
            raise AccountInformationDependencyError("broker gateway returned invalid account information")
        return self._result(request, AccountInformationDecision.SUCCESS, account, (True, True, True))

    def _result(self, request, decision, account, passed):
        names = ("policy_enabled", "session_active", "gateway_succeeded")
        details = ("account information policy enabled", "matching active session resolved",
                   "broker gateway returned broker-neutral account information")
        criteria = tuple(AccountInformationCriteriaResult(n, ok, detail)
                         for n, ok, detail in zip(names, passed, details))
        values = (account.account_id, account.account_type, account.account_status,
                  account.buying_power, account.cash_balance, account.equity, account.currency) if account else (
                     "", "", "", Decimal("0"), Decimal("0"), Decimal("0"), "")
        return AccountInformationResult(request.request_id, request.session_id, decision, *values, criteria,
                                        {"deterministic": True, "policy_version": self._policy.version})
