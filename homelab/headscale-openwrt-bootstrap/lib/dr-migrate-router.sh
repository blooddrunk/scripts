#!/bin/sh
# Guarded URL migration of the currently active OpenWrt Tailscale profile.
# The auth key is received on stdin and is never written to logs or Git.
set -eu
umask 077

fail() { echo "router migration: $*" >&2; exit 1; }
load_params() {
    work=$1
    case "$work" in /tmp/headscale-router-migrate.[A-Za-z0-9]*) ;; *) fail 'invalid work directory' ;; esac
    case "${work#/tmp/headscale-router-migrate.}" in ''|*[!A-Za-z0-9]*) fail 'invalid work directory' ;; esac
    [ -f "$work/params" ] || fail 'missing parameters'
    . "$work/params"
}

prefs_field() { tailscale debug prefs 2>/dev/null | jsonfilter -e "@.$1"; }
backend() { tailscale status --json 2>/dev/null | jsonfilter -e '@.BackendState'; }
self_ip() { tailscale status --json 2>/dev/null | jsonfilter -e '@.Self.TailscaleIPs[0]'; }

login_to() {
    url=$1
    set -- up "--login-server=$url" "--auth-key=file:$work/authkey" --force-reauth \
        "--accept-routes=$ACCEPT_ROUTES" "--accept-dns=$ACCEPT_DNS" \
        "--snat-subnet-routes=$SNAT" "--netfilter-mode=$NETFILTER" \
        "--advertise-routes=$ROUTES" --ssh=false --shields-up=false --timeout=60s
    [ -z "$HOSTNAME_PREF" ] || set -- "$@" "--hostname=$HOSTNAME_PREF"
    tailscale "$@" >/dev/null 2>&1
}

is_healthy_at() {
    [ "$(backend)" = Running ] && [ "$(prefs_field ControlURL)" = "$1" ] && [ "$(self_ip)" = "$SELF_IP" ]
}

rollback() {
    load_params "$1"
    mkdir "$work/decision.lock" 2>/dev/null || fail 'another decision is in progress'
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
    if is_healthy_at "$OLD_URL" || { login_to "$OLD_URL" && is_healthy_at "$OLD_URL"; }; then
        echo restored > "$work/rollback.status"
        printf 'restored %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "/tmp/headscale-router-result.${work##*.}"
        return 0
    fi
    echo recovery-unverified > "$work/rollback.status"
    return 1
}

case "${1:-}" in
    start)
        [ "$#" = 6 ] || fail 'start OLD_URL NEW_URL TIMEOUT_SECONDS OPERATION_ID KEY_BYTES (auth key on stdin)'
        OLD_URL=$2; NEW_URL=$3; GUARD_SECONDS=$4; OPERATION_ID=$5; KEY_BYTES=$6
        for url in "$OLD_URL" "$NEW_URL"; do
            case "$url" in https://*) domain=${url#https://} ;; *) fail 'URL must be HTTPS' ;; esac
            case "$domain" in ''|*[!A-Za-z0-9.-]*|.*|*.) fail 'invalid control domain' ;; esac
        done
        [ "$OLD_URL" != "$NEW_URL" ] || fail 'old and new URLs must differ'
        case "$GUARD_SECONDS" in ''|*[!0-9]*) fail 'invalid guard duration' ;; esac
        [ "$GUARD_SECONDS" -ge 180 ] && [ "$GUARD_SECONDS" -le 600 ] || fail 'guard duration must be 180..600 seconds'
        case "$OPERATION_ID" in ''|*[!a-f0-9]*) fail 'invalid operation ID' ;; esac
        [ "${#OPERATION_ID}" = 16 ] || fail 'invalid operation ID'
        case "$KEY_BYTES" in ''|*[!0-9]*) fail 'invalid key length' ;; esac
        [ "$KEY_BYTES" -ge 16 ] && [ "$KEY_BYTES" -le 512 ] || fail 'invalid key length'
        command -v setsid >/dev/null 2>&1 || fail 'setsid is required'
        [ "$(backend)" = Running ] && [ "$(prefs_field ControlURL)" = "$OLD_URL" ] || fail 'unexpected active client'
        ps w | grep -q '[t]ailscale-failover' && fail 'stop the multi-network watchdog before migration'
        for url in "$OLD_URL" "$NEW_URL"; do
            [ "$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "$url/health" 2>/dev/null)" = 200 ] || fail 'a control endpoint is unhealthy'
        done
        ACCEPT_ROUTES=$(prefs_field RouteAll); ACCEPT_DNS=$(prefs_field CorpDNS)
        case "$ACCEPT_ROUTES:$ACCEPT_DNS" in true:true|true:false|false:true|false:false) ;; *) fail 'unknown DNS or route preferences' ;; esac
        case "$(prefs_field NoSNAT)" in true) SNAT=false ;; false) SNAT=true ;; *) fail 'unknown subnet SNAT preference' ;; esac
        case "$(prefs_field NetfilterMode)" in 0) NETFILTER=off ;; 1) NETFILTER=nodivert ;; 2) NETFILTER=on ;; *) fail 'unknown netfilter preference' ;; esac
        [ "$(prefs_field RunSSH)" = false ] && [ "$(prefs_field ShieldsUp)" = false ] || fail 'unsupported SSH or shields-up preference'
        [ -z "$(prefs_field ExitNodeID)" ] && [ -z "$(prefs_field ExitNodeIP)" ] || fail 'exit node preference requires manual handling'
        [ -z "$(prefs_field AdvertiseTags)" ] || fail 'tagged node requires manual handling'
        ROUTES=$(prefs_field 'AdvertiseRoutes[*]' | tr '\n' ',' | sed 's/,$//')
        case "$ROUTES" in *[!0-9a-fA-F:./,]*) fail 'invalid advertised routes' ;; esac
        HOSTNAME_PREF=$(prefs_field Hostname)
        case "$HOSTNAME_PREF" in *[!A-Za-z0-9._-]*) fail 'unsupported hostname preference' ;; esac
        SELF_IP=$(self_ip)
        case "$SELF_IP" in ''|*[!0-9.]*) fail 'missing IPv4 identity' ;; esac
        mkdir /tmp/headscale-router-migrate.lock 2>/dev/null || fail 'another migration is in progress'
        work=/tmp/headscale-router-migrate.$OPERATION_ID
        mkdir -m 700 "$work" || { rmdir /tmp/headscale-router-migrate.lock; exit 1; }
        launched=0
        trap '[ "$launched" = 1 ] || { rm -rf "$work"; rmdir /tmp/headscale-router-migrate.lock 2>/dev/null || true; }' EXIT HUP INT TERM
        cp "$0" "$work/worker.sh"
        cat > "$work/authkey"
        [ "$(wc -c < "$work/authkey" | tr -d ' ')" = "$KEY_BYTES" ] || fail 'auth key transfer was incomplete'
        before=$(sha256sum /etc/tailscale/tailscaled.state | awk '{print $1}')
        cp -p /etc/tailscale/tailscaled.state "$work/original.state"
        after=$(sha256sum /etc/tailscale/tailscaled.state | awk '{print $1}')
        copied=$(sha256sum "$work/original.state" | awk '{print $1}')
        [ "$before" = "$after" ] && [ "$before" = "$copied" ] || fail 'identity changed during backup'
        chmod 600 "$work/original.state" "$work/authkey"
        printf "OLD_URL='%s'\nNEW_URL='%s'\nACCEPT_ROUTES='%s'\nACCEPT_DNS='%s'\nSNAT='%s'\nNETFILTER='%s'\nROUTES='%s'\nHOSTNAME_PREF='%s'\nSELF_IP='%s'\n" \
            "$OLD_URL" "$NEW_URL" "$ACCEPT_ROUTES" "$ACCEPT_DNS" "$SNAT" "$NETFILTER" "$ROUTES" "$HOSTNAME_PREF" "$SELF_IP" > "$work/params"
        setsid sh "$work/worker.sh" guard "$work" "$GUARD_SECONDS" </dev/null >"$work/guard.log" 2>&1 &
        launched=1
        setsid sh "$work/worker.sh" apply "$work" </dev/null >"$work/apply.log" 2>&1 &
        printf 'guarded_work=%s\n' "$work"
        ;;
    apply)
        load_params "$2"; : > "$work/APPLYING"; echo $$ > "$work/worker.pid"
        if login_to "$NEW_URL" && is_healthy_at "$NEW_URL"; then
            : > "$work/APPLY_OK"
        else
            : > "$work/FAILED"
            sh "$work/worker.sh" rollback "$work" || true
            exit 1
        fi
        ;;
    guard)
        load_params "$2"; started=$(date +%s); deadline=$((started + $3)); retry_deadline=$((deadline + 600))
        while [ "$(date +%s)" -lt "$deadline" ]; do
            [ ! -e "$work/COMMIT" ] || break
            if [ -e "$work/ROLLBACK" ] && [ -f "$work/rollback.status" ]; then break; fi
            sleep 5
        done
        if [ ! -e "$work/COMMIT" ] && [ ! -e "$work/ROLLBACK" ] && [ -e "$work/APPLYING" ]; then rollback "$work" || true; fi
        while [ -e "$work/ROLLBACK" ] && [ ! -e "$work/COMMIT" ] && \
              [ -f "$work/rollback.status" ] && ! grep -qx restored "$work/rollback.status" && \
              [ "$(date +%s)" -lt "$retry_deadline" ]; do
            sleep 30
            rollback "$work" || true
        done
        if [ -e "$work/COMMIT" ] || [ ! -e "$work/APPLYING" ] || \
           { [ -f "$work/rollback.status" ] && grep -qx restored "$work/rollback.status"; }; then
            rm -rf "$work"; rmdir /tmp/headscale-router-migrate.lock 2>/dev/null || true
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
        [ -e "$work/APPLY_OK" ] || fail 'migration has not passed local checks'
        mkdir "$work/decision.lock" 2>/dev/null || fail 'rollback decision is in progress'
        if [ -e "$work/ROLLBACK" ]; then rmdir "$work/decision.lock"; fail 'rollback already started'; fi
        : > "$work/COMMIT"
        printf 'committed %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "/tmp/headscale-router-result.${work##*.}"
        rmdir "$work/decision.lock"
        echo committed
        ;;
    rollback) rollback "$2" ;;
    *) fail 'use start, status, commit or rollback' ;;
esac
