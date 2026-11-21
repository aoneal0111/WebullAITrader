"""Fetch only pinned candle blobs from NFI's documented historical-data source.

Repository metadata is not market data. Verify the full required file set before
checkout, then verify byte sizes, Git identities and Arrow framing afterward.
"""
import argparse
from datetime import UTC, datetime
from hashlib import sha1, sha256
import json
from pathlib import Path
import re
import shutil
import subprocess

from .nfi_source_reproduction import COMMIT, digest, write

REPOSITORY = 'https://github.com/DigiTuccar/HistoricalDataForTradeBacktest.git'
LOCK = Path(__file__).with_name('nfi-history-source-lock.json')
MAX_BYTES = 3 * 1024 ** 3


def git(root, *args, input=None):
    # No shell and no executable code from the history repository is invoked.
    result = subprocess.run(['git', '-c', 'core.autocrlf=false', '-c',
        'core.hooksPath=' + str(Path(root).resolve() / '.disabled-hooks'),
        '-C', str(root), *args], input=input, text=True, check=False,
        stdout=subprocess.PIPE if args[0] in {'rev-parse', 'ls-tree'} else None)
    if result.returncode:
        raise RuntimeError('HISTORY_GIT_COMMAND_FAILED:' + args[0])
    return result.stdout or ''


def plan(work, lock_path=LOCK):
    work = Path(work)
    manifest = json.loads((work / 'source-run-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('source_commit') != COMMIT:
        raise ValueError('PREPARED_NFI_SOURCE_ID_REQUIRED')
    for name, expected in manifest['prepared_hashes'].items():
        path = work / name
        if path.is_symlink() or digest(path) != expected:
            raise ValueError('PREPARED_SOURCE_OR_CONFIG_CHANGED')
    lock = json.loads(Path(lock_path).read_text(encoding='utf-8'))
    if (lock.get('scope') != 'PINNED_NFI_DOCUMENTED_HISTORY_V1'
        or lock.get('repository') != REPOSITORY
        or not re.fullmatch('[0-9a-f]{40}', lock.get('commit', ''))):
        raise ValueError('REVIEWED_HISTORY_IDENTITY_REQUIRED')
    wanted = {'binance/' + pair.replace('/', '_') + '-' + tf + '.feather'
              for pair in set(manifest['pairs']) | {'BTC/USDT'} for tf in manifest['timeframes']}
    files = lock['files']
    if (len(files) != lock['file_count'] or len(files) > 800
        or len({f['path'] for f in files}) != len(files)
        or {f['path'] for f in files} != wanted):
        raise ValueError('ALL_PREPARED_PAIR_TIMEFRAMES_MUST_BE_LOCKED')
    for f in files:
        if (not re.fullmatch(r'binance/[A-Z0-9_]+-(5m|15m|1h|4h|1d)\.feather', f['path'])
            or not re.fullmatch('[0-9a-f]{40}', f.get('git_blob_sha', ''))
            or type(f.get('bytes')) is not int or not 12 <= f['bytes'] <= 128 * 1024 ** 2):
            raise ValueError('BOUNDED_PINNED_CANDLE_BLOB_REQUIRED')
    total = sum(f['bytes'] for f in files)
    if total != lock['total_bytes'] or total > MAX_BYTES:
        raise ValueError('HISTORY_DOWNLOAD_SIZE_BOUND')
    return lock


def verify_tree(text, files):
    entries = {}
    for line in text.splitlines():
        header, path = line.split('\t', 1)
        mode, kind, identity = header.split()
        entries[path] = (mode, kind, identity)
    for f in files:
        if entries.get(f['path']) != ('100644', 'blob', f['git_blob_sha']):
            raise ValueError('PINNED_HISTORY_TREE_MISMATCH:' + f['path'])


def verify_file(path, spec):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != spec['bytes']:
        raise ValueError('PINNED_HISTORY_FILE_SIZE_MISMATCH:' + spec['path'])
    # Git's content identity is SHA1("blob <size>\0" + bytes), not plain SHA1.
    gh = sha1(('blob ' + str(spec['bytes']) + '\0').encode())
    sh = sha256()
    with path.open('rb') as stream:
        first = stream.read(6)
        stream.seek(0)
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            gh.update(data); sh.update(data)
        stream.seek(-6, 2)
        last = stream.read(6)
    if gh.hexdigest() != spec['git_blob_sha'] or first != b'ARROW1' or last != b'ARROW1':
        raise ValueError('PINNED_HISTORY_CONTENT_MISMATCH:' + spec['path'])
    return {'bytes': spec['bytes'], 'git_blob_sha': gh.hexdigest(), 'sha256': sh.hexdigest()}


def download(work, output, *, lock_path=LOCK):
    work, output = Path(work).resolve(), Path(output).resolve()
    lock = plan(work, lock_path)
    if output.exists():
        raise FileExistsError('NEW_HISTORY_DIRECTORY_REQUIRED; PRESERVE_PREVIOUS_IMPORT')
    # Git object storage plus checked-out files; refuse before downloading on low disk.
    if shutil.disk_usage(output.parent).free < 2 * lock['total_bytes'] + 512 * 1024 ** 2:
        raise ValueError('HISTORY_NEEDS_OBJECT_AND_WORKTREE_DISK_SPACE')
    output.mkdir()
    summary = {'scope': 'PINNED_NFI_HISTORY_IMPORT_V1', 'status': 'PARTIAL',
        'repository': lock['repository'], 'commit': lock['commit'],
        'lock_sha256': digest(lock_path), 'expected_files': len(lock['files']),
        'expected_bytes': lock['total_bytes'], 'files': {}, 'orders': 0, 'grok_requests': 0,
        'publication_data_identity_attested': False,
        'source_note': 'NFI documented download-script repository; not attested as the earlier container snapshot'}
    try:
        git(output, 'init')
        git(output, 'remote', 'add', 'origin', lock['repository'])
        git(output, 'fetch', '--depth=1', '--filter=blob:none', '--no-tags', 'origin', lock['commit'])
        if git(output, 'rev-parse', 'FETCH_HEAD').strip() != lock['commit']:
            raise ValueError('PINNED_HISTORY_COMMIT_MISMATCH')
        # Deliberately no -l: asking Git for every blob's size would hydrate unrelated data.
        verify_tree(git(output, 'ls-tree', '-r', '--full-tree', 'FETCH_HEAD', 'binance'), lock['files'])
        git(output, 'update-ref', 'HEAD', lock['commit'])
        git(output, 'sparse-checkout', 'init', '--no-cone')
        print(f'HISTORY_SELECTED_FILES={len(lock["files"])}; BYTES={lock["total_bytes"]}', flush=True)
        git(output, 'sparse-checkout', 'set', '--no-cone', '--stdin',
            input=''.join('/' + f['path'] + '\n' for f in lock['files']))
        git(output, 'checkout', '--detach', lock['commit'])
        for f in lock['files']:
            summary['files'][f['path']] = verify_file(output / f['path'], f)
            if len(summary['files']) % 25 == 0:
                print('HISTORY_VERIFIED_FILES=' + str(len(summary['files'])), flush=True)
        summary['status'] = 'VERIFIED_PINNED_CANDLE_FILES'
    except Exception as exc:
        summary['error_type'] = type(exc).__name__
        raise
    finally:
        summary['finished_at'] = datetime.now(UTC).isoformat()
        write(output / 'history-import-summary.json', summary)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--download', action='store_true')
    a = p.parse_args()
    lock = plan(a.work)
    print(f'HISTORY_PLAN_FILES={len(lock["files"])}; BYTES={lock["total_bytes"]}', flush=True)
    if a.download:
        r = download(a.work, a.output)
        print('HISTORY_IMPORT=' + r['status'])
        print('UPLOAD_SUMMARY=' + str(a.output / 'history-import-summary.json'))


if __name__ == '__main__':
    main()
