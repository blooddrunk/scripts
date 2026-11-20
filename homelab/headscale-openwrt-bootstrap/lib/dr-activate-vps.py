#!/usr/bin/env python3
"""Install a verified recovery set on a fenced destination. Root only."""
import hashlib
import ipaddress
import json
import os
import pwd
import grp
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
import yaml

ROOT_PROXY = '''location ^~ / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
'''
HEADPLANE_PROXY = '''location = /admin { return 308 /admin/; }
location ^~ /admin/ {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
'''


def run(args, check=True, timeout=30):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout, check=check)


def atomic_write(path, text, mode=0o600):
    temporary = path.with_name(path.name + '.new')
    with temporary.open('x') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def check_stage(stage):
    manifest = json.loads((stage / 'manifest.json').read_text())
    for name, digest in manifest.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('unsafe manifest path')
        target = stage / relative
        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError('staged manifest mismatch')
    meta = json.loads((stage / 'metadata.json').read_text())
    if meta.get('database_method') != 'sqlite-online-backup' or not meta.get('headplane_database'):
        raise RuntimeError('Headscale and Headplane recovery data are required')
    if run(['headscale', 'version']).stdout.splitlines()[0] != meta['headscale_version'].splitlines()[0]:
        raise RuntimeError('Headscale version mismatch')
    return meta


def permissions(directory, user, group, directory_mode, file_mode):
    uid, gid = pwd.getpwnam(user).pw_uid, grp.getgrnam(group).gr_gid
    for base, dirs, files in os.walk(directory):
        for path in [Path(base)] + [Path(base) / x for x in dirs + files]:
            if path.is_symlink():
                raise RuntimeError('symlinks are not permitted in recovery data')
            os.chown(path, uid, gid)
            os.chmod(path, directory_mode if path.is_dir() else file_mode)


def status(domain, ip, path):
    result = run(['curl', '--silent', '--noproxy', '*', '--max-time', '8', '--output', '/dev/null',
                  '--write-out', '%{http_code}', '--resolve', domain + ':443:' + ip,
                  'https://' + domain + path], False, 12)
    return result.stdout if not result.returncode else 'failed'


def main():
    if os.geteuid() or len(sys.argv) != 7:
        raise RuntimeError('requires root, operation, stage, domain, legacy domain, target IP, legacy mode')
    op, stage_path, domain, legacy, ip, legacy_mode = sys.argv[1:]
    if not re.fullmatch(r'[a-f0-9]{16}', op) or not re.fullmatch(r'/var/lib/headscale-dr/staged/snapshot-[a-f0-9]{16}', stage_path):
        raise RuntimeError('invalid operation or stage')
    # The operator holds /run/headscale-dr-writer.lock when calling this. A
    # cancellation recorded under that same lock prevents a delayed SSH
    # command from starting this writer after the source has been restored.
    if (Path('/var/lib/headscale-dr/canceled') / op).exists():
        raise RuntimeError('activation was canceled before it acquired the writer lock')
    has_legacy = legacy != '-'
    for name in (domain, legacy) if has_legacy else (domain,):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) or '..' in name:
            raise RuntimeError('invalid domain')
    ipaddress.IPv4Address(ip)
    if legacy_mode not in ('local', 'blocked') or (has_legacy and domain == legacy):
        raise RuntimeError('invalid destination legacy mode')
    if not has_legacy and legacy_mode != 'blocked':
        # No legacy vhost exists to serve locally.
        raise RuntimeError('a legacy-less deployment must use the blocked mode')
    if not re.search(r'\binet\s+' + re.escape(ip) + r'/', run(['ip', '-o', '-4', 'addr', 'show', 'scope', 'global']).stdout):
        raise RuntimeError('target IP not assigned to host')
    stage = Path(stage_path)
    meta = check_stage(stage)
    for p in ('source/etc/headscale', 'source/var/lib/headscale', 'source/etc/headplane', 'source/var/lib/headplane'):
        if not (stage / p).is_dir():
            raise RuntimeError('required staged directory is missing')
    config = yaml.safe_load((stage / 'source/etc/headscale/config.yaml').read_text())
    if config.get('server_url') != 'https://' + domain or config['derp']['server']['enabled'] is not True:
        raise RuntimeError('unexpected control URL or embedded DERP state')
    stable_root = Path('/opt/1panel/www/sites') / domain / 'proxy/root.conf'
    legacy_root = Path('/opt/1panel/www/sites') / legacy / 'proxy/root.conf' if has_legacy else None
    admin = stable_root.with_name('headplane.conf')
    tracked = [stable_root] + ([legacy_root] if has_legacy else [])
    if any(not path.is_file() for path in tracked):
        raise RuntimeError('expected proxy sites must already exist')
    containers = run(['docker', 'ps', '--format', '{{.Names}} {{.Image}}']).stdout.splitlines()
    matches = [line.split()[0] for line in containers if 'openresty' in line.lower()]
    if len(matches) != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]+', matches[0]):
        raise RuntimeError('cannot identify exactly one OpenResty container')
    container = matches[0]
    # A restored writer must not accidentally appear through an untracked vhost.
    for path in Path('/opt/1panel/www/sites').glob('*/proxy/*.conf'):
        if '127.0.0.1:8080' in path.read_text(errors='replace') and path not in tracked:
            raise RuntimeError('an additional Headscale proxy site requires manual review')
    root = Path('/var/lib/headscale-dr/activations')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    txn = root / op
    txn.mkdir(mode=0o700)
    for name, path in [('stable-root', stable_root), ('legacy-root', legacy_root), ('admin', admin)]:
        if path is not None and path.exists():
            shutil.copyfile(path, txn / name)
            os.chmod(txn / name, 0o600)
    def journal(phase):
        path = txn / 'state.json'
        tmp = txn / 'state.tmp'
        with tmp.open('w') as f:
            json.dump({'phase': phase, 'operation': op, 'stage': stage_path,
                       'noise_sha256_prefix': meta['noise_sha256'][:12]}, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dirfd = os.open(txn, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    def reload_proxy():
        run(['docker', 'exec', container, 'openresty', '-t'])
        run(['docker', 'exec', container, 'openresty', '-s', 'reload'])
    journal('created')
    try:
        # Close every known public entry before touching the old target writer.
        stable_root.write_text('location ^~ / { return 503; }\n')
        if legacy_root is not None:
            legacy_root.write_text('location ^~ / { return 503; }\n')
        if admin.exists():
            admin.unlink()
        reload_proxy()
        journal('public-entries-closed')
        run(['systemctl', 'stop', 'headplane'], False)
        run(['systemctl', 'disable', 'headplane'], False)
        run(['systemctl', 'mask', '--now', 'headscale'])
        if run(['systemctl', 'is-active', 'headscale'], False).stdout.strip() == 'active':
            raise RuntimeError('destination service could not be stopped')
        paths = [('etc/headscale', '/etc/headscale'), ('var/lib/headscale', '/var/lib/headscale'),
                 ('etc/headplane', '/etc/headplane'), ('var/lib/headplane', '/var/lib/headplane')]
        for relative, absolute in paths:
            dest = Path(absolute)
            previous = dest.with_name(dest.name + '.dr-old-' + op)
            if not dest.is_dir() or previous.exists():
                raise RuntimeError('destination state cannot be preserved')
            dest.rename(previous)
            shutil.copytree(stage / 'source' / relative, dest, symlinks=False)
        permissions(Path('/etc/headscale'), 'root', 'headscale', 0o750, 0o640)
        permissions(Path('/var/lib/headscale'), 'headscale', 'headscale', 0o750, 0o600)
        permissions(Path('/etc/headplane'), 'root', 'headplane', 0o750, 0o640)
        permissions(Path('/var/lib/headplane'), 'headplane', 'headplane', 0o700, 0o600)
        config['derp']['server']['ipv4'] = ip
        config_path = Path('/etc/headscale/config.yaml')
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        os.chown(config_path, 0, grp.getgrnam('headscale').gr_gid)
        os.chmod(config_path, 0o640)
        journal('installed-but-never-started')
        # This marker is committed before unmask/start. If anything interrupts
        # later, rollback must fence and snapshot this writer, not use old data.
        journal('writer-may-have-started')
        run(['systemctl', 'unmask', 'headscale'])
        run(['systemctl', 'enable', '--now', 'headscale'])
        for attempt in range(20):
            probe = run(['curl', '-s', '--max-time', '2', '-o', '/dev/null', '-w', '%{http_code}',
                         'http://127.0.0.1:8080/health'], False)
            if probe.stdout == '200':
                break
            time.sleep(1)
        else:
            raise RuntimeError('restored service did not become healthy')
        derp = run(['curl', '-s', '--max-time', '3', '-o', '/dev/null', '-w', '%{http_code}',
                    'http://127.0.0.1:8080/derp/probe'], False).stdout
        if derp != '200':
            raise RuntimeError('restored DERP probe failed')
        stable_root.write_text(ROOT_PROXY)
        if legacy_root is not None and legacy_mode == 'local':
            legacy_root.write_text(ROOT_PROXY)
        admin.write_text(HEADPLANE_PROXY)
        run(['systemctl', 'enable', '--now', 'headplane'])
        reload_proxy()
        for attempt in range(15):
            checks = [status(domain, ip, '/health'), status(domain, ip, '/admin/login')]
            if legacy_root is not None:
                checks.append(status(legacy, ip, '/health'))
                expected = ['200', '200', '200' if legacy_mode == 'local' else '503']
            else:
                expected = ['200', '200']
            if checks == expected:
                break
            time.sleep(1)
        else:
            raise RuntimeError('target TLS/Headplane smoke failed')
        journal('active')
        print(json.dumps({'operation': op, 'phase': 'active', 'health': 200,
                          'headplane_login': 200, 'derp_probe': 200,
                          'noise_sha256_prefix': meta['noise_sha256'][:12]}, sort_keys=True))
    except Exception:
        # The old source remains persistently sealed. Once this writer may
        # have started, only a fenced snapshot of it can be used for rollback.
        run(['systemctl', 'stop', 'headplane'], False)
        run(['systemctl', 'disable', 'headplane'], False)
        run(['systemctl', 'mask', '--now', 'headscale'], False)
        stable_root.write_text('location ^~ / { return 503; }\n')
        if admin.exists():
            admin.unlink()
        try:
            reload_proxy()
        except Exception:
            pass
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('activation failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
