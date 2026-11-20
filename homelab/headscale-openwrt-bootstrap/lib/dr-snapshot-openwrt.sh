#!/bin/sh
# Stream a private OpenWrt snapshot to an encrypting caller; no secrets on stderr.
set -eu
umask 077

sqlite_dir=${1:-}
tmp=$(mktemp -d /tmp/headscale-dr-router.XXXXXX)
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
mkdir -p "$tmp/source"

copy_path() {
    src=$1
    [ -e "$src" ] || return 0
    mkdir -p "$tmp/source$(dirname "$src")"
    cp -a "$src" "$tmp/source$src"
}

state=/etc/tailscale/tailscaled.state
[ -f "$state" ] || { echo 'tailscaled state missing' >&2; exit 1; }
before=$(sha256sum "$state" | awk '{print $1}')
copy_path /etc/tailscale
after=$(sha256sum "$state" | awk '{print $1}')
copied=$(sha256sum "$tmp/source$state" | awk '{print $1}')
[ "$before" = "$after" ] && [ "$before" = "$copied" ] || {
    echo 'tailscaled state changed during snapshot; retry' >&2; exit 1;
}
jsonfilter -i "$tmp/source$state" -e '@' >/dev/null 2>&1 || {
    echo 'tailscaled state is not valid JSON' >&2; exit 1;
}

for src in /etc/config/network /etc/config/firewall /etc/config/dhcp \
    /etc/config/daed /etc/config/passwall2 /etc/config/honk /etc/config/https-dns-proxy \
    /etc/config/tailscale /etc/config/tailscale-bootstrap \
    /etc/init.d/tailscale-core /etc/init.d/tailscale-failover /etc/init.d/daed /etc/init.d/honk \
    /usr/sbin/tailscale-failover /etc/hotplug.d/iface /var/lib/tailscale; do
    copy_path "$src"
done

db_method=none
if [ -f /etc/daed/wing.db ]; then
    copy_path /etc/daed
    rm -f "$tmp/source/etc/daed/wing.db" \
        "$tmp/source/etc/daed/wing.db-wal" "$tmp/source/etc/daed/wing.db-shm"
    if [ -n "$sqlite_dir" ]; then
        sqlite="$sqlite_dir/root/usr/bin/sqlite3"
        export LD_LIBRARY_PATH="$sqlite_dir/root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    else
        sqlite=$(command -v sqlite3 || true)
    fi
    [ -n "$sqlite" ] && [ -x "$sqlite" ] || {
        echo 'SQLite CLI required for live daed database' >&2; exit 1;
    }
    "$sqlite" /etc/daed/wing.db ".backup '$tmp/source/etc/daed/wing.db'"
    [ "$("$sqlite" "$tmp/source/etc/daed/wing.db" 'PRAGMA quick_check;')" = ok ] || {
        echo 'daed snapshot failed quick_check' >&2; exit 1;
    }
    [ ! -e "$tmp/source/etc/daed/wing.db-wal" ] || {
        echo 'daed snapshot unexpectedly has a WAL' >&2; exit 1;
    }
    db_method=sqlite-online-backup
fi

honk_method=none
if [ -f /etc/honk/state/honk.db ]; then
    (cd /etc/honk && find . -type f ! -name 'honk.db' ! -name 'honk.db-wal' ! -name 'honk.db-shm' -exec sha256sum {} \; | LC_ALL=C sort) > "$tmp/honk.before"
    copy_path /etc/honk
    rm -f "$tmp/source/etc/honk/state/honk.db" \
        "$tmp/source/etc/honk/state/honk.db-wal" "$tmp/source/etc/honk/state/honk.db-shm"
    if [ -n "$sqlite_dir" ]; then
        sqlite="$sqlite_dir/root/usr/bin/sqlite3"
        export LD_LIBRARY_PATH="$sqlite_dir/root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    else
        sqlite=$(command -v sqlite3 || true)
    fi
    [ -n "$sqlite" ] && [ -x "$sqlite" ] || {
        echo 'SQLite CLI required for live honk database' >&2; exit 1;
    }
    "$sqlite" /etc/honk/state/honk.db ".backup '$tmp/source/etc/honk/state/honk.db'"
    [ "$("$sqlite" "$tmp/source/etc/honk/state/honk.db" 'PRAGMA quick_check;')" = ok ] || {
        echo 'honk snapshot failed quick_check' >&2; exit 1;
    }
    [ ! -e "$tmp/source/etc/honk/state/honk.db-wal" ] || {
        echo 'honk snapshot unexpectedly has a WAL' >&2; exit 1;
    }
    (cd /etc/honk && find . -type f ! -name 'honk.db' ! -name 'honk.db-wal' ! -name 'honk.db-shm' -exec sha256sum {} \; | LC_ALL=C sort) > "$tmp/honk.after"
    (cd "$tmp/source/etc/honk" && find . -type f ! -name 'honk.db' ! -name 'honk.db-wal' ! -name 'honk.db-shm' -exec sha256sum {} \; | LC_ALL=C sort) > "$tmp/honk.copy"
    cmp -s "$tmp/honk.before" "$tmp/honk.after" && cmp -s "$tmp/honk.before" "$tmp/honk.copy" || {
        echo 'honk configuration changed during snapshot; retry' >&2; exit 1;
    }
    rm -f "$tmp/honk.before" "$tmp/honk.after" "$tmp/honk.copy"
    honk_method=sqlite-online-backup
fi

{
    printf 'format=1\n'
    printf 'snapshot_type=openwrt\n'
    printf 'created_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'daed_database_method=%s\n' "$db_method"
    printf 'honk_database_method=%s\n' "$honk_method"
    printf 'tailscaled_state_sha256=%s\n' "$copied"
} > "$tmp/metadata.txt"
(
    cd "$tmp"
    find source -type f -exec sha256sum {} \; | LC_ALL=C sort
    sha256sum metadata.txt
) > "$tmp/manifest.sha256"
tar -czf - -C "$tmp" source metadata.txt manifest.sha256
