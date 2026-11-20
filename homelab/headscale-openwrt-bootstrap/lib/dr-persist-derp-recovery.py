#!/usr/bin/env python3
"""Persist an already verified, isolated DERP recovery unit without restarting it."""
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


def run(args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30, check=check)


def main():
    if os.geteuid() or len(sys.argv) != 3:
        raise RuntimeError('requires root, legacy domain and local IPv4')
    domain, ip = sys.argv[1:]
    import ipaddress
    ipaddress.IPv4Address(ip)
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', domain):
        raise RuntimeError('invalid legacy domain')
    if run(['systemctl','is-active','hs-dr-derp-recovery'],False).stdout.strip() != 'active':
        raise RuntimeError('isolated recovery instance is not running')
    if run(['systemctl','is-enabled','headscale'],False).stdout.strip() != 'masked':
        raise RuntimeError('main control service is not masked')
    if not Path('/etc/headscale-derp-recovery/config.yaml').is_file() or not Path('/var/lib/headscale-derp-recovery/derp_server_private.key').is_file():
        raise RuntimeError('recovery config or key missing')
    probe=run(['curl','-sS','--noproxy','*','--max-time','8','--resolve',domain+':443:'+ip,
               '-o','/dev/null','-w','%{http_code}','https://'+domain+'/derp/probe'],False)
    if probe.stdout != '200': raise RuntimeError('public recovery DERP probe failed')
    unit=Path('/etc/systemd/system/headscale-derp-recovery.service')
    if unit.exists() or unit.is_symlink(): raise RuntimeError('persistent recovery unit already exists')
    op=uuid.uuid4().hex[:16]
    work=Path('/run/headscale-dr')/('persist-derp-'+op); work.mkdir(parents=True,mode=0o700)
    marker=work/'COMMIT'; guard=work/'rollback.sh'
    guard.write_text('#!/bin/sh\n'
        f'if [ ! -e {marker} ]; then\n'
        ' systemctl disable headscale-derp-recovery >/dev/null 2>&1 || true\n'
        f' rm -f {unit}\n'
        ' systemctl daemon-reload >/dev/null 2>&1\n'
        'fi\n')
    os.chmod(guard,0o700)
    run(['systemd-run','--quiet','--unit=hs-dr-persist-derp-'+op,'--on-active=120s','/bin/sh',str(guard)])
    try:
        with unit.open('x') as f:
            f.write('''[Unit]
Description=Temporary isolated DERP recovery (no public control API)
After=network-online.target
ConditionPathExists=/etc/headscale-derp-recovery/config.yaml

[Service]
Type=simple
User=headscale
Group=headscale
ExecCondition=/bin/sh -c 'test "$(systemctl is-enabled headscale)" = masked'
ExecStart=/usr/bin/headscale -c /etc/headscale-derp-recovery/config.yaml serve
WorkingDirectory=/var/lib/headscale-derp-recovery
RuntimeDirectory=headscale-derp-recovery
StateDirectory=headscale-derp-recovery
NoNewPrivileges=true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
''')
        os.chmod(unit,0o644)
        run(['systemd-analyze','verify',str(unit)])
        run(['systemctl','daemon-reload'])
        run(['systemctl','enable','headscale-derp-recovery'])
        if run(['systemctl','is-enabled','headscale-derp-recovery'],False).stdout.strip() != 'enabled':
            raise RuntimeError('recovery unit was not enabled')
        if run(['systemctl','is-active','hs-dr-derp-recovery'],False).stdout.strip() != 'active':
            raise RuntimeError('existing recovery relay was interrupted')
        marker.touch(mode=0o600,exist_ok=False)
        print('recovery_unit=enabled existing_relay=active reboot_test=not_performed')
    except Exception:
        run(['/bin/sh',str(guard)],False)
        raise


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print('persistent DERP setup failed: '+str(exc),file=sys.stderr); sys.exit(1)
