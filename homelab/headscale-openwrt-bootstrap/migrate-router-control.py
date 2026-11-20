#!/usr/bin/env python3
"""Guard one OpenWrt control-URL reauthentication without restarting daed."""

import argparse
import hashlib
import ipaddress
import json
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path


HELPER = Path(__file__).resolve().parent / 'lib/dr-migrate-router.sh'


def ssh(host, command, data=None, timeout=20):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                           '-o', 'ConnectionAttempts=1', host, command], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def nodes(host):
    result = ssh(host, 'headscale nodes list --output json')
    if result.returncode:
        raise RuntimeError('cannot inspect authority nodes')
    return json.loads(result.stdout)


def identity(items, node_id):
    found = [node for node in items if str(node.get('id')) == str(node_id)]
    if len(found) != 1:
        raise RuntimeError('authority node ID is not unique')
    node = found[0]
    return {'id': node['id'], 'machine': hashlib.sha256(node['machine_key'].encode()).hexdigest(),
            'node_key': hashlib.sha256(node['node_key'].encode()).hexdigest(),
            'ips': node.get('ip_addresses', []),
            'routes': sorted(node.get('approved_routes', []))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--authority', required=True)
    parser.add_argument('--user-id', required=True, type=int)
    parser.add_argument('--node-id', required=True, type=int)
    parser.add_argument('--old-url', required=True)
    parser.add_argument('--new-url', required=True)
    parser.add_argument('--peer-ip', required=True)
    parser.add_argument('--check-host', action='append', default=[])
    args = parser.parse_args()
    for name in [args.host, args.authority] + args.check_host:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise RuntimeError('invalid SSH alias')
    for url in (args.old_url, args.new_url):
        if not re.fullmatch(r'https://[A-Za-z0-9][A-Za-z0-9.-]*', url) or '..' in url:
            raise RuntimeError('invalid control URL')
    ipaddress.ip_address(args.peer_ip)
    initial = identity(nodes(args.authority), args.node_id)
    status = ssh(args.host, 'tailscale status --json')
    if status.returncode:
        raise RuntimeError('cannot inspect router status')
    self_status = json.loads(status.stdout).get('Self', {})
    if self_status.get('TailscaleIPs', []) != initial['ips']:
        raise RuntimeError('router IPs do not match the expected authority node')
    for host in args.check_host:
        if ssh(host, 'true', timeout=12).returncode:
            raise RuntimeError('management SSH preflight failed for ' + host)

    data = HELPER.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    script = '/tmp/headscale-router-migrate-' + digest[:12] + '.sh'
    q = shlex.quote
    upload = (f'umask 077; if [ -e {q(script)} ]; then '
              f'test "$(sha256sum {q(script)} | cut -d " " -f 1)" = {digest}; '
              f'else set -C; cat > {q(script)}; chmod 700 {q(script)}; '
              f'test "$(sha256sum {q(script)} | cut -d " " -f 1)" = {digest}; fi')
    if ssh(args.host, upload, data=data).returncode:
        raise RuntimeError('router guard transfer failed')
    key_result = ssh(args.authority, 'headscale preauthkeys create --user ' + str(args.user_id) +
                     ' --reusable --expiration 20m --output json')
    if key_result.returncode:
        raise RuntimeError('could not create the short-lived migration key')
    key_data = json.loads(key_result.stdout)
    key, key_id = key_data['key'], str(key_data['id'])
    key_bytes = (key + '\n').encode()
    operation = uuid.uuid4().hex[:16]
    work = '/tmp/headscale-router-migrate.' + operation
    may_have_started = False
    safe_to_expire = False
    try:
        command = ' '.join(q(x) for x in ['sh', script, 'start', args.old_url, args.new_url,
                                         '300', operation, str(len(key_bytes))])
        may_have_started = True
        start = ssh(args.host, command, data=key_bytes, timeout=40)
        if start.returncode:
            # A failed SSH can occur after the detached guard has started.
            # Keep the key valid until rollback can be verified or it expires.
            raise RuntimeError('router did not confirm the guarded start')
        if start.stdout.decode(errors='replace').strip() != 'guarded_work=' + work:
            raise RuntimeError('router returned an unexpected guard identifier')
        print('guard armed:', args.host, work, flush=True)
        status_command = ' '.join(q(x) for x in ['sh', script, 'status', work])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                probe = ssh(args.host, status_command, timeout=12)
            except subprocess.TimeoutExpired:
                time.sleep(3)
                continue
            text = probe.stdout.decode(errors='replace')
            if 'FAILED' in text or 'ROLLBACK' in text:
                raise RuntimeError('router reported failed reauthentication or rollback')
            if probe.returncode == 0 and 'APPLY_OK' in text:
                break
            time.sleep(3)
        else:
            raise RuntimeError('router did not confirm reauthentication in time')
        ping = ssh(args.host, ' '.join(q(x) for x in ['tailscale', 'ping', '--c', '2',
                                                       '--timeout', '8s', '--until-direct=false',
                                                       args.peer_ip]), timeout=25)
        if ping.returncode:
            raise RuntimeError('real tailnet peer ping failed')
        for host in args.check_host:
            if ssh(host, 'true', timeout=12).returncode:
                raise RuntimeError('management SSH failed for ' + host)
        current = identity(nodes(args.authority), args.node_id)
        if any(initial[k] != current[k] for k in ('id', 'machine', 'ips', 'routes')):
            raise RuntimeError('node identity, addresses or approved routes changed')
        commit = ssh(args.host, ' '.join(q(x) for x in ['sh', script, 'commit', work]))
        if commit.returncode:
            raise RuntimeError('router guard acknowledgement failed')
        safe_to_expire = True
        route = ping.stdout.decode(errors='replace').lower()
        path = 'derp' if 'derp' in route else 'direct' if 'direct' in route else 'available'
        print('migrated:', args.host, 'identity=preserved node_key_rotated=' +
              str(initial['node_key'] != current['node_key']).lower(),
              'tailnet_ping=' + path, flush=True)
    except Exception:
        if may_have_started:
            try:
                rollback = ssh(args.host, ' '.join(q(x) for x in ['sh', script, 'rollback', work]), timeout=80)
                safe_to_expire = rollback.returncode == 0
                print('rollback:', 'verified' if safe_to_expire else 'guard remains responsible',
                      file=sys.stderr, flush=True)
            except (OSError, subprocess.TimeoutExpired):
                print('rollback: guard remains responsible', file=sys.stderr, flush=True)
        else:
            safe_to_expire = True
        raise
    finally:
        if safe_to_expire:
            result = ssh(args.authority, 'headscale preauthkeys expire --id ' + key_id + ' --force')
            if result.returncode:
                print('temporary key expires automatically within 20 minutes', file=sys.stderr)
        else:
            print('temporary key remains valid for the guard, at most 20 minutes', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('migrate-router-control:', str(exc), file=sys.stderr)
        sys.exit(1)
