"""Shared code prepended to remote helpers; never tests against the live DB."""
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
import yaml


def isolated_configtest(config_path):
    config = yaml.safe_load(Path(config_path).read_text())
    if config.get('database', {}).get('type') != 'sqlite' or config['database']['sqlite']['path'] != '/var/lib/headscale/db.sqlite':
        raise RuntimeError('isolated configtest only supports the expected SQLite layout')
    for base in (Path('/etc/headscale'), Path('/var/lib/headscale')):
        if any(path.is_symlink() for path in base.rglob('*')):
            raise RuntimeError('symlinks in Headscale configuration/data are unsupported')
    run_root = Path('/run/headscale-dr')
    run_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='configtest-', dir=run_root) as temp:
        root = Path(temp)
        shutil.copytree('/etc/headscale', root / 'etc/headscale')
        shutil.copytree('/var/lib/headscale', root / 'var/lib/headscale',
                        ignore=shutil.ignore_patterns('db.sqlite', 'db.sqlite-wal', 'db.sqlite-shm'))
        source = sqlite3.connect('file:/var/lib/headscale/db.sqlite?mode=ro', uri=True, timeout=15)
        target = sqlite3.connect(str(root / 'var/lib/headscale/db.sqlite'), timeout=15)
        try:
            source.backup(target)
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('configtest SQLite backup failed quick_check')
        finally:
            target.close()
            source.close()
        def rewrite(value):
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, str) and value.startswith('/'):
                for prefix in ('/etc/headscale', '/var/lib/headscale', '/var/run/headscale'):
                    if value == prefix or value.startswith(prefix + '/'):
                        return str(root / value.lstrip('/'))
                raise RuntimeError('configtest references an unsupported absolute path')
            return value
        (root / 'var/run/headscale').mkdir(parents=True, mode=0o700)
        check = root / 'config.yaml'
        check.write_text(yaml.safe_dump(rewrite(config), sort_keys=False))
        result = subprocess.run(['headscale', '-c', str(check), 'configtest'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode:
            raise RuntimeError('isolated Headscale configtest failed')
