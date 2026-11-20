#!/usr/bin/env python3
"""Persistently fence a Headscale writer, with a pre-commit recovery timer.

Root-only helper invoked over an authenticated SSH channel. A sealed fence
never self-recovers: a second writer may already have accepted changes.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def run(args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30, check=check)


def service_state():
    listeners = run(['ss', '-H', '-ltn', 'sport = :8080'], False)
    if listeners.returncode:
        raise RuntimeError('cannot verify the Headscale listener')
    return {'control_listener_present': bool(listeners.stdout.strip()),
            'headscale_active': run(['systemctl', 'is-active', 'headscale'], False).stdout.strip(),
            'headscale_enabled': run(['systemctl', 'is-enabled', 'headscale'], False).stdout.strip(),
            'headplane_active': run(['systemctl', 'is-active', 'headplane'], False).stdout.strip()}


def main():
    if os.geteuid() or len(sys.argv) != 3:
        raise RuntimeError('requires root, action and operation ID')
    action, op = sys.argv[1:]
    if action not in ('fence', 'seal', 'status', 'abort-unsealed', 'release-unwritten') or not re.fullmatch(r'[a-f0-9]{16}', op):
        raise RuntimeError('invalid fence action or operation ID')
    root = Path('/var/lib/headscale-dr/fences')
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    operation = root / op
    seal = operation / 'SEALED'
    if action == 'fence':
        operation.mkdir(mode=0o700)
    elif not operation.is_dir():
        raise RuntimeError('unknown fence operation')
    lock_path = operation / 'lock'
    lock_path.touch(mode=0o600, exist_ok=True)
    with lock_path.open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == 'fence':
            state = service_state()
            if state['headscale_active'] != 'active' or state['headscale_enabled'] == 'masked':
                raise RuntimeError('source Headscale must be active and unmasked')
            hp_enabled = run(['systemctl', 'is-enabled', 'headplane'], False).stdout.strip() == 'enabled'
            (operation / 'previous.json').write_text(json.dumps({'headplane_enabled': hp_enabled,
                                                                  'headplane_active': state['headplane_active'] == 'active'}))
            # flock serializes this timer with seal/release, preventing the timer
            # from starting the former writer after a successful seal.
            guard = operation / 'recover-unsealed.sh'
            guard.write_text('#!/bin/sh\n'
                'exec 9>"' + str(lock_path) + '"\nflock -x 9\n'
                '[ -e "' + str(seal) + '" ] && exit 0\n'
                'systemctl unmask headscale >/dev/null 2>&1 || exit 1\n'
                'systemctl start headscale >/dev/null 2>&1 || exit 1\n' +
                ('systemctl enable headplane >/dev/null 2>&1 || exit 1\n' if hp_enabled else '') +
                ('systemctl start headplane >/dev/null 2>&1 || exit 1\n' if state['headplane_active'] == 'active' else '') +
                'touch "' + str(operation / 'RECOVERED') + '"\n')
            os.chmod(guard, 0o700)
            run(['systemd-run', '--quiet', '--unit=hs-dr-fence-' + op, '--on-active=420s',
                 '/bin/sh', str(guard)])
            if state['headplane_active'] == 'active':
                run(['systemctl', 'stop', 'headplane'])
            if hp_enabled:
                run(['systemctl', 'disable', 'headplane'])
            run(['systemctl', 'mask', '--now', 'headscale'])
            # The mask must survive a host restart before a destination writer
            # can be allowed to start.
            os.sync()
            state = service_state()
            if state['headscale_active'] == 'active' or state['control_listener_present'] or state['headscale_enabled'] != 'masked' or state['headplane_active'] == 'active':
                raise RuntimeError('fence verification failed; unsealed timer is armed')
        elif action == 'seal':
            state = service_state()
            if (operation / 'RECOVERED').exists() or state['headscale_active'] == 'active' or state['control_listener_present'] or state['headscale_enabled'] != 'masked':
                raise RuntimeError('cannot seal a recovered or running source')
            seal.touch(mode=0o600, exist_ok=False)
            os.sync()
        elif action in ('abort-unsealed', 'release-unwritten'):
            # Caller must first independently prove the destination never started
            # the restored writer. Never use this after any destination write.
            if action == 'release-unwritten' and not seal.exists():
                raise RuntimeError('source is not sealed')
            if action == 'abort-unsealed' and seal.exists():
                raise RuntimeError('source was sealed; unsealed abort is unsafe')
            state = service_state()
            if state['headscale_active'] == 'active' or state['headscale_enabled'] != 'masked':
                raise RuntimeError('source is not fenced')
            # Prevent the already-armed timer from restarting this writer later,
            # potentially after a subsequent successful promotion.
            if action == 'abort-unsealed':
                seal.touch(mode=0o600, exist_ok=False)
            previous = json.loads((operation / 'previous.json').read_text())
            run(['systemctl', 'unmask', 'headscale'])
            run(['systemctl', 'start', 'headscale'])
            if previous['headplane_enabled']:
                run(['systemctl', 'enable', 'headplane'])
            if previous['headplane_active']:
                run(['systemctl', 'start', 'headplane'])
            (operation / 'RELEASED').touch(mode=0o600, exist_ok=False)
        state = service_state()
        state.update({'operation': op, 'sealed': seal.exists(),
                      'recovered': (operation / 'RECOVERED').exists(),
                      'released': (operation / 'RELEASED').exists()})
        print(json.dumps(state, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('fence failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
