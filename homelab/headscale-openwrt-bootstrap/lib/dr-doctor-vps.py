#!/usr/bin/env python3
"""Return a non-secret, read-only Headscale/Headplane status summary."""

import hashlib
import ipaddress
import json
import re
import sqlite3
import subprocess
import sys

import yaml


def run(args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, timeout=15)


def main():
    if len(sys.argv) != 3:
        raise RuntimeError('expected stable domain and public IPv4')
    domain, ip = sys.argv[1:]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', domain) or '..' in domain:
        raise RuntimeError('invalid domain')
    ipaddress.IPv4Address(ip)
    c = yaml.safe_load(open('/etc/headscale/config.yaml'))
    key = c['noise']['private_key_path']
    with open(key, 'rb') as f:
        fingerprint = hashlib.sha256(f.read()).hexdigest()[:12]
    db = c['database']['sqlite']['path']
    conn = sqlite3.connect('file:' + db + '?mode=ro', uri=True, timeout=5)
    try:
        integrity = conn.execute('PRAGMA quick_check').fetchone()[0]
    finally:
        conn.close()
    version = run(['headscale', 'version']).stdout.splitlines()[0]
    active = run(['systemctl', 'is-active', 'headscale']).stdout.strip()
    plane = run(['systemctl', 'is-active', 'headplane']).stdout.strip()
    count = None
    listing = run(['headscale', 'nodes', 'list', '--output', 'json'])
    if listing.returncode == 0:
        count = len(json.loads(listing.stdout))
    health = run(['curl', '--silent', '--show-error', '--noproxy', '*', '--max-time', '8',
                  '--resolve', f'{domain}:443:{ip}', '--output', '/dev/null',
                  '--write-out', '%{http_code}', f'https://{domain}/health'])
    print(json.dumps({'headscale_version': version, 'headscale_service': active,
                      'headplane_service': plane, 'server_url': c.get('server_url'),
                      'noise_sha256_prefix': fingerprint, 'sqlite_quick_check': integrity,
                      'node_count': count, 'derp_ipv4': c.get('derp', {}).get('server', {}).get('ipv4'),
                      'magic_dns_base_domain': c.get('dns', {}).get('base_domain'),
                      'target_https_health': health.stdout if health.returncode == 0 else 'failed'},
                     sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('doctor failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
