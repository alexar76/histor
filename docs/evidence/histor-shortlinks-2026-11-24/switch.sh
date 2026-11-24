#!/usr/bin/env bash
# Production HISTOR onto histor-histor:shortlinks-20261124 = the live image of 2026-11-24 06:11Z (tagged
# histor-histor:pre-shortlinks-20261124) plus diffing.py, app.py, security_events.py, desk.js and the five i18n files
# of aicom d3bcc7da3. Compose builds "histor-histor:latest" from /opt/histor, so the switch retags latest and recreates
# the service without building. Rollback: ./switch.sh rollback.
set -euo pipefail
D=/root/histor-shortlinks-20261124
NEW=$(cat $D/new-image-id); OLD=$(cat $D/old-image-id)
C="docker compose -p histor -f docker-compose.yml -f docker-compose.postgres.yml"
cd /opt/histor
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$D/switch.log"; }
crawls_running() {
  echo "SELECT count(*) FROM runs WHERE kind='crawl' AND finished_at IS NULL;" |
    docker exec -i histor-histor-postgres-1 sh -c 'psql -U ${POSTGRES_USER:-histor} -d ${POSTGRES_DB:-histor} -At'
}
up() {
  docker tag "$1" histor-histor:latest
  $C up -d --no-deps --no-build histor >> "$D/switch.log" 2>&1
  for i in $(seq 1 60); do
    s="$(docker inspect -f '{{.State.Health.Status}}' histor-histor-1)"; [ "$s" = healthy ] && break; sleep 5
  done
  log "health=$s image=$(docker inspect -f '{{.Image}}' histor-histor-1)"
  [ "$s" = healthy ] && [ "$(docker inspect -f '{{.Image}}' histor-histor-1)" = "$1" ]
}
back() { [ "$(docker image inspect -f '{{.Id}}' histor-histor:pre-shortlinks-20261124)" = "$OLD" ]; up "$OLD"; }
if [ "${1:-}" = rollback ]; then back && log "ROLLED-BACK"; exit; fi
[ "$(docker image inspect -f '{{.Id}}' histor-histor:shortlinks-20261124)" = "$NEW" ]
[ "$(docker inspect -f '{{.Image}}' histor-histor-1)" = "$OLD" ] || { log "production does not run $OLD; not switching"; exit 1; }
[ "${ALLOW_CRAWL:-0}" = 1 ] || [ "$(crawls_running)" = 0 ] || { log "a crawl is running; not switching"; exit 1; }
log "start"
if ! up "$NEW"; then log "SWITCH-FAILED"; back && log "ROLLED-BACK" || log "ROLLBACK-FAILED"; exit 1; fi
sleep 20
ok=1
for p in / /health /api/v1/stats "/api/v1/changes?limit=2" /feed.xml /assets/desk.js /api/v1/log/checkpoint/cosigned; do
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 --resolve histor.modelmarket.dev:443:127.0.0.1 "https://histor.modelmarket.dev$p")
  log "route $p $c"; [ "$c" = 200 ] || ok=0
done
t=$(docker exec histor-histor-1 python -c "from histor import diffing; print(diffing.short_links(['bit.ly','github.com']))" 2>&1)
v=$(docker exec histor-histor-1 node -e 'console.log(require("/app/scanner/node_modules/@aimarket/warden/package.json").version)' 2>&1)
log "short links $t; vendored warden $v"
[ "$t" = "['bit.ly']" ] || ok=0
[ "$v" = 0.38.0 ] || ok=0
if [ $ok = 1 ]; then log "SWITCH-OK"; else log "CHECK-FAILED: rolling back"; back && log "ROLLED-BACK" || log "ROLLBACK-FAILED"; exit 1; fi
