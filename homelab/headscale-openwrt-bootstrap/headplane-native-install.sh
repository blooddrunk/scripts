#!/bin/sh
# Install the pinned upstream build as a native, loopback-only systemd service.
# No Headscale API key or Headscale configuration write access is granted.
set -eu
umask 077

IMAGE='ghcr.io/tale/headplane@sha256:b053a0a6699d0a67be3f0d9bae8e7dbe35ed67ae8448412adcbad7ea49866faa'
RELEASE=/opt/headplane/releases/0.8.2
CONFIG=/etc/headplane
DATA=/var/lib/headplane
UNIT=/etc/systemd/system/headplane.service
DOMAIN=${1:-}
INSTALL_ROLE=${2:-active}

fail() { echo "headplane install: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'root is required'
case "$DOMAIN" in ''|*[!A-Za-z0-9.-]*|.*|*.) fail 'provide the stable control domain' ;; esac
case "$INSTALL_ROLE" in active|prepare-only) ;; *) fail 'role must be active or prepare-only' ;; esac
[ ! -e "$RELEASE" ] && [ ! -e "$CONFIG" ] && [ ! -e "$DATA" ] && [ ! -e "$UNIT" ] || fail 'existing Headplane installation detected; refusing to overwrite'
if ss -lnt | awk '{print $4}' | grep -Eq '(^|:)3000$'; then fail 'port 3000 is already in use'; fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || fail 'pinned upstream image is not available locally'
if id headplane >/dev/null 2>&1; then fail 'headplane system user already exists'; fi
if systemctl cat headplane.service >/dev/null 2>&1; then fail 'headplane systemd unit already exists'; fi

container=
created_user=0
created_unit=0
finished=0
cleanup() {
    if [ -n "$container" ]; then docker rm "$container" >/dev/null 2>&1 || true; fi
    if [ "$finished" != 1 ]; then
        if [ "$created_unit" = 1 ]; then
            systemctl disable --now headplane >/dev/null 2>&1 || true
            rm -f "$UNIT"
            systemctl daemon-reload >/dev/null 2>&1 || true
        fi
        rm -rf "$RELEASE" "$CONFIG" "$DATA"
        [ "$created_user" != 1 ] || userdel headplane >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT HUP INT TERM
useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin headplane
created_user=1
mkdir -p "$RELEASE" "$CONFIG" "$DATA"
chmod 755 /opt/headplane /opt/headplane/releases "$RELEASE"
chown root:headplane "$CONFIG"
chmod 750 "$CONFIG"
chown headplane:headplane "$DATA"
chmod 700 "$DATA"
container=$(docker create "$IMAGE")
docker cp "$container:/app/build" "$RELEASE/build"
docker cp "$container:/app/drizzle" "$RELEASE/drizzle"
docker cp "$container:/nodejs/bin/node" "$RELEASE/node"
docker rm "$container" >/dev/null
container=
chmod -R a+rX "$RELEASE"
"$RELEASE/node" --version | grep -Eq '^v24\.' || fail 'unexpected Node runtime'
if ldd "$RELEASE/node" | grep -q 'not found'; then fail 'Node runtime is missing a shared library'; fi

openssl rand -hex 16 > "$CONFIG/cookie.secret"
chown root:headplane "$CONFIG/cookie.secret"
chmod 640 "$CONFIG/cookie.secret"
cat > "$CONFIG/config.yaml" <<EOF
server:
  host: "127.0.0.1"
  port: 3000
  base_url: "https://$DOMAIN"
  cookie_secret_path: "$CONFIG/cookie.secret"
  cookie_secure: true
  data_path: "$DATA"
headscale:
  url: "http://127.0.0.1:8080"
  public_url: "https://$DOMAIN"
integration:
  agent:
    enabled: false
  proc:
    enabled: false
  docker:
    enabled: false
  kubernetes:
    enabled: false
    pod_name: "unused"
EOF
chown root:headplane "$CONFIG/config.yaml"
chmod 640 "$CONFIG/config.yaml"
(set -C; : > "$UNIT") || fail 'systemd unit appeared during installation'
created_unit=1
cat > "$UNIT" <<EOF
[Unit]
Description=Headplane native web interface
After=network.target headscale.service

[Service]
Type=simple
User=headplane
Group=headplane
WorkingDirectory=$RELEASE
ExecStart=$RELEASE/node $RELEASE/build/server/index.js
Environment=HEADPLANE_CONFIG_PATH=$CONFIG/config.yaml
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$DATA
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$UNIT"
systemctl daemon-reload
if [ "$INSTALL_ROLE" = prepare-only ]; then
    finished=1
    printf 'headplane_prepared version=0.8.2 service=not-started\n'
    exit 0
fi
if ! systemctl enable --now headplane >/dev/null 2>&1; then
    systemctl disable --now headplane >/dev/null 2>&1 || true
    fail 'systemd could not start Headplane'
fi
ok=0
for unused in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    code=$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/admin/ 2>/dev/null || true)
    case "$code" in 200|301|302|303|307|308) ok=1; break ;; esac
    sleep 1
done
if [ "$ok" != 1 ] || ! ss -lnt | awk '{print $4}' | grep -q '^127\.0\.0\.1:3000$'; then
    systemctl disable --now headplane >/dev/null 2>&1 || true
    fail 'loopback HTTP verification failed; service stopped and disabled'
fi
finished=1
printf 'headplane_ready version=0.8.2 bind=127.0.0.1:3000 http=%s\n' "$code"
