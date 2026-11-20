#!/usr/bin/env python3
"""Verify an operator-decrypted archive in an isolated target directory."""

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

import yaml


MAX_BYTES = 128 * 1024 * 1024


def rewrite_paths(value, root):
    if isinstance(value, dict):
        return {key: rewrite_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_paths(item, root) for item in value]
    if isinstance(value, str) and value.startswith('/'):
        for prefix in ('/etc/headscale', '/var/lib/headscale'):
            if value == prefix or value.startswith(prefix + '/'):
                return str(root / 'source' / value.lstrip('/'))
        if value.startswith('/var/run/headscale/'):
            return str(root / 'scratch' / value.lstrip('/'))
        raise RuntimeError('configuration references a path outside the snapshot')
    return value


def main():
    if os.geteuid() != 0:
        raise RuntimeError('target preparation requires root')
    encrypted_data = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(encrypted_data) > MAX_BYTES:
        raise RuntimeError('snapshot exceeds size limit')
    base = Path('/var/lib/headscale-dr/staged')
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    root = base / ('snapshot-' + uuid.uuid4().hex[:16])
    root.mkdir(mode=0o700)
    success = False
    try:
        contents = {}
        names = set()
        total = 0
        with tarfile.open(fileobj=io.BytesIO(encrypted_data), mode='r:gz') as archive:
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or member.name in names:
                    raise RuntimeError('unsafe or duplicate archive path')
                names.add(member.name)
                target = root.joinpath(*name.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                elif member.isfile():
                    total += member.size
                    if total > MAX_BYTES:
                        raise RuntimeError('unpacked archive exceeds size limit')
                    data = archive.extractfile(member).read()
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with target.open('xb') as output:
                        output.write(data)
                    os.chmod(target, member.mode & 0o700 or 0o600)
                    contents[member.name] = data
                else:
                    raise RuntimeError('archive contains a link or special file')
        manifest = json.loads(contents.pop('manifest.json'))
        actual = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
        if manifest != actual:
            raise RuntimeError('snapshot manifest mismatch')
        metadata = json.loads(contents['metadata.json'])
        expected_version = metadata['headscale_version'].splitlines()[0]
        installed_version = subprocess.check_output(['headscale', 'version'], text=True).splitlines()[0]
        if installed_version != expected_version:
            raise RuntimeError('installed Headscale version differs from snapshot')
        if metadata.get('database_method') != 'sqlite-online-backup':
            raise RuntimeError('unsupported Headscale backup method')
        key = root / 'source/var/lib/headscale/noise_private.key'
        if hashlib.sha256(key.read_bytes()).hexdigest() != metadata['noise_sha256']:
            raise RuntimeError('Noise identity fingerprint mismatch')
        databases = ['source/var/lib/headscale/db.sqlite']
        if metadata.get('headplane_database'):
            if metadata['headplane_database'] != 'source/var/lib/headplane/hp_persist.db':
                raise RuntimeError('unexpected Headplane database location')
            databases.append(metadata['headplane_database'])
        for relative in databases:
            path = root / relative
            conn = sqlite3.connect('file:' + str(path) + '?mode=ro&immutable=1', uri=True)
            try:
                if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('isolated SQLite quick_check failed')
            finally:
                conn.close()
        # configtest in this Headscale version can modify the SQLite file.
        # Never point it at the staged recovery set: use a disposable copy.
        run_root = Path('/run/headscale-dr')
        run_root.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='configtest-', dir=run_root) as temp:
            isolated_root = Path(temp)
            for subtree in ('etc/headscale', 'var/lib/headscale'):
                source = root / 'source' / subtree
                destination = isolated_root / 'source' / subtree
                destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                shutil.copytree(source, destination, symlinks=False)
            config_path = isolated_root / 'source/etc/headscale/config.yaml'
            config = yaml.safe_load(config_path.read_text())
            isolated = rewrite_paths(config, isolated_root)
            (isolated_root / 'scratch/var/run/headscale').mkdir(parents=True, mode=0o700)
            check_config = isolated_root / 'configtest.yaml'
            check_config.write_text(yaml.safe_dump(isolated, sort_keys=False))
            result = subprocess.run(['headscale', '-c', str(check_config), 'configtest'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if result.returncode:
                raise RuntimeError('isolated Headscale configtest failed')
        for name, expected in manifest.items():
            path = root / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError('staged snapshot changed during verification')
        service = subprocess.run(['systemctl', 'is-active', 'headscale'],
                                 stdout=subprocess.PIPE, text=True, timeout=10).stdout.strip()
        success = True
        print(json.dumps({'staging_dir': str(root), 'headscale_version': installed_version,
                          'noise_sha256_prefix': metadata['noise_sha256'][:12],
                          'sqlite_quick_check': 'ok', 'configtest': 'ok',
                          'legacy_headscale_service': service,
                          'authority_instance_started': False}, sort_keys=True))
    finally:
        if not success:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('prepare failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
