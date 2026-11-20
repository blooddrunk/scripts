#!/usr/bin/env python3
"""Supervised Headscale promotion with persistent fencing and guarded rollback.

This deliberately does not elect a primary automatically. Both SSH endpoints,
preinstalled compatible software, and a private Cloudflare token are required.
"""
import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent
DR = BASE / 'headscale-dr.py'
FENCE = BASE / 'lib/dr-fence-vps.py'
ACTIVATE = BASE / 'lib/dr-activate-vps.py'
RELAY = BASE / 'lib/dr-relay-vps.py'


def ssh_command(program='ssh'):
    config = os.environ.get('HEADSCALE_DR_SSH_CONFIG')
    return [program] + (['-F', config] if config else [])


def command(args, timeout=60, stdin=None):
    result = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('command failed: ' + result.stderr.strip()[-350:])
    return result.stdout.strip()


def ssh(host, remote, timeout=60):
    return command(ssh_command() + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host, remote], timeout)


def helper(host, path, args, timeout=90, writer_lock=False):
    words = ['python3', '-c', path.read_text()] + list(args)
    if writer_lock:
        words = ['flock', '-x', '/run/headscale-dr-writer.lock'] + words
    return json.loads(ssh(host, shlex.join(words), timeout))


def read_json_command(host, remote):
    return json.loads(ssh(host, remote))


def node_digest(host):
    rows = read_json_command(host, 'headscale nodes list -o json')
    identity = sorted((str(n['id']), n['machine_key'], n['node_key'],
                       tuple(n.get('ip_addresses', [])), tuple(sorted(n.get('approved_routes', []))))
                      for n in rows)
    return len(rows), hashlib.sha256(repr(identity).encode()).hexdigest()


def cf_call(token, method, path, body=None):
    request = urllib.request.Request('https://api.cloudflare.com/client/v4' + path,
        data=None if body is None else json.dumps(body).encode(), method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except (urllib.error.URLError, ValueError):
        raise RuntimeError('Cloudflare API request failed') from None
    if not result.get('success'):
        raise RuntimeError('Cloudflare API rejected the DNS operation')
    return result['result']


def cf_record(token, zone, domain):
    zones = cf_call(token, 'GET', '/zones?' + urllib.parse.urlencode({'name': zone, 'status': 'active'}))
    if len(zones) != 1:
        raise RuntimeError('Cloudflare zone lookup is not unique')
    zone_id = zones[0]['id']
    records = cf_call(token, 'GET', '/zones/' + zone_id + '/dns_records?' +
                      urllib.parse.urlencode({'name': domain, 'per_page': 100}))
    if len(records) != 1 or records[0]['type'] != 'A' or records[0].get('proxied'):
        raise RuntimeError('stable domain must have exactly one DNS-only A record')
    return zone_id, records[0]


def dns_change(token, zone, domain, expected, desired):
    zone_id, record = cf_record(token, zone, domain)
    if record['content'] == desired:
        return
    if record['content'] != expected:
        raise RuntimeError('DNS changed concurrently; refusing to overwrite it')
    cf_call(token, 'PATCH', '/zones/' + zone_id + '/dns_records/' + record['id'],
            {'content': desired, 'ttl': 60, 'proxied': False})
    _, result = cf_record(token, zone, domain)
    if result['content'] != desired or result.get('proxied'):
        raise RuntimeError('DNS update verification failed')


def snapshot(host, args):
    value = command(['python3', str(DR), '--passphrase-file', str(args.passphrase_file),
                     'snapshot', '--host', host, '--output-dir', str(args.backup_dir)], timeout=120)
    return json.loads(value)['archive']


def replicate(archive, hosts):
    digest = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    name = Path(archive).name
    for host in hosts:
        ssh(host, 'install -d -m 700 /var/backups/headscale-dr')
        command(ssh_command('scp') + ['-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', archive,
                 host + ':/var/backups/headscale-dr/' + name], timeout=90)
        result = ssh(host, 'sha256sum ' + shlex.quote('/var/backups/headscale-dr/' + name))
        if result.split()[0] != digest:
            raise RuntimeError('replicated encrypted backup hash mismatch')


def prepare(host, archive, args):
    output = command(['python3', str(DR), '--passphrase-file', str(args.passphrase_file),
                      'prepare', '--host', host, archive], timeout=120)
    return json.loads(output)['staging_dir']


def target_state(host, op):
    path = '/var/lib/headscale-dr/activations/' + op + '/state.json'
    remote = ('flock -w 60 -x /run/headscale-dr-writer.lock sh -c ' + shlex.quote(
        'install -d -m 700 /var/lib/headscale-dr/canceled; '
        'touch /var/lib/headscale-dr/canceled/' + op + '; '
        'systemctl stop headplane >/dev/null 2>&1 || true; '
        'systemctl disable headplane >/dev/null 2>&1 || true; '
        'systemctl mask --now headscale >/dev/null 2>&1 || exit 1; '
        '[ "$(systemctl is-enabled headscale)" = masked ] || exit 1; '
        '[ "$(systemctl is-active headscale)" != active ] || exit 1; '
        'sync; '
        'if [ -f ' + shlex.quote(path) + ' ]; then cat ' + shlex.quote(path) + '; else printf "{}\\n"; fi'))
    return json.loads(ssh(host, remote, timeout=100))


def client_smoke(host, domain, target_ip, peer_ip):
    prefs = read_json_command(host, 'tailscale debug prefs')
    status = read_json_command(host, 'tailscale status --json')
    derp = read_json_command(host, 'tailscale debug derp-map')
    regions = derp.get('Regions', {})
    nodes = regions.get('999', {}).get('Nodes', [])
    if not (prefs.get('ControlURL') == 'https://' + domain and status.get('BackendState') == 'Running'
            and any(n.get('IPv4') == target_ip for n in nodes)
            and any(k not in ('901', '999') for k in regions)):
        return False
    # A disco pong does not establish that WireGuard or SSH works.
    ssh(host, shlex.join(['tailscale', 'ping', '--tsmp', '--c', '2', '--timeout', '5s',
                          '--until-direct=false', peer_ip]), timeout=20)
    return True


def reject_duplicate_derp(host, target_ip):
    regions = read_json_command(host, 'tailscale debug derp-map').get('Regions', {})
    for region_id, region in regions.items():
        if region_id != '999' and any(node.get('IPv4') == target_ip for node in region.get('Nodes', [])):
            raise RuntimeError('destination also hosts DERP region ' + region_id +
                               '; remove or independently host that region before promotion')


def journal(path, **values):
    current = json.loads(path.read_text()) if path.exists() else {}
    current.update(values)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(current, sort_keys=True) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def promote(args):
    for host in (args.source, args.target, *args.routers):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
            raise RuntimeError('invalid SSH alias')
    for name in (args.domain, args.source_legacy, args.target_legacy, args.cf_zone):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) or '..' in name:
            raise RuntimeError('invalid DNS name')
    ipaddress.IPv4Address(args.source_ip)
    ipaddress.IPv4Address(args.target_ip)
    if args.source == args.target or args.source_ip == args.target_ip:
        raise RuntimeError('source and destination must differ')
    probes = {}
    for item in args.probes:
        host, separator, ip = item.partition('=')
        if not separator or host not in args.routers or host in probes:
            raise RuntimeError('each --probe must be a unique ROUTER=PEER_IP pair')
        ipaddress.ip_address(ip)
        probes[host] = ip
    if set(probes) != set(args.routers):
        raise RuntimeError('a WireGuard --probe ROUTER=PEER_IP is required for every router')
    from importlib.machinery import SourceFileLoader
    dr = SourceFileLoader('headscale_dr', str(DR)).load_module()
    passphrase = dr.private_file(args.passphrase_file)
    token = dr.private_file(args.cf_token_file).read_text().strip()
    args.passphrase_file = passphrase
    args.backup_dir = Path(args.backup_dir).expanduser()
    args.backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.backup_dir.stat().st_mode & 0o077:
        raise RuntimeError('backup directory must be private')
    ops = args.backup_dir.parent / 'operations'
    ops.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = ops / 'promote.lock'
    with lock_path.open('a+') as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        op = uuid.uuid4().hex[:16]
        log = ops / (op + '.json')
        journal(log, operation=op, source=args.source, target=args.target, source_ip=args.source_ip,
                target_ip=args.target_ip, domain=args.domain, phase='preflight')
        _, record = cf_record(token, args.cf_zone, args.domain)
        if record['content'] != args.source_ip:
            raise RuntimeError('DNS does not point to the expected source')
        recovery_status = ('if systemctl is-active --quiet hs-dr-derp-recovery || '
                           'systemctl is-active --quiet headscale-derp-recovery || '
                           'systemctl is-enabled --quiet headscale-derp-recovery; '
                           'then echo active; else echo inactive; fi')
        if ssh(args.target, recovery_status) != 'inactive':
            raise RuntimeError('destination has an active DERP recovery service; resolve its listener and region first')
        for host in args.routers:
            if not client_smoke(host, args.domain, args.source_ip, probes[host]):
                raise RuntimeError('preflight WireGuard or control verification failed on ' + host)
        reject_duplicate_derp(args.routers[0], args.target_ip)
        count, digest = node_digest(args.source)
        if count < 1:
            raise RuntimeError('source has no nodes')
        # Source and destination backups exist before any production service is stopped.
        legacy_archive = snapshot(args.target, args)
        replicate(legacy_archive, (args.source, args.target))
        preflight_archive = snapshot(args.source, args)
        preflight_stage = prepare(args.target, preflight_archive, args)
        rehearsal = json.loads(command(['python3', str(DR), 'rehearse-isolated',
                                        '--host', args.target, '--authority', args.source,
                                        '--stage', preflight_stage, '--ip', args.target_ip], timeout=120))
        if rehearsal.get('isolated_restore') != 'passed' or rehearsal.get('nodes_and_approved_routes') != count:
            raise RuntimeError('isolated restore rehearsal did not match the source')
        headplane_paths = ('/opt/headplane/releases/0.8.2/node '
                           '/opt/headplane/releases/0.8.2/build/server/index.js')
        if ssh(args.source, 'sha256sum ' + headplane_paths) != ssh(args.target, 'sha256sum ' + headplane_paths):
            raise RuntimeError('preinstalled Headplane release differs between hosts')
        if args.preflight_only:
            journal(log, legacy_backup=legacy_archive, source_backup=preflight_archive,
                    target_stage=preflight_stage, phase='preflight-complete')
            print(json.dumps({'preflight': 'passed', 'isolated_restore': 'passed',
                              'nodes': count, 'journal': str(log)}, sort_keys=True))
            return
        journal(log, legacy_backup=legacy_archive, phase='fencing-source')
        fenced = False
        sealed = False
        target_attempted = False
        try:
            result = helper(args.source, FENCE, ['fence', op])
            fenced = result['headscale_enabled'] == 'masked' and result['headscale_active'] != 'active'
            if not fenced:
                raise RuntimeError('source fence could not be verified')
            journal(log, phase='source-fenced')
            final_archive = snapshot(args.source, args)
            replicate(final_archive, (args.source, args.target))
            stage = prepare(args.target, final_archive, args)
            journal(log, source_backup=final_archive, target_stage=stage, phase='target-prepared')
            result = helper(args.source, FENCE, ['seal', op])
            sealed = result['sealed'] and result['headscale_enabled'] == 'masked' and result['headscale_active'] != 'active'
            if not sealed:
                raise RuntimeError('source fence could not be sealed')
            journal(log, phase='source-sealed')
            # Set before the remote call: a lost SSH response is ambiguous.
            target_attempted = True
            helper(args.target, ACTIVATE, [op, stage, args.domain, args.target_legacy,
                                         args.target_ip, 'blocked'], timeout=180, writer_lock=True)
            journal(log, phase='target-active')
            after_count, after_digest = node_digest(args.target)
            if (count, digest) != (after_count, after_digest):
                raise RuntimeError('destination identity or routes do not match source')
            helper(args.source, RELAY, [op, args.domain, args.source_legacy,
                                       args.source_ip, args.target_ip])
            journal(log, phase='legacy-relay-ready')
            dns_change(token, args.cf_zone, args.domain, args.source_ip, args.target_ip)
            journal(log, phase='dns-updated')
            deadline = time.monotonic() + 180
            pending = list(args.routers)
            while pending and time.monotonic() < deadline:
                remaining = []
                for host in pending:
                    try:
                        if not client_smoke(host, args.domain, args.target_ip, probes[host]):
                            remaining.append(host)
                    except Exception:
                        remaining.append(host)
                pending = remaining
                if pending:
                    time.sleep(8)
            if pending:
                raise RuntimeError('client recovery not verified for: ' + ', '.join(pending))
            journal(log, phase='complete')
            print(json.dumps({'operation': op, 'active': args.target, 'fenced': args.source,
                              'dns': args.target_ip, 'nodes': count, 'routers_verified': len(args.routers),
                              'legacy_relay': 'healthy', 'backup': final_archive, 'journal': str(log)}, sort_keys=True))
        except Exception as original:
            journal(log, phase='failed', error=str(original)[:350])
            if not sealed:
                # A timed-out seal may actually have succeeded; inspect it
                # before choosing the only safe release action. Target has not
                # been attempted anywhere on this branch.
                try:
                    state = helper(args.source, FENCE, ['status', op])
                    if state.get('headscale_enabled') == 'masked':
                        action = 'release-unwritten' if state.get('sealed') else 'abort-unsealed'
                        helper(args.source, FENCE, [action, op])
                        journal(log, phase='rolled-back-before-seal')
                        raise RuntimeError(str(original) + '; source restored before target start') from original
                except RuntimeError as recovery_error:
                    if 'source restored before target start' in str(recovery_error):
                        raise
                raise RuntimeError(str(original) + '; inspect fence status and unsealed recovery timer; journal=' + str(log)) from original
            if not target_attempted:
                helper(args.source, FENCE, ['release-unwritten', op])
                journal(log, phase='rolled-back-before-target-start')
                raise RuntimeError(str(original) + '; source restored before target start') from original
            try:
                target = target_state(args.target, op)
                phase = target.get('phase', '')
                if phase not in ('writer-may-have-started', 'active'):
                    helper(args.source, FENCE, ['release-unwritten', op])
                    journal(log, phase='rolled-back-before-target-write')
                else:
                    # Destination is persistently masked and inactive. Capture
                    # its most recent committed writes before starting old host.
                    rollback_archive = snapshot(args.target, args)
                    replicate(rollback_archive, (args.source, args.target))
                    rollback_stage = prepare(args.source, rollback_archive, args)
                    rollback_op = uuid.uuid4().hex[:16]
                    journal(log, rollback_backup=rollback_archive, rollback_stage=rollback_stage,
                            rollback_operation=rollback_op, phase='restoring-source')
                    helper(args.source, ACTIVATE, [rollback_op, rollback_stage, args.domain,
                                                  args.source_legacy, args.source_ip, 'local'],
                           timeout=180, writer_lock=True)
                    dns_change(token, args.cf_zone, args.domain, args.target_ip, args.source_ip)
                    journal(log, phase='rolled-back-from-target-snapshot')
                raise RuntimeError(str(original) + '; safe rollback completed; journal=' + str(log)) from original
            except Exception as rollback_error:
                if 'safe rollback completed' in str(rollback_error):
                    raise
                journal(log, phase='rollback-blocked', rollback_error=str(rollback_error)[:350])
                raise RuntimeError(str(original) + '; automatic rollback could not be verified; '
                                   'do not start either writer until the destination is independently fenced; journal=' + str(log)) from rollback_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('source', 'target', 'source-ip', 'target-ip', 'domain', 'source-legacy', 'target-legacy',
                'cf-zone', 'cf-token-file', 'passphrase-file', 'backup-dir'):
        parser.add_argument('--' + arg, required=True)
    parser.add_argument('--router', dest='routers', action='append', default=[])
    parser.add_argument('--probe', dest='probes', action='append', default=[],
                        metavar='ROUTER=PEER_IP', help='WireGuard TSMP peer required for each router')
    parser.add_argument('--preflight-only', action='store_true', help='verify preparation without fencing either host')
    args = parser.parse_args()
    if len(args.routers) < 1:
        parser.error('at least one --router is required for post-cutover verification')
    promote(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('promotion failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
