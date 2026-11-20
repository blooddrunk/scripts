#!/usr/bin/env python3
"""Add the official DERP map with a delayed, independent configuration rollback."""

import copy
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


URL = 'https://controlplane.tailscale.com/derpmap/default'


def run(args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30, check=check)


def health(domain, ip):
    p = run(['curl', '--silent', '--noproxy', '*', '--max-time', '8', '--resolve',
             f'{domain}:443:{ip}', '--output', '/dev/null', '--write-out', '%{http_code}',
             f'https://{domain}/health'], check=False)
    return p.stdout if p.returncode == 0 else 'failed'


def nodes_digest():
    entries = json.loads(run(['headscale', 'nodes', 'list', '-o', 'json']).stdout)
    selected = sorted((str(n['id']), n['machine_key'], n['node_key'],
                       tuple(n.get('ip_addresses', [])), tuple(sorted(n.get('approved_routes', []))))
                      for n in entries)
    return hashlib.sha256(repr(selected).encode()).hexdigest()


def main():
    if os.geteuid() or len(sys.argv) != 4:
        raise RuntimeError('expected root, stable domain, legacy domain and local IPv4')
    domain, legacy, ip = sys.argv[1:]
    for name in (domain, legacy):
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', name) or '..' in name:
            raise RuntimeError('invalid domain')
    ipaddress.IPv4Address(ip)
    fetched = run(['curl', '--fail', '--silent', '--show-error', '--noproxy', '*',
                   '--max-time', '15', URL])
    if len(json.loads(fetched.stdout).get('Regions', {})) < 1:
        raise RuntimeError('official DERP map is empty')
    if health(domain, ip) != '200' or health(legacy, ip) != '200':
        raise RuntimeError('existing HTTPS endpoints are not healthy')
    path = Path('/etc/headscale/config.yaml')
    text = path.read_text()
    config = yaml.safe_load(text)
    if config.get('server_url') != 'https://' + domain:
        raise RuntimeError('unexpected authority server_url')
    if config.get('derp', {}).get('urls') is not None:
        raise RuntimeError('DERP URL list is not the expected empty baseline')
    new_text, count = re.subn(r'(?m)^(  urls:[ \t]*)$', r'\1\n    - ' + URL, text)
    expected = copy.deepcopy(config)
    expected['derp']['urls'] = [URL]
    if count != 1 or yaml.safe_load(new_text) != expected:
        raise RuntimeError('cannot safely change exactly the DERP URL list')
    nodes = nodes_digest()
    noise = Path(config['noise']['private_key_path'])
    derp = Path(config['derp']['server']['private_key_path'])
    before_keys = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (noise, derp)]
    old_stat = path.stat()
    work = Path('/run/headscale-dr') / ('derp-' + uuid.uuid4().hex[:16])
    work.mkdir(parents=True, mode=0o700)
    saved, staged, marker = work / 'old.yaml', work / 'new.yaml', work / 'COMMIT'
    rollback = work / 'rollback.sh'
    saved.write_text(text)
    staged.write_text(new_text)
    os.chmod(saved, 0o600)
    os.chmod(staged, old_stat.st_mode & 0o777)
    os.chown(staged, old_stat.st_uid, old_stat.st_gid)
    armed = False
    changed = False
    samefs = None
    try:
        isolated_configtest(staged)
        rollback.write_text('#!/bin/sh\n'
                            f'if [ ! -e {marker} ]; then\n'
                            f'  cp {saved} {staged} && chmod {old_stat.st_mode & 0o777:o} {staged} && '
                            f'chown {old_stat.st_uid}:{old_stat.st_gid} {staged} && '
                            f'cp -p {staged} {path}.derp-rollback && mv {path}.derp-rollback {path} && '
                            'systemctl restart headscale || exit 1\n'
                            'fi\n'
                            f'if [ -e {marker} ]; then rm -f {saved} {staged}; fi\n')
        os.chmod(rollback, 0o700)
        run(['systemd-run', '--quiet', '--unit=hs-dr-derp-' + work.name[5:],
             '--on-active=300s', '/bin/sh', str(rollback)])
        armed = True
        # Stage in /etc on the same filesystem before the atomic replacement.
        samefs = path.parent / ('.headscale-dr-' + work.name + '.yaml')
        shutil_copy = run(['cp', '-p', str(staged), str(samefs)], check=False)
        if shutil_copy.returncode:
            raise RuntimeError('could not stage the configuration')
        os.replace(samefs, path)
        changed = True
        run(['systemctl', 'restart', 'headscale'])
        for i in range(20):
            if health(domain, ip) == health(legacy, ip) == '200':
                break
            if i < 19:
                time.sleep(1)
        else:
            raise RuntimeError('Headscale HTTPS did not recover')
        if [hashlib.sha256(p.read_bytes()).hexdigest() for p in (noise, derp)] != before_keys or nodes_digest() != nodes:
            raise RuntimeError('identity or approved routes changed')
        probe = run(['curl', '--silent', '--max-time', '3', '--output', '/dev/null',
                     '--write-out', '%{http_code}', 'http://127.0.0.1:8080/derp/probe'], check=False)
        if probe.stdout != '200':
            raise RuntimeError('embedded DERP probe failed')
        print(json.dumps({'guard_dir': str(work), 'node_identity': 'unchanged',
                          'old_and_new_health': 200, 'awaiting_client_verification': True}))
    except Exception:
        if changed:
            result = run(['/bin/sh', str(rollback)], check=False)
            if result.returncode == 0 and health(legacy, ip) == '200':
                marker.touch(mode=0o600, exist_ok=True)
        elif armed:
            marker.touch(mode=0o600, exist_ok=True)
        raise
    finally:
        if samefs:
            samefs.unlink(missing_ok=True)
        if not armed:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('DERP transaction failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
