#!/usr/bin/env python3
"""Change only Headscale's server_url, with independent timed rollback."""

import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml


def run(args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30, check=check)


def health(domain, ip):
    result = run(['curl', '--silent', '--show-error', '--noproxy', '*',
                  '--connect-timeout', '5', '--max-time', '8', '--output', '/dev/null',
                  '--write-out', '%{http_code}', '--resolve', f'{domain}:443:{ip}',
                  f'https://{domain}/health'], check=False)
    return result.stdout if result.returncode == 0 else 'failed'


def identities():
    nodes = json.loads(run(['headscale', 'nodes', 'list', '--output', 'json']).stdout)
    return sorted((str(n['id']), hashlib.sha256(n['machine_key'].encode()).hexdigest(),
                   tuple(n.get('ip_addresses', [])), tuple(sorted(n.get('approved_routes', []))))
                  for n in nodes)


def main():
    if os.geteuid() or len(sys.argv) != 4:
        raise RuntimeError('requires root and new domain, old domain, local IPv4')
    domain, old_domain, ip = sys.argv[1:]
    for name in (domain, old_domain):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) or '..' in name:
            raise RuntimeError('invalid domain')
    ipaddress.IPv4Address(ip)
    if domain == old_domain or not shutil.which('systemd-run'):
        raise RuntimeError('different domains and systemd rollback timer required')
    addresses = run(['ip', '-o', '-4', 'addr', 'show', 'scope', 'global']).stdout
    if not re.search(r'\binet\s+' + re.escape(ip) + r'/', addresses):
        raise RuntimeError('IPv4 is not assigned on this host')
    if run(['systemctl', 'is-active', 'headscale'], check=False).stdout.strip() != 'active':
        raise RuntimeError('Headscale is not active')
    if health(old_domain, ip) != '200' or health(domain, ip) != '200':
        raise RuntimeError('old and new TLS endpoints must both be healthy')

    config = Path('/etc/headscale/config.yaml')
    before = config.read_text()
    parsed = yaml.safe_load(before)
    if parsed.get('server_url') != 'https://' + old_domain:
        raise RuntimeError('current server_url does not match the expected old domain')
    pattern = re.compile(r'^server_url\s*:\s*[^\n]*$', re.MULTILINE)
    updated, count = pattern.subn('server_url: https://' + domain, before)
    if count != 1:
        raise RuntimeError('cannot safely identify exactly one server_url line')
    wanted = dict(parsed)
    wanted['server_url'] = 'https://' + domain
    if yaml.safe_load(updated) != wanted:
        raise RuntimeError('render changed other configuration values')
    key = Path(parsed['noise']['private_key_path'])
    fingerprint = hashlib.sha256(key.read_bytes()).hexdigest()
    nodes_before = identities()
    old_stat = config.stat()
    run_dir = Path('/run/headscale-dr')
    run_dir.mkdir(mode=0o700, exist_ok=True)
    operation = uuid.uuid4().hex[:16]
    saved = run_dir / (operation + '.config.yaml')
    marker = run_dir / (operation + '.committed')
    rollback = run_dir / (operation + '.rollback.sh')
    staged = config.parent / ('.headscale-dr-' + operation + '.yaml')
    saved.write_text(before)
    os.chmod(saved, 0o600)
    staged.write_text(updated)
    os.chmod(staged, old_stat.st_mode & 0o777)
    os.chown(staged, old_stat.st_uid, old_stat.st_gid)
    changed = False
    armed = False
    try:
        isolated_configtest(staged)
        rollback.write_text('#!/bin/sh\n'
                            f'if [ ! -e {marker} ]; then\n'
                            f'  cp {saved} {staged} && chmod {old_stat.st_mode & 0o777:o} {staged} && '
                            f'chown {old_stat.st_uid}:{old_stat.st_gid} {staged} && '
                            f'mv {staged} {config} && systemctl restart headscale || exit 1\n'
                            'fi\n'
                            f'if [ -e {marker} ]; then rm -f {saved} {staged}; fi\n')
        os.chmod(rollback, 0o700)
        run(['systemd-run', '--quiet', '--unit=hs-dr-control-' + operation,
             '--on-active=120s', '/bin/sh', str(rollback)])
        armed = True
        os.replace(staged, config)
        changed = True
        run(['systemctl', 'restart', 'headscale'])
        for attempt in range(15):
            local = run(['curl', '--silent', '--max-time', '2', '--output', '/dev/null',
                         '--write-out', '%{http_code}', 'http://127.0.0.1:8080/health'], check=False)
            if local.stdout == '200' and health(old_domain, ip) == '200' and health(domain, ip) == '200':
                break
            if attempt < 14:
                time.sleep(1)
        else:
            raise RuntimeError('Headscale or HTTPS health did not recover')
        if hashlib.sha256(key.read_bytes()).hexdigest() != fingerprint or identities() != nodes_before:
            raise RuntimeError('Headscale identity, nodes, addresses or approved routes changed')
        derp = run(['curl', '--silent', '--max-time', '3', '--output', '/dev/null',
                    '--write-out', '%{http_code}', 'http://127.0.0.1:8080/derp/probe'], check=False)
        if parsed.get('derp', {}).get('server', {}).get('enabled') and derp.stdout != '200':
            raise RuntimeError('embedded DERP probe failed')
        marker.touch(mode=0o600, exist_ok=False)
        print('control_url_updated nodes=' + str(len(nodes_before)) +
              ' noise_sha256_prefix=' + fingerprint[:12] + ' old_and_new_health=200')
    except Exception:
        restored = not changed
        if changed:
            result = run(['/bin/sh', str(rollback)], check=False)
            restored = result.returncode == 0 and health(old_domain, ip) == '200'
        if restored and armed:
            marker.touch(mode=0o600, exist_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True)
        if not armed:
            saved.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('control URL transaction failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
