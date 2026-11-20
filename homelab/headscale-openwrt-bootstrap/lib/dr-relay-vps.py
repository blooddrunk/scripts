#!/usr/bin/env python3
"""Relay old control domains from a persistently fenced former writer."""
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run(args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30, check=check)


def main():
    if os.geteuid() or len(sys.argv) not in (6, 7):
        raise RuntimeError('requires root, operation, stable/legacy domains, source/target IPs and optional legacy mode')
    op, domain, legacy, source_ip, target_ip = sys.argv[1:6]
    legacy_mode = sys.argv[6] if len(sys.argv) == 7 else 'local'
    if legacy_mode not in ('local', 'blocked'):
        raise RuntimeError('invalid legacy mode')
    if not re.fullmatch(r'[a-f0-9]{16}', op):
        raise RuntimeError('invalid operation')
    has_legacy = legacy != '-'
    for name in (domain, legacy) if has_legacy else (domain,):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) or '..' in name:
            raise RuntimeError('invalid domain')
    for ip in (source_ip, target_ip):
        ipaddress.IPv4Address(ip)
    fence = Path('/var/lib/headscale-dr/fences') / op
    if not (fence / 'SEALED').is_file():
        raise RuntimeError('former writer fence is not sealed')
    if run(['systemctl', 'is-enabled', 'headscale'], False).stdout.strip() != 'masked' or run(['systemctl', 'is-active', 'headscale'], False).stdout.strip() == 'active':
        raise RuntimeError('former writer is not persistently fenced')
    result = run(['curl', '-sS', '--noproxy', '*', '--max-time', '10', '-o', '/dev/null', '-w', '%{http_code}',
                  '--resolve', domain + ':443:' + target_ip, 'https://' + domain + '/health'], False)
    if result.returncode or result.stdout != '200':
        raise RuntimeError('new authority cannot be reached with valid TLS')
    containers = run(['docker', 'ps', '--format', '{{.Names}} {{.Image}}']).stdout.splitlines()
    matches = [line.split()[0] for line in containers if 'openresty' in line.lower()]
    if len(matches) != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]+', matches[0]):
        raise RuntimeError('cannot identify exactly one OpenResty container')
    container = matches[0]
    paths = [Path('/opt/1panel/www/sites') / domain / 'proxy/root.conf']
    if has_legacy:
        paths.append(Path('/opt/1panel/www/sites') / legacy / 'proxy/root.conf')
    admin = paths[0].with_name('headplane.conf')
    if any(not p.is_file() for p in paths):
        raise RuntimeError('expected former-writer sites are missing')
    backup = fence / 'proxy-before-relay'
    backup.mkdir(mode=0o700)
    for i, path in enumerate(paths + [admin]):
        if path.exists():
            shutil.copyfile(path, backup / str(i))
            os.chmod(backup / str(i), 0o600)
    config = '''location ^~ / {
    proxy_pass https://TARGET:443;
    proxy_ssl_server_name on;
    proxy_ssl_name DOMAIN;
    proxy_ssl_verify on;
    proxy_ssl_verify_depth 4;
    proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
    proxy_set_header Host DOMAIN;
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
'''.replace('TARGET', target_ip).replace('DOMAIN', domain)
    try:
        paths[0].write_text(config)
        if has_legacy:
            paths[1].write_text(config if legacy_mode == 'local' else 'location ^~ / { return 503; }\n')
        if admin.exists():
            admin.unlink()
        run(['docker', 'exec', container, 'openresty', '-t'])
        run(['docker', 'exec', container, 'openresty', '-s', 'reload'])
        for attempt in range(15):
            checks = [(domain, '/health')]
            if has_legacy:
                checks.append((legacy, '/health'))
            checks.append((domain, '/admin/login'))
            statuses = []
            for name, endpoint in checks:
                result = run(['curl', '-sS', '--noproxy', '*', '--max-time', '10', '-o', '/dev/null', '-w', '%{http_code}',
                              '--resolve', name + ':443:' + source_ip, 'https://' + name + endpoint], False)
                statuses.append(result.stdout if not result.returncode else 'failed')
            expected = ['200'] + (['200' if legacy_mode == 'local' else '503'] if has_legacy else []) + ['200']
            if statuses == expected:
                break
            time.sleep(1)
        else:
            raise RuntimeError('former domain relay health checks failed: ' + ','.join(statuses))
        (fence / 'RELAY_READY').touch(mode=0o600, exist_ok=False)
        result = {'former_writer': 'masked', 'stable_relay': 200,
                  'legacy_relay': (int(expected[1]) if has_legacy else 'absent'), 'headplane_login': 200}
        print(json.dumps(result, sort_keys=True))
    except Exception:
        for i, path in enumerate(paths + [admin]):
            saved = backup / str(i)
            if saved.exists():
                shutil.copyfile(saved, path)
            elif path.exists():
                path.unlink()
        run(['docker', 'exec', container, 'openresty', '-t'], False)
        run(['docker', 'exec', container, 'openresty', '-s', 'reload'], False)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('relay failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
