#!/usr/bin/env bash
# Root-owned service. Only our DNS and protocol traps are reachable from packages.
set -euo pipefail
IPT=/usr/sbin/iptables
[ "$(id -u)" -eq 0 ]
command -v "$IPT" >/dev/null
"$IPT" -w -N HISTOR-OBSERVE 2>/dev/null || true
"$IPT" -w -t nat -N HISTOR-TRAPS 2>/dev/null || true
if [ "${1:-check}" = apply ]; then
  # Block before NAT as well as INPUT: Docker's published mail ports must never
  # receive a sandbox packet, even during a rule rebuild.
  "$IPT" -w -t raw -I PREROUTING 1 -i br-histor-obs -j DROP
  while "$IPT" -w -D INPUT -i br-histor-obs -j HISTOR-OBSERVE 2>/dev/null; do :; done
  "$IPT" -w -F HISTOR-OBSERVE
  "$IPT" -w -A HISTOR-OBSERVE -d 10.231.0.1 -p udp --dport 53 -j ACCEPT
  "$IPT" -w -A HISTOR-OBSERVE -d 10.231.0.1 -p tcp -m multiport --dports 18025,18080,18443,18587 -j ACCEPT
  "$IPT" -w -A HISTOR-OBSERVE -j DROP
  "$IPT" -w -I INPUT 1 -i br-histor-obs -j HISTOR-OBSERVE
  while "$IPT" -w -t nat -D PREROUTING -i br-histor-obs -j HISTOR-TRAPS 2>/dev/null; do :; done
  "$IPT" -w -t nat -F HISTOR-TRAPS
  for ports in 25:18025 80:18080 443:18443 587:18587; do
    "$IPT" -w -t nat -A HISTOR-TRAPS -d 10.231.0.1 -p tcp --dport "${ports%:*}" -j REDIRECT --to-ports "${ports#*:}"
  done
  "$IPT" -w -t nat -I PREROUTING 1 -i br-histor-obs -j HISTOR-TRAPS
  while "$IPT" -w -D DOCKER-USER -i br-histor-obs -j DROP 2>/dev/null; do :; done
  "$IPT" -w -I DOCKER-USER 1 -i br-histor-obs -j DROP
  "$IPT" -w -t raw -D PREROUTING -i br-histor-obs -j DROP
fi
# Compare complete chains and first hooks. A late allow/drop cannot establish isolation.
python3 - <<'PY'
import json, subprocess, pathlib, time

def rules(chain, table='filter'):
    return subprocess.check_output(['/usr/sbin/iptables', '-w', '-t', table, '-S', chain], text=True).splitlines()
assert rules('HISTOR-OBSERVE') == ['-N HISTOR-OBSERVE',
 '-A HISTOR-OBSERVE -d 10.231.0.1/32 -p udp -m udp --dport 53 -j ACCEPT',
 '-A HISTOR-OBSERVE -d 10.231.0.1/32 -p tcp -m multiport --dports 18025,18080,18443,18587 -j ACCEPT',
 '-A HISTOR-OBSERVE -j DROP']
assert rules('INPUT')[1] == '-A INPUT -i br-histor-obs -j HISTOR-OBSERVE'
assert rules('DOCKER-USER')[1] == '-A DOCKER-USER -i br-histor-obs -j DROP'
assert rules('PREROUTING', 'nat')[1] == '-A PREROUTING -i br-histor-obs -j HISTOR-TRAPS'
assert rules('HISTOR-TRAPS', 'nat') == ['-N HISTOR-TRAPS'] + [
 f'-A HISTOR-TRAPS -d 10.231.0.1/32 -p tcp -m tcp --dport {src} -j REDIRECT --to-ports {dst}'
 for src, dst in [(25,18025),(80,18080),(443,18443),(587,18587)]]
assert '-A PREROUTING -i br-histor-obs -j DROP' not in rules('PREROUTING', 'raw')
network = json.loads(subprocess.check_output(['docker', 'network', 'inspect', 'histor-observe']))[0]
assert network['Internal'] and not network.get('EnableIPv6')
assert network['Options'].get('com.docker.network.bridge.name') == 'br-histor-obs'
assert network['IPAM']['Config'] == [{'Subnet':'10.231.0.0/24','Gateway':'10.231.0.1'}]
p = pathlib.Path('/run/histor-firewall'); p.mkdir(exist_ok=True, mode=0o755)
tmp = p / 'healthy.tmp'; tmp.write_text(json.dumps({'verifiedAt': time.time(), 'trapRedirects':1})); tmp.chmod(0o644); tmp.replace(p / 'healthy.json')
PY
