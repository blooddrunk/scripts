#!/usr/bin/env python3
"""Exercise the guarded router rollback without touching a real Tailscale service."""

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
HELPER = PROJECT / 'lib/dr-migrate-router.sh'
OLD = 'https://old.example.test'
NEW = 'https://new.example.test'
IP = '100.64.0.3'
SECRET_MARKER = 'fixture-private-auth-key-never-print'


def main():
    work = Path('/tmp/headscale-router-migrate.' + uuid.uuid4().hex[:16])
    work.mkdir(mode=0o700)
    result_marker = Path('/tmp/headscale-router-result.' + work.name.rsplit('.', 1)[1])
    with tempfile.TemporaryDirectory(prefix='router-guard-test-') as temp:
        try:
            root = Path(temp)
            state = root / 'state.json'
            tail = root / 'tailscale'
            tail.write_text('''#!/usr/bin/env python3
import json, os, sys
p=os.environ['TEST_STATE']
s=json.load(open(p))
if sys.argv[1:3] == ['debug','prefs']:
 print(json.dumps({'ControlURL':s['control']}))
elif sys.argv[1:3] == ['status','--json']:
 print(json.dumps({'BackendState':'Running','Self':{'TailscaleIPs':['100.64.0.3']}}))
elif sys.argv[1] == 'up':
 if os.environ.get('TEST_FAIL') == '1': sys.exit(1)
 s['control']=next(x.split('=',1)[1] for x in sys.argv if x.startswith('--login-server='))
 s['up_calls']=s.get('up_calls',0)+1
 json.dump(s,open(p,'w'))
else: sys.exit(2)
''')
            jsonfilter = root / 'jsonfilter'
            jsonfilter.write_text('''#!/usr/bin/env python3
import json,sys
x=json.load(sys.stdin); expr=sys.argv[sys.argv.index('-e')+1]
if expr == '@.ControlURL': print(x['ControlURL'])
elif expr == '@.BackendState': print(x['BackendState'])
elif expr == '@.Self.TailscaleIPs[0]': print(x['Self']['TailscaleIPs'][0])
else: sys.exit(2)
''')
            tail.chmod(0o755)
            jsonfilter.chmod(0o755)
            (work / 'authkey').write_text(SECRET_MARKER)
            (work / 'params').write_text(
                "OLD_URL='" + OLD + "'\nNEW_URL='" + NEW + "'\n"
                "ACCEPT_ROUTES='true'\nACCEPT_DNS='false'\nSNAT='true'\n"
                "NETFILTER='on'\nROUTES='192.168.10.0/24'\nHOSTNAME_PREF=''\n"
                "SELF_IP='" + IP + "'\n")
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], TEST_STATE=str(state))
            state.write_text(json.dumps({'control': NEW}))
            result = subprocess.run(['sh', str(HELPER), 'rollback', str(work)],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert result.returncode == 0, result.stderr
            assert json.loads(state.read_text()) == {'control': OLD, 'up_calls': 1}
            assert (work / 'rollback.status').read_text().strip() == 'restored'
            assert SECRET_MARKER not in result.stdout + result.stderr

            (work / 'ROLLBACK').unlink()
            (work / 'rollback.status').unlink()
            state.write_text(json.dumps({'control': NEW}))
            failed_env = dict(env, TEST_FAIL='1')
            result = subprocess.run(['sh', str(HELPER), 'rollback', str(work)],
                                    env=failed_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert result.returncode != 0
            assert (work / 'rollback.status').read_text().strip() == 'recovery-unverified'
            assert json.loads(state.read_text())['control'] == NEW
            assert SECRET_MARKER not in result.stdout + result.stderr
            print('Router rollback success and failure paths passed.')
        finally:
            shutil.rmtree(work, ignore_errors=True)
            result_marker.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
