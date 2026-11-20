#!/bin/sh
# Guarded, one-router daed migration. The operator must acknowledge after
# reconnecting; otherwise a detached watchdog restores the stopped-service
# SQLite snapshot and the routing fragment. Never run this on two routers at once.
set -eu
umask 077

fail() { echo "one-tailnet-daed: $*" >&2; exit 1; }

wait_daed_stopped() {
    # procd's stop is asynchronous: daed-guard can spend up to 30 seconds
    # forwarding TERM, reaping its child and cleaning network namespaces.
    n=0
    while pidof daed >/dev/null 2>&1 || pidof daed-guard >/dev/null 2>&1; do
        [ "$n" -lt 45 ] || return 1
        sleep 1
        n=$((n + 1))
    done
}

wait_daed_started() {
    n=0
    while [ "$n" -lt 30 ]; do
        pidof daed >/dev/null 2>&1 && return 0
        sleep 1
        n=$((n + 1))
    done
    return 1
}

load_params() {
    work=$1
    case "$work" in /tmp/one-tailnet-daed.[A-Za-z0-9]*) ;; *) fail 'invalid work directory' ;; esac
    case "${work#/tmp/one-tailnet-daed.}" in ''|*[!A-Za-z0-9]*) fail 'invalid work directory' ;; esac
    [ -f "$work/params" ] || fail 'missing work parameters'
    . "$work/params"
    export LD_LIBRARY_PATH="$SQLITE_DIR/root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    sqlite="$SQLITE_DIR/root/usr/bin/sqlite3"
}

prepare_db() {
    db=$1
    "$sqlite" "$db" 'PRAGMA wal_checkpoint(TRUNCATE); PRAGMA journal_mode=DELETE;' >/dev/null || fail 'cannot make offline database self-contained'
    group=$($sqlite "$db" "SELECT id FROM groups WHERE name='tailscale-ctrl';")
    case "$group" in ''|*[!0-9]*) fail 'control group is not unique' ;; esac
    primary=$($sqlite "$db" "SELECT subscription_id FROM group_subscriptions WHERE group_id=$group;")
    case "$primary" in ''|*[!0-9]*) fail 'expected exactly one current control subscription' ;; esac
    secondary=$($sqlite "$db" "SELECT id FROM subscriptions WHERE tag='$SECONDARY_TAG';")
    case "$secondary" in ''|*[!0-9]*) fail 'secondary subscription is not unique' ;; esac
    [ "$primary" != "$secondary" ] || fail 'control exits must use distinct subscriptions'
    for pair in "$primary:$PRIMARY_FILTER" "$secondary:$SECONDARY_FILTER"; do
        sid=${pair%%:*}; filter=${pair#*:}
        good=$($sqlite "$db" "SELECT count(*) FROM nodes WHERE subscription_id=$sid AND protocol='hysteria2' AND name LIKE '%$filter%';")
        bad=$($sqlite "$db" "SELECT count(*) FROM nodes WHERE subscription_id=$sid AND protocol!='hysteria2' AND name LIKE '%$filter%';")
        [ "$good" -ge 1 ] && [ "$bad" = 0 ] || fail 'filter does not select only Hysteria2 nodes'
    done
    old="domain(suffix: $OLD_DOMAIN) -> tailscale-ctrl"
    new="domain(suffix: $DOMAIN) -> tailscale-ctrl"
    selected=$($sqlite "$db" "SELECT count(*) FROM routings WHERE selected=1;")
    old_count=$($sqlite "$db" "SELECT (length(routing)-length(replace(routing,'$old','')))/length('$old') FROM routings WHERE selected=1;")
    new_count=$($sqlite "$db" "SELECT instr(routing,'$new') FROM routings WHERE selected=1;")
    [ "$selected" = 1 ] && [ "$old_count" = 1 ] && [ "$new_count" = 0 ] || fail 'unexpected active routing content'
    $sqlite "$db" <<SQL || fail 'could not update the offline database'
BEGIN IMMEDIATE;
UPDATE group_subscriptions SET name_filter_regex='$PRIMARY_FILTER' WHERE group_id=$group AND subscription_id=$primary;
INSERT INTO group_subscriptions(group_id,subscription_id,name_filter_regex) VALUES($group,$secondary,'$SECONDARY_FILTER');
UPDATE groups SET version=version+1 WHERE id=$group;
UPDATE routings SET routing=replace(routing,'$old','$new' || char(10) || '    ' || '$old'), version=version+1 WHERE selected=1;
COMMIT;
SQL
    [ "$($sqlite "$db" 'PRAGMA quick_check;')" = ok ] || fail 'modified database failed quick_check'
}

prepare_extra() {
    original=$1
    output=$2
    old="domain(suffix: $OLD_DOMAIN) -> tailscale-ctrl"
    new="domain(suffix: $DOMAIN) -> tailscale-ctrl"
    awk -v old="$old" -v new="$new" '
        index($0, new) { already=1 }
        index($0, old) { count++; if (count == 1) print new }
        { print }
        END { if (count != 1 || already) exit 1 }
    ' "$original" > "$output" || fail 'unexpected routing-extra content'
}

apply_change() {
    load_params "$1"
    : > "$work/APPLYING"
    # This worker and the watchdog are detached from the controlling SSH.
    echo $$ > "$work/worker.pid"
    if ! /etc/init.d/daed stop; then : > "$work/FAILED"; sh "$work/worker.sh" rollback "$work"; exit 1; fi
    if ! wait_daed_stopped; then : > "$work/FAILED"; sh "$work/worker.sh" rollback "$work"; exit 1; fi
    if ! "$sqlite" /etc/daed/wing.db ".backup '$work/db.original.partial'" || \
       [ "$("$sqlite" "$work/db.original.partial" 'PRAGMA quick_check;')" != ok ]; then
        : > "$work/FAILED"; sh "$work/worker.sh" rollback "$work"; exit 1
    fi
    mv "$work/db.original.partial" "$work/db.original"
    cp -p "$work/db.original" "$work/db.new"
    if ! (prepare_db "$work/db.new" && prepare_extra "$work/extra.original" "$work/extra.new"); then
        : > "$work/FAILED"; sh "$work/worker.sh" rollback "$work"; exit 1
    fi
    [ ! -s "$work/db.new-wal" ] || fail 'modified offline database has a WAL'
    local_db=/etc/daed/.wing.db.one-tailnet-${work##*.}
    local_extra=/etc/openwrt-dns-daed.d/.routing-extra.one-tailnet-${work##*.}
    cp -p /etc/daed/wing.db "$local_db"
    cp "$work/db.new" "$local_db"
    rm -f /etc/daed/wing.db-wal /etc/daed/wing.db-shm
    mv "$local_db" /etc/daed/wing.db
    cp -p "$work/extra.original" "$local_extra"
    cp "$work/extra.new" "$local_extra"
    mv "$local_extra" /etc/openwrt-dns-daed.d/routing-extra.dae
    if ! /etc/init.d/daed start; then : > "$work/FAILED"; sh "$work/worker.sh" rollback "$work"; exit 1; fi
    sleep 5
    if ! pidof daed >/dev/null 2>&1 || \
       [ "$(tailscale status --json | jsonfilter -e '@.BackendState')" != Running ] || \
       [ "$(curl -sS --max-time 12 -o /dev/null -w '%{http_code}' "https://$DOMAIN/health" 2>/dev/null)" != 200 ] || \
       [ "$(curl -sS --max-time 12 -o /dev/null -w '%{http_code}' "https://$OLD_DOMAIN/health" 2>/dev/null)" != 200 ]; then
        : > "$work/FAILED"; sh "$work/worker.sh" rollback "$work"; exit 1
    fi
    : > "$work/APPLY_OK"
}

rollback() {
    load_params "$1"
    # Serialize operator acknowledgement and automatic rollback.
    tries=0
    until mkdir "$work/decision.lock" 2>/dev/null; do
        tries=$((tries + 1)); [ "$tries" -lt 10 ] || exit 1; sleep 1
    done
    if [ -e "$work/COMMIT" ]; then rmdir "$work/decision.lock"; return 0; fi
    : > "$work/ROLLBACK"
    rmdir "$work/decision.lock"
    if [ -f "$work/worker.pid" ]; then
        pid=$(cat "$work/worker.pid")
        case "$pid" in ''|*[!0-9]*) pid= ;; esac
        if [ -n "$pid" ] && [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ] && \
           tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qF "$work/worker.sh"; then
            kill -TERM -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
            sleep 2
        fi
    fi
    /etc/init.d/daed stop >/dev/null 2>&1 || true
    if ! wait_daed_stopped; then echo failed-to-stop > "$work/rollback.status"; exit 1; fi
    if [ -s "$work/db.original" ]; then
        # Restore only after daed stopped; never combine old WAL with a new DB.
        rm -f /etc/daed/wing.db-wal /etc/daed/wing.db-shm
        restore_db=/etc/daed/.wing.db.restore-${work##*.}
        cp -p /etc/daed/wing.db "$restore_db"
        cp "$work/db.original" "$restore_db"
        mv "$restore_db" /etc/daed/wing.db
        cp -p "$work/extra.original" /etc/openwrt-dns-daed.d/routing-extra.dae
    fi
    if ! /etc/init.d/daed start >/dev/null 2>&1 || ! wait_daed_started; then
        echo restart-failed > "$work/rollback.status"
        exit 1
    fi
    n=0
    while [ "$n" -lt 6 ]; do
        if [ "$(tailscale status --json | jsonfilter -e '@.BackendState')" = Running ] && \
           [ "$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' "https://$OLD_DOMAIN/health" 2>/dev/null)" = 200 ]; then
            echo restored > "$work/rollback.status"
            printf 'restored %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "/tmp/one-tailnet-daed-result.${work##*.}"
            return 0
        fi
        sleep 5; n=$((n + 1))
    done
    echo restart-health-unverified > "$work/rollback.status"
    exit 1
}

case "${1:-}" in
    start|check)
        action=$1
        if [ "$action" = start ] && [ "${DAED_INDEPENDENT_ACCESS_VERIFIED:-0}" != 1 ]; then
            fail 'refusing restart without independently verified recovery access'
        fi
        [ "$#" = 8 ] || fail 'start SQLITE_DIR DOMAIN OLD_DOMAIN PRIMARY_FILTER SECONDARY_FILTER SECONDARY_TAG SECONDS'
        SQLITE_DIR=$2; DOMAIN=$3; OLD_DOMAIN=$4; PRIMARY_FILTER=$5; SECONDARY_FILTER=$6; SECONDARY_TAG=$7; GUARD_SECONDS=$8
        case "$SQLITE_DIR" in /tmp/headscale-dr-sqlite.[A-Za-z0-9]*) ;; *) fail 'invalid SQLite tool path' ;; esac
        case "${SQLITE_DIR#/tmp/headscale-dr-sqlite.}" in ''|*[!A-Za-z0-9]*) fail 'invalid SQLite tool path' ;; esac
        for v in "$DOMAIN" "$OLD_DOMAIN" "$PRIMARY_FILTER" "$SECONDARY_FILTER" "$SECONDARY_TAG"; do
            case "$v" in ''|*[!A-Za-z0-9._-]*) fail 'invalid argument' ;; esac
        done
        case "$GUARD_SECONDS" in ''|*[!0-9]*) fail 'invalid timeout' ;; esac
        [ "$GUARD_SECONDS" -ge 120 ] && [ "$GUARD_SECONDS" -le 600 ] || fail 'timeout must be 120..600 seconds'
        command -v setsid >/dev/null 2>&1 || fail 'setsid required'
        [ -x "$SQLITE_DIR/root/usr/bin/sqlite3" ] || fail 'SQLite CLI missing'
        [ -f /etc/openwrt-dns-daed.d/routing-extra.dae ] || fail 'routing-extra missing'
        mkdir /tmp/one-tailnet-daed.lock 2>/dev/null || fail 'another daed change is in progress'
        work=$(mktemp -d /tmp/one-tailnet-daed.XXXXXX) || { rmdir /tmp/one-tailnet-daed.lock; exit 1; }
        cp "$0" "$work/worker.sh"
        printf "SQLITE_DIR='%s'\nDOMAIN='%s'\nOLD_DOMAIN='%s'\nPRIMARY_FILTER='%s'\nSECONDARY_FILTER='%s'\nSECONDARY_TAG='%s'\n" \
            "$SQLITE_DIR" "$DOMAIN" "$OLD_DOMAIN" "$PRIMARY_FILTER" "$SECONDARY_FILTER" "$SECONDARY_TAG" > "$work/params"
        cp -p /etc/openwrt-dns-daed.d/routing-extra.dae "$work/extra.original"
        load_params "$work"
        if ! "$sqlite" /etc/daed/wing.db ".backup '$work/db.pre'" || \
           ! (prepare_db "$work/db.pre" && prepare_extra "$work/extra.original" "$work/extra.pre"); then
            rm -rf "$work"; rmdir /tmp/one-tailnet-daed.lock; fail 'offline preparation failed'
        fi
        rm -f "$work/db.pre" "$work/extra.pre"
        if [ "$action" = check ]; then
            rm -rf "$work"; rmdir /tmp/one-tailnet-daed.lock
            echo offline_validation_ok
            exit 0
        fi
        setsid sh "$work/worker.sh" guard "$work" "$GUARD_SECONDS" </dev/null >"$work/guard.log" 2>&1 &
        setsid sh "$work/worker.sh" apply "$work" </dev/null >"$work/apply.log" 2>&1 &
        printf 'guarded_work=%s\n' "$work"
        ;;
    apply) apply_change "$2" ;;
    guard)
        load_params "$2"; elapsed=0
        while [ "$elapsed" -lt "$3" ]; do
            [ ! -e "$work/COMMIT" ] || break
            if [ -e "$work/ROLLBACK" ] && [ -f "$work/rollback.status" ]; then break; fi
            sleep 5; elapsed=$((elapsed + 5))
        done
        if [ ! -e "$work/COMMIT" ] && [ ! -e "$work/ROLLBACK" ] && [ -e "$work/APPLYING" ]; then rollback "$work"; fi
        if [ -e "$work/COMMIT" ] || [ ! -e "$work/APPLYING" ] || \
           { [ -f "$work/rollback.status" ] && grep -qx restored "$work/rollback.status"; }; then
            rm -rf "$work"; rmdir /tmp/one-tailnet-daed.lock 2>/dev/null || true
        fi
        ;;
    status)
        load_params "$2"
        for state in APPLYING APPLY_OK FAILED ROLLBACK COMMIT; do [ ! -e "$work/$state" ] || printf '%s ' "$state"; done
        [ ! -f "$work/rollback.status" ] || cat "$work/rollback.status"
        echo
        ;;
    commit)
        load_params "$2"
        [ -e "$work/APPLY_OK" ] || fail 'apply has not passed local checks'
        mkdir "$work/decision.lock" 2>/dev/null || fail 'rollback decision is in progress'
        if [ -e "$work/ROLLBACK" ]; then rmdir "$work/decision.lock"; fail 'rollback already started'; fi
        : > "$work/COMMIT"
        printf 'committed %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "/tmp/one-tailnet-daed-result.${work##*.}"
        rmdir "$work/decision.lock"
        echo committed
        ;;
    rollback) rollback "$2" ;;
    *) fail 'use start, status or commit' ;;
esac
