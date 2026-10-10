#!/usr/bin/env bash
# Set up the host that runs strangers' MCP packages for HISTOR. Run as root on the sandbox host:
#
#   sudo HISTOR_CALLER_IP=<HISTOR host's IP> ./install.sh     # HTTPS service, firewall opened for that IP only
#   sudo ./install.sh "ssh-ed25519 AAAA…"                     # optionally also an SSH key (forced command)
#
# HTTPS: a self-signed certificate for this host's public IP (HISTOR pins it), a bearer token in
# /etc/histor-sandbox.env (generated once, never printed), systemd unit histor-sandbox, ufw rule.
#
# Needs Docker and gVisor (runsc) registered as a Docker runtime — see docs/operations.md,
# "Package sandbox". Refuses to continue without runsc: the observer must never fall back to runc.
# Idempotent.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME=histor-sandbox
STATE=/var/lib/histor-sandbox
NETWORK=histor-fetch
PUBKEY="${1:-}"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
command -v runsc >/dev/null || { echo "runsc not installed" >&2; exit 1; }
docker info --format '{{json .Runtimes}}' | grep -q '"runsc"' || { echo "runsc is not a Docker runtime (runsc install && systemctl reload docker)" >&2; exit 1; }

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE" --create-home --shell /bin/sh "$USER_NAME"
fi
usermod -aG docker "$USER_NAME"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE" "$STATE/runs" "$STATE/.ssh"
chmod 0700 "$STATE/.ssh"
install -o root -g root -m 0755 "$HERE/histor_observe.py" /usr/local/bin/histor-observe
install -o root -g root -m 0644 "$HERE/histor_sink.py" /usr/local/bin/histor_sink.py

# Resolvers for the install stage (gVisor cannot reach Docker's embedded DNS on a user network).
printf 'nameserver 1.1.1.1\nnameserver 9.9.9.9\noptions timeout:3 attempts:2\n' > "$STATE/resolv.conf"
chmod 0644 "$STATE/resolv.conf"

# Behaviour tracing: a second gVisor runtime that writes its own syscall trace (outside the
# sandbox), an internal network with no route out, and a resolver that is ours.
python3 - <<'PYJSON'
import json
p = "/etc/docker/daemon.json"
d = json.load(open(p))
want = {"path": "/usr/bin/runsc", "runtimeArgs": ["--strace",
        "--strace-syscalls=socket,connect,openat,open,execve,execveat,sendto,sendmsg,sendmmsg,bind,unlinkat,renameat,mkdirat",
        "--strace-log-size=512", "--debug-log=/var/lib/histor-sandbox/trace/%ID%/"]}
if d.setdefault("runtimes", {}).get("runsc-trace") != want:
    d["runtimes"]["runsc-trace"] = want
    json.dump(d, open(p, "w"), indent=4)
    print("runsc-trace registered")
PYJSON
systemctl reload docker
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0755 "$STATE/trace"
docker network inspect histor-observe >/dev/null 2>&1 || docker network create --internal --subnet 10.231.0.0/24 --gateway 10.231.0.1 \
  -o com.docker.network.bridge.name=br-histor-obs -o com.docker.network.bridge.enable_icc=false histor-observe >/dev/null
printf 'nameserver 10.231.0.1\noptions timeout:2 attempts:1\n' > "$STATE/resolv-observe.conf"
chmod 0644 "$STATE/resolv-observe.conf"
# Mandatory independently verified firewall, regardless of UFW installation or state.
install -o root -g root -m 0755 "$HERE/histor-firewall.sh" /usr/local/sbin/histor-firewall
cat > /etc/systemd/system/histor-firewall.service <<'UNIT'
[Unit]
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/histor-firewall apply
UNIT
cat > /etc/systemd/system/histor-firewall.timer <<'UNIT'
[Timer]
OnBootSec=5s
OnUnitActiveSec=30s
AccuracySec=1s
[Install]
WantedBy=timers.target
UNIT
/usr/local/sbin/histor-firewall apply
systemctl daemon-reload
systemctl enable --now histor-firewall.timer
# Traces are written by runsc as root; delete them once read.
cat > /etc/systemd/system/histor-sandbox-trace-clean.service <<'UNIT'
[Unit]
Description=Delete HISTOR sandbox traces once read
[Service]
Type=oneshot
ExecStart=/usr/bin/find /var/lib/histor-sandbox/trace -mindepth 1 -maxdepth 1 -mmin +20 -exec rm -rf {} +
UNIT
cat > /etc/systemd/system/histor-sandbox-trace-clean.timer <<'UNIT'
[Unit]
Description=Delete HISTOR sandbox traces every 10 minutes
[Timer]
OnBootSec=5min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now histor-sandbox-trace-clean.timer >/dev/null 2>&1

# The install stage's network: a bridge on which containers cannot reach each other.
docker network inspect "$NETWORK" >/dev/null 2>&1 || \
  docker network create --driver bridge -o com.docker.network.bridge.enable_icc=false "$NETWORK" >/dev/null

docker pull -q node:22-alpine >/dev/null
docker pull -q python:3.12-slim >/dev/null

if [ -n "$PUBKEY" ]; then
  case "$PUBKEY" in ssh-ed25519\ *) ;; *) echo "expected an ssh-ed25519 public key" >&2; exit 1 ;; esac
  # restrict: no forwarding, no pty, no agent; command=: the key can only ask for observations.
  printf 'restrict,command="/usr/local/bin/histor-observe" %s\n' "$PUBKEY" > "$STATE/.ssh/authorized_keys"
  chown "$USER_NAME:$USER_NAME" "$STATE/.ssh/authorized_keys"
  chmod 0600 "$STATE/.ssh/authorized_keys"
fi

# HTTPS service.
PUBLIC_IP="${HISTOR_SANDBOX_IP:-$(curl -fsS -4 https://api.ipify.org || hostname -I | awk '{print $1}')}"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0700 "$STATE/tls"
if [ ! -s "$STATE/tls/cert.pem" ]; then
  openssl req -x509 -newkey ed25519 -nodes -days 3650 -subj "/CN=histor-sandbox" \
    -addext "subjectAltName=IP:${PUBLIC_IP}" \
    -keyout "$STATE/tls/key.pem" -out "$STATE/tls/cert.pem" 2>/dev/null
  chown "$USER_NAME:$USER_NAME" "$STATE/tls/key.pem" "$STATE/tls/cert.pem"
  chmod 0600 "$STATE/tls/key.pem"
fi
if [ ! -s /etc/histor-sandbox.env ]; then
  umask 077
  printf 'HISTOR_SANDBOX_TOKEN=%s\nHISTOR_SANDBOX_LISTEN=0.0.0.0:9443\nHISTOR_SANDBOX_CERT=%s\nHISTOR_SANDBOX_KEY=%s\nHISTOR_SANDBOX_SLOTS=2\n' \
    "$(openssl rand -hex 32)" "$STATE/tls/cert.pem" "$STATE/tls/key.pem" > /etc/histor-sandbox.env
  chown root:"$USER_NAME" /etc/histor-sandbox.env
  chmod 0640 /etc/histor-sandbox.env
fi
install -o root -g root -m 0644 "$HERE/histor-sandbox.service" /etc/systemd/system/histor-sandbox.service
systemctl daemon-reload
systemctl enable --now histor-sandbox >/dev/null 2>&1
systemctl restart histor-sandbox
if [ -n "${HISTOR_CALLER_IP:-}" ] && command -v ufw >/dev/null; then
  ufw allow from "$HISTOR_CALLER_IP" to any port 9443 proto tcp comment "histor sandbox" >/dev/null
fi
echo "certificate sha256: $(openssl x509 -in "$STATE/tls/cert.pem" -noout -fingerprint -sha256 | cut -d= -f2)"

# Containers a crash left behind (labelled by the observer).
docker ps -aq --filter label=histor-sandbox=1 --filter status=exited | xargs -r docker rm >/dev/null

sudo -u "$USER_NAME" /usr/local/bin/histor-observe selftest
