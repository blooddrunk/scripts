#!/usr/bin/env python3
"""Add official DERP fallback only after real clients receive the new map."""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path


HELPER = Path(__file__).resolve().parent / 'lib/dr-derp-stage.py'
CONFIGTEST_HELPER = Path(__file__).resolve().parent / 'lib/dr-isolated-configtest.py'


def ssh(host, command, timeout=20):
    config = os.environ.get('HEADSCALE_DR_SSH_CONFIG')
    return subprocess.run(['ssh'] + (['-F', config] if config else []) +
                          ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', host, command],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--authority', required=True)
    p.add_argument('--domain', required=True)
    p.add_argument('--legacy-domain', required=True)
    p.add_argument('--ip', required=True)
    p.add_argument('--client', action='append', required=True)
    a = p.parse_args()
    for host in [a.authority] + a.client:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
            raise RuntimeError('invalid SSH alias')
    command = ' '.join(shlex.quote(x) for x in
                       ['python3', '-c', CONFIGTEST_HELPER.read_text() + '\n' + HELPER.read_text(),
                        a.domain, a.legacy_domain, a.ip])
    staged = ssh(a.authority, command, timeout=90)
    if staged.returncode:
        raise RuntimeError('server transaction failed: ' + staged.stderr.decode(errors='replace')[-250:])
    work = json.loads(staged.stdout)['guard_dir']
    if not re.fullmatch(r'/run/headscale-dr/derp-[a-f0-9]{16}', work):
        raise RuntimeError('unexpected rollback guard directory')
    print('DERP fallback staged; rollback guard armed', flush=True)
    try:
        deadline = time.monotonic() + 100
        remaining = set(a.client)
        while remaining and time.monotonic() < deadline:
            for host in list(remaining):
                try:
                    result = ssh(host, 'tailscale debug derp-map', timeout=15)
                    if result.returncode:
                        continue
                    regions = json.loads(result.stdout).get('Regions', {})
                    ids = {int(x) for x in regions}
                    if {901, 999}.issubset(ids) and any(0 < x < 900 for x in ids):
                        remaining.remove(host)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    continue
            if remaining:
                time.sleep(5)
        if remaining:
            raise RuntimeError('official DERP map not confirmed on: ' + ', '.join(sorted(remaining)))
        for host in a.client:
            state = ssh(host, "tailscale status --json | jsonfilter -e '@.BackendState'")
            if state.returncode or state.stdout.decode().strip() != 'Running':
                raise RuntimeError('client not running after DERP update: ' + host)
        commit = ssh(a.authority, 'umask 077; : > ' + shlex.quote(work + '/COMMIT'))
        if commit.returncode:
            raise RuntimeError('could not acknowledge the DERP update')
        print('official DERP fallback verified on', len(a.client), 'clients; custom regions retained')
    except Exception:
        try:
            result = ssh(a.authority, '/bin/sh ' + shlex.quote(work + '/rollback.sh'), timeout=45)
            print('rollback:', 'requested' if result.returncode == 0 else 'timer remains armed',
                  file=sys.stderr, flush=True)
        except (OSError, subprocess.TimeoutExpired):
            print('rollback timer remains armed', file=sys.stderr, flush=True)
        raise


if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('enable-official-derp:', str(exc), file=sys.stderr)
        sys.exit(1)
