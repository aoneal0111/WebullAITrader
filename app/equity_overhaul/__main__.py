"""Offline release-status diagnostic. Does not construct or start Atlas."""
import json
from .admission import production_policy
from .strategies import RULES,rule_fingerprint


def main():
    print(json.dumps(dict(scope='OFFLINE_EQUITY_RELEASE_STATUS',orders=0,network_requests=0,
        **production_policy().status(),
        candidates=[dict(policy_id=k,rule_fingerprint=rule_fingerprint(k),**v) for k,v in RULES.items()]),indent=2))


if __name__=='__main__':main()
