#!/usr/bin/env python3
"""Stream a consistent Headscale snapshot; run as root on the source VPS.

The archive is plaintext on stdout, so the caller MUST encrypt that stream.
No database, configuration or key contents are written to diagnostic output.
"""

import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml


os.umask(0o077)
CONFIG = Path('/etc/headscale')
DATA = Path('/var/lib/headscale')
DATABASE = DATA / 'db.sqlite'
DATABASE_FILES = {'db.sqlite', 'db.sqlite-wal', 'db.sqlite-shm'}


def files_and_hashes(root, excluded=()):
    result = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        if rel in excluded:
            continue
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RuntimeError('unexpected non-regular file in snapshot source')
        if path.is_file():
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def copy_file(source, root):
    if source.is_symlink() or not source.is_file():
        raise RuntimeError('unsupported proxy or unit file')
    destination = root / 'source' / source.relative_to('/')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def backup_sqlite(source_path, destination_path):
    source = sqlite3.connect('file:' + str(source_path) + '?mode=ro', uri=True, timeout=15)
    dest = sqlite3.connect(str(destination_path), timeout=15)
    try:
        source.backup(dest)
        if dest.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('database snapshot failed quick_check')
        dest.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        dest.execute('PRAGMA journal_mode=DELETE')
    finally:
        dest.close()
        source.close()
    if any(Path(str(destination_path) + suffix).exists() for suffix in ('-wal', '-shm')):
        raise RuntimeError('database snapshot has unexpected WAL sidecars')


def main():
    if os.geteuid() != 0:
        raise RuntimeError('source snapshot requires root')
    config = yaml.safe_load((CONFIG / 'config.yaml').read_text())
    if config.get('database', {}).get('type') != 'sqlite':
        raise RuntimeError('only SQLite installations are supported')
    if config.get('database', {}).get('sqlite', {}).get('path') != str(DATABASE):
        raise RuntimeError('database is outside the supported data location')
    if config.get('noise', {}).get('private_key_path') != str(DATA / 'noise_private.key'):
        raise RuntimeError('Noise key is outside the supported data location')
    if not DATABASE.is_file() or not (DATA / 'noise_private.key').is_file():
        raise RuntimeError('database or Noise key missing')

    with tempfile.TemporaryDirectory(prefix='headscale-dr-', dir='/tmp') as temp:
        root = Path(temp)
        config_before = files_and_hashes(CONFIG)
        data_before = files_and_hashes(DATA, DATABASE_FILES)
        dest_config = root / 'source/etc/headscale'
        dest_data = root / 'source/var/lib/headscale'
        shutil.copytree(CONFIG, dest_config, symlinks=True)
        shutil.copytree(DATA, dest_data, symlinks=True,
                        ignore=shutil.ignore_patterns(*DATABASE_FILES))

        # SQLite's Online Backup API takes a consistent snapshot, including
        # committed WAL pages, without copying a live database or stopping it.
        backup_sqlite(DATABASE, dest_data / DATABASE.name)

        if (config_before != files_and_hashes(CONFIG)
                or config_before != files_and_hashes(dest_config)
                or data_before != files_and_hashes(DATA, DATABASE_FILES)
                or data_before != files_and_hashes(dest_data, {DATABASE.name})):
            raise RuntimeError('configuration or identity changed during snapshot; retry')

        # Include only the Headscale-related reverse-proxy material, not the
        # rest of 1Panel. The caller stores the whole archive encrypted.
        for path in sorted(Path('/opt/1panel/www/conf.d').glob('hs*.conf')):
            copy_file(path, root)
        for site in sorted(Path('/opt/1panel/www/sites').glob('hs*')):
            paths = list((site / 'proxy').glob('*.conf'))
            paths.extend((site / 'ssl/fullchain.pem', site / 'ssl/privkey.pem'))
            for path in paths:
                if path.exists():
                    copy_file(path, root)
        for path in (Path('/lib/systemd/system/headscale.service'),
                     Path('/etc/systemd/system/headscale.service')):
            if path.exists() and not path.is_symlink():
                copy_file(path, root)

        headplane_database = None
        headplane_prepared_only = False
        hp_config, hp_data = Path('/etc/headplane'), Path('/var/lib/headplane')
        if hp_config.exists() or hp_data.exists():
            hp_db = hp_data / 'hp_persist.db'
            if not hp_config.is_dir() or not hp_data.is_dir():
                raise RuntimeError('incomplete Headplane installation; cannot snapshot it')
            if not hp_db.is_file() and any(hp_data.iterdir()):
                raise RuntimeError('unrecognized Headplane data; cannot snapshot it')
            hp_files = {'hp_persist.db', 'hp_persist.db-wal', 'hp_persist.db-shm'}
            hp_config_before = files_and_hashes(hp_config)
            hp_data_before = files_and_hashes(hp_data, hp_files)
            hp_config_dst = root / 'source/etc/headplane'
            hp_data_dst = root / 'source/var/lib/headplane'
            shutil.copytree(hp_config, hp_config_dst, symlinks=True)
            shutil.copytree(hp_data, hp_data_dst, symlinks=True,
                            ignore=shutil.ignore_patterns(*hp_files))
            if hp_db.is_file():
                backup_sqlite(hp_db, hp_data_dst / hp_db.name)
                headplane_database = 'source/var/lib/headplane/hp_persist.db'
            else:
                headplane_prepared_only = True
            if (hp_config_before != files_and_hashes(hp_config)
                    or hp_config_before != files_and_hashes(hp_config_dst)
                    or hp_data_before != files_and_hashes(hp_data, hp_files)
                    or hp_data_before != files_and_hashes(hp_data_dst, {hp_db.name})):
                raise RuntimeError('Headplane configuration changed during snapshot; retry')
            hp_unit = Path('/etc/systemd/system/headplane.service')
            if hp_unit.is_file() and not hp_unit.is_symlink():
                copy_file(hp_unit, root)

        version = subprocess.check_output(['headscale', 'version'], text=True).strip()
        meta = {
            'format': 1,
            'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'hostname': os.uname().nodename,
            'headscale_version': version,
            'database': 'source/var/lib/headscale/db.sqlite',
            'database_method': 'sqlite-online-backup',
            'sqlite_quick_check': 'ok',
            'noise_sha256': data_before['noise_private.key'],
            'headplane_database': headplane_database,
            'headplane_prepared_only': headplane_prepared_only,
        }
        (root / 'metadata.json').write_text(json.dumps(meta, sort_keys=True) + '\n')
        manifest = files_and_hashes(root)
        (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
            for path in sorted(root.iterdir()):
                archive.add(path, arcname=path.name, recursive=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('snapshot failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
