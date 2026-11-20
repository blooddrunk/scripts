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
    if os.geteuid() or len(sys.argv) != 6:
        raise RuntimeError('requires root, operation, stable/legacy domains, source/target IPs')
    op, domain, legacy, source_ip, target_ip = sys.argv[1:]
    if not re.fullmatch(r'[a-f0-9]{16}', op):
        raise RuntimeError('invalid operation')
    for name in (domain, legacy):
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
    paths = [Path('/opt/1panel/www/sites') / name / 'proxy/root.conf' for name in (domain, legacy)]
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
        for path in paths:
            path.write_text(config)
        if admin.exists():
            admin.unlink()
        run(['docker', 'exec', container, 'openresty', '-t'])
        run(['docker', 'exec', container, 'openresty', '-s', 'reload'])
        for attempt in range(15):
            statuses = []
            for name, endpoint in ((domain, '/health'), (legacy, '/health'), (domain, '/admin/login')):
                result = run(['curl', '-sS', '--noproxy', '*', '--max-time', '10', '-o', '/dev/null', '-w', '%{http_code}',
                              '--resolve', name + ':443:' + source_ip, 'https://' + name + endpoint], False)
                statuses.append(result.stdout if not result.returncode else 'failed')
            if statuses == ['200', '200', '200']:
                break
            time.sleep(1)
        else:
            raise RuntimeError('former domain relay health checks failed: ' + ','.join(statuses))
        (fence / 'RELAY_READY').touch(mode=0o600, exist_ok=False)
        print(json.dumps({'former_writer': 'masked', 'stable_relay': 200, 'legacy_relay': 200,
                          'headplane_login': 200}, sort_keys=True))
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
