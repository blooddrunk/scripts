#!/bin/sh
# Uses a private mount namespace and fake systemctl; never affects live units.
set -eu
[ "$#" -eq 1 ] && [ -f "$1" ] && [ "$(id -u)" -eq 0 ] || exit 2
helper=$1
work=$(mktemp -d /tmp/headscale-fence-test.XXXXXXXX)
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT HUP INT TERM
mkdir -p "$work/bin" "$work/upper" "$work/overlay-work"
cat >"$work/bin/systemd-run" <<'EOF'
#!/bin/sh
exit 0
EOF
cat >"$work/bin/ss" <<'EOF'
#!/bin/sh
[ "$(cat "$TEST_ACTIVE")" = active ] && echo 'LISTEN 0 128 127.0.0.1:8080 0.0.0.0:*'
exit 0
EOF
cat >"$work/bin/systemctl" <<'EOF'
#!/bin/sh
case "$1:$2" in
 is-active:headscale) cat "$TEST_ACTIVE";;
 is-active:headplane) cat "$TEST_HP_ACTIVE";;
 is-enabled:headscale) cat "$TEST_ENABLED";;
 is-enabled:headplane) cat "$TEST_HP_ENABLED";;
 stop:headplane) echo inactive >"$TEST_HP_ACTIVE";;
 disable:headplane) echo disabled >"$TEST_HP_ENABLED";;
 mask:--now) echo masked >"$TEST_ENABLED"; echo inactive >"$TEST_ACTIVE";;
 unmask:headscale) echo enabled >"$TEST_ENABLED";;
 start:headscale) echo active >"$TEST_ACTIVE";;
 enable:headplane) echo enabled >"$TEST_HP_ENABLED";;
 start:headplane) echo active >"$TEST_HP_ACTIVE";;
 *) exit 1;;
esac
EOF
chmod 700 "$work/bin/"*
export TEST_ACTIVE="$work/active" TEST_ENABLED="$work/enabled" TEST_HP_ACTIVE="$work/hp-active" TEST_HP_ENABLED="$work/hp-enabled"
echo active >"$TEST_ACTIVE"; echo enabled >"$TEST_ENABLED"; echo active >"$TEST_HP_ACTIVE"; echo enabled >"$TEST_HP_ENABLED"
unshare --mount --propagation private /bin/sh -eu -s -- "$work" "$helper" <<'EOF'
work=$1; helper=$2
mount -t overlay overlay -o "redirect_dir=on,lowerdir=/var/lib,upperdir=$work/upper,workdir=$work/overlay-work" /var/lib
PATH="$work/bin:/usr/sbin:/usr/bin:/sbin:/bin"; export PATH
[ "$(command -v systemctl)" = "$work/bin/systemctl" ]
a=3333333333333333
python3 "$helper" fence "$a" >/dev/null
python3 "$helper" seal "$a" >/dev/null
/bin/sh "/var/lib/headscale-dr/fences/$a/recover-unsealed.sh"
[ "$(cat "$TEST_ACTIVE")" = inactive ] && [ "$(cat "$TEST_ENABLED")" = masked ]
python3 "$helper" release-unwritten "$a" >/dev/null
[ "$(cat "$TEST_ACTIVE")" = active ] && [ "$(cat "$TEST_HP_ACTIVE")" = active ]
b=4444444444444444
python3 "$helper" fence "$b" >/dev/null
/bin/sh "/var/lib/headscale-dr/fences/$b/recover-unsealed.sh"
[ "$(cat "$TEST_ACTIVE")" = active ]
if python3 "$helper" seal "$b" >/dev/null 2>&1; then echo 'recovered writer was sealed' >&2; exit 1; fi
c=5555555555555555
python3 "$helper" fence "$c" >/dev/null
python3 "$helper" abort-unsealed "$c" >/dev/null
# Simulate a later fence: the old recovery timer must no longer restart it.
echo inactive >"$TEST_ACTIVE"; echo masked >"$TEST_ENABLED"
/bin/sh "/var/lib/headscale-dr/fences/$c/recover-unsealed.sh"
[ "$(cat "$TEST_ACTIVE")" = inactive ] && [ "$(cat "$TEST_ENABLED")" = masked ]
printf 'namespace_fence_recovery=passed\n'
EOF
