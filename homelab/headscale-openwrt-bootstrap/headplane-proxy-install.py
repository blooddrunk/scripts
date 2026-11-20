#!/usr/bin/env python3
"""Expose the native Headplane login on the existing 1Panel site."""

import ipaddress
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path


def run(args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30, check=check)


def status(domain, ip, path):
    result = run(['curl', '--silent', '--show-error', '--noproxy', '*',
                  '--connect-timeout', '5', '--max-time', '8', '--output', '/dev/null',
                  '--write-out', '%{http_code}', '--resolve', f'{domain}:443:{ip}',
                  f'https://{domain}{path}'], check=False)
    return result.stdout if result.returncode == 0 else 'failed'


def main():
    if os.geteuid() or len(sys.argv) != 4:
        raise RuntimeError('requires root, stable domain, legacy domain and local IPv4')
    domain, legacy, ip = sys.argv[1:]
    for name in (domain, legacy):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) or '..' in name:
            raise RuntimeError('invalid domain')
    ipaddress.IPv4Address(ip)
    if run(['systemctl', 'is-active', 'headplane'], check=False).stdout.strip() != 'active':
        raise RuntimeError('Headplane is not active')
    if status(domain, ip, '/health') != '200' or status(legacy, ip, '/health') != '200':
        raise RuntimeError('existing Headscale endpoints are unhealthy')
    containers = run(['docker', 'ps', '--format', '{{.Names}} {{.Image}}']).stdout.splitlines()
    matches = [line.split()[0] for line in containers if 'openresty' in line.lower()]
    if len(matches) != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]+', matches[0]):
        raise RuntimeError('cannot identify exactly one OpenResty container')
    container = matches[0]
    target = Path('/opt/1panel/www/sites') / domain / 'proxy/headplane.conf'
    if target.exists() or not target.parent.is_dir():
        raise RuntimeError('Headplane proxy already exists or the site is missing')
    run_dir = Path('/run/headscale-dr')
    run_dir.mkdir(mode=0o700, exist_ok=True)
    op = uuid.uuid4().hex[:16]
    marker = run_dir / (op + '.committed')
    rollback = run_dir / (op + '.headplane-rollback.sh')
    rollback.write_text('#!/bin/sh\n'
                        f'if [ ! -e {marker} ]; then\n'
                        f'  rm -f {target}\n'
                        f'  docker exec {container} openresty -t >/dev/null 2>&1 && '
                        f'docker exec {container} openresty -s reload >/dev/null 2>&1\n'
                        'fi\n')
    os.chmod(rollback, 0o700)
    run(['systemd-run', '--quiet', '--unit=hs-dr-headplane-' + op,
         '--on-active=120s', '/bin/sh', str(rollback)])
    created = False
    reloaded = False
    try:
        with target.open('x') as f:
            created = True
            f.write('''location = /admin { return 308 /admin/; }
location ^~ /admin/ {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
''')
        run(['docker', 'exec', container, 'openresty', '-t'])
        reloaded = True
        run(['docker', 'exec', container, 'openresty', '-s', 'reload'])
        for attempt in range(12):
            headscale = status(domain, ip, '/health')
            old_health = status(legacy, ip, '/health')
            login = status(domain, ip, '/admin/login')
            entry = status(domain, ip, '/admin/')
            if headscale == old_health == '200' and login == '200' and entry in ('302', '303', '307', '308'):
                break
            if attempt < 11:
                time.sleep(1)
        else:
            raise RuntimeError('HTTPS checks failed: health=' + headscale + ' old=' + old_health
                               + ' login=' + login + ' entry=' + entry)
        marker.touch(mode=0o600, exist_ok=False)
        print('headplane_proxy_ready login=200 unauthenticated_entry=' + entry + ' headscale_health=200')
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        restored = True
        if reloaded:
            restored = run(['docker', 'exec', container, 'openresty', '-t'], check=False).returncode == 0
            if restored:
                restored = run(['docker', 'exec', container, 'openresty', '-s', 'reload'], check=False).returncode == 0
        if restored:
            marker.touch(mode=0o600, exist_ok=True)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('headplane proxy failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
