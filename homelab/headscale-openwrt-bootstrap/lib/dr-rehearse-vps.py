#!/usr/bin/env python3
"""Start a restored copy only inside an isolated systemd network namespace."""

import hashlib
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml


def run(args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=25, check=check)


def node_digest(entries):
    data = sorted((str(n['id']), n['machine_key'], n['node_key'],
                   tuple(n.get('ip_addresses', [])), tuple(sorted(n.get('approved_routes', []))))
                  for n in entries)
    return hashlib.sha256(repr(data).encode()).hexdigest()


def rewrite(value, work):
    if isinstance(value, dict):
        return {key: rewrite(item, work) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item, work) for item in value]
    if isinstance(value, str) and value.startswith('/'):
        for prefix in ('/etc/headscale', '/var/lib/headscale'):
            if value == prefix or value.startswith(prefix + '/'):
                return str(work / 'source' / value.lstrip('/'))
        if value.startswith('/var/run/headscale/'):
            return str(work / 'scratch' / value.lstrip('/'))
        raise RuntimeError('configuration references a path outside the snapshot')
    return value


def main():
    if os.geteuid() or len(sys.argv) != 4:
        raise RuntimeError('expected root, staged snapshot, target IPv4 and node digest')
    stage_path, ip, expected_nodes = sys.argv[1:]
    if not re.fullmatch(r'/var/lib/headscale-dr/staged/snapshot-[a-f0-9]{16}', stage_path):
        raise RuntimeError('invalid staged snapshot path')
    ipaddress.IPv4Address(ip)
    if not re.fullmatch(r'[a-f0-9]{64}', expected_nodes):
        raise RuntimeError('invalid expected node digest')
    stage = Path(stage_path)
    manifest = json.loads((stage / 'manifest.json').read_text())
    for name, expected in manifest.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('unsafe staged manifest path')
        path = stage / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('staged snapshot changed or failed its manifest')
    meta = json.loads((stage / 'metadata.json').read_text())
    if meta.get('database_method') != 'sqlite-online-backup':
        raise RuntimeError('unsupported database snapshot')
    previous_service = run(['systemctl', 'is-active', 'headscale'], check=False).stdout.strip()
    previous_enabled = run(['systemctl', 'is-enabled', 'headscale'], check=False).stdout.strip()
    legacy_health = run(['curl', '--silent', '--max-time', '5', '--output', '/dev/null',
                         '--write-out', '%{http_code}', 'http://127.0.0.1:8080/health'], check=False)
    if previous_service == 'active':
        if legacy_health.stdout != '200':
            raise RuntimeError('legacy service health preflight failed')
    elif previous_enabled != 'masked' or legacy_health.stdout == '200':
        raise RuntimeError('inactive standby is not persistently fenced')

    run_root = Path('/run/headscale-dr')
    run_root.mkdir(mode=0o700, exist_ok=True)
    operation = uuid.uuid4().hex[:16]
    work = run_root / ('rehearsal-' + operation)
    work.mkdir(mode=0o700)
    unit = 'hs-dr-rehearsal-' + operation
    cleanup = run_root / ('rehearsal-' + operation + '-cleanup.sh')
    started = False
    timer_armed = False
    try:
        for subtree in ('etc/headscale', 'var/lib/headscale'):
            source = stage / 'source' / subtree
            destination = work / 'source' / subtree
            destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copytree(source, destination, symlinks=False)
        source_config = yaml.safe_load((work / 'source/etc/headscale/config.yaml').read_text())
        config = rewrite(source_config, work)
        config['derp']['server']['ipv4'] = ip
        config['derp']['urls'] = []
        config['derp']['auto_update_enabled'] = False
        (work / 'scratch/var/run/headscale').mkdir(parents=True, mode=0o700)
        config_path = work / 'config.yaml'
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        os.chmod(config_path, 0o600)
        noise = work / 'source/var/lib/headscale/noise_private.key'
        if hashlib.sha256(noise.read_bytes()).hexdigest() != meta['noise_sha256']:
            raise RuntimeError('restored Noise identity differs')
        conn = sqlite3.connect('file:' + str(work / 'source/var/lib/headscale/db.sqlite') +
                               '?mode=ro&immutable=1', uri=True)
        try:
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('restored SQLite quick_check failed')
        finally:
            conn.close()
        run(['headscale', '-c', str(config_path), 'configtest'])
        cleanup.write_text('#!/bin/sh\n' +
                           f'systemctl stop {unit} >/dev/null 2>&1 || true\n' +
                           f'rm -rf {work}\n')
        os.chmod(cleanup, 0o700)
        run(['systemd-run', '--quiet', '--unit=' + unit + '-cleanup', '--on-active=120s',
             '/bin/sh', str(cleanup)])
        timer_armed = True
        run(['systemd-run', '--quiet', '--unit=' + unit,
             '--property=PrivateNetwork=yes', '--property=RuntimeMaxSec=90s',
             '--property=NoNewPrivileges=yes', '--property=ProtectSystem=strict',
             '--property=ReadWritePaths=' + str(work),
             '/usr/bin/headscale', '-c', str(config_path), 'serve'])
        started = True
        pid = 0
        for attempt in range(25):
            status = run(['systemctl', 'show', unit, '-p', 'MainPID', '--value'], check=False)
            pid = int(status.stdout.strip() or '0')
            if pid:
                health = run(['nsenter', '-t', str(pid), '-n', 'curl', '--silent', '--max-time', '2',
                              '--output', '/dev/null', '--write-out', '%{http_code}',
                              'http://127.0.0.1:8080/health'], check=False)
                if health.stdout == '200':
                    break
            if attempt < 24:
                time.sleep(1)
        else:
            raise RuntimeError('isolated restored service did not become healthy')
        links = run(['nsenter', '-t', str(pid), '-n', 'ip', '-o', 'link', 'show']).stdout.splitlines()
        if len(links) != 1 or not re.search(r'^\d+: lo:', links[0]):
            raise RuntimeError('rehearsal namespace is not loopback-only')
        derp = run(['nsenter', '-t', str(pid), '-n', 'curl', '--silent', '--max-time', '3',
                    '--output', '/dev/null', '--write-out', '%{http_code}',
                    'http://127.0.0.1:8080/derp/probe'], check=False)
        if derp.stdout != '200':
            raise RuntimeError('restored embedded DERP probe failed')
        listed = run(['headscale', '-c', str(config_path), 'nodes', 'list', '-o', 'json'])
        restored_nodes = json.loads(listed.stdout)
        if node_digest(restored_nodes) != expected_nodes:
            raise RuntimeError('restored nodes, keys, IPs or approved routes differ from authority')
        after = run(['curl', '--silent', '--max-time', '5', '--output', '/dev/null',
                     '--write-out', '%{http_code}', 'http://127.0.0.1:8080/health'], check=False)
        current_service = run(['systemctl', 'is-active', 'headscale'], check=False).stdout.strip()
        current_enabled = run(['systemctl', 'is-enabled', 'headscale'], check=False).stdout.strip()
        if (current_service != previous_service or current_enabled != previous_enabled
                or (previous_service == 'active' and after.stdout != '200')
                or (previous_service != 'active' and after.stdout == '200')):
            raise RuntimeError('existing destination service changed during rehearsal')
        print(json.dumps({'isolated_restore': 'passed', 'network': 'loopback-only',
                          'headscale_health': 200, 'derp_probe': 200,
                          'nodes_and_approved_routes': len(restored_nodes),
                          'noise_sha256_prefix': meta['noise_sha256'][:12],
                          'derp_ipv4_in_rehearsal': ip,
                          'previous_headscale_service': previous_service,
                          'legacy_health': 200 if previous_service == 'active' else None,
                          'public_authority_started': False}, sort_keys=True))
    finally:
        if started:
            run(['systemctl', 'stop', unit], check=False)
        shutil.rmtree(work, ignore_errors=True)
        if not timer_armed:
            cleanup.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('isolated rehearsal failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
