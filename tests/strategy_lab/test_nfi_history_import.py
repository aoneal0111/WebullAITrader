from hashlib import sha1
import json
from pathlib import Path
import subprocess

import pytest

from app.strategy_lab import nfi_history_import as m


@pytest.fixture
def source(tmp_path, monkeypatch):
    repo = tmp_path / 'upstream'; repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init')
    (repo / 'binance').mkdir()
    # Framing is synthetic: importer verifies identity/framing, not OHLCV semantics.
    data = b'ARROW1' + b'synthetic-test-candles' + b'ARROW1'
    p = 'binance/BTC_USDT-5m.feather'
    (repo / p).write_bytes(data)
    (repo / 'UNSELECTED.txt').write_text('must not check out unrelated contents')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
    commit = git('rev-parse', 'HEAD')
    identity = sha1(('blob ' + str(len(data)) + '\0').encode() + data).hexdigest()
    monkeypatch.setattr(m, 'REPOSITORY', str(repo))
    lock = {'scope': 'PINNED_NFI_DOCUMENTED_HISTORY_V1', 'repository': str(repo),
            'commit': commit, 'file_count': 1, 'total_bytes': len(data),
            'files': [{'path': p, 'bytes': len(data), 'git_blob_sha': identity}]}
    lock_path = tmp_path / 'lock.json'; m.write(lock_path, lock)
    work = tmp_path / 'work'; work.mkdir()
    (work / 'config.json').write_text('{}')
    m.write(work / 'source-run-manifest.json', {'source_commit': m.COMMIT,
        'pairs': ['BTC/USDT'], 'timeframes': ['5m'],
        'prepared_hashes': {'config.json': m.digest(work / 'config.json')}})
    return work, lock_path, lock, data


def test_real_git_sparse_checkout_copies_only_required_candles(source, tmp_path):
    work, lock_path, lock, data = source
    output = tmp_path / 'history'
    r = m.download(work, output, lock_path=lock_path)
    assert r['status'] == 'VERIFIED_PINNED_CANDLE_FILES'
    assert (output / lock['files'][0]['path']).read_bytes() == data
    assert not (output / 'UNSELECTED.txt').exists()
    assert len(r['files']) == 1 and not r['publication_data_identity_attested']
    with pytest.raises(FileExistsError):
        m.download(work, output, lock_path=lock_path)


def test_empty_checkout_is_not_a_completed_import(source, tmp_path):
    work, lock_path, lock, data = source
    with pytest.raises(ValueError, match='SIZE'):
        m.verify_file(tmp_path / 'absent.feather', lock['files'][0])
    with pytest.raises(ValueError, match='TREE_MISMATCH'):
        m.verify_tree('100644 blob ' + '0' * 40 + '\t' + lock['files'][0]['path'], lock['files'])


def test_git_lfs_pointer_and_changed_candles_refused(source, tmp_path):
    _, _, lock, data = source
    path = tmp_path / 'file.feather'
    path.write_bytes(b'x' * len(data))
    with pytest.raises(ValueError, match='CONTENT'):
        m.verify_file(path, lock['files'][0])
    path.write_text('version https://git-lfs.github.com/spec/v1\n')
    with pytest.raises(ValueError):
        m.verify_file(path, lock['files'][0])


def test_partial_lock_or_modified_strategy_is_refused_before_download(source):
    work, lock_path, lock, _ = source
    lock['files'] = []; lock['file_count'] = 0
    m.write(lock_path, lock)
    with pytest.raises(ValueError, match='ALL_PREPARED'):
        m.plan(work, lock_path)
    (work / 'config.json').write_text('{"stake_amount":50}')
    with pytest.raises(ValueError, match='CHANGED'):
        m.plan(work, lock_path)


def test_large_or_unsafe_source_lock_refused(source):
    work, lock_path, lock, _ = source
    lock['files'][0]['bytes'] = m.MAX_BYTES + 1
    m.write(lock_path, lock)
    with pytest.raises(ValueError, match='BOUNDED'):
        m.plan(work, lock_path)


def test_git_tree_read_does_not_request_sizes_that_fetch_every_blob(monkeypatch, tmp_path):
    calls = []
    class Result:
        returncode = 0
        stdout = ''
    def run(args, **kwargs):
        calls.append(args)
        return Result()
    monkeypatch.setattr(m.subprocess, 'run', run)
    m.git(tmp_path, 'ls-tree', '-r', '--full-tree', 'FETCH_HEAD', 'binance')
    assert '-l' not in calls[0] and '-rl' not in calls[0]


def test_checked_in_lock_contains_all_800_files_with_bounded_download():
    d = json.loads(m.LOCK.read_text())
    assert len(d['files']) == d['file_count'] == 800
    assert len({f['path'] for f in d['files']}) == 800
    assert d['total_bytes'] == sum(f['bytes'] for f in d['files']) == 2496224192
    assert d['total_bytes'] < m.MAX_BYTES
