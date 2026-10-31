"""Compare allowlisted installed source bytes without importing the runtime.

File agreement does not prove the GUI loaded those bytes or establish effective
environment/configuration. No credentials, raw source, database or log is read.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path

FILES = (
    'app/market/scalp_session.py',
    'app/strategies/warrior_momentum/configuration.py',
    'app/strategies/warrior_momentum/quick_scalper.py',
    'app/strategies/warrior_momentum/quick_scalper_runtime.py',
    'app/strategies/warrior_momentum/autonomous_paper.py',
    'app/strategies/warrior_momentum/profit_retention.py',
    'app/strategies/warrior_momentum/profit_retention_shadow.py',
    'app/strategies/warrior_momentum/forward_runtime.py',
    'app/paper_gateway/gateway.py',
    'app/paper_gateway/order_validity.py',
    'app/asset_modules/controlled_paper_service.py',
)


def fingerprint(root,relative):
    root=Path(root).resolve()
    path=(root/relative).resolve()
    if not path.is_relative_to(root):
        return {'status':'OUTSIDE_ROOT'}
    try:
        if path.stat().st_size>2*1024*1024:
            return {'status':'SIZE_LIMIT'}
        with path.open('rb') as stream:raw=stream.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:
            return {'status':'SIZE_LIMIT'}
        # Windows CRLF and LF are the same source for this comparison.
        canonical=raw.replace(b'\r\n',b'\n')
        return dict(status='READ',sha256_lf=sha256(canonical).hexdigest())
    except FileNotFoundError:
        return {'status':'MISSING'}
    except OSError:
        return {'status':'READ_ERROR'}


def compare_sources(installed_root,review_root):
    rows=[]
    for name in FILES:
        installed=fingerprint(installed_root,name);review=fingerprint(review_root,name)
        status=('MATCH' if installed['sha256_lf']==review['sha256_lf'] else 'DIFFERENT') if (
            installed['status']==review['status']=='READ') else 'UNAVAILABLE'
        rows.append(dict(path=name,installed=installed,review=review,comparison=status))
    return dict(schema_version='INSTALLED_SOURCE_AUDIT_V1',execution_authorized=False,
        runtime_loaded_version_verified=False,effective_configuration_verified=False,
        all_files_match_review=all(r['comparison']=='MATCH' for r in rows),files=rows,
        limitations=['Only fixed source paths are read; credentials and environment values are excluded.',
            'Line endings are normalized; file agreement is not proof of the loaded process version.',
            'Custom session configuration and environment overrides are not inspected.'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed-root',type=Path,required=True)
    parser.add_argument('--review-root',type=Path,required=True)
    args=parser.parse_args(argv)
    print(json.dumps(compare_sources(args.installed_root,args.review_root),indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
