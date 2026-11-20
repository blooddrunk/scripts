#!/bin/sh
# Run ONLY on a disposable/private mount namespace. No real services are called.
set -eu
[ "$#" -eq 5 ] || { echo 'usage: test-activation-namespace.sh HELPER STAGE DOMAIN LEGACY_DOMAIN IPV4' >&2; exit 2; }
helper=$1
stage=$2
export TEST_DOMAIN=$3 TEST_LEGACY=$4 TEST_IP=$5
[ "$(id -u)" -eq 0 ] || { echo 'root required' >&2; exit 2; }
case "$stage" in /var/lib/headscale-dr/staged/snapshot-????????????????) ;; *) exit 2 ;; esac
[ -f "$helper" ] && [ -f "$stage/manifest.json" ] || exit 2
work=$(mktemp -d /tmp/headscale-dr-test.XXXXXXXX)
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT HUP INT TERM
mkdir -p "$work/bin"
cat >"$work/bin/systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "systemctl $*" >>"$TEST_LOG"
case "$1" in is-active) echo inactive; exit 3 ;; esac
exit 0
EOF
cat >"$work/bin/docker" <<'EOF'
#!/bin/sh
printf '%s\n' "docker $*" >>"$TEST_LOG"
if [ "$1" = ps ]; then echo 'mock-openresty openresty'; exit 0; fi
case "$*" in *'openresty -s reload'*)
  n=0; [ ! -f "$TEST_COUNT" ] || n=$(cat "$TEST_COUNT")
  n=$((n + 1)); echo "$n" > "$TEST_COUNT"
  if [ "$TEST_FAILURE" = yes ] && [ "$n" -eq 2 ]; then exit 1; fi ;;
esac
exit 0
EOF
cat >"$work/bin/ip" <<'EOF'
#!/bin/sh
printf '2: eth0 inet %s/24 scope global eth0\n' "$TEST_IP"
EOF
cat >"$work/bin/curl" <<'EOF'
#!/bin/sh
for arg do
  if [ "$arg" = "https://$TEST_LEGACY/health" ]; then
    if grep -q 'return 503' "/opt/1panel/www/sites/$TEST_LEGACY/proxy/root.conf"; then
      printf 503
      exit 0
    fi
  fi
done
printf 200
EOF
cat >"$work/bin/headscale" <<'EOF'
#!/bin/sh
[ "$1" = version ] || exit 1
exec /usr/bin/headscale version
EOF
chmod 700 "$work/bin/"*
for scenario in success fail-after-start; do
    mkdir -p "$work/$scenario/etc-upper" "$work/$scenario/etc-work" \
             "$work/$scenario/lib-upper" "$work/$scenario/lib-work" \
             "$work/$scenario/sites-upper" "$work/$scenario/sites-work"
    case "$scenario" in success) operation=1111111111111111; fail=no;; *) operation=2222222222222222; fail=yes;; esac
    export TEST_LOG="$work/$scenario/log" TEST_COUNT="$work/$scenario/count" TEST_FAILURE="$fail"
    mkdir -p "$work/$scenario/isolated-run"
    chown headscale:headscale "$work/$scenario/isolated-run"
    chmod 750 "$work/$scenario/isolated-run"
    unshare --mount --net --propagation private /bin/sh -eu -s -- "$work" "$scenario" "$helper" "$stage" "$operation" <<'EOF'
work=$1; scenario=$2; helper=$3; stage=$4; operation=$5
mount -t overlay overlay -o "redirect_dir=on,lowerdir=/etc,upperdir=$work/$scenario/etc-upper,workdir=$work/$scenario/etc-work" /etc
mount -t overlay overlay -o "redirect_dir=on,lowerdir=/var/lib,upperdir=$work/$scenario/lib-upper,workdir=$work/$scenario/lib-work" /var/lib
mount -t overlay overlay -o "lowerdir=/opt/1panel/www/sites,upperdir=$work/$scenario/sites-upper,workdir=$work/$scenario/sites-work" /opt/1panel/www/sites
mount -t tmpfs -o mode=755 tmpfs /run
mkdir -p /run/headscale
mount --bind "$work/$scenario/isolated-run" /run/headscale
/usr/sbin/ip link set lo up
PATH="$work/bin:/usr/sbin:/usr/bin:/sbin:/bin"; export PATH
[ "$(command -v systemctl)" = "$work/bin/systemctl" ]
[ "$(command -v docker)" = "$work/bin/docker" ]
set +e
python3 "$helper" "$operation" "$stage" "$TEST_DOMAIN" "$TEST_LEGACY" "$TEST_IP" blocked >"$work/$scenario/output" 2>"$work/$scenario/error"
rc=$?
set -e
state="/var/lib/headscale-dr/activations/$operation/state.json"
if [ "$scenario" = success ]; then
    [ "$rc" -eq 0 ] || { cat "$work/$scenario/error" >&2; exit 1; }
    python3 - "$state" "$TEST_IP" <<'PY'
import json, sys, yaml
assert json.load(open(sys.argv[1]))['phase'] == 'active'
assert yaml.safe_load(open('/etc/headscale/config.yaml'))['derp']['server']['ipv4'] == sys.argv[2]
print('namespace_activation=passed')
PY
    # Now exercise the restored files with the real service user and binary.
    # Only the isolated copy is changed and the namespace has no public links.
    python3 - <<'PY'
import yaml
p = '/etc/headscale/config.yaml'
c = yaml.safe_load(open(p))
c['derp']['urls'] = []
c['derp']['auto_update_enabled'] = False
open(p, 'w').write(yaml.safe_dump(c, sort_keys=False))
PY
    /usr/sbin/runuser -u headscale -- /usr/bin/headscale configtest >"$work/$scenario/configtest" 2>&1 || {
        echo 'restored configtest failed for service user' >&2; exit 1; }
    /usr/bin/timeout 45s /usr/sbin/runuser -u headscale -- /usr/bin/headscale serve >"$work/$scenario/real-headscale.log" 2>&1 &
    pid=$!
    trap 'kill "$pid" 2>/dev/null || true' EXIT HUP INT TERM
    ready=no
    for attempt in $(seq 1 20); do
        if [ "$(/usr/bin/curl -s --noproxy '*' --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health)" = 200 ]; then ready=yes; break; fi
        sleep 1
    done
    [ "$ready" = yes ] || { echo 'restored service did not start as headscale user' >&2; exit 1; }
    [ "$(/usr/bin/curl -s --noproxy '*' --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/derp/probe)" = 200 ]
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    trap - EXIT HUP INT TERM
    echo 'namespace_real_service_user=passed'
else
    [ "$rc" -ne 0 ] || { echo 'failure injection unexpectedly succeeded' >&2; exit 1; }
    python3 - "$state" <<'PY'
import json, sys
assert json.load(open(sys.argv[1]))['phase'] == 'writer-may-have-started'
print('namespace_failure_fences_writer=passed')
PY
    grep -q 'systemctl mask --now headscale' "$TEST_LOG"
fi
EOF
done
