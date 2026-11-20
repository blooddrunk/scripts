#!/usr/bin/env python3
"""Install a new 1Panel/OpenResty virtual host with a timed rollback.

Invocation: python3 -c SOURCE DOMAIN LEGACY_DOMAIN_OR_DASH VPS_IPV4 active|standby
The TLS certificate and key arrive as a two-member tar stream on stdin.
When LEGACY_DOMAIN_OR_DASH is '-' no legacy vhost exists; an embedded
template matching the hand-maintained 8443 layout is used instead.
"""

import ipaddress
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

# Mirrors the 1Panel site blocks this project hand-maintains: public IPv4 443
# stays with Xray; OpenResty only listens on loopback 8443 with SNI routing.
TEMPLATE_CONF = '''server {
    listen 80 ;
    listen [::]:80 ;
    listen 127.0.0.1:8443 ssl ;
    server_name DOMAIN ;
    index index.php index.html index.htm default.php default.htm default.html ;
    access_log /www/sites/DOMAIN/log/access.log main;
    error_log /www/sites/DOMAIN/log/error.log;
    location ~ ^/(\\.user.ini|\\.htaccess|\\.git|\\.env|\\.svn|\\.project|LICENSE|README.md) {
        return 404 ;
    }
    location ^~ /.well-known/acme-challenge {
        allow all ;
        root /usr/share/nginx/html ;
    }
    http2 on ;
    if ($scheme = http) {
        return 301 https://$host$request_uri ;
    }
    ssl_certificate /www/sites/DOMAIN/ssl/fullchain.pem ;
    ssl_certificate_key /www/sites/DOMAIN/ssl/privkey.pem ;
    ssl_protocols TLSv1.3 TLSv1.2 ;
    ssl_session_cache shared:SSL:10m ;
    ssl_session_timeout 10m ;
    error_page 497 https://$host$request_uri ;
    proxy_set_header X-Forwarded-Proto https ;
    root /www/sites/DOMAIN/index ;
    include /www/sites/DOMAIN/proxy/*.conf ;
}
'''

# Same long-timeout upstream block the activate/relay helpers install.
ROOT_PROXY_TEMPLATE = '''location ^~ / {
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


def run(args, *, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30, check=check)


def health(domain, ip):
    result = run(['curl', '--silent', '--show-error', '--noproxy', '*',
                  '--connect-timeout', '5', '--max-time', '12', '--output', '/dev/null',
                  '--write-out', '%{http_code}', '--resolve', f'{domain}:443:{ip}',
                  f'https://{domain}/health'], check=False)
    return result.stdout if result.returncode == 0 else 'failed'


def main():
    if os.geteuid() != 0 or len(sys.argv) != 5:
        raise RuntimeError('requires root and domain, legacy domain, IPv4, role')
    domain, legacy, ip, role = sys.argv[1:]
    has_legacy = legacy != '-'
    for name in (domain, legacy) if has_legacy else (domain,):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) or '..' in name:
            raise RuntimeError('invalid domain')
    ipaddress.IPv4Address(ip)
    if (has_legacy and domain == legacy) or role not in ('active', 'standby'):
        raise RuntimeError('invalid site role or domains')
    addresses = run(['ip', '-o', '-4', 'addr', 'show', 'scope', 'global']).stdout
    if not re.search(r'\binet\s+' + re.escape(ip) + r'/', addresses):
        raise RuntimeError('requested IPv4 is not assigned on this host')
    if not shutil.which('systemd-run'):
        raise RuntimeError('systemd timer is required for rollback')

    containers = run(['docker', 'ps', '--format', '{{.Names}} {{.Image}}']).stdout.splitlines()
    matches = [line.split()[0] for line in containers if 'openresty' in line.lower()]
    if len(matches) != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]+', matches[0]):
        raise RuntimeError('cannot identify exactly one OpenResty container')
    container = matches[0]
    target_conf = Path('/opt/1panel/www/conf.d') / (domain + '.conf')
    target_site = Path('/opt/1panel/www/sites') / domain
    if target_conf.exists() or target_site.exists():
        raise RuntimeError('target site already exists; refusing to overwrite it')
    if has_legacy:
        source_conf = Path('/opt/1panel/www/conf.d') / (legacy + '.conf')
        source_root = Path('/opt/1panel/www/sites') / legacy / 'proxy/root.conf'
        template = source_conf.read_text()
        if not re.search(r'\bserver_name\s+' + re.escape(legacy) + r'\s*;', template):
            raise RuntimeError('legacy vhost does not match expected server name')
        if health(legacy, ip) != '200':
            raise RuntimeError('legacy HTTPS health preflight failed')
    else:
        source_root = None
        template = TEMPLATE_CONF
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise RuntimeError('certificate input too large')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        members = archive.getmembers()
        if sorted(x.name for x in members) != ['cert.pem', 'key.pem'] or any(not x.isfile() for x in members):
            raise RuntimeError('invalid TLS input')
        cert = archive.extractfile('cert.pem').read()
        key = archive.extractfile('key.pem').read()

    target_site.mkdir(mode=0o755)
    created_conf = False
    reload_attempted = False
    marker = None
    try:
        for sub in ('ssl', 'log', 'index', 'proxy'):
            (target_site / sub).mkdir(mode=0o755)
        cert_path = target_site / 'ssl/fullchain.pem'
        key_path = target_site / 'ssl/privkey.pem'
        cert_path.write_bytes(cert)
        key_path.write_bytes(key)
        os.chmod(cert_path, 0o644)
        os.chmod(key_path, 0o600)
        host_check = run(['openssl', 'x509', '-in', str(cert_path), '-noout', '-checkhost', domain]).stdout
        if 'does match certificate' not in host_check:
            raise RuntimeError('certificate does not match the requested domain')
        run(['openssl', 'x509', '-in', str(cert_path), '-noout', '-checkend', '86400'])
        cert_pub = run(['openssl', 'x509', '-in', str(cert_path), '-noout', '-pubkey']).stdout
        key_pub = run(['openssl', 'pkey', '-in', str(key_path), '-pubout']).stdout
        if cert_pub != key_pub:
            raise RuntimeError('certificate and key do not match')
        if role == 'active':
            root_template = source_root.read_text() if source_root is not None else ROOT_PROXY_TEMPLATE
            (target_site / 'proxy/root.conf').write_text(root_template)
        else:
            (target_site / 'proxy/root.conf').write_text('location ^~ / { return 503; }\n')

        # If this process or SSH disappears, a separate systemd timer removes
        # the new vhost unless both the old and new endpoints passed checks.
        run_dir = Path('/run/headscale-dr')
        run_dir.mkdir(mode=0o700, exist_ok=True)
        operation = uuid.uuid4().hex[:16]
        marker = run_dir / (operation + '.committed')
        rollback = run_dir / (operation + '.rollback.sh')
        rollback.write_text('#!/bin/sh\n'
                            f'if [ ! -e {marker} ]; then\n'
                            f'  rm -f {target_conf}\n'
                            f'  docker exec {container} openresty -t >/dev/null 2>&1 && '
                            f'docker exec {container} openresty -s reload >/dev/null 2>&1\n'
                            'fi\n')
        os.chmod(rollback, 0o700)
        run(['systemd-run', '--quiet', '--unit=hs-dr-site-' + operation,
             '--on-active=120s', '/bin/sh', str(rollback)])
        with target_conf.open('x') as output:
            created_conf = True
            output.write(template.replace(legacy if has_legacy else 'DOMAIN', domain))
        run(['docker', 'exec', container, 'openresty', '-t'])
        reload_attempted = True
        run(['docker', 'exec', container, 'openresty', '-s', 'reload'])
        expected = '200' if role == 'active' else '503'
        for attempt in range(10):
            new_health = health(domain, ip)
            old_health = health(legacy, ip) if has_legacy else '200'
            if old_health == '200' and new_health == expected:
                break
            if attempt < 9:
                time.sleep(1)
        else:
            raise RuntimeError('post-reload HTTPS check failed: legacy=' + old_health
                               + ' new=' + new_health + ' expected=' + expected)
        marker.touch(mode=0o600, exist_ok=False)
        print('site_ready role=' + role + ' stable_health=' + expected
              + ' legacy_health=' + ('200' if has_legacy else 'absent'))
    except Exception:
        if created_conf:
            target_conf.unlink(missing_ok=True)
        restored = True
        if reload_attempted:
            restored = run(['docker', 'exec', container, 'openresty', '-t'], check=False).returncode == 0
            if restored:
                restored = run(['docker', 'exec', container, 'openresty', '-s', 'reload'], check=False).returncode == 0
        shutil.rmtree(target_site, ignore_errors=True)
        if marker and restored:
            marker.touch(mode=0o600, exist_ok=True)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('site setup failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
