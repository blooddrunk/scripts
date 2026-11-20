#!/usr/bin/env python3
"""Recover a legacy DERP relay with an isolated, empty Headscale database.

Only /derp is proxied publicly; the temporary control listener is loopback-only.
The original DERP key is reused, while its old control database is never opened.
"""
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
import yaml


def run(args, check=True, timeout=30):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout, check=check)


def rewrite(value):
    if isinstance(value, dict): return {k: rewrite(v) for k, v in value.items()}
    if isinstance(value, list): return [rewrite(v) for v in value]
    if isinstance(value, str):
        for old, new in (('/etc/headscale', '/etc/headscale-derp-recovery'),
                         ('/var/lib/headscale', '/var/lib/headscale-derp-recovery'),
                         ('/var/run/headscale', '/var/run/headscale-derp-recovery')):
            if value == old or value.startswith(old + '/'):
                return new + value[len(old):]
    return value


def health(domain, ip, path):
    p = run(['curl', '-sS', '--noproxy', '*', '--max-time', '8', '--resolve',
             domain + ':443:' + ip, '-o', '/dev/null', '-w', '%{http_code}',
             'https://' + domain + path], False)
    return p.stdout if not p.returncode else 'failed'


def main():
    if os.geteuid() or len(sys.argv) != 4:
        raise RuntimeError('requires root, previous operation, legacy domain, local IPv4')
    oldop, domain, ip = sys.argv[1:]
    if not re.fullmatch(r'[a-f0-9]{16}', oldop) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', domain):
        raise RuntimeError('invalid input')
    import ipaddress
    ipaddress.IPv4Address(ip)
    if run(['systemctl','is-enabled','headscale'],False).stdout.strip() != 'masked' or run(['systemctl','is-active','headscale'],False).stdout.strip() == 'active':
        raise RuntimeError('the old main control service is not fenced')
    if run(['ss','-H','-ltn','sport = :18080']).stdout.strip() or run(['ss','-H','-lun','sport = :3478']).stdout.strip():
        raise RuntimeError('recovery listener or STUN port already in use')
    old_config = Path('/etc/headscale.dr-old-' + oldop)
    old_data = Path('/var/lib/headscale.dr-old-' + oldop)
    old_key = old_data / 'derp_server_private.key'
    if not (old_config / 'config.yaml').is_file() or not old_key.is_file() or old_key.is_symlink():
        raise RuntimeError('preserved legacy DERP key/config missing')
    config_dir = Path('/etc/headscale-derp-recovery')
    data_dir = Path('/var/lib/headscale-derp-recovery')
    site = Path('/opt/1panel/www/sites') / domain / 'proxy/root.conf'
    if config_dir.exists() or data_dir.exists() or not site.is_file():
        raise RuntimeError('recovery state or legacy site is unexpected')
    if health(domain, ip, '/health') != '503':
        raise RuntimeError('legacy control endpoint is not blocked')
    containers = run(['docker','ps','--format','{{.Names}} {{.Image}}']).stdout.splitlines()
    names = [x.split()[0] for x in containers if 'openresty' in x.lower()]
    if len(names) != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]+', names[0]):
        raise RuntimeError('OpenResty container is ambiguous')
    container = names[0]
    work = Path('/run/headscale-dr') / ('standalone-derp-' + uuid.uuid4().hex[:16])
    work.mkdir(parents=True, mode=0o700)
    saved, marker, guard = work/'old-proxy.conf', work/'COMMIT', work/'rollback.sh'
    st = site.stat(); shutil.copyfile(site, saved); os.chown(saved,st.st_uid,st.st_gid); os.chmod(saved,st.st_mode & 0o777)
    unit = 'hs-dr-derp-recovery'
    guard.write_text('#!/bin/sh\n'
        f'if [ ! -e {marker} ]; then\n'
        f' cp -p {saved} {site} || exit 1\n'
        f' docker exec {container} openresty -t >/dev/null 2>&1 && docker exec {container} openresty -s reload >/dev/null 2>&1\n'
        f' systemctl stop {unit} >/dev/null 2>&1 || true\n'
        'fi\n')
    os.chmod(guard,0o700)
    run(['systemd-run','--quiet','--unit=hs-dr-derp-recover-guard-'+work.name[-16:],
         '--on-active=300s','/bin/sh',str(guard)])
    try:
        config_dir.mkdir(mode=0o750)
        data_dir.mkdir(mode=0o750)
        uid=pwd.getpwnam('headscale').pw_uid; gid=grp.getgrnam('headscale').gr_gid
        os.chown(config_dir,0,gid); os.chown(data_dir,uid,gid)
        cfg = rewrite(yaml.safe_load((old_config/'config.yaml').read_text()))
        cfg['listen_addr']='127.0.0.1:18080'
        cfg['metrics_listen_addr']='127.0.0.1:19090'
        cfg['grpc_listen_addr']='127.0.0.1:15443'
        cfg['derp']['paths']=[]; cfg['derp']['urls']=[]; cfg['derp']['auto_update_enabled']=False
        cfg['derp']['server']['region_id']=901
        cfg['derp']['server']['region_code']='tokyo'
        cfg['derp']['server']['region_name']='Recovery DERP'
        cfg['derp']['server']['ipv4']=ip
        cfg['derp']['server']['stun_listen_addr']='0.0.0.0:3478'
        path=config_dir/'config.yaml'; path.write_text(yaml.safe_dump(cfg,sort_keys=False)); os.chown(path,0,gid); os.chmod(path,0o640)
        derp=data_dir/'derp_server_private.key'; shutil.copyfile(old_key,derp); os.chown(derp,uid,gid); os.chmod(derp,0o600)
        key=run(['headscale','generate','private-key']).stdout.strip()
        if not re.fullmatch(r'privkey:[0-9a-f]{64}',key):
            raise RuntimeError('unexpected generated key format')
        noise=data_dir/'noise_private.key'; noise.write_text(key+'\n'); os.chown(noise,uid,gid); os.chmod(noise,0o600)
        os.makedirs('/run/headscale-derp-recovery',mode=0o750,exist_ok=True)
        os.chown('/run/headscale-derp-recovery',uid,gid)
        run(['runuser','-u','headscale','--','headscale','-c',str(path),'configtest'])
        if hashlib.sha256(derp.read_bytes()).digest() != hashlib.sha256(old_key.read_bytes()).digest():
            raise RuntimeError('legacy DERP key changed')
        run(['systemd-run','--quiet','--unit='+unit,'--property=User=headscale','--property=Group=headscale',
             '--property=NoNewPrivileges=yes','--property=WorkingDirectory='+str(data_dir),
             '--property=Restart=on-failure','/usr/bin/headscale','-c',str(path),'serve'])
        for attempt in range(20):
            r=run(['curl','-s','--max-time','2','-o','/dev/null','-w','%{http_code}',
                   'http://127.0.0.1:18080/derp/probe'],False)
            if r.stdout == '200': break
            time.sleep(1)
        else: raise RuntimeError('isolated recovery DERP did not start')
        listeners=run(['ss','-H','-ltn','sport = :18080']).stdout
        if '127.0.0.1:18080' not in listeners or '0.0.0.0:18080' in listeners or '[::]:18080' in listeners:
            raise RuntimeError('recovery control listener is not loopback-only')
        site.write_text('''location ^~ /derp {
    proxy_pass http://127.0.0.1:18080;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
location ^~ / { return 503; }
''')
        run(['docker','exec',container,'openresty','-t'])
        run(['docker','exec',container,'openresty','-s','reload'])
        for attempt in range(15):
            if health(domain,ip,'/derp/probe') == '200' and health(domain,ip,'/health') == '503': break
            time.sleep(1)
        else: raise RuntimeError('DERP-only public checks failed')
        print(json.dumps({'guard_dir':str(work),'recovery_derp':'running',
                          'old_control':'masked_and_blocked','control_listener':'loopback_only',
                          'legacy_database':'unused','derp_key':'preserved'},sort_keys=True))
    except Exception:
        run(['/bin/sh',str(guard)],False)
        raise


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print('DERP recovery failed: '+str(exc),file=sys.stderr); sys.exit(1)
