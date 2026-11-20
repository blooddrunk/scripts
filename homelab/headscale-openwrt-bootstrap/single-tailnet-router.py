#!/usr/bin/env python3
"""Keep the active OpenWrt Tailscale profile and remove named inactive profiles."""

import argparse
import hashlib
import ipaddress
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path


DR = Path(__file__).with_name('headscale-dr.py')


def ssh(host, command, data=None, timeout=25):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', host, command],
                          input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--control-domain', required=True)
    p.add_argument('--retire-domain', action='append', required=True)
    p.add_argument('--backup', required=True)
    p.add_argument('--passphrase-file', required=True)
    p.add_argument('--peer-ip', required=True)
    p.add_argument('--check-host', action='append', default=[])
    p.add_argument('--dry-run', action='store_true', help='verify prerequisites without changing profiles')
    a = p.parse_args()
    for host in [a.host] + a.check_host:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
            raise RuntimeError('invalid SSH alias')
    for domain in [a.control_domain] + a.retire_domain:
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', domain) or '..' in domain:
            raise RuntimeError('invalid domain')
    ipaddress.ip_address(a.peer_ip)
    backup = Path(a.backup).expanduser()
    if not backup.name.startswith(a.host + '-') or not backup.name.endswith('.gpg'):
        raise RuntimeError('backup filename does not identify this router')
    checked = subprocess.run([sys.executable, str(DR), '--passphrase-file', a.passphrase_file,
                              'verify', str(backup)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if checked.returncode or json.loads(checked.stdout).get('snapshot_type') != 'openwrt':
        raise RuntimeError('encrypted router backup failed verification')
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    remote_backup = '/root/headscale-dr-backups/' + backup.name
    q = shlex.quote
    check = ssh(a.host, 'sha256sum ' + q(remote_backup))
    if check.returncode or check.stdout.decode().split()[0] != digest:
        raise RuntimeError('router-local encrypted backup is missing or differs')

    def profiles():
        result = ssh(a.host, 'tailscale switch --list --json')
        if result.returncode:
            raise RuntimeError('cannot inspect live Tailscale profiles')
        return json.loads(result.stdout)

    def verify_active():
        status = ssh(a.host, 'tailscale status --json')
        if status.returncode:
            raise RuntimeError('cannot read Tailscale status')
        node = json.loads(status.stdout)
        if node.get('BackendState') != 'Running':
            raise RuntimeError('Tailscale backend is not running')
        prefs = ssh(a.host, "tailscale debug prefs | jsonfilter -e '@.ControlURL'")
        if prefs.returncode or prefs.stdout.decode().strip() != 'https://' + a.control_domain:
            raise RuntimeError('unexpected active control URL')
        return node['Self']['TailscaleIPs']

    original_ips = verify_active()
    inventory = profiles()
    selected = [x for x in inventory if x.get('selected')]
    if len(selected) != 1 or selected[0].get('tailnet') != a.control_domain:
        raise RuntimeError('active profile is not uniquely identified')
    active_id = selected[0]['id']
    if not re.fullmatch(r'[a-f0-9]+', active_id):
        raise RuntimeError('unexpected active profile ID')
    inactive = [x for x in inventory if not x.get('selected')]
    if any(x.get('tailnet') not in a.retire_domain or
           not re.fullmatch(r'[a-f0-9]+', x.get('id', '')) for x in inactive):
        raise RuntimeError('an unexpected inactive profile requires manual review')
    if ssh(a.host, 'uci changes tailscale-bootstrap').stdout.strip():
        raise RuntimeError('pending UCI changes must be resolved first')
    uci = ssh(a.host, 'uci show tailscale-bootstrap')
    if uci.returncode:
        raise RuntimeError('cannot inspect bootstrap configuration')
    entries = {}
    for line in uci.stdout.decode(errors='replace').splitlines():
        match = re.fullmatch(r'tailscale-bootstrap\.([A-Za-z0-9_]+)\.(ts_id|login_server)=(.*)', line)
        if match:
            values = shlex.split(match.group(3))
            if len(values) != 1:
                raise RuntimeError('unsupported UCI profile value')
            entries.setdefault(match.group(1), {})[match.group(2)] = values[0]
    active_sections = [name for name, entry in entries.items() if entry.get('ts_id') == active_id]
    if len(active_sections) != 1:
        raise RuntimeError('active profile has no unique bootstrap entry')
    inactive_ids = {x['id'] for x in inactive}
    remove_sections = []
    for name, entry in entries.items():
        if name == active_sections[0]:
            continue
        if entry.get('ts_id') not in inactive_ids or entry.get('login_server', '').removeprefix('https://') not in a.retire_domain:
            raise RuntimeError('unexpected bootstrap profile requires manual review')
        remove_sections.append(name)
    if a.dry_run:
        print('single profile preflight:', a.host, 'inactive=' + str(len(inactive)),
              'bootstrap_entries_to_remove=' + str(len(remove_sections)))
        return

    for old in inactive:
        result = ssh(a.host, 'tailscale switch remove ' + q(old['id']))
        if result.returncode or verify_active() != original_ips:
            raise RuntimeError('inactive profile removal did not preserve the active node')
    remaining = profiles()
    if len(remaining) != 1 or not remaining[0].get('selected') or remaining[0]['id'] != active_id:
        raise RuntimeError('profile inventory is not a single active profile')
    ping = ssh(a.host, ' '.join(q(x) for x in ['tailscale', 'ping', '--c', '1',
                                               '--timeout', '8s', '--until-direct=false', a.peer_ip]),
               timeout=20)
    if ping.returncode:
        raise RuntimeError('tailnet peer ping failed after profile removal')
    for host in a.check_host:
        if ssh(host, 'true', timeout=12).returncode:
            raise RuntimeError('management SSH failed after profile removal: ' + host)

    # Only non-secret bootstrap metadata is changed here. Do not alter
    # tailscaled.state, log out, or restart the proxy/daemon.
    section = active_sections[0]
    commands = ['set -eu', 'uci set ' + q('tailscale-bootstrap.' + section + '.login_server=https://' + a.control_domain)]
    commands.extend('uci delete ' + q('tailscale-bootstrap.' + name) for name in remove_sections)
    commands += ['uci set ' + q('tailscale-bootstrap.watchdog.enabled=0'), 'uci commit tailscale-bootstrap']
    update = ssh(a.host, '\n'.join(commands))
    if update.returncode:
        raise RuntimeError('profile metadata update failed; encrypted backup remains available')
    # The state file is non-secret; update it atomically after UCI succeeds.
    state = ssh(a.host, 'cat /etc/tailscale-bootstrap/state.json')
    if state.returncode:
        raise RuntimeError('bootstrap state file is unavailable')
    state_data = json.loads(state.stdout)
    if state_data.get('managed_by') != 'headscale-openwrt-bootstrap':
        raise RuntimeError('refusing to update an unrelated state file')
    state_data.update({'login_server': 'https://' + a.control_domain,
                       'profiles': 'https://' + a.control_domain, 'failover_enabled': 'no'})
    encoded = (json.dumps(state_data, ensure_ascii=False, indent=2) + '\n').encode()
    write = ssh(a.host, "umask 077; t=/etc/tailscale-bootstrap/.state.single.tmp; "
                       "test ! -e \"$t\"; trap 'rm -f \"$t\"' EXIT HUP INT TERM; "
                       "cat > \"$t\"; chmod 600 \"$t\"; mv \"$t\" /etc/tailscale-bootstrap/state.json; "
                       "trap - EXIT HUP INT TERM", data=encoded)
    if write.returncode or verify_active() != original_ips:
        raise RuntimeError('state update failed or active identity changed')
    print('single profile verified:', a.host, 'retired=' + str(len(inactive)),
          'control=https://' + a.control_domain, 'watchdog=disabled')


if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('single-tailnet-router:', str(exc), file=sys.stderr)
        sys.exit(1)
