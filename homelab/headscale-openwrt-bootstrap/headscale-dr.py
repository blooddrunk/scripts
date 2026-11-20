#!/usr/bin/env python3
"""Operator-side encrypted Headscale snapshots and verification."""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shlex
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


HELPER = Path(__file__).resolve().parent / 'lib/dr-snapshot-vps.py'
ROUTER_HELPER = Path(__file__).resolve().parent / 'lib/dr-snapshot-openwrt.sh'
SITE_HELPER = Path(__file__).resolve().parent / 'lib/dr-install-site.py'
CONTROL_HELPER = Path(__file__).resolve().parent / 'lib/dr-set-control.py'
PREPARE_HELPER = Path(__file__).resolve().parent / 'lib/dr-prepare-vps.py'
DOCTOR_HELPER = Path(__file__).resolve().parent / 'lib/dr-doctor-vps.py'
REHEARSE_HELPER = Path(__file__).resolve().parent / 'lib/dr-rehearse-vps.py'
CONFIGTEST_HELPER = Path(__file__).resolve().parent / 'lib/dr-isolated-configtest.py'
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


def ssh_command(program='ssh'):
    """Optionally use an operator-supplied SSH config, including jump hosts."""
    config = os.environ.get('HEADSCALE_DR_SSH_CONFIG')
    return [program] + (['-F', config] if config else [])


def private_file(path):
    path = Path(path).expanduser()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('passphrase must be an owner-only regular file')
    if not path.read_bytes().strip():
        raise RuntimeError('passphrase file is empty')
    return path.resolve()


def gpg_args(passphrase):
    return ['gpg', '--batch', '--no-tty', '--pinentry-mode', 'loopback',
            '--passphrase-file', str(passphrase)]


def verify(path, passphrase):
    if Path(path).stat().st_size > MAX_ARCHIVE_BYTES:
        raise RuntimeError('encrypted archive exceeds the verification size limit')
    proc = subprocess.run(gpg_args(passphrase) + ['--decrypt', str(path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError('archive decryption or authentication failed')
    if len(proc.stdout) > MAX_ARCHIVE_BYTES:
        raise RuntimeError('archive exceeds the verification size limit')
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode='r:gz') as archive:
        members = archive.getmembers()
        names = set()
        contents = {}
        total = 0
        for member in members:
            path_parts = Path(member.name).parts
            if member.name.startswith('/') or '..' in path_parts or member.name in names:
                raise RuntimeError('archive contains an unsafe or duplicate path')
            names.add(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError('archive contains a link or special file')
            total += member.size
            if total > MAX_ARCHIVE_BYTES:
                raise RuntimeError('unpacked archive exceeds the verification size limit')
            contents[member.name] = archive.extractfile(member).read()
    if 'manifest.sha256' in contents:
        return verify_router(contents)
    manifest = json.loads(contents.pop('manifest.json'))
    actual = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    if manifest != actual:
        raise RuntimeError('snapshot manifest mismatch')
    metadata = json.loads(contents['metadata.json'])
    if metadata.get('database_method') != 'sqlite-online-backup':
        raise RuntimeError('unsupported snapshot method')
    database = metadata['database']
    if database != 'source/var/lib/headscale/db.sqlite':
        raise RuntimeError('unexpected database location')
    noise = contents['source/var/lib/headscale/noise_private.key']
    if hashlib.sha256(noise).hexdigest() != metadata['noise_sha256']:
        raise RuntimeError('Noise key fingerprint mismatch')
    for required in ('source/etc/headscale/config.yaml', database):
        if required not in contents:
            raise RuntimeError('required snapshot file missing')
    with tempfile.TemporaryDirectory(prefix='headscale-dr-verify-',
                                     dir='/dev/shm' if os.path.isdir('/dev/shm') else None) as temp:
        databases = [database]
        if metadata.get('headplane_database'):
            if metadata['headplane_database'] != 'source/var/lib/headplane/hp_persist.db':
                raise RuntimeError('unexpected Headplane database location')
            databases.append(metadata['headplane_database'])
        for i, database_path in enumerate(databases):
            db = Path(temp) / ('db-' + str(i) + '.sqlite')
            db.write_bytes(contents[database_path])
            conn = sqlite3.connect('file:' + str(db) + '?mode=ro&immutable=1', uri=True)
            try:
                if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('snapshot database failed quick_check')
            finally:
                conn.close()
    return metadata


def verify_router(contents):
    manifest_data = contents.pop('manifest.sha256').decode('utf-8')
    expected = {}
    for line in manifest_data.splitlines():
        digest, separator, name = line.partition('  ')
        if not separator or not re.fullmatch(r'[0-9a-f]{64}', digest) or name in expected:
            raise RuntimeError('invalid router manifest')
        expected[name] = digest
    actual = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    if expected != actual:
        raise RuntimeError('router snapshot manifest mismatch')
    metadata = dict(line.split('=', 1) for line in
                    contents['metadata.txt'].decode('utf-8').splitlines())
    if metadata.get('snapshot_type') != 'openwrt':
        raise RuntimeError('unsupported router snapshot')
    state = contents['source/etc/tailscale/tailscaled.state']
    if hashlib.sha256(state).hexdigest() != metadata['tailscaled_state_sha256']:
        raise RuntimeError('router identity fingerprint mismatch')
    json.loads(state)
    for method, db_path in [('daed_database_method', 'source/etc/daed/wing.db'),
                            ('honk_database_method', 'source/etc/honk/state/honk.db')]:
        value = metadata.get(method, 'none')
        if value == 'sqlite-online-backup':
            db_bytes = contents[db_path]
            with tempfile.TemporaryDirectory(prefix='headscale-dr-router-verify-',
                                             dir='/dev/shm' if os.path.isdir('/dev/shm') else None) as temp:
                db = Path(temp) / 'router.db'
                db.write_bytes(db_bytes)
                conn = sqlite3.connect('file:' + str(db) + '?mode=ro&immutable=1', uri=True)
                try:
                    if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise RuntimeError('router database failed quick_check')
                finally:
                    conn.close()
        elif value != 'none':
            raise RuntimeError('unsupported router database method')
    return metadata


def snapshot(args, passphrase):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.host):
        raise RuntimeError('invalid SSH host alias')
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output_dir.is_symlink() or output_dir.stat().st_mode & 0o077:
        raise RuntimeError('backup directory must be private')
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    final = output_dir / (args.host + '-' + timestamp + '.tar.gz.gpg')
    partial = output_dir / ('.' + final.name + '.partial')
    if final.exists() or partial.exists():
        raise RuntimeError('backup file already exists')
    try:
        with partial.open('xb') as encrypted:
            os.chmod(partial, 0o600)
            router = args.command == 'snapshot-router'
            remote = ['sh', '-s', '--'] if router else ['python3', '-']
            if router and args.sqlite_dir:
                if not re.fullmatch(r'/tmp/headscale-dr-sqlite\.[A-Za-z0-9]+', args.sqlite_dir):
                    raise RuntimeError('invalid temporary SQLite tool directory')
                remote.append(args.sqlite_dir)
            ssh = subprocess.Popen(ssh_command() + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                                    args.host] + remote, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            gpg = subprocess.Popen(gpg_args(passphrase) + ['--symmetric', '--cipher-algo', 'AES256'],
                                   stdin=ssh.stdout, stdout=encrypted, stderr=subprocess.PIPE)
            ssh.stdout.close()
            ssh.stdin.write((ROUTER_HELPER if router else HELPER).read_bytes())
            ssh.stdin.close()
            gpg_error = gpg.communicate()[1]
            ssh_error = ssh.stderr.read()
            ssh_status = ssh.wait()
            if ssh_status or gpg.returncode:
                message = ssh_error.decode(errors='replace').strip()
                if not message:
                    message = gpg_error.decode(errors='replace').strip()
                raise RuntimeError('snapshot stream failed: ' + message[:300])
        metadata = verify(partial, passphrase)
        os.replace(partial, final)
        print(json.dumps(summary(metadata, str(final)), sort_keys=True))
    finally:
        partial.unlink(missing_ok=True)


def summary(metadata, archive=None):
    result = {'created_utc': metadata['created_utc']}
    if archive:
        result['archive'] = archive
    if metadata.get('snapshot_type') == 'openwrt':
        result['snapshot_type'] = 'openwrt'
        result['daed_database_method'] = metadata['daed_database_method']
        result['honk_database_method'] = metadata.get('honk_database_method', 'none')
    else:
        result['headscale_version'] = metadata['headscale_version'].splitlines()[0]
        result['noise_sha256_prefix'] = metadata['noise_sha256'][:12]
        result['sqlite_quick_check'] = 'ok'
        result['headplane_included'] = bool(metadata.get('headplane_database'))
    return result


def install_site(args):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.host):
        raise RuntimeError('invalid SSH host alias')
    tls_input = io.BytesIO()
    with tarfile.open(fileobj=tls_input, mode='w:') as archive:
        for name, path in [('cert.pem', args.certificate), ('key.pem', args.key)]:
            data = Path(path).expanduser().read_bytes()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(data))
    command = ' '.join(shlex.quote(x) for x in
                       ['python3', '-c', SITE_HELPER.read_text(), args.domain,
                        args.legacy_domain, args.ip, args.role])
    proc = subprocess.run(ssh_command() + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                           args.host, command], input=tls_input.getvalue(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if proc.returncode:
        raise RuntimeError('site setup failed: ' + proc.stderr.decode(errors='replace')[-400:])
    print(proc.stdout.decode(errors='replace').strip())


def set_control(args):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.host):
        raise RuntimeError('invalid SSH host alias')
    command = ' '.join(shlex.quote(x) for x in
                       ['python3', '-c', CONFIGTEST_HELPER.read_text() + '\n' + CONTROL_HELPER.read_text(), args.domain,
                        args.old_domain, args.ip])
    proc = subprocess.run(ssh_command() + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                           args.host, command], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=110)
    if proc.returncode:
        raise RuntimeError('control URL transaction failed: ' + proc.stderr.decode(errors='replace')[-400:])
    print(proc.stdout.decode(errors='replace').strip())


def prepare(args, passphrase):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.host):
        raise RuntimeError('invalid SSH host alias')
    archive = Path(args.archive).expanduser()
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    metadata = verify(archive, passphrase)
    if metadata.get('snapshot_type') == 'openwrt':
        raise RuntimeError('a VPS snapshot is required')
    decoded = subprocess.run(gpg_args(passphrase) + ['--decrypt', str(archive)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if decoded.returncode or hashlib.sha256(archive.read_bytes()).hexdigest() != before:
        raise RuntimeError('snapshot changed or failed authentication')
    command = ' '.join(shlex.quote(x) for x in ['python3', '-c', PREPARE_HELPER.read_text()])
    proc = subprocess.run(ssh_command() + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                           args.host, command], input=decoded.stdout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    if proc.returncode:
        raise RuntimeError('target preparation failed: ' + proc.stderr.decode(errors='replace')[-400:])
    print(proc.stdout.decode(errors='replace').strip())


def doctor(args):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.host):
        raise RuntimeError('invalid SSH host alias')
    command = ' '.join(shlex.quote(x) for x in
                       ['python3', '-c', DOCTOR_HELPER.read_text(), args.domain, args.ip])
    proc = subprocess.run(ssh_command() + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                           args.host, command], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=60)
    if proc.returncode:
        raise RuntimeError('remote doctor failed: ' + proc.stderr.decode(errors='replace')[-300:])
    result = json.loads(proc.stdout)
    try:
        result['dns_addresses'] = sorted({entry[4][0] for entry in
                                          socket.getaddrinfo(args.domain, 443, type=socket.SOCK_STREAM)})
    except socket.gaierror:
        result['dns_addresses'] = []
    if args.backup_dir:
        backups = sorted(Path(args.backup_dir).expanduser().glob(args.host + '-*.gpg'),
                         key=lambda path: path.stat().st_mtime, reverse=True)
        if backups:
            age = datetime.datetime.now(datetime.timezone.utc).timestamp() - backups[0].stat().st_mtime
            result['latest_backup_age_seconds_unverified'] = max(0, int(age))
    if args.cf_token_file:
        if not args.cf_zone:
            raise RuntimeError('--cf-zone is required with --cf-token-file')
        token = private_file(args.cf_token_file).read_text().strip()
        def cloudflare(path):
            request = urllib.request.Request('https://api.cloudflare.com/client/v4' + path,
                                             headers={'Authorization': 'Bearer ' + token})
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)['result']
        zones = cloudflare('/zones?' + urllib.parse.urlencode({'name': args.cf_zone, 'status': 'active'}))
        if len(zones) != 1:
            raise RuntimeError('Cloudflare zone lookup was not unique')
        records = cloudflare('/zones/' + zones[0]['id'] + '/dns_records?' +
                             urllib.parse.urlencode({'name': args.domain, 'per_page': 100}))
        result['cloudflare_records'] = [{k: record.get(k) for k in ('type', 'content', 'proxied', 'ttl')}
                                        for record in records]
    print(json.dumps(result, sort_keys=True))


def rehearse(args):
    for host in (args.host, args.authority):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
            raise RuntimeError('invalid SSH host alias')
    listing = subprocess.run(ssh_command() + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                              args.authority, 'headscale nodes list -o json'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if listing.returncode:
        raise RuntimeError('cannot read current authority node identities')
    entries = json.loads(listing.stdout)
    identity = sorted((str(n['id']), n['machine_key'], n['node_key'],
                       tuple(n.get('ip_addresses', [])), tuple(sorted(n.get('approved_routes', []))))
                      for n in entries)
    digest = hashlib.sha256(repr(identity).encode()).hexdigest()
    command = ' '.join(shlex.quote(x) for x in ['python3', '-c', REHEARSE_HELPER.read_text(),
                                                args.stage, args.ip, digest])
    proc = subprocess.run(ssh_command() + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                           args.host, command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    if proc.returncode:
        raise RuntimeError('isolated rehearsal failed: ' + proc.stderr.decode(errors='replace')[-300:])
    print(proc.stdout.decode(errors='replace').strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--passphrase-file', help='required for encrypted snapshots')
    sub = parser.add_subparsers(dest='command', required=True)
    take = sub.add_parser('snapshot', help='online SQLite backup, encrypted locally over SSH')
    take.add_argument('--host', required=True, help='root SSH alias')
    take.add_argument('--output-dir', required=True)
    router = sub.add_parser('snapshot-router', help='encrypted OpenWrt and daed backup')
    router.add_argument('--host', required=True, help='root SSH alias')
    router.add_argument('--output-dir', required=True)
    router.add_argument('--sqlite-dir', help='temporary SQLite CLI bundle on the router')
    check = sub.add_parser('verify', help='authenticate, verify manifest and SQLite quick_check')
    check.add_argument('archive')
    prep = sub.add_parser('prepare', help='verify a snapshot in an isolated target directory')
    prep.add_argument('--host', required=True)
    prep.add_argument('archive')
    site = sub.add_parser('install-site', help='prepare a new 1Panel site with timed rollback')
    site.add_argument('--host', required=True)
    site.add_argument('--domain', required=True)
    site.add_argument('--legacy-domain', required=True)
    site.add_argument('--ip', required=True)
    site.add_argument('--role', choices=('active', 'standby'), required=True)
    site.add_argument('--certificate', required=True)
    site.add_argument('--key', required=True)
    control = sub.add_parser('set-control-url', help='change only server_url with timed rollback')
    control.add_argument('--host', required=True)
    control.add_argument('--domain', required=True)
    control.add_argument('--old-domain', required=True)
    control.add_argument('--ip', required=True)
    checkup = sub.add_parser('doctor', help='read-only host, DNS, identity and backup summary')
    checkup.add_argument('--host', required=True)
    checkup.add_argument('--domain', required=True)
    checkup.add_argument('--ip', required=True)
    checkup.add_argument('--backup-dir')
    checkup.add_argument('--cf-token-file')
    checkup.add_argument('--cf-zone')
    rehearsal = sub.add_parser('rehearse-isolated', help='start the restored copy in a loopback-only namespace')
    rehearsal.add_argument('--host', required=True)
    rehearsal.add_argument('--authority', required=True)
    rehearsal.add_argument('--stage', required=True)
    rehearsal.add_argument('--ip', required=True)
    args = parser.parse_args()
    if args.command == 'install-site':
        install_site(args)
        return
    if args.command == 'set-control-url':
        set_control(args)
        return
    if args.command == 'doctor':
        doctor(args)
        return
    if args.command == 'rehearse-isolated':
        rehearse(args)
        return
    if not args.passphrase_file:
        parser.error('--passphrase-file is required for snapshots and verification')
    passphrase = private_file(args.passphrase_file)
    if args.command in ('snapshot', 'snapshot-router'):
        snapshot(args, passphrase)
    elif args.command == 'prepare':
        prepare(args, passphrase)
    else:
        metadata = verify(args.archive, passphrase)
        print(json.dumps(summary(metadata), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error,
            tarfile.TarError, subprocess.TimeoutExpired) as exc:
        print('headscale-dr: ' + str(exc), file=sys.stderr)
        sys.exit(1)
