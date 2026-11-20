#!/usr/bin/env python3
"""Run one guarded daed change and acknowledge only after independent SSH checks."""

import argparse
import hashlib
import ipaddress
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path


SCRIPT = Path(__file__).with_name('one-tailnet-daed.sh')


def ssh(host, command, data=None, timeout=20):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                           '-o', 'ConnectionAttempts=1', host, command], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--sqlite-dir', required=True)
    p.add_argument('--domain', required=True)
    p.add_argument('--old-domain', required=True)
    p.add_argument('--primary-filter', required=True)
    p.add_argument('--secondary-filter', required=True)
    p.add_argument('--secondary-tag', required=True)
    p.add_argument('--peer-ip', required=True)
    p.add_argument('--check-host', action='append', default=[])
    p.add_argument('--independent-access-verified', action='store_true',
                   help='required: operator has verified a recovery path independent of this daed')
    args = p.parse_args()
    if not args.independent_access_verified:
        raise RuntimeError('daed restart requires an independently verified recovery path')
    for value in [args.host] + args.check_host:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
            raise RuntimeError('invalid SSH alias')
    for value in (args.domain, args.old_domain, args.primary_filter,
                  args.secondary_filter, args.secondary_tag):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value):
            raise RuntimeError('invalid routing argument')
    if not re.fullmatch(r'/tmp/headscale-dr-sqlite\.[A-Za-z0-9]+', args.sqlite_dir):
        raise RuntimeError('invalid SQLite tool path')
    ipaddress.ip_address(args.peer_ip)
    code = SCRIPT.read_bytes()
    digest = hashlib.sha256(code).hexdigest()
    remote_script = '/tmp/one-tailnet-daed-' + digest[:12] + '.sh'
    q = shlex.quote
    upload = (f'umask 077; if [ -e {q(remote_script)} ]; then '
              f'test "$(sha256sum {q(remote_script)} | cut -d " " -f 1)" = {digest}; '
              f'else set -C; cat > {q(remote_script)}; chmod 700 {q(remote_script)}; '
              f'test "$(sha256sum {q(remote_script)} | cut -d " " -f 1)" = {digest}; fi')
    if ssh(args.host, upload, data=code).returncode:
        raise RuntimeError('could not securely transfer the daed guard')
    parameters = [args.sqlite_dir, args.domain, args.old_domain, args.primary_filter,
                  args.secondary_filter, args.secondary_tag, '300']
    start = 'DAED_INDEPENDENT_ACCESS_VERIFIED=1 ' + ' '.join(
        q(x) for x in ['sh', remote_script, 'start'] + parameters)
    result = ssh(args.host, start, timeout=30)
    if result.returncode:
        raise RuntimeError('daed preparation failed: ' + result.stderr.decode(errors='replace')[-300:])
    match = re.fullmatch(r'guarded_work=(/tmp/one-tailnet-daed\.[A-Za-z0-9]+)\s*',
                         result.stdout.decode(errors='replace'))
    if not match:
        raise RuntimeError('guard did not return a work directory')
    work = match.group(1)
    print('guard armed:', args.host, work, flush=True)
    try:
        status_command = ' '.join(q(x) for x in ['sh', remote_script, 'status', work])
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            try:
                state = ssh(args.host, status_command, timeout=12)
            except subprocess.TimeoutExpired:
                time.sleep(3)
                continue
            state_text = state.stdout.decode(errors='replace')
            if 'FAILED' in state_text or 'ROLLBACK' in state_text:
                raise RuntimeError('router reported a failed change or rollback')
            if state.returncode == 0 and 'APPLY_OK' in state_text:
                break
            time.sleep(3)
        else:
            raise RuntimeError('router did not finish local verification in time')

        # A new SSH connection after the restart and a real tailnet ping do
        # not by themselves establish an independent recovery channel.
        for attempt in range(3):
            probe = ssh(args.host, 'curl -fsS --max-time 8 -o /dev/null ' +
                        q('https://' + args.domain + '/health'), timeout=12)
            if probe.returncode:
                raise RuntimeError('control endpoint failed during the settling period')
            if attempt < 2:
                time.sleep(10)
        ping_command = ' '.join(q(x) for x in ['tailscale', 'ping', '--c', '2',
                                              '--timeout', '8s', '--until-direct=false',
                                              args.peer_ip])
        ping = ssh(args.host, ping_command, timeout=25)
        if ping.returncode:
            raise RuntimeError('tailnet peer ping failed')
        for other in args.check_host:
            if ssh(other, 'true', timeout=12).returncode:
                raise RuntimeError('independent management SSH failed for ' + other)

        database = args.sqlite_dir + '/root/usr/bin/sqlite3'
        library = args.sqlite_dir + '/root/usr/lib'
        query = ("SELECT s.tag,gs.name_filter_regex FROM group_subscriptions gs "
                 "JOIN groups g ON g.id=gs.group_id JOIN subscriptions s ON s.id=gs.subscription_id "
                 "WHERE g.name='tailscale-ctrl' ORDER BY s.tag; "
                 "SELECT instr(routing,'domain(suffix: " + args.domain + ") -> tailscale-ctrl') "
                 "FROM routings WHERE selected=1; PRAGMA quick_check;")
        cmd = 'LD_LIBRARY_PATH=' + q(library) + ' ' + q(database) + ' -readonly /etc/daed/wing.db ' + q(query)
        inspected = ssh(args.host, cmd)
        rows = inspected.stdout.decode(errors='replace').splitlines()
        if (inspected.returncode or len(rows) != 4 or rows[-1] != 'ok'
                or not rows[-2].isdigit() or int(rows[-2]) <= 0
                or not any(row.endswith('|' + args.primary_filter) for row in rows[:2])
                or not any(row == args.secondary_tag + '|' + args.secondary_filter for row in rows[:2])):
            raise RuntimeError('live database did not match the intended control group and routing')
        commit_command = ' '.join(q(x) for x in ['sh', remote_script, 'commit', work])
        if ssh(args.host, commit_command).returncode:
            raise RuntimeError('could not acknowledge the guarded change')
        details = ping.stdout.decode(errors='replace').lower()
        route = 'derp' if 'derp' in details else 'direct' if 'direct' in details else 'available'
        print('committed:', args.host, 'database=ok control_exits=2 tailnet_ping=' + route, flush=True)
    except Exception:
        try:
            command = ' '.join(q(x) for x in ['sh', remote_script, 'rollback', work])
            rollback = ssh(args.host, command, timeout=35)
            print('rollback requested:', 'completed' if rollback.returncode == 0 else 'timer remains armed',
                  file=sys.stderr, flush=True)
        except (OSError, subprocess.TimeoutExpired):
            print('rollback timer remains armed', file=sys.stderr, flush=True)
        raise


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('one-tailnet-control:', str(exc), file=sys.stderr)
        sys.exit(1)
